"""Provider discovery.

Plugin modules expose a module-level ``PROVIDER`` instance; the registry walks
the ``providers`` package and collects them, so adding a site means adding a
folder - no central list to edit.
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache

import providers
from providers.base import ProviderPlugin
from webflow.domain.errors import ProviderNotFoundError
from webflow.logging import get_logger

log = get_logger(__name__)


@lru_cache(maxsize=1)
def discover() -> dict[str, ProviderPlugin]:
    found: dict[str, ProviderPlugin] = {}
    for module_info in pkgutil.walk_packages(providers.__path__, prefix="providers."):
        if module_info.name.endswith((".base", ".registry")):
            continue
        try:
            module = importlib.import_module(module_info.name)
        except Exception as exc:
            log.warning("provider_import_failed", module=module_info.name, error=str(exc))
            continue
        plugin = getattr(module, "PROVIDER", None)
        if isinstance(plugin, ProviderPlugin):
            existing = found.get(plugin.id)
            if existing is not None and existing is not plugin:
                log.warning("provider_id_conflict", provider_id=plugin.id)
            found[plugin.id] = plugin
    log.debug("providers_discovered", providers=sorted(found))
    return found


def get_provider(provider_id: str) -> ProviderPlugin:
    plugins = discover()
    if provider_id not in plugins:
        raise ProviderNotFoundError(
            f"Unknown provider {provider_id!r}. Known: {sorted(plugins)}"
        )
    return plugins[provider_id]


def list_providers() -> list[ProviderPlugin]:
    return list(discover().values())
