"""webflow - a resumable, LLM-driven web-flow agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from webflow.api import answer, gather, pending
    from webflow.preflight import preflight

__version__ = "0.1.0"

__all__ = ["answer", "gather", "pending", "preflight"]

# Lazy, because webflow.api reaches into the orchestrator, which imports
# providers.base - importing a provider first would otherwise deadlock the cycle.
_LAZY = {
    "answer": "webflow.api",
    "gather": "webflow.api",
    "pending": "webflow.api",
    "preflight": "webflow.preflight",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is not None:
        from importlib import import_module

        return getattr(import_module(module_name), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
