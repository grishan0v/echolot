"""Signals: the detectors over a session.

Each signal is one small function over the normalised session and the derived
facts. It returns a `Signal` — or None when it has nothing to say. Add a
function, append it to SIGNALS, done: there is no registration elsewhere.

Three severities:

    warn   the protocol was broken or the tool failed — look at it
    info   friction or a workaround: a hint at what the CLI could absorb
    ok     a protocol check that passed; kept so the report shows it

A `hint` is one line: what this usually means for the tool. It is a pointer,
not a conclusion — the reasoning is left to whoever reads the report.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from .facts import (
    RE_ECHOLOT,
    RE_RE_RECORD,
    RE_REPORT_BY_HAND,
    RE_TRACE_LITERAL,
    RE_TRACE_OPEN,
    Facts,
    config_writes,
)
from .model import MAIN, Session, ts_to_epoch


@dataclass
class Signal:
    id: str
    severity: str                 # warn | info | ok
    title: str
    why: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Detector = Callable[[Session, Facts, "Config | None"], "Signal | None"]

_BYPASS = [
    # A gradle *run* of instrumented tests captures traces; `assembleBenchmark`
    # only builds the APK and is not a capture.
    ("gradle", re.compile(r"gradlew\b[^\n]*(?:\bconnected\w*|AndroidTest\b)")),
    ("adb", re.compile(r"\badb\s+(?:-s\s+\S+\s+)?shell\s+(?:perfetto|am\s+start|cmd\s+activity)")),
    ("perfetto", re.compile(r"(?:^|\s)perfetto\s+(?:-c|--txt|-o)")),
]
_ENV_KINDS = [
    ("hook redirected the call", re.compile(r"hook|redirected", re.I)),
    ("missing python module", re.compile(r"ModuleNotFoundError|No module named", re.I)),
    ("path not found", re.compile(r"No such file or directory|no such file", re.I)),
    ("command not found", re.compile(r"command not found", re.I)),
    ("permission denied", re.compile(r"Permission denied", re.I)),
    ("timeout", re.compile(r"timed out|timeout", re.I)),
]
_DETECTOR_KEY = re.compile(r"\b(?:min|max)_[a-z_]+\s*:")
_YAML_REDIRECT = re.compile(r">>?\s*[\"']?([^\s\"'<>|;&]+\.ya?ml)")


def _short(path: str | None, keep: int = 3) -> str:
    if not path:
        return ""
    return "/".join(Path(path).parts[-keep:])


def _t(ts: str) -> str:
    return ts[11:19] if ts and len(ts) >= 19 else ts


# ------------------------------------------------------------------ entry

def entry_fumbling(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    e = f.entry
    n = e.get("slash_in_entry", 0)
    stops = e.get("interruptions_in_entry", 0)
    if n < 3 and stops == 0:
        return None
    rows = [{"ts": _t(x["ts"]), "command": x["command"], "args": x.get("args") or ""}
            for x in e.get("slash_commands", [])]
    return Signal(
        "entry_fumbling", "info",
        "It took several attempts to get into the tool",
        f"{n} slash command(s) and {stops} interruption(s) by the user before "
        f"real work started" + (f" ({e['entry_seconds'] // 60} min in)"
                                 if e.get("entry_seconds") else "") + ".",
        rows,
        "/echolot is the one door: it runs `echolot`, reads `next` and goes to "
        "init, setup or hunt itself. If the human still needed several tries, "
        "see which command they typed first and whether the skill's routing "
        "table covers it.")


def agent_prompt_gaps(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    rows = []
    for h in f.hunts:
        missing = [k for k, v in h["prompt_mentions"].items() if not v]
        if missing:
            rows.append({"agent": h["type"] or h["id"], "missing": ", ".join(missing),
                         "prompt_chars": h["prompt_chars"]})
    if not rows:
        return None
    return Signal(
        "agent_prompt_gaps", "info",
        "The prompt handed to the subagent lacks something echolot-hunt asks for",
        "echolot-hunt.md says to pass the traces, what regressed against what, "
        "and after which change. Missing pieces are what the agent then guesses.",
        rows,
        "If the human never had 'after which change', /echolot-hunt should "
        "ask for it explicitly rather than let it be silently omitted.")


# --------------------------------------------------------------- protocol

def doctor_first(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    analyzes = [c for c in f.echolot_calls if c.sub == "analyze"]
    if not analyzes:
        return None
    doctors = [c for c in f.echolot_calls if c.sub == "doctor"]
    first_an = ts_to_epoch(analyzes[0].ts)
    before = [d for d in doctors if ts_to_epoch(d.ts) <= first_an]
    if before:
        failed = [d for d in before if d.exit not in (0, None)]
        if failed:
            return Signal("doctor_first", "warn",
                          "doctor failed and the run went on anyway",
                          "A non-zero doctor means no report can be trusted; the "
                          "protocol says stop.",
                          [{"ts": _t(d.ts), "exit": d.exit, "agent": d.agent}
                           for d in failed])
        return Signal("doctor_first", "ok", "doctor ran before the first analyze",
                      "The environment was checked before any conclusion.")
    return Signal("doctor_first", "warn", "analyze ran without a preceding doctor",
                  "The protocol starts with doctor: an unchecked environment can "
                  "compute a plausible but wrong report.",
                  [{"ts": _t(analyzes[0].ts), "agent": analyzes[0].agent}],
                  "If this repeats, put doctor into analyze itself (a cached "
                  "self-check) instead of asking the agent to remember.")


def trace_opened_directly(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    rows = []
    for c in s.bash():
        cmd = c.command or ""
        if RE_TRACE_OPEN.search(cmd):
            rows.append({"ts": _t(c.ts), "agent": c.agent, "how": c.tool,
                         "command": cmd[:120]})
    for c in s.calls:
        if c.tool == "Read" and c.path and RE_TRACE_LITERAL.search(c.path):
            rows.append({"ts": _t(c.ts), "agent": c.agent, "how": "Read",
                         "command": _short(c.path)})
    if not rows:
        return Signal("trace_opened_directly", "ok", "the trace was never opened directly",
                      "Everything went through echolot, as the one rule says.")
    return Signal("trace_opened_directly", "warn", "the agent opened the trace itself",
                  "The one rule of the skill: the agent never looks at the trace. "
                  "When it does, the window fills and the run becomes unstable.",
                  rows,
                  "Whatever it went looking for is a missing view in echolot: "
                  "see what the query was and consider a command for it.")


def loop_in_main_context(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    if not s.subagents and not f.instrumentation.get("files"):
        return None
    prefix = f.instrumentation.get("prefix", "AGENTTMP_")
    main_edits = [c for c in s.edits(MAIN)
                  if prefix in str((c.input or {}).get("new_string", "")) or
                  prefix in str((c.input or {}).get("old_string", ""))]
    first_edit = min((ts_to_epoch(c.ts) for c in main_edits), default=None)
    main_rerecords = [c for c in s.bash(MAIN) if RE_RE_RECORD.search(c.command or "")
                      and first_edit is not None and ts_to_epoch(c.ts) > first_edit]
    if not main_edits:
        if s.subagents:
            return Signal("loop_in_main_context", "ok",
                          "the hunt loop stayed inside the subagent",
                          "The main context only saw the conclusion.")
        return None
    rows = [{"ts": _t(c.ts), "what": f"{c.tool} {_short(c.path)}"} for c in main_edits]
    rows += [{"ts": _t(c.ts), "what": f"re-record: {(c.command or '')[:80]}"}
             for c in main_rerecords]
    return Signal("loop_in_main_context", "warn",
                  "the iterative loop ran in the main context",
                  "Instrumentation edits and re-records happened outside the "
                  "subagent. That is exactly what fills the window.",
                  rows,
                  "The skill should send the human to /echolot-hunt earlier, or "
                  "the command should refuse to loop in place.")


def rounds_over_max(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    if not f.hunts:
        return None
    rows = []
    for h in f.hunts:
        limit = h.get("max_rounds") or 3
        if h["rounds"] > limit:
            rows.append({"agent": h["type"] or h["id"], "rounds": h["rounds"],
                         "max_rounds": limit, "re_records": h["re_records"]})
    if rows:
        return Signal("rounds_over_max", "warn", "more rounds than loop.max_rounds",
                      "Stopping is hard-coded, not a feeling; the agent went past it.",
                      rows,
                      "If it happens with an honest count, the limit is not "
                      "visible enough in perf-hunter.md; if the count is off, "
                      "the re-record detection here needs the runner's pattern.")
    return Signal("rounds_over_max", "ok", "rounds within loop.max_rounds",
                  ", ".join(f"{h['type'] or h['id']}: {h['rounds']} round(s), "
                            f"{h['re_records']} re-record(s)" for h in f.hunts))


def instrumentation_prefix(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    inst = f.instrumentation
    if not inst.get("files") and not inst.get("unprefixed_trace_calls"):
        return None
    rows = inst.get("unprefixed_trace_calls") or []
    if rows:
        return Signal("instrumentation_prefix", "warn",
                      f"tracing calls inserted without the {inst['prefix']} prefix",
                      "The prefix is what makes cleanup deterministic: grep and "
                      "delete. An unprefixed slice survives cleanup.",
                      [{"ts": _t(r["ts"]), "file": r["file"], "agent": r["agent"]}
                       for r in rows])
    return Signal("instrumentation_prefix", "ok",
                  f"every inserted tracing call carries {inst['prefix']}",
                  f"{len(inst['files'])} file(s) touched.")


def edits_outside_allowed(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    inst = f.instrumentation
    if not s.edits():
        return None
    if not inst.get("allowed"):
        return Signal("edits_outside_allowed", "info",
                      "instrumentation.allowed is not set — edits were not checked",
                      "Without the list there is no way to say where the agent "
                      "was permitted to write.")
    rows = inst.get("edits_outside_allowed") or []
    if rows:
        return Signal("edits_outside_allowed", "warn",
                      "source files edited outside instrumentation.allowed",
                      "The agent may write only into the permitted paths. Anything "
                      "else — benchmark code, generated code, third-party modules — "
                      "is off limits even when restored afterwards.",
                      [{"ts": _t(r["ts"]), "file": r["file"], "agent": r["agent"],
                        "temporary": r["temporary"]} for r in rows],
                      "If the same path keeps appearing, either add it to "
                      "`allowed` deliberately or say in perf-hunter.md why not.")
    return Signal("edits_outside_allowed", "ok",
                  "all source edits stayed inside instrumentation.allowed",
                  ", ".join(inst["allowed"]))


def cleanup_balance(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    inst = f.instrumentation
    files = inst.get("files") or {}
    if not files:
        return None
    unbalanced = inst.get("unbalanced") or {}
    rows = [{"file": k, "added": v["added"], "removed": v["removed"]}
            for k, v in unbalanced.items()]
    grep_after = inst.get("cleanup_grep_after_last_edit", 0)
    clean = inst.get("cleanup_grep_clean")
    shell = inst.get("shell_edits", 0)
    if rows:
        return Signal("cleanup_balance", "warn",
                      "temporary instrumentation may have been left behind",
                      "Per file, additions of the prefix do not match removals. "
                      "Editing counts are a proxy — check the tree with grep.",
                      rows,
                      f"Run: grep -rn {inst['prefix']} <source_root>")
    if grep_after == 0:
        return Signal("cleanup_balance", "warn",
                      "edits balance out, but no grep confirmed the cleanup"
                      if not shell else
                      "instrumentation was edited through the shell and no grep "
                      "confirmed the cleanup",
                      "perf-hunter.md asks for a grep after the last edit and a "
                      "sentence about it in the conclusion." +
                      (" Edits made with python or sed have no direction to balance; "
                       "the grep is the only evidence." if shell else ""),
                      [{"files": len(files), "shell_edits": shell,
                        "grep_calls_total": inst.get("grep_calls_total", 0)}])
    if clean is False:
        return Signal("cleanup_balance", "warn",
                      "the grep after the last edit still finds the prefix",
                      "The agent checked and the marker was still there — or the "
                      "check ran before the last removal.",
                      [{"files": len(files), "shell_edits": shell}],
                      f"Run: grep -rn {inst['prefix']} <source_root>")
    how = (f"{len(files)} file(s), {shell} edit(s) through the shell"
           if shell else f"{len(files)} file(s)")
    what = ("found nothing" if clean else "ran")
    return Signal("cleanup_balance", "ok",
                  "temporary instrumentation added and removed in equal measure"
                  if not shell else "temporary instrumentation cleaned up",
                  f"{how}; a grep for the prefix after the last edit {what}.")


def conclusion_shape(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    if not f.hunts:
        return None
    rows = []
    for h in f.hunts:
        if not h["final_text"]:
            rows.append({"agent": h["type"] or h["id"], "missing": "no return value at all"})
            continue
        missing = [k for k, ok in h["conclusion_fields"].items() if not ok]
        if missing:
            rows.append({"agent": h["type"] or h["id"], "missing": ", ".join(missing)})
    if rows:
        return Signal("conclusion_shape", "warn",
                      "the subagent's conclusion is missing fields",
                      "The return shape is Place / Evidence / Mechanism / Suggestion / "
                      "Confidence / Cleanup. A missing field is a decision the human "
                      "now has to make blind.",
                      rows)
    return Signal("conclusion_shape", "ok", "the conclusion came back in the agreed shape",
                  "; ".join(f"{h['type'] or h['id']}: confidence {h['confidence'] or '?'}"
                            for h in f.hunts))


def config_bypassed(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    project_cfg = Path(cfg.path).name if cfg and cfg.path else "echolot.yml"
    rows = []
    seen: set[tuple[str, str, str]] = set()
    for c in f.echolot_calls:
        if c.sub not in ("analyze", "calibrate", "collect", "names"):
            continue
        if not c.config or not c.ran:
            continue
        p = c.config.strip("\"'")
        if Path(p).name != project_cfg or "/scratchpad/" in p or p.startswith("/tmp/") \
                or "/T/" in p:
            key = (c.ts, c.sub, p)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"ts": c.ts, "agent": c.agent, "sub": c.sub, "config": p[-80:]})
    written = []
    for c in s.calls:
        if c.tool == "Write" and c.path and c.path.endswith((".yml", ".yaml")) \
                and Path(c.path).name != project_cfg:
            written.append({"ts": c.ts, "agent": c.agent, "sub": "Write",
                            "config": c.path[-80:]})
    for c in s.bash():
        cmd = c.command or ""
        # Any redirect into a yaml file that is not the project's: `cat >`
        # heredocs, `sed … > /tmp/x.yml`, `>>` appends.
        for m in _YAML_REDIRECT.finditer(cmd):
            p = m.group(1)
            if Path(p).name == project_cfg or (c.ts, "w", p) in seen:
                continue
            seen.add((c.ts, "w", p))
            written.append({"ts": c.ts, "agent": c.agent, "sub": "Bash redirect",
                            "config": p[-80:]})
    if not rows and not written:
        return None
    # Before the project config exists in this session, a draft in the
    # scratchpad is how /echolot-setup verifies anchors — six candidates
    # through analyze, then echolot.yml. That is the setup working, not a
    # bypass. Only what happens after the config was written counts.
    first_cfg = min((ts_to_epoch(w["ts"]) for w in config_writes(s, (project_cfg,))),
                    default=None)
    all_rows = rows + written
    drafts = False
    if first_cfg is not None:
        for r in all_rows:
            r["phase"] = "setup draft" if ts_to_epoch(r["ts"]) < first_cfg else "after setup"
        drafts = all(r["phase"] == "setup draft" for r in all_rows)
    for r in all_rows:
        r["ts"] = _t(r["ts"])
    if drafts:
        return Signal("config_bypassed", "info",
                      "draft configs were analyzed while the project config was "
                      "being built",
                      "Every analyze against a config of the agent's own happened "
                      "before echolot.yml was written — /echolot-setup verifying "
                      "anchor candidates on a draft. Expected; listed so a draft "
                      "that outlives setup does not hide here.",
                      all_rows,
                      "A `--dry-run`-style window check on analyze would make the "
                      "draft file unnecessary.")
    return Signal("config_bypassed", "warn",
                  "analysis ran on a config other than the project's",
                  "The agent wrote its own config or pointed -c elsewhere. Whatever "
                  "was in the project config — calibrated thresholds, anchors — was "
                  "not what produced the conclusion.",
                  all_rows,
                  "Ask why: if the project thresholds were unusable, that is a "
                  "calibrate problem, not a discipline problem.")


def thresholds_by_hand(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    touched = [w for w in config_writes(s) if _DETECTOR_KEY.search(w["text"])]
    if not touched:
        return None
    calibrates = [c for c in f.echolot_calls if c.sub == "calibrate"]
    first_touch = ts_to_epoch(touched[0]["ts"])
    applied = any(ts_to_epoch(c.ts) < first_touch for c in calibrates)
    rows = [{"ts": _t(w["ts"]), "agent": w["agent"], "file": w["file"],
             "tool": w["tool"] if w["tool"] in ("Edit", "Write", "MultiEdit") else "shell"}
            for w in touched]
    if applied:
        return Signal("thresholds_by_hand", "info",
                      "detector thresholds were written into the config after calibrate",
                      "Most likely the agent applied calibrate's output — fine. Listed "
                      "so that a hand-tuned number does not hide among them.",
                      rows)
    return Signal("thresholds_by_hand", "warn",
                  "detector thresholds edited without a calibrate run",
                  "Thresholds come from `echolot calibrate` on healthy runs, not from "
                  "the agent's taste.",
                  rows)


# ---------------------------------------------------------- workarounds

def _around(text: str, needle: str, width: int = 110) -> str:
    """The stretch of a command around the interesting part, one line."""
    flat = " ".join(text.split())
    i = flat.find(needle)
    if i < 0:
        return flat[:width]
    start = max(0, i - width // 3)
    return ("…" if start else "") + flat[start:start + width]


def report_sliced_by_hand(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    rows = [{"ts": _t(c.ts), "agent": c.agent,
             "command": _around(c.command or "", "report.json")}
            for c in s.bash() if RE_REPORT_BY_HAND.search(c.command or "")]
    if not rows:
        return None
    return Signal("report_sliced_by_hand", "info",
                  f"report.json was cut up by hand {len(rows)} time(s)",
                  "The skill says to read the json; the agent then needed several "
                  "one-liners to get at what it wanted. Each is a view the report "
                  "does not offer directly.",
                  rows,
                  "Look at what the one-liners select (a detector, top rows, the "
                  "window): a filtered view or a shorter format would save them.")


def help_lookups(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    # The invocation itself, not the head of a Bash line it shared with a
    # `cd` and a python one-liner.
    rows = [{"ts": _t(c.ts), "agent": c.agent,
             "command": f"echolot {c.sub} {c.argv}".strip()[:100]}
            for c in f.echolot_calls if c.is_help]
    if not rows:
        return None
    return Signal("help_lookups", "info",
                  f"the agent consulted --help / explain {len(rows)} time(s) mid-work",
                  "The skill and references did not answer a question about how to "
                  "call the tool.",
                  rows,
                  "See which subcommand and flag it was after; add that line to "
                  "SKILL.md or the reference.")


def bypass_tools(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    counts: dict[str, list[dict[str, Any]]] = {}
    for c in s.bash():
        cmd = c.command or ""
        if RE_ECHOLOT.search(cmd):
            continue
        for kind, rx in _BYPASS:
            if rx.search(cmd):
                counts.setdefault(kind, []).append(
                    {"ts": _t(c.ts), "agent": c.agent, "command": cmd[:110]})
                break
    if not counts:
        return None
    rows = []
    for kind, items in counts.items():
        rows.append({"kind": kind, "count": len(items), "example": items[0]["command"],
                     "agents": ", ".join(sorted({i["agent"] for i in items}))})
    return Signal("bypass_tools", "info",
                  "traces were captured around echolot rather than through it",
                  "gradle / adb / perfetto were driven directly. `echolot collect` "
                  "exists for this; either it did not fit or the agent did not reach "
                  "for it.",
                  rows,
                  "If the runner mode the project needs is not implemented "
                  "(gradle, for one), that is the item.")


_PRESERVE = re.compile(r"(?:^|[\s;&|(])(?:cp|rsync|tar|zip|ditto)\s")
_MOVE = re.compile(r"(?:^|[\s;&|(])mv\s+(?:-\S+\s+)*(\"[^\"]*\"|'[^']*'|\S+)\s+(\"[^\"]*\"|'[^']*'|\S+)")
_TRACE_ARG = re.compile(r"(\"[^\"]*\"|'[^']*'|\S+)?[^\s\"']*\.(?:perfetto-trace|pftrace)\b")


_ASSIGN = re.compile(r"(?m)^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S+)")


def _expand_vars(cmd: str, token: str) -> str:
    """`$OUT` and `${OUT}` from assignments in the same command, one level.

    Enough for the shape agents write — `OUT="…/SM-A515F - 13"` two lines
    above `mv "$OUT" "${OUT}_before"` — without pretending to be a shell.
    """
    env = {m.group(1): m.group(2).strip("\"'") for m in _ASSIGN.finditer(cmd)}
    for name, value in env.items():
        token = token.replace("${" + name + "}", value).replace("$" + name, value)
    return token


def _trace_dir(argv: str) -> str | None:
    """The directory the analyzed traces sat in, as far as the argv shows it.

    `"build/…/SM-A515F - 13/"Foo_iter*.perfetto-trace` → `build/…/SM-A515F - 13`.
    A `$VAR` or `"${traces[@]}"` gives nothing usable — None.
    """
    m = _TRACE_ARG.search(argv)
    if not m:
        return None
    token = m.group(0).replace('"', "").replace("'", "")
    if token.startswith("$"):
        return None
    d = token.rsplit("/", 1)[0] if "/" in token else ""
    return d or None


_SEGMENT = re.compile(r"\n|;|&&|\|\||\|")


def _touches_traces(segment: str, dirs: list[str]) -> bool:
    """Does this one shell command take the traces themselves as an argument?

    A `.perfetto-trace` literal or glob does; so does one of the analyzed
    directories taken whole (`cp -r "$OUT" …`, `$OUT/*`). `$OUT/metrics.json`
    does not — that copies a file next to the traces, not the traces, and it
    is exactly what a session did while believing the baseline was safe.
    """
    if RE_TRACE_LITERAL.search(segment):
        return True
    for d in dirs:
        start = 0
        while True:
            i = segment.find(d, start)
            if i < 0:
                break
            start = i + len(d)
            # `out` inside `build/out` is another directory
            if i > 0 and segment[i - 1] not in " \"'=":
                continue
            rest = segment[start:]
            if not rest or rest[0] in " \"'":
                return True
            if rest[0] == "/":
                tail = re.split(r"[\s\"']", rest[1:], 1)[0]
                if not tail or "*" in tail:
                    return True
    return False


def baseline_lost(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    """A re-record after an analyze, with no copy of the analyzed traces first.

    The traces before a change are the baseline every after-the-fix
    comparison stands on, and a re-record writes the same place: a
    macrobenchmark's output directory is cleaned by gradle, `collect` writes
    the same file names (it sets the old set aside now). A `cp`/`rsync`/`tar`
    of the traces before re-recording, or `echolot collect`, counts as
    preserved. A `mv` into the same build tree does not — gradle removes it.
    """
    calls = sorted(s.bash(), key=lambda c: ts_to_epoch(c.ts))
    analyzed_dirs: list[str] = []      # from analyze calls seen so far
    last_analyze_ts: str | None = None
    preserved_since_analyze = False
    moved_in_build: str | None = None
    touched: str | None = None         # the analyzed dir the mv/cp named
    rows = []
    for c in calls:
        cmd = c.command or ""
        if RE_ECHOLOT.search(cmd) and re.search(r"echolot\s+analyze\b", cmd):
            for m in RE_ECHOLOT.finditer(cmd):
                if m.group(1) == "analyze":
                    d = _trace_dir(m.group(2) or "")
                    if d and d not in analyzed_dirs:
                        analyzed_dirs.append(d)
            last_analyze_ts = c.ts
            preserved_since_analyze = False
            moved_in_build = touched = None
            continue
        if last_analyze_ts is None:
            continue
        # One shell line at a time: `cp metrics.json /tmp && mv "$OUT" …`
        # is a copy of something else and a move of the traces.
        for raw_seg in _SEGMENT.split(cmd):
            seg = _expand_vars(cmd, raw_seg.strip())
            if not seg:
                continue
            if _PRESERVE.search(seg) and _touches_traces(seg, analyzed_dirs):
                preserved_since_analyze = True
            mv = _MOVE.search(seg)
            if mv and _touches_traces(mv.group(1), analyzed_dirs):
                src = mv.group(1).strip("\"'")
                touched = next((d for d in analyzed_dirs if d in src), touched)
                dst = mv.group(2).strip("\"'")
                parents = {d.rsplit("/", 1)[0] for d in analyzed_dirs if "/" in d}
                if "/build/" in dst or dst.startswith("build/") \
                        or any(dst.startswith(p) for p in parents):
                    moved_in_build = f"mv → {dst[-70:]} (inside the build tree)"
                else:
                    preserved_since_analyze = True
        if RE_RE_RECORD.search(cmd):
            if re.search(r"echolot\s+collect\b", cmd):
                # collect sets the previous set aside itself
                preserved_since_analyze = False
                moved_in_build = touched = None
                last_analyze_ts = None
                continue
            if not preserved_since_analyze:
                before = touched or (analyzed_dirs[-1] if analyzed_dirs else "?")
                rows.append({
                    "ts": _t(c.ts), "agent": c.agent,
                    "re_record": cmd.replace("\n", " ")[:90],
                    "traces_before": before[-60:],
                    "note": moved_in_build or "no copy of the previous traces first",
                })
            preserved_since_analyze = False
            moved_in_build = touched = None
            last_analyze_ts = None
    if not rows:
        return None
    return Signal("baseline_lost", "warn",
                  "traces were re-recorded without keeping the set analyzed before",
                  "The traces from before a change are the baseline that every "
                  "after-the-fix comparison stands on. A re-record writes the "
                  "same place — gradle cleans the benchmark output directory, and "
                  "a rename inside it goes with the cleaning. Nothing in the "
                  "transcript copies them out first.",
                  rows,
                  "Record through `echolot collect` (it sets the previous set aside), "
                  "or copy the traces into .echolot/traces/<label>/ before "
                  "re-recording — the skill should say so.")


# ------------------------------------------------------------- failures

def echolot_failures(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    rows = []
    for c in f.echolot_calls:
        if c.is_help:
            continue
        failed = c.traceback or c.is_error or c.shell_error or (c.exit not in (0, None))
        if not failed:
            continue
        # A recorded run with exit 0 outranks the transcript: the Bash call
        # failed around echolot (a `| tail`, a `&&`, the agent's own python
        # after it), not inside it. echolot does not exit 0 on a traceback.
        if c.recorded and c.recorded.get("exit") == 0:
            continue
        where = "shell" if c.shell_error else "echolot"
        if c.recorded is None and f.runs and not c.traceback:
            where = "shell (no run recorded)"
        rows.append({"ts": _t(c.ts), "agent": c.agent, "sub": c.sub, "exit": c.exit,
                     "where": where, "traceback": c.traceback,
                     "head": (c.output_head or "").replace("\n", " ")[:160]})
    if not rows:
        return None
    tracebacks = sum(1 for r in rows if r["traceback"])
    own = sum(1 for r in rows if r["where"] == "echolot")
    return Signal("echolot_failures", "warn" if own else "info",
                  f"{len(rows)} echolot call(s) failed" +
                  (f", {tracebacks} with a traceback" if tracebacks else "") +
                  (f", {len(rows) - own} in the shell before echolot ran" if len(rows) - own else ""),
                  "A traceback out of the CLI is a bug in echolot. A clean non-zero "
                  "exit is either a bad argument (a usability item) or a real "
                  "finding (doctor). A shell failure — cd into a path with spaces, "
                  "an unmatched glob — is friction the CLI could take over.",
                  rows,
                  "Tracebacks first; then read the failing argv against --help "
                  "and see whether the CLI could have accepted it.")


def retries(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    rows = []
    calls = f.echolot_calls
    for a, b in zip(calls, calls[1:]):
        if a.sub != b.sub or a.agent != b.agent:
            continue
        # `--help` then the real call is reading the manual, not a retry; two
        # invocations in one shell line were written together, not one after
        # the other failed.
        if a.is_help or b.is_help or a.ts == b.ts:
            continue
        a_failed = a.traceback or a.is_error or (a.exit not in (0, None))
        if a.recorded and a.recorded.get("exit") == 0:
            a_failed = False
        if a_failed and ts_to_epoch(b.ts) - ts_to_epoch(a.ts) <= 180:
            rows.append({"ts": _t(a.ts), "agent": a.agent, "sub": a.sub,
                         "first": a.argv[:80], "then": b.argv[:80],
                         "seconds": round(ts_to_epoch(b.ts) - ts_to_epoch(a.ts))})
    if not rows:
        return None
    return Signal("retries", "info",
                  f"{len(rows)} echolot call(s) were retried after failing",
                  "The agent recovered by itself. What it changed between the two "
                  "attempts is what the first error message should have said.",
                  rows)


def env_friction(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    kinds: dict[str, list[dict[str, Any]]] = {}
    for c in s.calls:
        if not c.is_error:
            continue
        if c.command is not None and RE_ECHOLOT.search(c.command):
            continue   # echolot's own failures are a separate signal
        head = c.output_head or ""
        for kind, rx in _ENV_KINDS:
            if rx.search(head):
                kinds.setdefault(kind, []).append(
                    {"ts": _t(c.ts), "agent": c.agent, "tool": c.tool,
                     "what": (c.command or c.path or "")[:80],
                     "error": head.replace("\n", " ")[:120]})
                break
        else:
            kinds.setdefault("other", []).append(
                {"ts": _t(c.ts), "agent": c.agent, "tool": c.tool,
                 "what": (c.command or c.path or "")[:80],
                 "error": head.replace("\n", " ")[:120]})
    if not kinds:
        return None
    rows = []
    for kind, items in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
        rows.append({"kind": kind, "count": len(items),
                     "example": items[0]["error"], "what": items[0]["what"]})
    return Signal("env_friction", "info",
                  f"{sum(len(v) for v in kinds.values())} tool error(s) outside echolot",
                  "Environment noise: missing modules, paths with spaces, hooks. "
                  "Not the tool's fault, but the tool can absorb some of it.",
                  rows,
                  "Paths with spaces and missing yaml are the classic ones — accept "
                  "a directory/glob in analyze, never make the agent write yaml.")


# ------------------------------------------------------------------ cost

def code_read_by_hand(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    """The subagent's window went to reading the app rather than the report.

    Twenty-one `cat`/`sed -n`/`grep` calls over sources, forty percent of
    everything that entered the window — in two hunts out of two. It reads
    because the report points at system slices and threads and `domains`
    has nothing to map them to; the reading is how it decides where to put
    the first markers. Which is the tool's gap as much as the agent's habit.
    """
    rows = []
    worst = 0
    for h in f.hunts:
        w = h.get("window") or {}
        src = (w.get("by_activity") or {}).get("source reading") or {}
        total = w.get("total_chars") or 0
        if not src.get("calls") or not total:
            continue
        share = src.get("share", 0)
        if share < 20 and src["calls"] < 10:
            continue
        worst = max(worst, share)
        rows.append({
            "agent": h["type"] or h["id"],
            "source reads": src["calls"],
            "chars": src["chars"],
            "share of window": f"{share}%",
            "before first instrumentation edit": w.get("source_reads_before_first_edit", 0),
            "echolot calls": ((w.get("by_activity") or {}).get("echolot") or {}).get("calls", 0),
        })
    if not rows:
        return None
    severity = "warn" if worst >= 40 else "info"
    return Signal("code_read_by_hand", severity,
                  "the subagent read the application's source by hand, at length",
                  "Chars of tool output by activity — what actually entered the "
                  "window. Reading sources is where the loop's window goes when the "
                  "report names system slices and threads and `domains` has no "
                  "instrumentation to map them to: the agent reads to decide where "
                  "the first markers go.",
                  rows,
                  "The bridge is missing on the tool's side: something that proposes "
                  "the first instrumentation sites for a scenario (Application, the "
                  "launcher Activity, setContent, the first ViewModel, Room) would "
                  "replace most of these reads. Until then perf-hunter.md should say: "
                  "instrument the skeleton first, read second, and never `cat` a "
                  "whole file.")


def long_gaps(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    rows = [g for g in f.gaps if g["seconds"] >= 120]
    if not rows:
        return None
    return Signal("long_gaps", "info",
                  f"{len(rows)} silence(s) of two minutes or more",
                  "Almost always the agent waiting on gradle or a device. Listed so "
                  "the wall time reads honestly.",
                  [{"agent": g["agent"], "seconds": g["seconds"], "after": g["after"]}
                   for g in rows])


def context_hogs(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    big = [o for o in f.top_outputs if o["chars"] >= 8000]
    if not big:
        return None
    from_echolot = [o for o in big if o["echolot"]]
    return Signal("context_hogs", "info",
                  f"{len(big)} tool output(s) over 8k characters",
                  "Every character comes out of the window the loop runs in.",
                  big,
                  ("echolot's own output is among them — a shorter default or a "
                   "--brief flag would help." if from_echolot else
                   "None of them is echolot's; the agent read something large by hand."))


SIGNALS: list[Detector] = [
    # entry
    entry_fumbling,
    agent_prompt_gaps,
    # protocol
    doctor_first,
    trace_opened_directly,
    loop_in_main_context,
    rounds_over_max,
    instrumentation_prefix,
    edits_outside_allowed,
    cleanup_balance,
    conclusion_shape,
    config_bypassed,
    thresholds_by_hand,
    baseline_lost,
    # workarounds
    report_sliced_by_hand,
    help_lookups,
    bypass_tools,
    # failures
    echolot_failures,
    retries,
    env_friction,
    # cost
    code_read_by_hand,
    long_gaps,
    context_hogs,
]


def run(session: Session, facts: Facts, cfg: Config | None) -> list[Signal]:
    out: list[Signal] = []
    for det in SIGNALS:
        try:
            sig = det(session, facts, cfg)
        except Exception as e:   # one broken detector must not kill the report
            sig = Signal(det.__name__, "info", f"detector {det.__name__} failed",
                         f"{type(e).__name__}: {e}")
        if sig is not None:
            out.append(sig)
    order = {"warn": 0, "info": 1, "ok": 2}
    out.sort(key=lambda x: order.get(x.severity, 3))
    return out
