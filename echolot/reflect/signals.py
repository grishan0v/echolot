"""Signals: the detectors over a session.

Each signal is one small function over the normalised session and the derived
facts. It returns a `Signal` — or None when it has nothing to say. Add a
function, append it to SIGNALS, done: there is no registration elsewhere.

Four severities:

    warn   the protocol was broken or the tool failed — look at it
    info   friction or a workaround: a hint at what the CLI could absorb
    ok     a protocol check that passed; kept so the report shows it
    skip   the source does not carry what this check reads. Not a verdict,
           and the report has to say so — a check that quietly returns "ok"
           over data it never had is worse than one that does not run

A `hint` is one line: what this usually means for the tool. It is a pointer,
not a conclusion — the reasoning is left to whoever reads the report.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from ..config import DEFAULT_PREFIX, Config
from . import facts as facts_mod
from . import shell as _shell
from .facts import (
    RE_ECHOLOT,
    RE_RE_RECORD,
    RE_CODE_READS_REPORT,
    RE_REPORT_BY_HAND,
    RE_TRACE_LITERAL,
    Facts,
    config_writes,
    gaps,
    hunt_agents,
    opens_trace,
    report_paths,
    runs_analyze,
    runs_guide,
    shell_edits,
    sql_tables,
    trace_reads,
)
from .facts import SEGMENT as _SEGMENT
from .facts import expand_vars as _expand_vars
from .facts import short_path as _short
from .model import BRIEFS, MAIN, TOOLS, Session, strip_heredocs, ts_to_epoch


@dataclass
class Signal:
    id: str
    severity: str                 # warn | info | ok | skip
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
    # `assemble…AndroidTest`, `bundle…`, `compile…` build the test APK and
    # capture nothing, whatever the task's name ends in.
    ("gradle", re.compile(r"gradlew\b[^\n]*(?:\bconnected\w*|"
                          r"(?<![\w:])(?!assemble|bundle|compile)\w*AndroidTest\b)")),
    # A launch, `am start` or `cmd activity start-activity`, records nothing.
    ("adb", re.compile(r"\badb\s+(?:-s\s+\S+\s+)?shell\s+perfetto\b")),
    ("perfetto", re.compile(r"(?:^|\s)perfetto\s+(?:-c|--txt|-o)")),
]
# What the agent's harness says when it, not the command, stopped the call:
# a permission screen refused it, the person declined it at the prompt, or
# the call outlived the harness's patience and went on in the background.
# None of those is a failure of what was called — an `echolot init` that
# never ran did not fail — and on a real session two of the four "echolot
# failures" were the first kind and one the third.
_HOST = re.compile(
    r"Permission for this action was denied|Blocked by classifier|"
    r"The user doesn't want to proceed|tool use was rejected|"
    r"still running after \d+s|moved to the background", re.I)

_ENV_KINDS = [
    ("denied by the agent's harness",
     re.compile(r"Permission for this action was denied|Blocked by classifier|"
                r"The user doesn't want to proceed|tool use was rejected", re.I)),
    ("moved to the background by the harness",
     re.compile(r"still running after \d+s|moved to the background", re.I)),
    ("hook redirected the call", re.compile(r"hook|redirected", re.I)),
    ("missing python module", re.compile(r"ModuleNotFoundError|No module named", re.I)),
    ("path not found", re.compile(r"No such file or directory|no such file", re.I)),
    ("command not found", re.compile(r"command not found", re.I)),
    ("permission denied", re.compile(r"Permission denied", re.I)),
    ("timeout", re.compile(r"timed out|timeout", re.I)),
]
_DETECTOR_VALUE = re.compile(r"\b((?:min|max)_[a-z_]+)\s*:\s*([^\s#,}\]]+)")
_YAML_REDIRECT = re.compile(r">>?\s*[\"']?([^\s\"'<>|;&]+\.ya?ml)")


def _t(ts: str) -> str:
    return ts[11:19] if ts and len(ts) >= 19 else ts


def _age(seconds: float) -> str:
    """`15 min`, `3 h` — how long before, the way `echolot` says it."""
    m = int(seconds) // 60
    return f"{m} min" if m < 120 else f"{m // 60} h"


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
    # Calls that ran, and not `--help`: an `analyze --help` read first was
    # the first analyze, a `doctor --help` passed for the check, and an
    # analyze the shell skipped on a glob counted as one.
    real = [c for c in f.echolot_calls if not c.is_help and c.ran]
    analyzes = [c for c in real if c.sub == "analyze"]
    if not analyzes:
        return None
    doctors = [c for c in real if c.sub == "doctor"]
    first_an = ts_to_epoch(analyzes[0].ts)
    before = [d for d in doctors if ts_to_epoch(d.ts) <= first_an]
    if before:
        # The last doctor before the analyze speaks: one that failed, a fix,
        # one that passed, then the analyze, is the path the protocol asks
        # for, and it was reported as going on past a failure.
        last = max(before, key=lambda d: ts_to_epoch(d.ts))
        failed = [last] if last.exit not in (0, None) else []
        if failed:
            return Signal("doctor_first", "warn",
                          "doctor failed and the run went on anyway",
                          "A non-zero doctor means no report can be trusted; the "
                          "protocol says stop.",
                          [{"ts": _t(d.ts), "exit": d.exit, "agent": d.agent}
                           for d in failed])
        return Signal("doctor_first", "ok", "doctor ran before the first analyze",
                      "The environment was checked before any conclusion.")
    # None in the session, and the run log has one that passed shortly
    # before: `echolot` showed it, its `next` did not ask for another, and
    # the session followed the door (#544).
    prior = f.prior_doctor
    if prior and prior["passed"] and prior["same_version"] \
            and prior["age_s"] <= facts_mod.DOCTOR_FRESH_S:
        what = "doctor" if prior.get("cmd") == "doctor" else f"the self-check in {prior.get('cmd')}"
        return Signal("doctor_first", "ok",
                      f"{what} passed {_age(prior['age_s'])} before the first analyze",
                      "Not in this session: the run log has it, and `echolot` reports "
                      "it and asks for no other doctor after one that passed.")
    return Signal("doctor_first", "warn", "analyze ran without a preceding doctor",
                  "The protocol starts with doctor: an unchecked environment can "
                  "compute a plausible but wrong report.",
                  [{"ts": _t(analyzes[0].ts), "agent": analyzes[0].agent}],
                  _doctor_advice(s, analyzes[0], prior))


_REDIRECTION = re.compile(r"^\d*(?:[<>]|&>)")


def _door(s: Session, until: float) -> str | None:
    """When the session ran `echolot` (or `status`) before `until`: the door
    that says whether a doctor is due. None when it never did."""
    for c in sorted(s.bash(), key=lambda x: ts_to_epoch(x.ts)):
        if ts_to_epoch(c.ts) > until:
            break
        for cmd, h, _ in _shell.runs(strip_heredocs(c.shell)):
            if _shell.name(cmd.words[h].value) != "echolot":
                continue
            rest = [w.value for w in cmd.words[h + 1:]
                    if not w.value.startswith("-") and not _REDIRECTION.match(w.value)]
            if not rest or rest[0] in ("status", "next"):
                return c.ts
    return None


def _doctor_advice(s: Session, first, prior: dict[str, Any] | None) -> str:
    """What the run log and the session say about the missing doctor, and
    where that points: the advice used to be one sentence for every session,
    "put doctor into analyze itself", whatever had happened (#547)."""
    if prior is None:
        log = "Neither the session nor the run log has a doctor before it."
    elif not prior["passed"]:
        failed = (f" ({', '.join(map(str, prior['failed']))})"
                  if prior.get("failed") else "")
        log = (f"The run log's last self-check, `{prior.get('cmd')}` "
               f"{_age(prior['age_s'])} before it, failed{failed}, and none ran since.")
    elif not prior["same_version"]:
        log = (f"The run log's last passing `{prior.get('cmd')}` ran under echolot "
               f"{prior.get('version')}, not the version that analysed.")
    else:
        log = (f"The run log's last passing `{prior.get('cmd')}` was "
               f"{_age(prior['age_s'])} before it — past the "
               f"{facts_mod.DOCTOR_FRESH_S // 3600} h `echolot` takes one for.")
    door = _door(s, ts_to_epoch(first.ts))
    if door:
        where = (f"`echolot` ran at {_t(door)}, before the analyze, and says when a "
                 "doctor is due: the session went on past what it said — the door's "
                 "wording is what to look at.")
    else:
        where = ("The session never ran `echolot`, which asks for one: it went "
                 "straight to `analyze`, so whatever sent it there skipped the door.")
    return f"The analyze at {_t(first.ts)}. {log} {where}"


def trace_opened_directly(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    # Read off what the shell ran, echolot's own invocations left out:
    # `--tp-binary` names a trace_processor_shell, and a call that went
    # through echolot was reported as the agent opening the trace itself.
    rows = [{**r, "ts": _t(r["ts"])} for r in trace_reads(s)]
    for c in s.calls:
        if c.tool == "Read" and c.path and RE_TRACE_LITERAL.search(c.path):
            rows.append({"ts": _t(c.ts), "agent": c.agent, "how": "Read",
                         "command": _short(c.path)})
        # Python through an execute tool, or a trace handed to one as the
        # file it processes (#542).
        elif c.code is not None and (opens_trace(c.code)
                                     or (c.path and RE_TRACE_LITERAL.search(c.path))):
            row = {"ts": _t(c.ts), "agent": c.agent,
                   "how": f"{c.tool} ({c.language or 'code'})",
                   "command": " ".join((c.code or "").split())[:120]}
            if sql_tables(c.code or ""):
                row["queried"] = ", ".join(sql_tables(c.code or ""))
            rows.append(row)
    if not rows:
        return Signal("trace_opened_directly", "ok", "the trace was never opened directly",
                      "Everything went through echolot, as the one rule says.")
    return Signal("trace_opened_directly", "warn", "the agent opened the trace itself",
                  "The one rule of the skill: the agent never looks at the trace. "
                  "When it does, the window fills and the run becomes unstable.",
                  rows, _trace_advice(rows))


def _trace_advice(rows: list[dict[str, Any]]) -> str:
    """What the agent ran on the trace and what its queries read: the view
    it went looking for, named (#547)."""
    hows = list(dict.fromkeys(r["how"] for r in rows))
    tables = list(dict.fromkeys(t.strip() for r in rows
                                for t in (r.get("queried") or "").split(",") if t.strip()))
    said = f"It went round echolot {len(rows)} time(s): {'; '.join(hows)}."
    if tables:
        return (f"{said} Its queries read {', '.join(f'`{t}`' for t in tables)}: what it "
                "asked of them is the view echolot did not give it. Look for it among the "
                "detectors and in `names` first; if none answers it, that is the one to add.")
    return (f"{said} The query itself is not in the transcript: the script or the "
            "command it ran says what it was after, and that is the view echolot did "
            "not give it.")


def loop_in_main_context(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    hunters = hunt_agents(s)
    if not hunters and not f.instrumentation.get("files"):
        return None
    prefix = f.instrumentation.get("prefix", DEFAULT_PREFIX)
    # A marker written with Write is in `content`, and one placed through the
    # shell is in no tool edit at all; both are the loop in the main
    # context, as `instrumentation()` counts them.
    main_edits = [c for c in s.edits(MAIN)
                  if any(prefix in str((c.input or {}).get(k, ""))
                         for k in ("new_string", "old_string", "content"))]
    main_shell = [e for e in shell_edits(s, prefix) if e["agent"] == MAIN]
    first_edit = min([ts_to_epoch(c.ts) for c in main_edits]
                     + [ts_to_epoch(e["ts"]) for e in main_shell], default=None)
    main_rerecords = [c for c in s.bash(MAIN) if RE_RE_RECORD.search(c.shell)
                      and first_edit is not None and ts_to_epoch(c.ts) > first_edit]
    if not main_edits and not main_shell:
        if hunters:
            return Signal("loop_in_main_context", "ok",
                          "the hunt loop stayed inside the subagent",
                          "The main context only saw the conclusion.")
        return None
    rows = [{"ts": _t(c.ts), "what": f"{c.tool} {_short(c.path)}"} for c in main_edits]
    rows += [{"ts": _t(e["ts"]), "what": "shell edit " + ", ".join(_short(p) for p in e["files"])}
             for e in main_shell]
    rows += [{"ts": _t(c.ts), "what": f"re-record: {c.shell[:80]}"}
             for c in main_rerecords]
    return Signal("loop_in_main_context", "warn",
                  "the iterative loop ran in the main context",
                  "Instrumentation edits and re-records happened outside the "
                  "subagent. That is exactly what fills the window.",
                  rows,
                  "The skill should send the human to /echolot-hunt earlier, or "
                  "the command should refuse to loop in place.")


# The guides the loop reads for itself, in its subagent.
_LOOP_GUIDES = ("loop", "report")


def guides_in_main(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    """The loop's own guides, read in the main context before it handed the loop down.

    A hunt starts the loop in a subagent so that the main thread's window sees
    the conclusion and nothing else. In two live Codex runs the main thread
    read `echolot guide loop` first — 17,933 characters — although the text it
    was following said the guide was the subagent's, and the subagent read
    it again (#202). The overview is left out: it is every host's door to
    the tool, and `context_hogs` counts it anyway.
    """
    loops = hunt_agents(s)
    if not loops:
        return None
    handed = min((ts_to_epoch(a.started) for a in loops if a.started), default=None)
    rows = []
    for c in s.bash(MAIN):
        topic = next((t for t in _LOOP_GUIDES if runs_guide(c.shell, t) is not None), None)
        if topic is None:
            continue
        if handed is not None and ts_to_epoch(c.ts) > handed:
            continue
        rows.append({"ts": _t(c.ts), "guide": f"echolot guide {topic}",
                     "chars": c.output_chars})
    if not rows:
        return Signal("guides_in_main", "ok",
                      "the loop's guides stayed with the subagent",
                      "The main thread handed the loop down without reading them.")
    return Signal("guides_in_main", "warn" if any(r["guide"].endswith("loop") for r in rows)
                  else "info",
                  "the loop's guides were read in the main context",
                  "They are the subagent's to read, and it reads them again; in the main "
                  "thread they fill the window the subagent is there to protect.",
                  rows,
                  "The hunt skill and `echolot guide hunt` hand the brief over as a "
                  "block; whatever sent the main thread to the guide is what to change.")


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


def _cleanup_grep(prefix: str) -> str:
    """The check the agent texts give, from the checkout's root: every Kotlin
    and Java file, where markers may go under any `instrumentation.allowed`
    path, and the tag that the `endSection()` half of a pair carries without
    the prefix (#274)."""
    return (f"grep -rn --include='*.kt' --include='*.java' -e {prefix} "
            f"-e 'echolot:mark' .")


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
    # The tree outranks the transcript. What the agent typed, edited or
    # restored with git is evidence of intent; whether markers are in the
    # sources now is the fact, and it was read at report time — counted the
    # way `hunt` and `mark --remove` count them (`mark.Markers`), the prefix
    # or the `echolot:mark` tag, in the whole tree (#749, #838).
    tree = inst.get("tree") or {}
    if tree.get("checked"):
        left = tree.get("files") or []
        if left:
            outside = set(tree.get("outside") or [])
            return Signal("cleanup_balance", "warn",
                          "the source tree still carries temporary markers",
                          "Read off the checkout when this report was made, not "
                          "off the transcript: these files hold the temporary "
                          "prefix or a line tagged `echolot:mark` now.",
                          [{"file": p, **({"outside_allowed": True} if p in outside else {})}
                           for p in left],
                          f"Run: {_cleanup_grep(inst['prefix'])}; "
                          f"`echolot mark --remove` takes out what `mark --apply` wrote.")
        return Signal("cleanup_balance", "ok",
                      "the source tree carries no temporary marker",
                      f"Read off the checkout when this report was made: "
                      f"{len(files)} file(s) were touched"
                      + (f", {shell} edit(s) through the shell" if shell else "")
                      + ", and none holds a temporary marker now.")
    if rows:
        return Signal("cleanup_balance", "warn",
                      "temporary instrumentation may have been left behind",
                      "Per file, additions of the prefix do not match removals. "
                      "Editing counts are a proxy — check the tree with grep.",
                      rows,
                      f"Run: {_cleanup_grep(inst['prefix'])}")
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
                      f"Run: {_cleanup_grep(inst['prefix'])}")
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
                      "Confidence / Ruled out / Also measured / Cleanup. A missing "
                      "field is a decision the human now has to make blind — a "
                      "missing `Also measured` is every number the hunt took and did "
                      "not pass on, and a missing `Ruled out` is the next round spent "
                      "looking where this one already looked.",
                      rows)
    return Signal("conclusion_shape", "ok", "the conclusion came back in the agreed shape",
                  "; ".join(f"{h['type'] or h['id']}: confidence {h['confidence'] or '?'}"
                            for h in f.hunts))


def config_bypassed(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    project_cfg = Path(cfg.path).name if cfg and cfg.path else "echolot.yml"
    # `local.yml` is the other half of the project's config, not a config of
    # the agent's own: it sits beside echolot.yml, is merged on top of it, and
    # holds the device serial and the path to this machine's
    # trace_processor. Writing it is how a session gets a device — and every
    # one of those writes was being reported as analysis run against
    # somebody else's config.
    local_cfg = Path(cfg.local_path).name if cfg and cfg.local_path else "local.yml"
    ours = {project_cfg, local_cfg}
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
    # A YAML file written in the session is a config of the agent's own only
    # if a command was then given it with -c. A GitHub workflow for a
    # nightly benchmark, written and never passed to echolot, was listed as
    # analysis run on another config.
    given = [(ts_to_epoch(c.ts), c.config.strip("\"'")) for c in f.echolot_calls
             if c.config and c.sub in ("analyze", "calibrate", "collect", "names")]

    def passed_later(ts: str, path: str) -> bool:
        return any(when >= ts_to_epoch(ts)
                   and (cfg == path or Path(cfg).name == Path(path).name)
                   for when, cfg in given)

    written = []
    for c in s.calls:
        if c.tool == "Write" and c.path and c.path.endswith((".yml", ".yaml")) \
                and Path(c.path).name not in ours and passed_later(c.ts, c.path):
            written.append({"ts": c.ts, "agent": c.agent, "sub": "Write",
                            "config": c.path[-80:]})
    for c in s.bash():
        cmd = c.shell
        # Any redirect into a yaml file that is not the project's: `cat >`
        # heredocs, `sed … > /tmp/x.yml`, `>>` appends.
        for m in _YAML_REDIRECT.finditer(cmd):
            p = m.group(1)
            if Path(p).name in ours or (c.ts, "w", p) in seen or not passed_later(c.ts, p):
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


# The config's sections that hold no detector threshold: `loop.max_rounds`
# is the one number a human sets to bound a hunt, and was read as one.
_NOT_DETECTORS = {"project", "scenario", "runner", "loop", "instrumentation",
                  "domains", "toolchain", "names"}


def _threshold_values(text: str) -> dict[str, str]:
    """The detector parameters a piece of config text carries, with values.

    A whole file is read as YAML, and each numeric parameter under
    `detectors:` is keyed `detector.parameter`: by the bare key, the three
    detectors that have a `max_total_ms` overwrote each other, and a change
    to one of them read as no change. A fragment, what an Edit carries, is
    read for its `min_*` / `max_*` keys, unless it is plainly another
    section's.
    """
    try:
        data = yaml.safe_load(text or "")
    except yaml.YAMLError:
        data = None
    if isinstance(data, dict) and "detectors" in data:
        out = {}
        dets = data.get("detectors")
        for det, params in (dets.items() if isinstance(dets, dict) else ()):
            for k, v in (params.items() if isinstance(params, dict) else ()):
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out[f"{det}.{k}"] = str(v)
        return out
    if isinstance(data, dict) and data and set(data) <= _NOT_DETECTORS:
        return {}
    return {m.group(1): m.group(2) for m in _DETECTOR_VALUE.finditer(text or "")
            if m.group(1) != "max_rounds"}


def thresholds_by_hand(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    """A threshold that changed — not a file that happens to contain one.

    The test was "the written text mentions a `min_*` key", and a `Write`
    carries the whole file: an agent that rewrote echolot.yml to add a domain
    or fix the process name shipped the untouched detectors section along with
    it, and every such edit was reported as thresholds tuned by hand. The
    numbers have to move for that claim to hold, so each write is compared
    with what the file said before it — the previous write in this session, or
    failing that the config as it now stands on disk.
    """
    on_disk = {}
    if cfg and cfg.path and Path(cfg.path).exists():
        on_disk = _threshold_values(
            Path(cfg.path).read_text(encoding="utf-8", errors="replace"))
    before: dict[str, dict[str, str]] = {}
    touched = []
    for w in config_writes(s):
        now = _threshold_values(w["text"])
        if not now:
            continue
        # An Edit carries both sides of the change; a Write or a shell command
        # carries only the result, so the baseline is what the file last said.
        was = _threshold_values(w.get("before", "")) if w["tool"] == "Edit" \
            else before.get(w["file"], on_disk)
        before[w["file"]] = {**before.get(w["file"], on_disk), **now}
        if any(was.get(k) != v for k, v in now.items()):
            touched.append(w)
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


def confirmed_changed(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    """A value a person confirmed, changed while an investigation was open.

    `analyze` holds the config to what the investigation recorded when it
    opened, and logs each value that moved. So this reads echolot's own calls
    and nothing else, and it works from the log where there is no
    transcript — which is where it happened first: a Codex session rewrote
    the end of the scenario a person had confirmed, and said so only after
    the report on the new window was made (#196).
    """
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for c in f.echolot_calls:
        facts = (c.recorded or {}).get("facts") or {}
        for ch in facts.get("confirmed_changed") or []:
            key = (str(ch.get("field")), repr(ch.get("was")), repr(ch.get("now")))
            row = seen.setdefault(key, {"ts": _t(c.ts), "agent": c.agent,
                                        "field": ch.get("field"), "was": ch.get("was"),
                                        "now": ch.get("now"), "reports": 0})
            row["reports"] += 1
    if not seen:
        return None
    return Signal("confirmed_changed", "warn",
                  "a value a person confirmed changed during an investigation",
                  "`_source: confirmed_by_user` marks the person's own choice, and every "
                  "report after the change measured a window they did not confirm.",
                  list(seen.values()),
                  hint="If the agent changed it, the loop went past what it may change: "
                       "perf-hunter.md says to stop and ask. If the person did, the "
                       "investigation holds the old value; a new one starts from theirs.")


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
    def row(c, command: str, code: str) -> dict[str, Any]:
        return {"ts": _t(c.ts), "agent": c.agent, "command": command,
                "selected": ", ".join(report_paths(code)) or None}

    rows = [row(c, _around(c.shell, "report.json"), c.shell)
            for c in s.bash() if RE_REPORT_BY_HAND.search(c.shell)]
    # The same cut made in python through an execute tool: in one session
    # about ten of them, none listed (#542). A file handed to the tool
    # whole counts when it is the report.
    rows += [row(c, _around(c.code or "", "report.json")
                 if "report.json" in (c.code or "") else f"{_short(c.path)}: "
                 + " ".join((c.code or "").split())[:80], c.code or "")
             for c in s.calls if c.code is not None
             and (RE_CODE_READS_REPORT.search(c.code)
                  or (c.path or "").endswith("report.json"))]
    rows.sort(key=lambda r: r["ts"])
    if not rows:
        return None
    return Signal("report_sliced_by_hand", "info",
                  f"report.json was cut up by hand {len(rows)} time(s)",
                  "The skill says to read the json; the agent then needed several "
                  "one-liners to get at what it wanted. Each is a view the report "
                  "does not offer directly.",
                  rows, _sliced_advice(rows))


def _sliced_advice(rows: list[dict[str, Any]]) -> str:
    """Which parts of the report the one-liners took, by how often (#547)."""
    count: dict[str, int] = {}
    for r in rows:
        for path in (r.get("selected") or "").split(", "):
            if path:
                count[path] = count.get(path, 0) + 1
    if not count:
        return ("The one-liners select nothing by name — they print the report "
                "whole or walk it: read them for what they were after, and the "
                "report's schema reference for whether it says where that is.")
    parts = ", ".join(f"`{p}` ({n}×)" for p, n in
                      sorted(count.items(), key=lambda kv: (-kv[1], kv[0])))
    return (f"The one-liners took {parts} out of report.json. Those are the parts "
            "the agent wanted by themselves: `references/report-schema.md` is where "
            "to say where they are, and a part asked for in most sessions is a view "
            "`report` could print.")


# Flags every command takes, which say nothing about what the agent was after.
_EVERY_COMMAND = {"-c", "--config", "--local", "-q", "--quiet"}
_FLAG = re.compile(r"(?:^|\s)(--?[A-Za-z][\w-]*)")


def _flags(argv: str) -> list[str]:
    return list(dict.fromkeys(f.split("=")[0] for f in _FLAG.findall(argv or "")
                              if f.split("=")[0] not in _EVERY_COMMAND
                              and f not in ("-h", "--help")))


def help_lookups(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    # The invocation itself, not the head of a Bash line it shared with a
    # `cd` and a python one-liner.
    real = sorted((c for c in f.echolot_calls if not c.is_help and c.ran),
                  key=lambda c: ts_to_epoch(c.ts))
    rows = []
    for c in f.echolot_calls:
        if not c.is_help:
            continue
        row = {"ts": _t(c.ts), "agent": c.agent,
               "command": f"echolot {c.sub} {c.argv}".strip()[:100]}
        # What the lookup was for: the command it was about, and what that
        # command then ran with. `explain` is about a detector, and `help`
        # alone is the list of commands; neither has a call to follow.
        if c.sub not in ("help", "explain"):
            at = ts_to_epoch(c.ts)
            row["used before"] = sum(1 for r in real if r.sub == c.sub and ts_to_epoch(r.ts) < at)
            after = next((r for r in real if r.sub == c.sub and ts_to_epoch(r.ts) >= at), None)
            row["then"] = (" ".join(_flags(after.argv)) or "no flag") if after \
                else f"no `{c.sub}` after it"
        rows.append(row)
    if not rows:
        return None
    return Signal("help_lookups", "info",
                  f"the agent consulted --help / explain {len(rows)} time(s) mid-work",
                  "The skill and references did not answer a question about how to "
                  "call the tool.",
                  rows, _help_advice(rows))


def _help_advice(rows: list[dict[str, Any]]) -> str:
    """Which commands' help the agent needed, whether before their first use,
    and the flags it then used: the line that belongs in the skill. The
    advice used to be the same sentence in every session (#547)."""
    first: dict[str, list[str]] = {}
    again, unused, explained, listing = [], [], [], 0
    for r in rows:
        words = r["command"].split()
        sub = words[1] if len(words) > 1 else ""
        if sub == "explain":
            explained.append(f"`{' '.join(words[1:3])}`")
            continue
        if "then" not in r:
            listing += 1
            continue
        name = f"`{sub} --help`"
        if r["used before"]:
            again.append(name)
        elif r["then"].startswith("no `"):
            unused.append(name)
        else:
            first.setdefault(name, [w for w in r["then"].split() if w.startswith("-")])
    said = []
    if first:
        said.append("It read " + _and([
            f"{name} (then ran with {_and([f'`{x}`' for x in flags]) if flags else 'no flag'})"
            for name, flags in first.items()])
            + " before the first use: the text that sends the agent there does not "
            "show how to call " + ("it" if len(first) == 1 else "them")
            + ", and those flags are the line to add.")
    if again:
        said.append(f"It read {_and(again)} after using "
                    + ("it" if len(again) == 1 else "them")
                    + " already: a flag it went back for is missing from the reference.")
    if unused:
        said.append(f"It read {_and(unused)} and did not run "
                    + ("it" if len(unused) == 1 else "them")
                    + ": the help did not answer what it was after.")
    if explained:
        said.append(f"It asked for {_and(explained)}: the report's row did not say "
                    "enough to act on.")
    if listing:
        said.append("It listed echolot's commands: it was looking for which one to run.")
    return " ".join(said)


def _and(items: list[str]) -> str:
    items = list(dict.fromkeys(items))
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


_COMPLAINT = re.compile(
    r"\b(?:bug|bugs|баг|баги|багов|traceback|false positive|ложн\w*|"
    r"wrong|broken|refus\w*|отказ\w*|стёр|затёр|сломал\w*|не (?:видит|замечает|"
    r"работает|читает)|does not (?:see|read|work)|doesn't (?:see|read|work))\b", re.I)


def agent_reported_bugs(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    """The agent said, in its own words, that something in echolot was wrong.

    On a real session the main context wrote "three bugs of echolot's own
    turned up on the way" and listed them, and no signal here read that
    paragraph: every check is a rule over calls and edits, and the sentence
    that names the defect directly was the one thing the report did not
    carry. It is the cheapest feedback the tool can get, and it is quoted.
    """
    rows = []
    for t in s.turns:
        if t.role != "assistant":
            continue
        for para in re.split(r"\n\s*\n", t.text):
            low = para.lower()
            if "echolot" in low and _COMPLAINT.search(para):
                rows.append({"ts": _t(t.ts), "agent": t.agent,
                             "said": " ".join(para.split())[:240]})
                break
    if not rows:
        return None
    return Signal("agent_reported_bugs", "info",
                  f"the agent reported {len(rows)} problem(s) with echolot in its own words",
                  "Quoted rather than classified: a sentence that names the defect "
                  "is worth more than any rule here.",
                  rows,
                  "Read each one against the signals above; the ones no signal "
                  "caught are the ones worth a check of their own.")


def bypass_tools(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    counts: dict[str, list[dict[str, Any]]] = {}
    for c in s.bash():
        cmd = c.shell
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
                tail = re.split(r"[\s\"']", rest[1:], maxsplit=1)[0]
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
        cmd = c.shell
        # One command of the line at a time, in the order they run:
        # `cp metrics.json /tmp && mv "$OUT" …` is a copy of something else
        # and a move of the traces, and in `./gradlew connected… && echolot
        # analyze …` the re-record comes first. Taken whole, a line with an
        # analyze in it was an analyze and nothing else, and the re-record
        # before it was never checked.
        for raw_seg in _SEGMENT.split(cmd):
            if runs_analyze(raw_seg):
                for m in RE_ECHOLOT.finditer(raw_seg):
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
            if RE_RE_RECORD.search(seg):
                before = touched or (analyzed_dirs[-1] if analyzed_dirs else "?")
                # A set under .echolot/ is where `hunt` files the evidence and
                # where this signal's own hint says to copy it: a re-record
                # writes into the build tree and leaves it alone.
                kept = ".echolot/" in before or before.startswith(".echolot")
                if not re.search(r"echolot\s+collect\b", seg) \
                        and not preserved_since_analyze and not kept:
                    # collect sets the previous set aside itself
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
        if _HOST.search(c.output_head or ""):
            continue   # the harness spoke, not echolot — see env_friction
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
        # The run log's own sentence outranks the transcript's copy of the
        # output: a `collect` that failed under an agent's harness left the
        # harness's "moved to the background" notice as its output, and the
        # reason sat in nobody's record until the recorder kept it.
        head = ((c.recorded or {}).get("error") or c.output_head or "")
        rows.append({"ts": _t(c.ts), "agent": c.agent, "sub": c.sub, "exit": c.exit,
                     "where": where, "traceback": c.traceback,
                     "head": head.replace("\n", " ")[:160]})
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


def report_not_the_scenario(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    """An `analyze` that exited 0 on numbers that were not the scenario's.

    The costliest events of a session left no trace in `reflect`: a round
    where the scenario never ran, a window that was the whole trace, both
    counted as a successful `analyze` (#545). Read off the run's own line in
    the log and the report it wrote — see `facts.reports`.
    """
    rows = []
    for r in f.reports:
        if not r["said"] or r["exit"] not in (0, None):
            continue   # a refusal is already a failure, in echolot_failures
        if r["printed"] is None:
            seen = "not in this source"
        else:
            seen = f"printed {r['printed']} chars" if r["printed"] else "printed nothing"
        if r["read_after"]:
            seen += f", read back {r['read_after']}×"
        rows.append({"ts": _t(r["ts"]), "agent": r["agent"], "said": "; ".join(r["said"]),
                     "seen": seen,
                     "round": f"{r['round_s'] // 60}m{r['round_s'] % 60:02d}s"
                              if r.get("round_s") is not None else "—",
                     "report": r["on_disk"] + (f", {facts_mod.size_text(r['json_bytes'])}"
                                               if r.get("json_bytes") else "")})
    if not rows:
        return None
    return Signal("report_not_the_scenario", "warn",
                  f"{len(rows)} analyze run(s) exited 0 on numbers that were not "
                  f"about the scenario",
                  "The run's own record says its window was not the scenario's — "
                  "an anchor that matched nothing, markers missing from the build, "
                  "a warm run in a cold set, a round in which the app drew no frame "
                  "or a tenth of the round before. `analyze` exited 0 all the same, "
                  "and what the agent did next was built on it: `round` is the time "
                  "until its next analyze.",
                  rows,
                  "Read `seen` first: the header said so, and the agent did not "
                  "take it in, or took it in and went on. A non-zero exit, or the "
                  "warning as the first line of what `analyze` prints, is what "
                  "would have stopped it.")


def retries(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    rows = []
    calls = f.echolot_calls
    for a, b in zip(calls, calls[1:], strict=False):
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
        # Stopped by the agent's harness — a permission screen, a declined
        # prompt — is not a failure, as `echolot_failures` already reads it.
        if _HOST.search(a.output_head or ""):
            a_failed = False
        if a_failed and ts_to_epoch(b.ts) - ts_to_epoch(a.ts) <= 180:
            # The same argv twice says nothing; what moved between the two
            # attempts was a config file, and that is the column to read.
            t0, t1 = ts_to_epoch(a.ts), ts_to_epoch(b.ts)
            written = sorted({w["file"] for w in config_writes(s)
                              if t0 <= ts_to_epoch(w["ts"]) <= t1})
            rows.append({"ts": _t(a.ts), "agent": a.agent, "sub": a.sub,
                         "first": a.argv[:80], "then": b.argv[:80],
                         "between": ", ".join(f"{w} written" for w in written) or
                         ("the same argv again" if a.argv == b.argv else "the argv changed"),
                         "seconds": round(t1 - t0)})
    if not rows:
        return None
    return Signal("retries", "info",
                  f"{len(rows)} echolot call(s) were retried after failing",
                  "The agent recovered by itself. What it changed between the two "
                  "attempts is what the first error message should have said.",
                  rows)


# Commands whose exit 1 is an answer rather than an error: `diff` and `cmp`
# say the files differ, the greps that nothing matched.
_ANSWERS_WITH_1 = {"diff", "cmp", "grep", "egrep", "fgrep", "zgrep", "rg"}
_EXIT_1 = re.compile(r"^\s*Exit code 1\b")


def _answered(c) -> bool:
    """A shell call that exited 1 because the command it ends with said "no".

    The line's exit status is its last command's, and of a pipeline, its last
    stage's: `cd x && diff a b` is diff's answer, `diff a b | head` is
    head's. `diff` saying the files differ was filed as friction (#546).
    """
    if c.command is None or not _EXIT_1.match(c.output_head or ""):
        return False
    last = [seg for seg in re.split(r"\n|;|&&|\|\|", c.shell) if seg.strip()]
    if not last:
        return False
    words = last[-1].split("|")[-1].split()
    while words and (words[0] == "!" or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0])):
        words = words[1:]
    return bool(words) and Path(words[0]).name in _ANSWERS_WITH_1


def env_friction(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    kinds: dict[str, list[dict[str, Any]]] = {}
    for c in s.calls:
        head = c.output_head or ""
        host = bool(_HOST.search(head))
        if not c.is_error and not host:
            continue
        if not host and _answered(c):
            continue
        if c.command is not None and RE_ECHOLOT.search(c.shell) and not host:
            continue   # echolot's own failures are a separate signal
        for kind, rx in _ENV_KINDS:
            if rx.search(head):
                kinds.setdefault(kind, []).append(
                    {"ts": _t(c.ts), "agent": c.agent, "tool": c.tool,
                     "what": (c.shell or c.path or "")[:80],
                     "error": head.replace("\n", " ")[:120]})
                break
        else:
            kinds.setdefault("other", []).append(
                {"ts": _t(c.ts), "agent": c.agent, "tool": c.tool,
                 "what": (c.shell or c.path or "")[:80],
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
        # Whether the loop ran `mark`, which proposes the first markers, and
        # how much of the reading came after it (#547).
        label = f"sub:{h['id']}"
        marks = [c for c in f.echolot_calls
                 if c.agent == label and c.sub == "mark" and c.ran and not c.is_help]
        after = None
        if marks:
            at = ts_to_epoch(marks[0].ts)
            prefix = (cfg.temp_prefix if cfg else None) or DEFAULT_PREFIX
            after = sum(1 for c in s.calls_of(label) if ts_to_epoch(c.ts) > at
                        and facts_mod.activity_of(c, prefix) == "source reading")
        rows.append({
            "agent": h["type"] or h["id"],
            "source reads": src["calls"],
            "chars": src["chars"],
            "share of window": f"{share}%",
            "before first instrumentation edit": w.get("source_reads_before_first_edit", 0),
            "echolot calls": ((w.get("by_activity") or {}).get("echolot") or {}).get("calls", 0),
            "echolot mark": len(marks),
            "reads after mark": after,
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
                  rows, _reading_advice(rows))


def _reading_advice(rows: list[dict[str, Any]]) -> str:
    """Whether each loop that read the app had run `mark`, said per loop:
    the advice used to tell the reader to go and check (#547)."""
    said = []
    for r in rows:
        who = f"`{r['agent']}` read sources {r['source reads']} time(s), " \
              f"{r['before first instrumentation edit']} before its first marker"
        if not r["echolot mark"]:
            said.append(f"{who}, and never ran `echolot mark`: the loop placed its first "
                        "markers by reading instead of from what mark proposes — the "
                        "loop guide's step that sends it to mark is what to look at.")
        else:
            after = r["reads after mark"]
            said.append(f"{who}; it ran `echolot mark` and read {after} more time(s) after "
                        "it" + (": those reads are where mark proposed nothing, the places "
                                "to teach it." if after else
                                ": the reading was all before it, so the loop reached mark late."))
    return " ".join(said)


def long_gaps(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    rows = [g for g in f.gaps if g["seconds"] >= 120]
    if not rows:
        return None
    # The facts keep the longest few; the title counts them all, or it never
    # said more than eight.
    total = len(gaps(s, keep=None))
    return Signal("long_gaps", "info",
                  f"{total} silence(s) of two minutes or more"
                  + (f", the {len(rows)} longest listed" if total > len(rows) else ""),
                  "A subagent running, the human answering, a build in the "
                  "background, a device — the cause is read off what the session "
                  "was waiting on during the silence, and failing that the call "
                  "before it. Listed so the wall time reads honestly.",
                  [{"agent": g["agent"], "seconds": g["seconds"],
                    "why": g.get("why", "unknown"), "after": g["after"]}
                   for g in rows])


def context_hogs(s: Session, f: Facts, cfg: Config | None) -> Signal | None:
    big = [o for o in f.top_outputs if o["chars"] >= 8000]
    if not big:
        return None
    from_echolot = [o for o in big if o["echolot"]]
    total = sum(1 for c in s.calls if c.output_chars >= 8000)
    return Signal("context_hogs", "info",
                  f"{total} tool output(s) over 8k characters"
                  + (f", the {len(big)} largest listed" if total > len(big) else ""),
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
    guides_in_main,
    rounds_over_max,
    instrumentation_prefix,
    edits_outside_allowed,
    cleanup_balance,
    conclusion_shape,
    config_bypassed,
    thresholds_by_hand,
    confirmed_changed,
    baseline_lost,
    # workarounds
    report_sliced_by_hand,
    help_lookups,
    bypass_tools,
    agent_reported_bugs,
    # failures
    echolot_failures,
    report_not_the_scenario,
    retries,
    env_friction,
    # cost
    code_read_by_hand,
    long_gaps,
    context_hogs,
]


# The signals that need nothing but echolot's own calls — which is all the
# recorder log holds, and therefore all a session read without a transcript
# has. Named as an allowlist rather than the other way round on purpose: get
# this list wrong by omission and a check is skipped that could have run,
# which costs a line in the report. Get it wrong the other way and a check
# reports "clean" over evidence it never had, which is worse than useless.
FROM_CALLS_ALONE = {
    "doctor_first",
    "confirmed_changed",
    "echolot_failures",
    "report_not_the_scenario",
    "retries",
    "help_lookups",
    "long_gaps",
}

# What still means something when the session was building echolot rather
# than using it. Everything else here is a rule for a hunt — do not open the
# trace yourself, analyse against the project's config, capture through the
# tool — and a session spent writing detectors breaks all of them by
# definition. See `facts.building_the_tool` for the shape that produced.
#
# An allowlist rather than a list of what to drop, and the same shape as the
# set above, so a signal added later is held back here until somebody decides
# it means something in this mode. The other default is a new signal quietly
# misfiring on every session that touches this repository.
MEANS_SOMETHING_WHILE_BUILDING = {
    "echolot_failures",
    "retries",
    "env_friction",
    "long_gaps",
    "context_hogs",
    "help_lookups",
}


# A check that reads one thing more than tool calls, and the thing. Codex keeps
# the message a subagent is handed encrypted on disk: read from there, the
# brief is empty, and every piece of it would be reported as missing.
NEEDS = {
    "agent_prompt_gaps": (BRIEFS, "what the main context handed the subagent"),
}


def run(session: Session, facts: Facts, cfg: Config | None) -> list[Signal]:
    out: list[Signal] = []
    partial = not session.shows(TOOLS)
    building = bool(facts.building)
    for det in SIGNALS:
        if building and det.__name__ not in MEANS_SOMETHING_WHILE_BUILDING:
            out.append(Signal(
                det.__name__, "skip", f"{det.__name__} — not checked",
                "This session was building echolot rather than using it: the "
                "project is the tool's own checkout and nothing was collected "
                "or hunted. Every check held back here is a rule for a hunt, "
                "and a session spent writing detectors breaks them by "
                "definition. Silence is no verdict, the same as above."))
            continue
        if partial and det.__name__ not in FROM_CALLS_ALONE:
            out.append(Signal(
                det.__name__, "skip", f"{det.__name__} — not checked",
                "This source carries echolot's own calls and nothing else, and "
                "the check needs more than that. Silence here is not a clean "
                "verdict; it is no verdict."))
            continue
        need = NEEDS.get(det.__name__)
        if need and not session.shows(need[0]):
            out.append(Signal(
                det.__name__, "skip", f"{det.__name__} — not checked",
                f"This source does not carry {need[1]}, and the check reads "
                f"nothing else. Silence here is no verdict."))
            continue
        try:
            sig = det(session, facts, cfg)
        except Exception as e:   # one broken detector must not kill the report
            # `skip`, and not `info`. A signal that raised checked nothing,
            # which is the same state as one whose source could not carry
            # what it needed — and the section above already argues that
            # silence there is no verdict rather than a clean one. Filed as
            # `info` it sat among the friction hints, where a reader looking
            # for what the session did wrong would take it for one more
            # observation about the session instead of a hole in the report.
            sig = Signal(det.__name__, "skip",
                         f"{det.__name__} — not checked, the signal broke",
                         f"{type(e).__name__}: {e}. Nothing was examined, so "
                         f"nothing here is a verdict. This is a bug in "
                         f"echolot rather than anything about the session.")
        if sig is not None:
            out.append(sig)
    order = {"warn": 0, "info": 1, "ok": 2, "skip": 3}
    out.sort(key=lambda x: order.get(x.severity, 4))
    return out
