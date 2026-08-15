"""Orchestrator: state your requirements once in profile.json, get quotes back.

Usage:
    python main.py --record bilforsikring     # learn a flow interactively, once
    python main.py                            # replay cached flows for all
                                                # products listed in profile.json
                                                # and print the collected quotes
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from Old.insurance_agent.extractor import extract_quotes
from Old.insurance_agent.models import Flow
from Old.insurance_agent.player import FlowPlaybackError, play_flow, play_flow_interactive
from Old.insurance_agent.recorder import PRODUCT_URLS, record_flow

PROFILE_PATH = Path(__file__).parent / "profile.json"
FAILURE_SNAPSHOT_DIR = Path(__file__).parent / "flows" / "failures"
FAILURE_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def load_profile() -> dict:
    if not PROFILE_PATH.exists():
        sys.exit(
            f"No profile.json found. Copy profile.example.json to {PROFILE_PATH.name} "
            "and fill in your requirements."
        )
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def run_product(product: str, profile: dict, headless: bool) -> list[dict]:
    flow = Flow.load(product)
    if flow is None:
        print(f"No cached flow for '{product}' yet -- recording one now (manual walkthrough).")
        flow = record_flow(product, headless=False)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            play_flow(page, flow, profile)
            quotes = extract_quotes(page)
            if not quotes:
                snapshot_path = FAILURE_SNAPSHOT_DIR / f"{product}.png"
                page.screenshot(path=str(snapshot_path))
                print(f"[{product}] flow finished but no quotes were found (url: {page.url}).")
                print(f"[{product}] screenshot saved to {snapshot_path}")
                print(f"[{product}] the flow likely stalled on a validation step; try re-recording: python main.py --record {product}")
        except FlowPlaybackError as e:
            snapshot_path = FAILURE_SNAPSHOT_DIR / f"{product}.png"
            page.screenshot(path=str(snapshot_path))
            print(f"[{product}] cached flow failed at step {e.step_index} (url: {e.url}): {e}")
            print(f"[{product}] screenshot saved to {snapshot_path}")
            print(f"[{product}] re-record it with: python main.py --record {product}")
            quotes = []
        finally:
            browser.close()

    return quotes


def run_product_interactive(product: str, profile: dict, mode: str, delay_ms: int) -> list[dict]:
    flow = Flow.load(product)
    if flow is None:
        print(f"No cached flow for '{product}' yet -- record one first: python main.py --record {product}")
        return []

    quotes: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # step mode is for watching, always visible
        page = browser.new_page()
        modified = False
        try:
            modified = play_flow_interactive(page, flow, profile, mode=mode, delay_ms=delay_ms)
            quotes = extract_quotes(page)
            print(f"[{product}] quotes found: {len(quotes)}")
        except FlowPlaybackError as e:
            print(f"[{product}] step {e.step_index} failed (url: {e.url}): {e}")
        finally:
            if modified:
                answer = input(f"Flow was updated with repaired step(s). Save to {flow.path}? [y/N]: ").strip().lower()
                if answer == "y":
                    flow.save()
                    print(f"[{product}] saved {len(flow.steps)} steps to {flow.path}")
            browser.close()

    return quotes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", choices=sorted(PRODUCT_URLS), help="Interactively (re-)learn a flow")
    parser.add_argument("--headless", action="store_true", help="Run replay without a visible browser")
    parser.add_argument(
        "--step", choices=sorted(PRODUCT_URLS), help="Replay a cached flow step-by-step with the browser visible"
    )
    parser.add_argument(
        "--step-mode", choices=["manual", "auto"], default="manual",
        help="manual = press Enter per step (default); auto = auto-advance with --step-delay",
    )
    parser.add_argument("--step-delay", type=int, default=1000, help="Delay in ms between steps in auto mode")
    args = parser.parse_args()

    if args.record:
        record_flow(args.record, headless=False)
        return

    if args.step:
        profile = load_profile()
        quotes = run_product_interactive(args.step, profile, args.step_mode, args.step_delay)
        print(json.dumps(quotes, indent=2, ensure_ascii=False))
        return

    profile = load_profile()
    products = profile.get("products", [])
    if not products:
        sys.exit("profile.json has no 'products' listed.")

    all_quotes: dict[str, list[dict]] = {}
    for product in products:
        all_quotes[product] = run_product(product, profile, headless=args.headless)

    print("\n=== QUOTES ===")
    print(json.dumps(all_quotes, indent=2, ensure_ascii=False))
    


if __name__ == "__main__":
    main()
