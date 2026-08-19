"""Where a project stands with echolot, and what to do next.

`echolot` with no arguments and `/echolot` in an agent both come here. The
question is the same for both — is the layer installed, is there a config, are
there traces, did doctor pass, is an investigation open — and the answer is one
word from NEXT_KINDS that a caller can switch on.

The facts and the decision are kept apart on purpose. `project_state` gathers
and judges nothing; `next_kind` judges and reads nothing from disk. That is
what lets the self-check pin the routing over made-up states instead of
building a project on disk for every case.
"""

from __future__ import annotations

import contextlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import hunt as hunt_mod
from . import layer, recorder
from .config import Config, ConfigError


def project_state(project: Path, config: str = "echolot.yml") -> dict:
    """Where this project stands with echolot: the facts `status` and `init`
    decide the next step from.

    Everything here is read from disk and the run log; nothing runs.
    """
    st: dict = {"project": project}
    st["layer_verdict"], st["layer_line"] = layer.one_line(project)

    cfg_path = project / config
    st["config"] = None
    if cfg_path.exists():
        try:
            cfg = Config.load(cfg_path)
            calibrated = bool(cfg.detector_overrides)
            st["config"] = {
                "path": cfg_path, "scenario": cfg.scenario_name,
                "process": cfg.get("project.process") or cfg.get("project.package"),
                "thresholds": "from the config" if calibrated else "built-in defaults",
                "local": cfg.local_path is not None,
                "runner": str(cfg.runner.get("mode", "launch")) if cfg.runner else None,
                "sha": cfg.sha,
            }
        except ConfigError as e:
            st["config"] = {"path": cfg_path, "error": str(e)}

    traces_dir = project / ".echolot" / "traces"
    traces = [p for pat in ("*.perfetto-trace", "*.pftrace")
              for p in traces_dir.glob(pat)] if traces_dir.is_dir() else []
    st["traces"] = {"dir": traces_dir, "count": len(traces),
                    "newest": max((p.stat().st_mtime for p in traces), default=None)}

    st["report"] = None
    rep = project / ".echolot" / "out" / "report.json"
    if rep.exists():
        try:
            r = json.loads(rep.read_text(encoding="utf-8"))
            s = r.get("summary") or {}
            c = r.get("config") or {}
            st["report"] = {
                "path": rep, "generated_at": r.get("generated_at"),
                "fired": s.get("detectors_fired"), "run": s.get("detectors_run"),
                "runs": len(r.get("traces") or []) or 1,
                "config_sha": c.get("sha"), "defaults": c.get("defaults"),
            }
        except (OSError, ValueError):
            st["report"] = {"path": rep, "error": "unreadable"}

    # Which question all of the above is about. None is a normal answer:
    # every project predates its first investigation.
    st["hunt"] = hunt_mod.load(project)

    st["last_doctor"] = st["last_analyze"] = None
    for run in recorder.read(project / recorder.LOG_FILE):
        if run.get("cmd") == "doctor":
            st["last_doctor"] = run
        elif run.get("cmd") == "analyze":
            st["last_analyze"] = run
    return st


# The next step as one word — what `/echolot` in Claude Code switches on —
# and as the line a person reads. Both from the same decision.
NEXT_KINDS = ("init", "init-force", "doctor", "setup", "fix-config",
              "resume-or-new", "hunt")


def next_kind(st: dict) -> str:
    # `opted-out` falls through on purpose: nothing to install and nothing
    # wrong, so the next step is whatever the config says.
    if st["layer_verdict"] == "absent":
        return "init"
    if st["layer_verdict"] == "stale":
        return "init"
    if st["layer_verdict"] == "differs":
        return "init-force"
    d = st.get("last_doctor")
    if d and d.get("facts", {}).get("failed"):
        return "doctor"
    cfg = st.get("config")
    if not cfg:
        return "setup"
    if cfg.get("error"):
        return "fix-config"
    # There is an investigation open, it left traces or a report behind, and
    # enough time has passed that the human may have come back for something
    # else entirely. The CLI does not ask — it says the answer is open, and
    # the agent puts the question with the recap `status` prints below.
    if hunt_mod.needs_choice(st.get("hunt"), st):
        return "resume-or-new"
    return "hunt"


def _door(st: dict) -> str:
    """How this project's agent is reached, in its own words.

    Leading with "/echolot in Claude Code" on a project that has just declined
    the layer names a command its human does not have.
    """
    if st.get("layer_verdict") == "opted-out":
        return "`echolot guide"
    return "/echolot in Claude Code, or `echolot guide"


def next_step(st: dict) -> str:
    """One line: what to do next, from the state. Shared by status and init."""
    kind = next_kind(st)
    if kind == "init":
        if st["layer_verdict"] == "absent":
            return "echolot init — installs the .claude/ layer; then /echolot in Claude Code"
        return "echolot init — brings the .claude/ layer up to date (the agent reads it)"
    if kind == "init-force":
        return ("echolot init --force — the .claude/ layer differs from the package's and "
                "nothing says whether you edited it; --force overwrites, keep your edits with git")
    if kind == "doctor":
        return "echolot doctor — the last self-check failed; no report is trustworthy until it passes"
    if kind == "setup":
        return (f"{_door(st)} setup` — echolot.yml from the repository "
                f"and a probe trace")
    if kind == "fix-config":
        return f"fix echolot.yml — it does not load: {st['config']['error']}"
    if kind == "resume-or-new":
        q = (st.get("hunt") or {}).get("question") or "the earlier question"
        return (f'/echolot in Claude Code — it will ask whether to carry on with '
                f'"{q}" or start a new investigation')
    if not st["traces"]["count"]:
        return (f"{_door(st)} hunt` — or by hand: "
                f"echolot collect -c echolot.yml -n 5")
    return (f"{_door(st)} hunt` — or by hand: "
            f"echolot analyze .echolot/traces/*.perfetto-trace -c echolot.yml")


def ago(epoch: float | None) -> str:
    if not epoch:
        return "never"
    delta = time.time() - epoch
    if delta < 90:
        return f"{int(delta)}s ago"
    if delta < 5400:
        return f"{int(delta // 60)}m ago"
    if delta < 172800:
        return f"{delta / 3600:.0f}h ago"
    return f"{delta / 86400:.0f}d ago"


def iso_epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
