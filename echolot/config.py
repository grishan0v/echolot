"""Loading and access for echolot.yml.

Deliberately a thin layer: no external schema validation, only what the
detectors actually need. Anything absent from the config falls back to the
default declared in the detector's own .sql file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    pass


# No anchor configured. The string is substituted into a GLOB and must not
# match any real slice name; context.sql then collapses onto the whole trace.
NO_ANCHOR = "__echolot_no_anchor__"


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge: values on the right override the ones on the left."""
    result = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    def __init__(self, raw: dict[str, Any], path: Path | None = None,
                 local_path: Path | None = None):
        self.raw = raw
        self.path = path
        self.local_path = local_path

    @classmethod
    def load(cls, path: str | Path, local: str | Path | None = None) -> "Config":
        """The project config, with local overrides layered on top.

        Reads like `gradle.properties` next to `local.properties`: `echolot.yml`
        is committed and identical for everyone, `local.yml` sits beside it in
        `.gitignore` and holds machine-specific things — device serials, a path
        to your own `trace_processor_shell`. The merge is recursive; local wins.
        """
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config not found: {p}")
        with p.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        local_path = Path(local) if local else p.parent / "local.yml"
        used_local = None
        if local_path.exists():
            with local_path.open(encoding="utf-8") as f:
                overlay = yaml.safe_load(f) or {}
            if not isinstance(overlay, dict):
                raise ConfigError(f"{local_path}: expected a mapping")
            raw = merge(raw, overlay)
            used_local = local_path
        elif local:
            # A file named explicitly but missing is almost certainly a typo.
            raise ConfigError(f"local config not found: {local_path}")

        return cls(raw, p, used_local)

    @property
    def tp_binary(self) -> str | None:
        """Your own trace_processor_shell. Usually arrives from local.yml."""
        value = self.get("toolchain.tp_binary") or self.get("tp_binary")
        return str(value) if value else None

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # --- the fields context.sql relies on ---

    @property
    def process(self) -> str:
        proc = self.get("project.process") or self.get("project.package")
        if not proc:
            raise ConfigError("neither project.process nor project.package set")
        return str(proc)

    @property
    def scenario_start(self) -> str:
        return self._anchor("scenario.start")

    @property
    def scenario_end(self) -> str:
        return self._anchor("scenario.end")

    def _anchor(self, dotted: str) -> str:
        node = self.get(dotted)
        if node is None:
            return NO_ANCHOR
        if isinstance(node, dict):
            name = node.get("name")
            if not name:
                raise ConfigError(f"{dotted}: field 'name' is missing")
            return str(name)
        return str(node)

    @property
    def detector_overrides(self) -> dict[str, dict[str, Any]]:
        node = self.get("detectors") or {}
        if not isinstance(node, dict):
            raise ConfigError("the detectors section must be a mapping")
        return {k: (v or {}) for k, v in node.items()}

    @property
    def enabled_detectors(self) -> set[str] | None:
        """None means every detector found is enabled."""
        node = self.get("detectors")
        if not node:
            return None
        return set(node.keys())

    @property
    def runner(self) -> dict[str, Any]:
        """The runner section. Absence is fine: the defaults are enough."""
        node = self.get("runner") or {}
        if not isinstance(node, dict):
            raise ConfigError("the runner section must be a mapping")
        return node

    @property
    def scenario_name(self) -> str:
        return str(self.get("scenario.name") or "run")

    def context_params(self, upid: int) -> dict[str, Any]:
        """upid is resolved against the trace in main.py — see _resolve_process."""
        return {
            "upid": upid,
            "scenario_start": self.scenario_start,
            "scenario_end": self.scenario_end,
        }
