"""Loading and access for echolot.yml.

Deliberately a thin layer: no external schema validation, only what the
detectors actually need. Anything absent from the config falls back to the
default declared in the detector's own .sql file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    pass


# No anchor configured. The string is substituted into a GLOB and must not
# match any real slice name; context.sql then collapses onto the whole trace.
NO_ANCHOR = "__echolot_no_anchor__"


def _load_yaml(p: Path) -> Any:
    """A file that does not parse is a config error, said with the line.

    Left as yaml's own exception it came out of every command as a
    traceback — for a missing colon in echolot.yml.
    """
    try:
        with p.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        problem = getattr(e, "problem", None) or str(e).splitlines()[0]
        raise ConfigError(f"{p}: does not parse{where}: {problem}") from e


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
        raw = _load_yaml(p)
        if not isinstance(raw, dict):
            raise ConfigError(f"{p}: expected a mapping at the top level")

        local_path = Path(local) if local else p.parent / "local.yml"
        used_local = None
        if local_path.exists():
            overlay = _load_yaml(local_path)
            if not isinstance(overlay, dict):
                raise ConfigError(f"{local_path}: expected a mapping")
            raw = merge(raw, overlay)
            used_local = local_path
        elif local:
            # A file named explicitly but missing is almost certainly a typo.
            raise ConfigError(f"local config not found: {local_path}")

        return cls(raw, p, used_local)

    @property
    def sha(self) -> str | None:
        """A short content hash of the file: enough to tell two configs apart.

        Goes into report.json and the run log, so a report can be matched to
        the exact config that produced it — and an edited config shows as a
        different one.
        """
        if self.path is None or not Path(self.path).exists():
            return None
        return hashlib.sha256(Path(self.path).read_bytes()).hexdigest()[:12]

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

    def _detectors(self) -> dict[str, Any]:
        node = self.get("detectors") or {}
        if not isinstance(node, dict):
            raise ConfigError("the detectors section must be a mapping")
        return node

    @property
    def detector_overrides(self) -> dict[str, dict[str, Any]]:
        """Thresholds, per detector. `false` is not a threshold — see below."""
        return {k: (v or {}) for k, v in self._detectors().items() if v is not False}

    @property
    def disabled_detectors(self) -> set[str]:
        """The detectors this config turned off, and only those.

        `detectors:` says what the thresholds are. It used to say *which
        detectors run* as well, and the two are not the same sentence: writing
        down a calibrated number for six detectors switched off the other
        four, which nobody had decided.

        That is how it went wrong on a real project. `calibrate` prints a
        ready section, a human pastes it and tidies the entries that came back
        with nothing but comments — and four detectors left the config that
        way. Three runs in a row then measured six of ten. The report said so
        at the top of every one of them, and being told is not the same as
        having chosen.

        So turning one off is now a thing you write:

            detectors:
              main_thread_block:
                min_slice_ms: 83.3
              frame_jank: false       # this device has no frame timeline

        A section that names some detectors and not others means what it
        looks like it means: those have tuned thresholds, the rest run on the
        numbers they shipped with.
        """
        return {k for k, v in self._detectors().items() if v is False}

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
