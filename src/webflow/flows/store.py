"""Where cached flows live on disk.

Flows are plain versioned JSON so they can be diffed and committed:
``<root>/<provider_id>/<goal>/v<N>.json``. Two roots are consulted - the
provider package (shared, in git) and the local data directory (private
overrides, e.g. a flow you repaired locally but have not committed).
"""

from __future__ import annotations

import re
from pathlib import Path

from webflow.config import get_settings
from webflow.domain.flow import Flow
from webflow.logging import get_logger

log = get_logger(__name__)

_VERSION_FILE = re.compile(r"^v(\d+)\.json$")


class FlowStore:
    def __init__(self, roots: list[Path] | None = None, write_root: Path | None = None) -> None:
        settings = get_settings()
        default_local = settings.data_path / "flows"
        self.roots = roots or [default_local]
        self.write_root = write_root or self.roots[0]

    def _dirs(self, provider_id: str, goal: str) -> list[Path]:
        return [root / provider_id / goal for root in self.roots]

    def versions(self, provider_id: str, goal: str) -> list[int]:
        found: set[int] = set()
        for directory in self._dirs(provider_id, goal):
            if not directory.is_dir():
                continue
            for path in directory.iterdir():
                match = _VERSION_FILE.match(path.name)
                if match:
                    found.add(int(match.group(1)))
        return sorted(found)

    def get(self, provider_id: str, goal: str, version: int) -> Flow | None:
        for directory in self._dirs(provider_id, goal):
            path = directory / f"v{version}.json"
            if path.is_file():
                return Flow.model_validate_json(path.read_text(encoding="utf-8"))
        return None

    def latest(self, provider_id: str, goal: str) -> Flow | None:
        versions = self.versions(provider_id, goal)
        return self.get(provider_id, goal, versions[-1]) if versions else None

    def next_version(self, provider_id: str, goal: str) -> int:
        versions = self.versions(provider_id, goal)
        return (versions[-1] + 1) if versions else 1

    def save(self, flow: Flow, root: Path | None = None) -> Path:
        directory = (root or self.write_root) / flow.provider_id / flow.goal
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"v{flow.version}.json"
        path.write_text(
            flow.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8"
        )
        log.info("flow_saved", flow=flow.slug, path=str(path), steps=len(flow.steps))
        return path

    def prune(self, provider_id: str, goal: str, keep: int = 3) -> list[Path]:
        """Delete all but the newest ``keep`` versions from the writable root."""
        versions = self.versions(provider_id, goal)
        removed: list[Path] = []
        for version in versions[:-keep] if len(versions) > keep else []:
            path = self.write_root / provider_id / goal / f"v{version}.json"
            if path.is_file():
                path.unlink()
                removed.append(path)
        return removed
