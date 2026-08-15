"""Command-line entry point.

Thin wrapper around the three public verbs (``gather``, ``pending``, ``answer``)
plus ``preflight`` and ``providers``. No behaviour lives here beyond argument
parsing and printing - see ``webflow.api``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

import webflow


def _split_field(raw: str) -> tuple[str, str]:
    key, sep, value = raw.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"Expected key=value, got {raw!r}")
    return key, value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="webflow",
        description="Resumable, LLM-driven web-flow agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_preflight = sub.add_parser(
        "preflight", help="Check the browser, LLM, profile and providers without a real run."
    )
    p_preflight.add_argument(
        "targets", nargs="*", help="provider/goal targets to check, e.g. forsikringsguiden/bilforsikring"
    )
    p_preflight.add_argument(
        "--no-probe-llm",
        action="store_true",
        help="Skip the live (paid) call that verifies the LLM actually responds.",
    )

    sub.add_parser("providers", help="List discovered providers and their goals.")

    p_gather = sub.add_parser("gather", help="Run one or more provider/goal targets.")
    p_gather.add_argument(
        "targets", nargs="+", help="provider/goal targets, e.g. forsikringsguiden/bilforsikring"
    )
    p_gather.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window instead of running headless.",
    )
    p_gather.add_argument(
        "--interactive",
        action="store_true",
        help=(
            "Open a visible browser and let you take over on errors or questions "
            "instead of suspending the run; your actions are logged and reviewed "
            "by the planner to update the learned flow. Implies --headed."
        ),
    )

    p_pending = sub.add_parser("pending", help="List runs currently waiting for a human.")
    p_pending.add_argument("--provider", default=None, help="Filter by provider id.")

    p_answer = sub.add_parser("answer", help="Answer the question blocking a paused run.")
    p_answer.add_argument("run_id", help="Run id shown by 'webflow pending'.")
    p_answer.add_argument(
        "--field",
        action="append",
        dest="fields",
        default=[],
        type=_split_field,
        metavar="KEY=VALUE",
        help="A field to answer, e.g. --field annual_km=15000. Repeatable.",
    )
    p_answer.add_argument(
        "--abort", action="store_true", help="Abandon the run instead of answering it."
    )
    p_answer.add_argument(
        "--no-resume", action="store_true", help="Record the answer but don't continue the run."
    )
    p_answer.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window instead of running headless.",
    )
    p_answer.add_argument(
        "--interactive",
        action="store_true",
        help=(
            "Resume with a visible browser and take over on further errors or "
            "questions instead of suspending again. Implies --headed."
        ),
    )

    return parser


async def _run_preflight(targets: list[str], *, probe_llm: bool) -> int:
    report = await webflow.preflight(targets, probe_llm=probe_llm)
    print(report)
    return 0 if report.ready else 1


async def _run_providers() -> int:
    from providers.registry import list_providers

    plugins = list_providers()
    if not plugins:
        print("No providers discovered.")
        return 1
    for plugin in plugins:
        print(plugin.id)
        for goal_name in plugin.goals:
            print(f"  {plugin.id}/{goal_name}")
    return 0


async def _run_gather(targets: list[str], *, headed: bool, interactive: bool) -> int:
    batch = await webflow.gather(targets, headless=not headed, interactive=interactive)
    print(batch.summary())
    return 1 if batch.error_count else 0


async def _run_pending(provider_id: str | None) -> int:
    questions = await webflow.pending(provider_id)
    if not questions:
        print("Nothing pending.")
        return 0
    for question in questions:
        print(question.describe())
    return 0


async def _run_answer(
    run_id: str,
    fields: list[tuple[str, str]],
    *,
    abort: bool,
    resume: bool,
    headed: bool,
    interactive: bool,
) -> int:
    values: dict[str, str] = dict(fields)
    outcome = await webflow.answer(
        run_id,
        values or None,
        aborted=abort,
        resume=resume,
        headless=not headed,
        interactive=interactive,
    )
    print(outcome)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    coro: Any
    if args.command == "preflight":
        coro = _run_preflight(args.targets, probe_llm=not args.no_probe_llm)
    elif args.command == "providers":
        coro = _run_providers()
    elif args.command == "gather":
        coro = _run_gather(args.targets, headed=args.headed, interactive=args.interactive)
    elif args.command == "pending":
        coro = _run_pending(args.provider)
    elif args.command == "answer":
        coro = _run_answer(
            args.run_id,
            args.fields,
            abort=args.abort,
            resume=not args.no_resume,
            headed=args.headed,
            interactive=args.interactive,
        )
    else:  # pragma: no cover - argparse enforces valid subcommands
        parser.error(f"Unknown command {args.command!r}")
        return 2

    return asyncio.run(coro)


if __name__ == "__main__":
    sys.exit(main())
