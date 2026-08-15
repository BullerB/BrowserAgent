"""Human take-over inside a live (headed) browser.

``interactive`` mode keeps the browser visible and, whenever the run would
otherwise stop and wait, hands the page to a human instead. Whatever they
click or fill is captured with a tiny injected script, turned into the same
:class:`~webflow.domain.actions.Action` objects the agent itself would
produce, and handed back to the run so it can be reviewed by the planner and
folded into the learned flow.
"""

from __future__ import annotations

import asyncio
from typing import Any

from playwright.async_api import Page

from webflow.domain.actions import Action, ClickAction, FillAction
from webflow.domain.selectors import Selector, SelectorSet
from webflow.domain.values import ValueSource
from webflow.logging import get_logger

log = get_logger(__name__)

_RECORDER_JS = """
(() => {
  if (window.__webflowInteractiveInstalled) return;
  window.__webflowInteractiveInstalled = true;

  function cssPath(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    const path = [];
    while (el && el.nodeType === 1 && el.tagName.toLowerCase() !== 'html') {
      let sel = el.tagName.toLowerCase();
      if (el.parentElement) {
        const sibs = Array.from(el.parentElement.children).filter((c) => c.tagName === el.tagName);
        if (sibs.length > 1) sel += ':nth-of-type(' + (sibs.indexOf(el) + 1) + ')';
      }
      path.unshift(sel);
      el = el.parentElement;
    }
    return path.join(' > ');
  }

  function describe(el) {
    return {
      tag: el.tagName ? el.tagName.toLowerCase() : '',
      id: el.id || null,
      name: el.getAttribute ? el.getAttribute('name') : null,
      input_type: el.getAttribute ? el.getAttribute('type') : null,
      role: el.getAttribute ? el.getAttribute('role') : null,
      test_id: el.getAttribute ? el.getAttribute('data-testid') : null,
      text: (el.innerText || el.value || '').toString().slice(0, 80),
      css: cssPath(el),
    };
  }

  document.addEventListener(
    'click',
    (e) => {
      const el = e.target.closest(
        "button, a, [role='button'], input[type='checkbox'], input[type='radio'], label"
      );
      if (!el || !window.__webflowRecordEvent) return;
      window.__webflowRecordEvent({ kind: 'click', element: describe(el) });
    },
    true
  );

  document.addEventListener(
    'change',
    (e) => {
      const el = e.target;
      if (!el || !('value' in el) || !window.__webflowRecordEvent) return;
      const sensitive = (el.getAttribute('type') || '').toLowerCase() === 'password';
      window.__webflowRecordEvent({
        kind: 'change',
        element: describe(el),
        value: sensitive ? null : el.value,
        sensitive,
      });
    },
    true
  );
})();
"""

_BANNER_JS = """
(text) => {
  const old = document.getElementById('__webflow_interactive_banner');
  if (old) old.remove();
  const bar = document.createElement('div');
  bar.id = '__webflow_interactive_banner';
  bar.style.cssText =
    'position:fixed;bottom:0;left:0;right:0;z-index:2147483647;' +
    'background:#1f2933;color:#fff;padding:10px 16px;font:14px sans-serif;' +
    'display:flex;align-items:center;justify-content:space-between;gap:12px;';
  const label = document.createElement('span');
  label.textContent = text;
  const button = document.createElement('button');
  button.textContent = 'Resume automation';
  button.style.cssText =
    'background:#2563eb;color:#fff;border:none;padding:8px 14px;border-radius:4px;cursor:pointer;';
  button.onclick = () => window.__webflowResume && window.__webflowResume();
  bar.appendChild(label);
  bar.appendChild(button);
  document.body.appendChild(bar);
}
"""

_REMOVE_BANNER_JS = "document.getElementById('__webflow_interactive_banner')?.remove()"


class InteractiveRecorder:
    """Captures clicks and field changes a human makes on ``page``."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.events: list[dict[str, Any]] = []
        self._resume = asyncio.Event()
        self._bound = False

    async def start(self, banner_text: str) -> None:
        """Install listeners, show the take-over banner, and start recording."""
        if not self._bound:
            await self.page.expose_binding("__webflowRecordEvent", self._on_event)
            await self.page.expose_binding("__webflowResume", self._on_resume)
            self._bound = True
        await self.page.add_init_script(_RECORDER_JS)
        await self.page.evaluate(_RECORDER_JS)
        await self.page.evaluate(_BANNER_JS, banner_text)
        log.info("interactive_takeover_started", url=self.page.url)

    async def _on_event(self, source: Any, payload: dict[str, Any]) -> None:
        self.events.append(payload)

    async def _on_resume(self, source: Any = None) -> None:
        self._resume.set()

    async def wait_for_resume(self, timeout_ms: int | None = None) -> None:
        """Block until the human clicks 'Resume automation'."""
        if timeout_ms is None:
            await self._resume.wait()
        else:
            await asyncio.wait_for(self._resume.wait(), timeout_ms / 1000)

    async def stop(self) -> list[dict[str, Any]]:
        """Remove the banner and return every captured raw event."""
        try:
            await self.page.evaluate(_REMOVE_BANNER_JS)
        except Exception:  # pragma: no cover - page may have navigated away
            pass
        return self.events

    def to_actions(self) -> list[Action]:
        """Turn captured DOM events into replayable :class:`Action` objects."""
        actions: list[Action] = []
        for event in self.events:
            element = event.get("element") or {}
            target = _selector_set(element)
            if target is None:
                continue
            if event["kind"] == "click":
                actions.append(
                    ClickAction(target=target, reasoning="captured from human demonstration")
                )
            elif event["kind"] == "change":
                value = event.get("value")
                if value is None:
                    continue  # sensitive or unreadable field; do not learn secrets
                actions.append(
                    FillAction(
                        target=target,
                        value=ValueSource.of(value),
                        reasoning="captured from human demonstration",
                    )
                )
        return actions


def _selector_set(element: dict[str, Any]) -> SelectorSet | None:
    candidates: list[Selector] = []
    if element.get("test_id"):
        candidates.append(Selector(kind="test_id", value=element["test_id"]))
    if element.get("id"):
        candidates.append(Selector(kind="element_id", value=element["id"]))
    if element.get("name"):
        candidates.append(Selector(kind="name_attr", value=element["name"]))
    if element.get("css"):
        candidates.append(Selector(kind="css", value=element["css"]))
    if not candidates:
        return None
    description = element.get("text") or element.get("tag") or ""
    return SelectorSet(candidates=candidates, description=description)


__all__ = ["InteractiveRecorder"]
