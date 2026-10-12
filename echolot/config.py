"""Loading and access for echolot.yml.

Deliberately a thin layer: no external schema validation, only what the
detectors actually need. Anything absent from the config falls back to the
default declared in the detector's own .sql file.
"""

from __future__ import annotations

import hashlib
import re
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
    except UnicodeDecodeError as e:
        # A config saved in another encoding, with one non-ASCII comment.
        raise ConfigError(f"{p}: is not UTF-8 (byte {e.start}): save it as UTF-8") from e
    except OSError as e:
        raise ConfigError(f"{p}: cannot be read: {e.strerror or e}") from e


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge: values on the right override the ones on the left."""
    result = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


# A scenario's name goes into the names of its traces,
# `feed_scroll_iter000.perfetto-trace`, so it is held to what a file name
# carries everywhere.
_SCENARIO_NAME = re.compile(r"[A-Za-z0-9][\w.-]*")
# What a scenario under `scenarios:` may hold: what `scenario:` holds, less
# the name its key already gives, and the runner keys and the thresholds it
# changes.
_SCENARIO_KEYS = ("start", "end", "budget_ms", "runner", "detectors")


def _detectors_section(node: Any, where: str) -> dict[str, Any]:
    """A `detectors:` section, refused in a sentence when it has no shape.

    Under a detector go its thresholds, or `false`, or nothing at all.
    `frame_jank: true` used to pass here and fail two calls later as
    `dict(True)` — a traceback for what reads like switching a detector
    on, which is what every detector already is. `where` is the section's
    place in the file: `detectors`, or a scenario's own.
    """
    node = node or {}
    if not isinstance(node, dict):
        lead = "the detectors section" if where == "detectors" else where
        raise ConfigError(
            f"{lead} must be a mapping of detector names, "
            f"got a {type(node).__name__}. Each detector is a key: "
            f"`frame_jank: false` turns one off, and thresholds go under "
            f"its name — `main_thread_block:` with `min_slice_ms: 16` "
            f"beneath it.")
    for name, value in node.items():
        if value is None or value is False or isinstance(value, dict):
            continue
        said = "true" if value is True else repr(value)
        raise ConfigError(
            f"{where}.{name}: {said} is not a setting. Every detector "
            f"runs unless the config turns it off: leave `{name}` out to "
            f"run it on its shipped thresholds, write thresholds under it "
            f"to tune it (`echolot explain` lists them), or `{name}: "
            f"false` to turn it off.")
    return node


class Config:
    def __init__(self, raw: dict[str, Any], path: Path | None = None,
                 local_path: Path | None = None):
        self.raw = raw
        self.path = path
        self.local_path = local_path
        # Which of `scenarios:` the scenario's accessors read. None is the
        # first, and the only one of a config with one `scenario:`.
        self.scenario_key: str | None = None

    @classmethod
    def load(cls, path: str | Path, local: str | Path | None = None,
             scenario: str | None = None) -> "Config":
        """The project config, with local overrides layered on top, reading
        the scenario called `scenario` (see `select`).

        Reads like `gradle.properties` next to `local.properties`: `echolot.yml`
        is committed and identical for everyone, `local.yml` sits beside it in
        `.gitignore` and holds machine-specific things — device serials, a path
        to your own `trace_processor_shell`. The merge is recursive; local wins.
        """
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config not found: {p}")
        if not p.is_file():
            raise ConfigError(f"{p}: is not a file — -c takes the path to echolot.yml")
        raw = _load_yaml(p)
        if not isinstance(raw, dict):
            raise ConfigError(f"{p}: expected a mapping at the top level")

        local_path = Path(local) if local else p.parent / "local.yml"
        used_local = None
        if local_path.exists():
            if not local_path.is_file():
                raise ConfigError(f"{local_path}: is not a file")
            overlay = _load_yaml(local_path)
            if not isinstance(overlay, dict):
                raise ConfigError(f"{local_path}: expected a mapping")
            raw = merge(raw, overlay)
            used_local = local_path
        elif local:
            # A file named explicitly but missing is almost certainly a typo.
            raise ConfigError(f"local config not found: {local_path}")

        cfg = cls(raw, p, used_local)
        # Checked on load rather than on first use, because the first use
        # is not always inside a command that turns a ConfigError into a
        # sentence: `reflect` reads the thresholds for its snapshot outside
        # any such handler, and `detectors:` written as a list took it down
        # with a traceback. Every loader already answers a config that does
        # not load. The scenarios too, for the same reason.
        cfg._detectors()
        cfg.select(scenario)
        return cfg

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
        """Your own trace_processor_shell. Usually arrives from local.yml.

        `~` is expanded and a relative path is taken from the config's
        directory, as `project.mapping` is: `analyze` is often run from the
        directory that holds the traces, and `tools/trace_processor_shell`
        resolved there was no file. A bare name is a command on PATH unless
        the config's directory holds a file of that name.
        """
        value = self.get("toolchain.tp_binary") or self.get("tp_binary")
        if not value:
            return None
        text = str(value)
        path = Path(text).expanduser()
        if path.is_absolute() or self.path is None:
            return str(path)
        beside = Path(self.path).parent / path
        if len(path.parts) == 1 and not text.startswith(".") and not beside.is_file():
            return text
        return str(beside)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def confirmed(self) -> dict[str, Any]:
        """Every value a person confirmed, by its dotted key.

        Setup marks a block `_source: confirmed_by_user` when a human chose
        it — the end of the scenario, most often — and that choice is theirs:
        an agent that finds it wrong asks, and does not change it. Each key of
        such a block is listed, the provenance keys (`_source`, `_evidence`)
        left out.
        """
        found: dict[str, Any] = {}

        def walk(node: Any, prefix: str) -> None:
            if not isinstance(node, dict):
                return
            if node.get("_source") == "confirmed_by_user":
                for key, value in node.items():
                    if not str(key).startswith("_"):
                        found[f"{prefix}{key}"] = value
            for key, value in node.items():
                walk(value, f"{prefix}{key}.")

        walk(self.raw, "")
        return found

    # --- the fields context.sql relies on ---

    @property
    def process(self) -> str:
        proc = self.get("project.process") or self.get("project.package")
        if not proc:
            raise ConfigError("neither project.process nor project.package set")
        return str(proc)

    @property
    def mapping(self) -> Path | None:
        """The build's R8 or ProGuard `mapping.txt`, or None when there is none to read.

        A relative path is taken from the config's directory, where the build
        outputs live as well. The mapping names a package's frames back, so it
        needs `project.package`: the package as installed on the device, which
        for a benchmark build is often the release's with a suffix.

        A file that is not there yet is no mistake of the config's: AGP
        writes it when the variant is built, and `scan` names it before
        anything has been. It reads as None here, and `missing_mapping`
        names it for whoever says so.
        """
        path = self._mapping_named()
        return path if path is not None and path.is_file() else None

    @property
    def missing_mapping(self) -> Path | None:
        """The mapping the config names, when there is no file there yet."""
        path = self._mapping_named()
        return path if path is not None and not path.is_file() else None

    def _mapping_named(self) -> Path | None:
        """The path `project.mapping` names, whether or not the file is there.

        What the value cannot mean is the config's mistake and is refused: a
        value that is no path, and a mapping without the package it is for.
        """
        value = self.get("project.mapping")
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ConfigError(
                f"project.mapping must be a path to the build's mapping.txt — "
                f"got {value!r}")
        if not self.get("project.package"):
            raise ConfigError(
                "project.mapping needs project.package: the mapping names back "
                "the frames of that package, as it is installed on the device")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (Path(self.path).parent if self.path else Path.cwd()) / path
        return path

    @property
    def scenario_start(self) -> str:
        return self._anchor("start")

    @property
    def scenario_end(self) -> str:
        return self._anchor("end")

    @property
    def scenarios(self) -> list[str]:
        """The scenarios this config names, in its order.

        `scenarios:` maps each name to a scenario; `scenario:` is one, named
        by its `name`. A config with neither has one scenario too, the whole
        trace, named `run` as before.
        """
        many = self._many()
        return list(many) if many is not None else [self.scenario_name]

    def select(self, name: str | None) -> "Config":
        """Read the scenario called `name` from here on: its window, its
        name, and its runner keys and thresholds over the config's.

        None reads the first. A name this config does not have is refused,
        with the names it does have.
        """
        many = self._many()
        if name is None:
            self.scenario_key = None
        elif many is None:
            if name != self.scenario_name:
                raise ConfigError(
                    f"--scenario {name}: this config has one scenario, "
                    f"`{self.scenario_name}`. Name several under `scenarios:` "
                    f"to choose among them")
        elif name not in many:
            raise ConfigError(
                f"--scenario {name}: no such scenario in this config. It has "
                + ", ".join(f"`{n}`" for n in many))
        else:
            self.scenario_key = name
        return self

    def _many(self) -> dict[str, dict[str, Any]] | None:
        """`scenarios:`, refused in a sentence when it has no shape; None
        for a config that writes one `scenario:` or none."""
        many = self.get("scenarios")
        if many is None:
            return None
        if self.get("scenario") is not None:
            raise ConfigError(
                "scenario and scenarios are two ways to write the scenarios: "
                "keep one. One scenario goes under `scenario:`; several go "
                "under `scenarios:`, each named by its key")
        if not isinstance(many, dict) or not many:
            raise ConfigError(
                f"scenarios must map a name to a scenario, got {many!r}: "
                f"`scenarios:`, then `cold_start:` indented, with its `start` "
                f"and `end` under it")
        out: dict[str, dict[str, Any]] = {}
        for name, block in many.items():
            if not isinstance(name, str) or not _SCENARIO_NAME.fullmatch(name):
                raise ConfigError(
                    f"scenarios: {name!r} cannot name a scenario. The name goes "
                    f"into its traces' file names: letters, digits, `_`, `.` "
                    f"and `-`, starting with a letter or a digit")
            block = {} if block is None else block
            if not isinstance(block, dict):
                raise ConfigError(
                    f"scenarios.{name} must be a mapping with `start` and "
                    f"`end` under it, got {block!r}")
            if "name" in block:
                raise ConfigError(
                    f"scenarios.{name}: a scenario under `scenarios:` is named "
                    f"by its key, so it has no `name`")
            unknown = [k for k in block
                       if k not in _SCENARIO_KEYS and not str(k).startswith("_")]
            if unknown:
                raise ConfigError(
                    f"scenarios.{name}: no key {', '.join(repr(k) for k in unknown)} "
                    f"in a scenario. It takes {', '.join(_SCENARIO_KEYS)}")
            if block.get("runner") is not None and not isinstance(block["runner"], dict):
                raise ConfigError(
                    f"scenarios.{name}.runner must be a mapping of the runner "
                    f"keys this scenario changes, got {block['runner']!r}")
            _detectors_section(block.get("detectors"), f"scenarios.{name}.detectors")
            out[name] = block
        return out

    def _scenario(self) -> tuple[str, dict[str, Any]]:
        """The scenario the accessors read: where it is written, and its block."""
        many = self._many()
        if many is None:
            node = self.get("scenario")
            return "scenario", node if isinstance(node, dict) else {}
        name = self.scenario_key or next(iter(many))
        return f"scenarios.{name}", many[name]

    def _anchor(self, key: str) -> str:
        where, block = self._scenario()
        dotted = f"{where}.{key}"
        node = block.get(key)
        if node is None:
            return NO_ANCHOR
        if isinstance(node, dict):
            if "name" not in node:
                raise ConfigError(
                    f"{dotted}: the block has no `name`. Write the slice name "
                    f"there, or `name: null` when nothing was found")
            name = node["name"]
            # `name: null` beside `_source` and `_evidence` is how setup
            # writes "nothing found", and it means no anchor.
            if name is None or str(name).strip() == "":
                return NO_ANCHOR
            return str(name)
        return str(node)

    def _shared_detectors(self) -> dict[str, Any]:
        """The top-level `detectors:` section, every scenario's."""
        return _detectors_section(self.get("detectors"), "detectors")

    def _own_detectors(self) -> dict[str, Any]:
        """The selected scenario's own `detectors:`, empty for a config with
        one `scenario:`, which keeps its thresholds at the top level."""
        where, block = self._scenario()
        if where == "scenario":
            # Read nowhere, and so refused rather than left to look as if it
            # tuned something: one scenario's thresholds are the config's.
            if block.get("detectors") is not None:
                raise ConfigError(
                    "scenario.detectors: a config with one scenario keeps its "
                    "thresholds in the top-level `detectors:`. A scenario's own "
                    "go under its name in `scenarios:`, beside the others'")
            return {}
        return _detectors_section(block.get("detectors"), f"{where}.detectors")

    def _detectors(self) -> dict[str, Any]:
        """The thresholds this scenario runs on: `detectors:`, with the
        scenario's own section laid over it, a threshold at a time.

        Two journeys of one app can differ two- or threefold in what is
        normal for them, and one set calibrated on either hid rows in the
        other or listed the other's normal run (#351). So a scenario under
        `scenarios:` may carry a `detectors:` of its own. What it names wins
        for it alone; what it leaves out comes from the top. `false` there
        turns a detector off for that scenario, thresholds there turn on one
        the top turned off, and an entry with nothing under it adds nothing.
        """
        out = dict(self._shared_detectors())
        for name, value in self._own_detectors().items():
            if value is None:
                continue
            base = out.get(name)
            out[name] = (merge(base, value) if isinstance(value, dict) and isinstance(base, dict)
                         else value)
        return out

    @property
    def detector_overrides(self) -> dict[str, dict[str, Any]]:
        """Thresholds, per detector. `false` is not a threshold — see below.

        An entry with nothing under it tunes nothing: `calibrate` prints the
        name with only comments beneath for a threshold it could not derive,
        and pasted as it was, it made `status` and `analyze` say the config
        tuned a detector that ran on its defaults.
        """
        return {k: v for k, v in self._detectors().items() if v}

    @property
    def scenario_overrides(self) -> dict[str, dict[str, Any]]:
        """The thresholds the selected scenario sets itself, per detector:
        what makes a detector's `params_source` say `scenario`."""
        return {k: v for k, v in self._own_detectors().items() if isinstance(v, dict) and v}

    @property
    def shared_overrides(self) -> dict[str, dict[str, Any]]:
        """The thresholds of the top-level `detectors:`, per detector."""
        return {k: v for k, v in self._shared_detectors().items() if isinstance(v, dict) and v}

    @property
    def overrides_by_scenario(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Each scenario's own thresholds, by name, for the ones that set
        any, whichever is selected; empty for a config with one `scenario:`."""
        out = {}
        for name, block in (self._many() or {}).items():
            own = _detectors_section(block.get("detectors"), f"scenarios.{name}.detectors")
            mine = {k: v for k, v in own.items() if isinstance(v, dict) and v}
            if mine:
                out[name] = mine
        return out

    @property
    def scenario_disabled(self) -> set[str]:
        """The detectors the selected scenario turns off itself."""
        return {k for k, v in self._own_detectors().items() if v is False}

    def unknown_detectors(self, known: set[str]) -> None:
        """Refuse a name under `detectors:` that is no detector.

        `frame_jnk: false` left `frame_jank` running, and the thresholds under
        a misspelled name were dropped, both without a word — while `--set`
        refuses the same typo, because a silently ignored one is worse than
        none. Every scenario's own section is read too, the ones not being
        measured included: a typo there would wait for the day that scenario
        is measured.
        """
        sections = [("detectors", self._shared_detectors())]
        sections += [(f"scenarios.{name}.detectors",
                      _detectors_section(block.get("detectors"), f"scenarios.{name}.detectors"))
                     for name, block in (self._many() or {}).items()]
        for where, section in sections:
            unknown = sorted(str(k) for k in section if k not in known)
            if unknown:
                names = ", ".join(f"'{u}'" for u in unknown)
                raise ConfigError(
                    f"{where}: no detector {names}. Known: {', '.join(sorted(known))}")

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
        """The runner section, with the selected scenario's runner keys laid
        over it. Absence is fine: the defaults are enough."""
        node = self.get("runner") or {}
        if not isinstance(node, dict):
            # `runner: gradle` is the likely shape of it: the mode written
            # where the section goes.
            raise ConfigError(
                f"the runner section must be a mapping, got {node!r}. The mode "
                f"is one key under it: `runner:`, then `mode: "
                f"{node if node in ('launch', 'command', 'gradle') else 'launch'}`"
                f" on the next line, indented.")
        where, block = self._scenario()
        own = block.get("runner") if where != "scenario" else None
        return merge(node, own) if own else node

    @property
    def report_dir(self) -> str:
        """Where `analyze` writes the report by default, from the config's
        directory, and where `report`, `compare` and `status` look for it.

        `.echolot/out`, as it has always been. A config that names its
        scenarios under `scenarios:` keeps a directory per scenario under it,
        `.echolot/out/feed_scroll`, so that measuring one does not overwrite
        the other's latest report.
        """
        if self.get("scenarios") is None:
            return ".echolot/out"
        return f".echolot/out/{self.scenario_name}"

    @property
    def scenario_name(self) -> str:
        """What the scenario is called, and what its traces are named after."""
        where, block = self._scenario()
        if where != "scenario":
            return where.split(".", 1)[1]
        return str(block.get("name") or "run")

    def context_params(self, upid: int) -> dict[str, Any]:
        """upid is resolved against the trace in main.py — see _resolve_process."""
        return {
            "upid": upid,
            "scenario_start": self.scenario_start,
            "scenario_end": self.scenario_end,
        }
