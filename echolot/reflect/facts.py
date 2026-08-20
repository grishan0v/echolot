"""Derived facts over a normalised session.

Everything here is a pure function of the `Session` (plus the config and the
recorder log): the echolot invocations as a table, the milestones of the
protocol, the number of hunt rounds, the cost, the balance of temporary
instrumentation per file. Signals and the report both read from here, so a
number appears the same way in both.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from .model import MAIN, Call, Session, ts_to_epoch

# Shared with signals.py — the vocabulary of what an agent does around the tool.
RE_ECHOLOT = re.compile(r"(?:^|[\s;&|(`$/])echolot\s+([a-z-]+)((?:\s+[^;&|\n]*)?)")
RE_RE_RECORD = re.compile(
    r"gradlew\b(?![^\n]*\btasks\b)[^\n]*\bconnected\w+|echolot\s+collect|"
    r"adb\s+shell\s+perfetto|adb\s+shell\s+am\s+start|record_android_trace",
    re.I | re.S)
RE_TRACE_LITERAL = re.compile(r"\.(perfetto-trace|pftrace)\b")
RE_TRACE_OPEN = re.compile(
    r"trace_processor|from\s+perfetto|import\s+perfetto|TraceProcessor\(", re.I)
RE_REPORT_BY_HAND = re.compile(
    r"(json\.load|jq\b|python3?\s+-c).*report\.json|report\.json.*(json\.load|jq\b)",
    re.S)
RE_HELP_FLAG = re.compile(r"(?:^|\s)(?:--help|-h)(?:\s|$)")

_EXIT = re.compile(r"Exit code (\d+)")
_SHELL_ERROR = re.compile(r"\(eval\):|\bcd:\d*:|no matches found|command not found: (?!echolot)")
# zsh names the glob that matched nothing; that names the invocation that
# never ran when several share one Bash line. Paths carry spaces (device
# names do), so take the whole rest of the line.
_GLOB_MISS = re.compile(r"no matches found: ([^\n]+)")
_REDIRECT = re.compile(r"(?:^|\s+)\d?[<>]\S*.*$")
_CONFIG_ARG = re.compile(r"(?:^|\s)(?:-c|--config)\s+(\S+)")
_TRACE_CALL = re.compile(
    r"\btrace\s*\(\s*\"|Trace\.beginSection\s*\(|beginAsyncSection\s*\(|"
    r"androidx\.tracing", re.S)
_CONCLUSION_FIELDS = {
    "place": r"\bPlace\b|Место",
    "evidence": r"\bEvidence\b|Улик|Доказательств",
    "mechanism": r"\bMechanism\b|Механизм",
    "suggestion": r"\bSuggestion\b|\bFix\b|Предложен|Что делать|Что чинить|Что исправ|Рекоменд",
    "confidence": r"\bConfidence\b|Уверенност",
    "cleanup": r"\bCleanup\b|Уборк|Очистк",
}
# The value after "Confidence:", up to the end of the phrase — markdown
# emphasis, a bracket or a full stop close it.
_CONFIDENCE = re.compile(
    r"(?:Confidence|Уверенность)\s*[:*]*\s*\**\s*([^\n.*(]{0,60})", re.I)
# A heredoc body is data, not commands: `cat > x.yml <<EOF … echolot calibrate …`
# is a config being written, not calibrate being run.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(?:.*?\n)?\s*\2[ \t]*(?=\n|$)", re.S)
# A traceback whose first frame is the shell's inline python belongs to the
# agent's one-liner, not to echolot.
_TB_FIRST_FRAME = re.compile(r"Traceback \(most recent call last\):\s*File \"([^\"]*)\"")
# Python in a shell command writing some file; which file is usually in a
# variable, so this is paired with "the name is mentioned somewhere".
_PY_WRITES = re.compile(r"open\([^)]*[\"'](?:w|a|r\+)[\"']|\.write_text\(|\.write\(", re.S)


@dataclass
class EcholotCall:
    ts: str
    agent: str
    sub: str                      # subcommand: analyze, doctor, ...
    argv: str                     # the rest of the line, clipped
    command: str                  # the whole Bash command, clipped
    is_error: bool
    exit: int | None
    duration_s: float | None
    output_chars: int
    output_head: str
    config: str | None            # -c value, if any
    traceback: bool
    is_help: bool
    shell_error: bool = False     # the shell failed before echolot ran (cd, glob)
    shared: int = 1               # echolot invocations in the same Bash call
    ran: bool = True              # False when the shell skipped this one (glob miss)
    recorded: dict[str, Any] | None = None   # matched runs.jsonl entry


@dataclass
class Facts:
    echolot_calls: list[EcholotCall] = field(default_factory=list)
    milestones: list[dict[str, Any]] = field(default_factory=list)
    hunts: list[dict[str, Any]] = field(default_factory=list)   # per perf-hunter
    cost: dict[str, Any] = field(default_factory=dict)
    top_outputs: list[dict[str, Any]] = field(default_factory=list)
    instrumentation: dict[str, Any] = field(default_factory=dict)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    entry: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    runs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------ echolot calls

def _glob_score(argv: str, missing: str) -> int:
    """How much of the glob the shell could not expand appears in this argv.

    zsh prints the glob after variable expansion; the argv still has `$BASE`
    and its quotes. So compare on suffixes: the number of trailing path
    segments of the missing glob that the (unquoted) argv contains. Two
    invocations that differ only in a directory tie on the file name and
    part on the directory.
    """
    plain = argv.replace('"', "").replace("'", "")
    parts = missing.strip().split("/")
    best = 0
    for i in range(len(parts)):
        suffix = "/".join(parts[len(parts) - 1 - i:])
        if suffix and suffix in plain:
            best = i + 1
        else:
            break
    return best


def _skipped_by_glob(argvs: list[str], missing: str) -> int | None:
    """Index of the invocation the shell skipped, or None when it is unclear."""
    scores = [_glob_score(a, missing) for a in argvs]
    top = max(scores) if scores else 0
    if top == 0 or scores.count(top) != 1:
        return None
    return scores.index(top)


def strip_heredocs(cmd: str) -> str:
    """The command with heredoc bodies removed, first lines kept.

    What is between `<<EOF` and `EOF` is data — a config being written, a
    python script — and an `echolot calibrate` mentioned in there is not a
    call. Everything that scans for commands works on this view.
    """
    return _HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0] + "\n", cmd)


def _is_echolot_traceback(head: str) -> bool:
    """A traceback in the output, and not from the agent's inline python."""
    if "Traceback (most recent call last)" not in head:
        return False
    first = _TB_FIRST_FRAME.search(head)
    return not (first and first.group(1) in ("<stdin>", "<string>"))


def writes_file(cmd: str, name: str) -> bool:
    """Does this shell command write the named file (a name, or a path tail)?

    A redirect into it, `sed -i` / `perl -pi` / `tee` / `cp` / `mv` with it as
    an argument, or python that opens something for writing while the name
    is mentioned in the same command (the path is usually in a variable by
    then). Edit/Write-only reading missed all of these.
    """
    if name not in cmd:
        return False
    n = re.escape(name)
    if re.search(r">>?\s*[\"']?[^\s\"'|;&]*" + n, cmd):
        return True
    if re.search(r"(?:^|[\s;&|(])(?:sed\s+-[a-zA-Z]*i\S*|perl\s+-[a-zA-Z]*i\S*|tee|cp|mv)\b"
                 r"[^\n;|&]*" + n, cmd):
        return True
    return bool(_PY_WRITES.search(cmd))


def verbs() -> frozenset[str]:
    """Every subcommand the installed package registers.

    Late import: main imports reflect, so this cannot be a module-level one.
    Read from the parser rather than written down here, for the same reason
    `--help` is generated from it — a list kept by hand goes stale on the next
    verb, and what it produces in the meantime is plausible.
    """
    from ..main import ORDER
    return frozenset(ORDER)


def is_invocation(word: str, known: frozenset[str] | None = None) -> bool:
    """Is this word after `echolot` a subcommand, or is it English?

    The word `echolot` followed by a word is not a call. It is also an echo, a
    comment, a leftover heredoc line and, most often, prose: a real report once
    counted `ran`, `call`, `calls` and `without` as subcommands, and its "By
    subcommand" line read like a sentence. Everything downstream — the
    timeline, the tally, the signals — was measuring English.

    A leading `-` is `echolot --help`, which is a call and is spelled `help`.
    """
    return word.startswith("-") or word in (known if known is not None else verbs())


def subcommands(command: str) -> list[str]:
    """The subcommands one shell command really invokes, in order.

    What the readers report as "this session used echolot". Heredoc bodies are
    data, not commands — `cat > x.yml <<EOF … echolot calibrate … EOF` is a
    config being written.
    """
    known = verbs()
    return ["help" if m.group(1).startswith("-") else m.group(1)
            for m in RE_ECHOLOT.finditer(strip_heredocs(command))
            if is_invocation(m.group(1), known)]


def echolot_calls(session: Session) -> list[EcholotCall]:
    out: list[EcholotCall] = []
    known = verbs()
    for c in session.bash():
        cmd = strip_heredocs(c.command or "")
        head = c.output_head or ""
        matches = [m for m in RE_ECHOLOT.finditer(cmd)
                   if is_invocation(m.group(1), known)]
        exit_m = _EXIT.search(head)
        shell_err = bool(_SHELL_ERROR.search(head))
        glob_miss = _GLOB_MISS.search(head)
        argvs = [_REDIRECT.sub("", m.group(2) or "").strip() for m in matches]
        skipped = _skipped_by_glob(argvs, glob_miss.group(1)) if glob_miss else None
        for i, m in enumerate(matches):
            argv = argvs[i]
            cfg = _CONFIG_ARG.search(argv)
            sub = m.group(1)
            if sub.startswith("-"):
                sub = "help"
            # Per invocation, not per Bash line: `echolot doctor; echolot
            # names --help` is one lookup, not two.
            is_help = sub in ("help", "explain") or bool(RE_HELP_FLAG.search(argv))
            # One Bash line, several invocations: the tool's exit code,
            # duration and output belong to the line, not to any one of
            # them. A glob that matched nothing names the one zsh skipped;
            # the rest ran and share the numbers.
            ran = True
            if skipped is not None:
                ran = i != skipped
            elif shell_err and c.is_error and len(matches) == 1:
                ran = False
            this_shell_err = shell_err and (not ran or c.is_error)
            exit_code: int | None
            if exit_m:
                exit_code = int(exit_m.group(1))
            elif not ran or c.is_error:
                exit_code = None
            else:
                exit_code = 0
            out.append(EcholotCall(
                ts=c.ts, agent=c.agent, sub=sub, argv=argv[:200],
                command=cmd[:300], is_error=c.is_error,
                exit=exit_code,
                duration_s=c.duration_s if ran else None,
                output_chars=c.output_chars,
                output_head=head,
                config=cfg.group(1) if cfg else None,
                traceback=_is_echolot_traceback(head),
                is_help=is_help,
                shell_error=this_shell_err,
                shared=len(matches),
                ran=ran,
            ))
    out.sort(key=lambda e: ts_to_epoch(e.ts))
    return out


def match_runs(calls: list[EcholotCall], runs: list[dict[str, Any]],
               window: tuple[float, float] | None, slack_s: float = 15.0
               ) -> list[dict[str, Any]]:
    """Attach recorder entries to transcript calls by subcommand and time.

    Returns the recorder entries that fall inside the session window (the
    session's own view of the runs), having stamped the matching ones onto
    the calls. The recorder's timestamp is the start of the run; the
    transcript's is when the agent issued the Bash call — the two differ by
    the shell's own startup, so a few seconds of slack is expected.
    """
    inside = []
    for r in runs:
        t = ts_to_epoch(r.get("ts"))
        if window and not (window[0] - slack_s <= t <= window[1] + slack_s):
            continue
        inside.append(r)
    used: set[int] = set()
    for c in calls:
        best, best_d = None, None
        for i, r in enumerate(inside):
            if i in used or r.get("cmd") != c.sub:
                continue
            d = abs(ts_to_epoch(r.get("ts")) - ts_to_epoch(c.ts))
            if d <= slack_s and (best_d is None or d < best_d):
                best, best_d = i, d
        if best is not None:
            used.add(best)
            c.recorded = inside[best]
            if c.exit is None or c.exit == 0:
                c.exit = inside[best].get("exit", c.exit)
    return inside


# ------------------------------------------------------------ config writes

def config_writes(session: Session, names: tuple[str, ...] = ("echolot.yml", "local.yml")
                  ) -> list[dict[str, Any]]:
    """Every time the project config was written — by a tool or from the shell.

    Write/Edit on the file is the obvious way; agents also do it with a
    python heredoc, `sed -i`, or a redirect, and that used to be invisible.
    Each entry: ts, agent, file, tool ("Bash" for the shell), text — the new
    content or the command, for whoever wants to look for detector keys.
    """
    out = []
    for c in session.calls:
        if c.tool in ("Edit", "Write", "MultiEdit") and c.path \
                and Path(c.path).name in names:
            inp = c.input or {}
            out.append({"ts": c.ts, "agent": c.agent, "file": Path(c.path).name,
                        "tool": c.tool,
                        "text": str(inp.get("new_string") or inp.get("content") or "")})
            continue
        if c.command is not None:
            for name in names:
                if writes_file(c.command, name):
                    out.append({"ts": c.ts, "agent": c.agent, "file": name,
                                "tool": c.tool, "text": c.command})
                    break
    out.sort(key=lambda e: ts_to_epoch(e["ts"]))
    return out


# --------------------------------------------------------------- milestones

def _mark(ts: str, label: str, agent: str, detail: str = "") -> dict[str, Any]:
    return {"ts": ts, "label": label, "agent": agent, "detail": detail}


def milestones(session: Session, calls: list[EcholotCall]) -> list[dict[str, Any]]:
    marks: list[dict[str, Any]] = []
    for t in session.turns:
        if t.kind == "slash":
            marks.append(_mark(t.ts, f"slash {t.command}", t.agent, t.args or ""))
        elif t.kind == "interrupt":
            marks.append(_mark(t.ts, "user interrupted", t.agent))
    firsts: dict[str, EcholotCall] = {}
    for c in calls:
        firsts.setdefault(c.sub, c)
    for sub, c in firsts.items():
        marks.append(_mark(c.ts, f"first echolot {sub}", c.agent, c.argv[:80]))
    for a in session.asks:
        marks.append(_mark(a.ts, "asked the human", a.agent, a.question[:80]))
    for w in config_writes(session):
        how = w["tool"] if w["tool"] in ("Edit", "Write", "MultiEdit") else "shell wrote"
        marks.append(_mark(w["ts"], f"{how} {w['file']}", w["agent"]))
    for s in session.subagents:
        marks.append(_mark(s.started or "", f"agent {s.type or '?'} launched",
                           MAIN, s.description or ""))
        if s.ended:
            marks.append(_mark(s.ended, f"agent {s.type or '?'} finished", MAIN))
    first_added = None
    for c in session.calls:
        if c.tool in ("Edit", "Write") and _has_prefix_added(c):
            first_added = _mark(c.ts, "temporary instrumentation added", c.agent,
                                _short(c.path))
            break
    for e in shell_edits(session, "AGENTTMP_"):
        if first_added is None or ts_to_epoch(e["ts"]) < ts_to_epoch(first_added["ts"]):
            first_added = _mark(e["ts"], "temporary instrumentation added (shell)",
                                e["agent"], ", ".join(_short(f) for f in e["files"][:2]))
        break
    if first_added:
        marks.append(first_added)
    marks.sort(key=lambda m: ts_to_epoch(m["ts"]))
    return marks


def _short(path: str | None, keep: int = 3) -> str:
    if not path:
        return ""
    return "/".join(Path(path).parts[-keep:])


def describe(c: Call, limit: int = 80) -> str:
    """`tool: what` — the command, the path, or the first string argument."""
    what = c.command or c.path
    if not what:
        inp = c.input or {}
        for key in ("description", "prompt", "code", "query", "skill", "url"):
            if isinstance(inp.get(key), str) and inp[key]:
                what = inp[key]
                break
        else:
            what = next((v for v in inp.values() if isinstance(v, str) and v), "")
    return f"{c.tool}: {' '.join(str(what).split())[:limit]}"


# --------------------------------------------------------------------- hunt

def hunts(session: Session, cfg: Config | None) -> list[dict[str, Any]]:
    """One entry per perf-hunter run: rounds, tools, tokens, conclusion."""
    out = []
    for s in session.subagents:
        label = f"sub:{s.id}"
        calls = session.calls_of(label)
        bash = session.bash(label)
        analyzes = [c for c in bash if re.search(r"echolot\s+analyze\b", c.command or "")]
        rerecords = [c for c in bash if RE_RE_RECORD.search(c.command or "")]
        # A round is an analyze that follows a re-record; the first analyze
        # opens round one. Ordering by time, per protocol.
        rounds = 0
        seen_record = False
        for c in sorted(bash, key=lambda x: ts_to_epoch(x.ts)):
            if RE_RE_RECORD.search(c.command or ""):
                seen_record = True
            elif re.search(r"echolot\s+analyze\b", c.command or ""):
                if rounds == 0 or seen_record:
                    rounds += 1
                    seen_record = False
        tools: dict[str, int] = {}
        for c in calls:
            tools[c.tool] = tools.get(c.tool, 0) + 1
        fields_present = {
            k: bool(re.search(p, s.final_text)) for k, p in _CONCLUSION_FIELDS.items()
        }
        conf = _CONFIDENCE.search(s.final_text or "")
        dur = None
        if s.started and s.ended:
            dur = round(ts_to_epoch(s.ended) - ts_to_epoch(s.started))
        out.append({
            "id": s.id,
            "type": s.type,
            "description": s.description,
            "started": s.started,
            "ended": s.ended,
            "duration_s": dur,
            "prompt_chars": len(s.prompt),
            "prompt_head": s.prompt[:400],
            "prompt_mentions": _prompt_mentions(s.prompt),
            "rounds": rounds,
            "max_rounds": (cfg.get("loop.max_rounds") if cfg else None),
            "analyze_calls": len(analyzes),
            "re_records": len(rerecords),
            "tools": tools,
            "usage": asdict(s.usage),
            "window": window_mix(session, label,
                                 (cfg.get("instrumentation.temp_prefix") if cfg else None)
                                 or "AGENTTMP_"),
            "thinking_blocks": s.thinking_blocks,
            "conclusion_fields": fields_present,
            "confidence": conf.group(1).strip(" *:") if conf else None,
            "final_text": s.final_text,
            "has_transcript": bool(s.source),
        })
    return out


def _prompt_mentions(prompt: str) -> dict[str, bool]:
    """The three things echolot-hunt.md says to pass down."""
    p = prompt or ""
    return {
        "traces": bool(RE_TRACE_LITERAL.search(p) or re.search(r"\btraces?\b|трейс", p, re.I)),
        "regression": bool(re.search(
            r"было|стало|regress|was\b.*\bnow\b|просел|P9\d|\d+\s*(?:ms|мс|s|с)\b", p, re.I)),
        "since_change": bool(re.search(
            r"после\s+(?:какого|коммит|измен)|after\s+(?:which|the)\s+(?:change|commit)|"
            r"since\s+commit|\bcommit\b|\bPR\b|\bMR\b", p, re.I)),
    }


# --------------------------------------------------------------------- cost

_SOURCE_FILE = re.compile(r"\.(?:kt|java|kts|xml|toml|properties|gradle|json|proto)\b|/src/")
_READ_VERB = re.compile(r"(?:^|[\s;&|(])(?:cat|sed\s+-n|head|tail|grep|rg|find|ls|wc|awk|less)\b")
_BUILD = re.compile(r"gradlew|\badb\s|\bsleep\s+\d|emulator\b")
_REPORT = re.compile(r"report\.(?:json|md)|\.echolot/out/")

ACTIVITIES = ("echolot", "report reading", "source reading",
              "instrumentation edit", "build/device", "other")


def activity_of(c: Call, prefix: str = "AGENTTMP_") -> str:
    """What one tool call was for — the buckets the window is split by.

    Deliberately coarse; the question it answers is "did the agent reason
    over the report or go read the app by hand", and for that a handful of
    buckets is enough. `echolot` wins over everything else in the same
    command, so a `names … | grep` is echolot, not reading.
    """
    if c.tool in ("Read",):
        p = c.path or ""
        if _REPORT.search(p):
            return "report reading"
        return "source reading" if _SOURCE_FILE.search(p) else "other"
    if c.tool in ("Edit", "Write", "MultiEdit"):
        return "instrumentation edit" if c.path and _SOURCE_FILE.search(c.path) else "other"
    cmd = c.command
    if cmd is None:
        return "other"
    if RE_ECHOLOT.search(strip_heredocs(cmd)):
        return "echolot"
    if _REPORT.search(cmd):
        return "report reading"
    if prefix in cmd and (_PY_WRITES.search(cmd) or _EDIT_VERB.search(cmd)):
        return "instrumentation edit"
    if _BUILD.search(cmd):
        return "build/device"
    if _SOURCE_FILE.search(cmd) and _READ_VERB.search(cmd):
        return "source reading"
    return "other"


def window_mix(session: Session, agent: str, prefix: str = "AGENTTMP_") -> dict[str, Any]:
    """What fed one agent's window, by activity: calls and tool-output chars.

    Characters are what the transcript has exactly; tokens are roughly a
    quarter of that for code and English, and the report says so rather
    than pretending to know. `first_edit_ts` lets a signal separate reading
    done to decide where to instrument from reading done instead of it.
    """
    mix: dict[str, dict[str, int]] = {a: {"calls": 0, "chars": 0} for a in ACTIVITIES}
    calls = sorted(session.calls_of(agent), key=lambda c: ts_to_epoch(c.ts))
    first_edit = None
    reads_before_edit = 0
    for c in calls:
        a = activity_of(c, prefix)
        mix[a]["calls"] += 1
        mix[a]["chars"] += c.output_chars
        if a == "instrumentation edit" and first_edit is None:
            first_edit = c.ts
        if a == "source reading" and first_edit is None:
            reads_before_edit += 1
    total = sum(v["chars"] for v in mix.values())
    for v in mix.values():
        v["share"] = round(100 * v["chars"] / total) if total else 0
    return {
        "by_activity": {a: v for a, v in mix.items() if v["calls"]},
        "total_chars": total,
        "source_reads_before_first_edit": reads_before_edit,
        "first_edit_ts": first_edit,
    }


def cost(session: Session) -> dict[str, Any]:
    tools_main: dict[str, int] = {}
    tools_sub: dict[str, int] = {}
    for c in session.calls:
        target = tools_main if c.agent == MAIN else tools_sub
        target[c.tool] = target.get(c.tool, 0) + 1
    sub_usage = {"input": 0, "cache_read": 0, "cache_create": 0, "output": 0,
                 "messages": 0}
    for s in session.subagents:
        for k in sub_usage:
            sub_usage[k] += getattr(s.usage, k)
    total_output_chars = sum(c.output_chars for c in session.calls)
    return {
        "duration_s": (round(session.duration_s()) if session.duration_s() else None),
        "usage_main": asdict(session.usage),
        "usage_subagents": sub_usage,
        "window_main": window_mix(session, MAIN),
        "tools_main": dict(sorted(tools_main.items(), key=lambda kv: -kv[1])),
        "tools_subagents": dict(sorted(tools_sub.items(), key=lambda kv: -kv[1])),
        "thinking_blocks_main": session.thinking_blocks,
        "tool_output_chars": total_output_chars,
        "user_turns": sum(1 for t in session.turns if t.role == "user" and t.kind == "text"),
        "asks": len(session.asks),
    }


def top_outputs(session: Session, n: int = 5) -> list[dict[str, Any]]:
    ranked = sorted(session.calls, key=lambda c: -c.output_chars)[:n]
    return [{
        "chars": c.output_chars, "tool": c.tool, "agent": c.agent,
        "what": (c.command or c.path or "")[:120],
        "echolot": bool(c.command and re.search(r"\becholot\s", c.command)),
    } for c in ranked if c.output_chars]


# ------------------------------------------------------- instrumentation

def _has_prefix_added(c: Call, prefix: str = "AGENTTMP_") -> bool:
    inp = c.input or {}
    new = str(inp.get("new_string") or inp.get("content") or "")
    old = str(inp.get("old_string") or "")
    return prefix in new and prefix not in old


def _has_prefix_removed(c: Call, prefix: str = "AGENTTMP_") -> bool:
    inp = c.input or {}
    new = str(inp.get("new_string") or "")
    old = str(inp.get("old_string") or "")
    return prefix in old and prefix not in new


_SRC_PATH = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:kt|java|kts))\b")
_CD_INTO = re.compile(r"(?:^|[\s;&|])cd\s+([\"']?)([^\s;&|\"']+)\1")
_EDIT_VERB = re.compile(r"(?:^|[\s;&|(])(?:sed\s+-[a-zA-Z]*i|perl\s+-[a-zA-Z]*i)\b")


def shell_edits(session: Session, prefix: str) -> list[dict[str, Any]]:
    """Source edits made through the shell that mention the prefix.

    A python heredoc with `open(path, 'w').write(...)`, a `sed -i`: the
    perf-hunter is given Edit, and still writes files this way — 45 Bash
    calls, zero Edit, in one session, and the whole "Temporary
    instrumentation" section went missing. Which files: every .kt/.java
    path in the command, made relative to a leading `cd`. Whether the prefix
    was added or removed cannot be told from an edit helper's arguments;
    the grep after the last edit settles that.
    """
    out = []
    for c in session.bash():
        cmd = c.command or ""
        if prefix not in cmd:
            continue
        if not (_PY_WRITES.search(cmd) or _EDIT_VERB.search(cmd)):
            continue
        base = ""
        m = _CD_INTO.search(cmd)
        if m:
            base = m.group(2)
        files = []
        for p in dict.fromkeys(_SRC_PATH.findall(cmd)):
            rel = p if p.startswith("/") or not base else base.rstrip("/") + "/" + p
            rel = _relative(rel, session.cwd)
            if _is_source(rel) and rel not in files:
                files.append(rel)
        out.append({"ts": c.ts, "agent": c.agent, "files": files,
                    "command": cmd[:200]})
    return out


def _grep_verdict(output: str, prefix: str) -> bool | None:
    """What the cleanup grep found: True = nothing, False = still there.

    `grep -rn PREFIX … | wc -l` → "0"; an `echo "label: $(… | wc -l)"` →
    ends with ": 0"; a bare grep with no matches exits 1 and prints nothing;
    a match prints the line, prefix and all. Anything else: None, unknown.
    """
    lines = [ln for ln in output.splitlines() if ln.strip()]
    if lines and lines[0].startswith("Exit code"):
        lines = lines[1:]
    if not lines:
        return True
    first = lines[0].strip()
    m = re.search(r"(?:^|[\s:])(\d+)\s*$", first)
    if m:
        return int(m.group(1)) == 0
    if prefix in first:
        return False
    return None


def instrumentation(session: Session, cfg: Config | None) -> dict[str, Any]:
    prefix = (cfg.get("instrumentation.temp_prefix") if cfg else None) or "AGENTTMP_"
    allowed = list((cfg.get("instrumentation.allowed") if cfg else None) or [])
    per_file: dict[str, dict[str, int]] = {}
    unprefixed: list[dict[str, Any]] = []
    outside: list[dict[str, Any]] = []
    for c in session.edits():
        if not c.path:
            continue
        rel = _relative(c.path, session.cwd)
        inp = c.input or {}
        new = str(inp.get("new_string") or inp.get("content") or "")
        old = str(inp.get("old_string") or "")
        entry = per_file.setdefault(rel, {"added": 0, "removed": 0, "shell": 0})
        if prefix in new and prefix not in old:
            entry["added"] += 1
        elif prefix in old and prefix not in new:
            entry["removed"] += 1
        # A tracing call inserted without the prefix: cleanup by grep will
        # miss it.
        if _TRACE_CALL.search(new) and prefix not in new and not _TRACE_CALL.search(old):
            unprefixed.append({"ts": c.ts, "file": rel, "agent": c.agent})
        # `allowed` governs the agent's temporary instrumentation. A fix the
        # human asked for in the main context is a different matter, so only
        # subagent edits and prefixed edits are held against the list.
        temporary = prefix in new or prefix in old
        if allowed and _is_source(rel) and not _under_any(rel, allowed) \
                and (c.agent != MAIN or temporary):
            outside.append({"ts": c.ts, "file": rel, "agent": c.agent,
                            "temporary": temporary})
    # Edits made through the shell: counted per file, direction unknown.
    shell = shell_edits(session, prefix)
    for e in shell:
        for rel in e["files"]:
            entry = per_file.setdefault(rel, {"added": 0, "removed": 0, "shell": 0})
            entry["shell"] += 1
            if allowed and _is_source(rel) and not _under_any(rel, allowed):
                outside.append({"ts": e["ts"], "file": rel, "agent": e["agent"],
                                "temporary": True})
    touched = {f: v for f, v in per_file.items()
               if v["added"] or v["removed"] or v["shell"]}
    outside.sort(key=lambda r: ts_to_epoch(r["ts"]))
    # The cleanup grep must follow the last edit that touched the prefix —
    # not the last edit of the session, which may be a fix made much later.
    last_edit = max(
        [ts_to_epoch(c.ts) for c in session.edits()
         if prefix in str((c.input or {}).get("new_string", ""))
         or prefix in str((c.input or {}).get("old_string", ""))
         or prefix in str((c.input or {}).get("content", ""))]
        + [ts_to_epoch(e["ts"]) for e in shell],
        default=0.0)
    def is_grep(c: Call) -> bool:
        cmd = c.command or ""
        return "grep" in cmd and prefix in cmd and not _PY_WRITES.search(cmd)
    # Calls are stored main-first, then subagents: sort by time, the verdict
    # is the latest grep's.
    final_grep = sorted((c for c in session.bash()
                         if is_grep(c) and ts_to_epoch(c.ts) >= last_edit),
                        key=lambda c: ts_to_epoch(c.ts))
    verdict = _grep_verdict(final_grep[-1].output_head or "", prefix) if final_grep else None
    return {
        "prefix": prefix,
        "allowed": allowed,
        "files": touched,
        # Balance is judged on tool edits only; a file edited through the
        # shell has no direction to balance, the grep verdict speaks for it.
        "unbalanced": {f: v for f, v in touched.items()
                       if v["added"] != v["removed"] and not v["shell"]},
        "shell_edits": len(shell),
        "unprefixed_trace_calls": unprefixed,
        "edits_outside_allowed": outside,
        "cleanup_grep_after_last_edit": len(final_grep),
        "cleanup_grep_clean": verdict,
        "grep_calls_total": sum(1 for c in session.bash() if is_grep(c)),
    }


def _relative(path: str, cwd: str | None) -> str:
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        return path[len(cwd.rstrip("/")) + 1:]
    return path


def _is_source(rel: str) -> bool:
    if rel.startswith("/") or "/scratchpad/" in rel or rel.startswith(".echolot/"):
        return False
    return rel.endswith((".kt", ".java", ".kts", ".xml"))


def _under_any(rel: str, roots: list[str]) -> bool:
    return any(rel == r.rstrip("/") or rel.startswith(r.rstrip("/") + "/")
               for r in roots)


# --------------------------------------------------------------------- gaps

def gaps(session: Session, min_s: float = 120.0) -> list[dict[str, Any]]:
    """Silences between consecutive events, per agent, above the floor.

    Long ones are usually the agent waiting for gradle or a device — worth
    seeing, but not the agent's fault.
    """
    out = []
    by_agent: dict[str, list[tuple[float, str]]] = {}
    for c in session.calls:
        by_agent.setdefault(c.agent, []).append((ts_to_epoch(c.ts), describe(c)))
    for agent, events in by_agent.items():
        events.sort()
        for (t0, what), (t1, _) in zip(events, events[1:], strict=False):
            if t1 - t0 >= min_s:
                out.append({"agent": agent, "seconds": round(t1 - t0),
                            "after": what})
    out.sort(key=lambda g: -g["seconds"])
    return out[:8]


# -------------------------------------------------------------------- entry

def entry(session: Session, calls: list[EcholotCall]) -> dict[str, Any]:
    """How the session got into the tool: prompts, slash commands, skills.

    The entry window closes when real work starts: the first analyze /
    calibrate / collect, or the launch of a subagent — whichever comes first.
    Slash commands and interruptions inside it are the cost of finding the
    door.
    """
    first_call = ts_to_epoch(calls[0].ts) if calls else None
    work = [ts_to_epoch(c.ts) for c in calls if c.sub in ("analyze", "calibrate", "collect")]
    work += [ts_to_epoch(s.started) for s in session.subagents if s.started]
    window_end = min(work) if work else (first_call or float("inf"))
    before = [t for t in session.turns
              if t.role == "user" and ts_to_epoch(t.ts) < window_end]
    return {
        "user_prompts": [
            {"ts": t.ts, "text": t.text} for t in session.turns
            if t.role == "user" and t.kind == "text" and t.agent == MAIN
        ][:12],
        "slash_commands": [
            {"ts": t.ts, "command": t.command, "args": t.args}
            for t in session.turns if t.kind == "slash"
        ],
        "skills_loaded": list(session.skills_loaded),
        "interruptions": sum(1 for t in session.turns if t.kind == "interrupt"),
        "interruptions_in_entry": sum(1 for t in before if t.kind == "interrupt"),
        "slash_in_entry": sum(1 for t in before if t.kind == "slash"),
        "slash_before_first_call": sum(
            1 for t in session.turns
            if t.kind == "slash" and first_call is not None
            and ts_to_epoch(t.ts) < first_call),
        "entry_seconds": (round(window_end - ts_to_epoch(session.started))
                          if work and session.started else None),
        "seconds_to_first_call": (
            round(first_call - ts_to_epoch(session.started))
            if first_call and session.started else None),
    }


# ------------------------------------------------------------------- config

def config_snapshot(cfg: Config | None) -> dict[str, Any]:
    if cfg is None:
        return {"present": False}
    return {
        "present": True,
        "path": str(cfg.path) if cfg.path else None,
        "local": str(cfg.local_path) if cfg.local_path else None,
        "scenario": cfg.get("scenario.name"),
        "process": cfg.get("project.process"),
        "source_root": cfg.get("project.source_root"),
        "max_rounds": cfg.get("loop.max_rounds"),
        "instrumentation_allowed": cfg.get("instrumentation.allowed"),
        "temp_prefix": cfg.get("instrumentation.temp_prefix"),
        "detector_overrides": cfg.detector_overrides,
    }


# ------------------------------------------------------------------ gather

def gather(session: Session, cfg: Config | None,
           runs: list[dict[str, Any]]) -> Facts:
    calls = echolot_calls(session)
    window = None
    if session.started and session.ended:
        window = (ts_to_epoch(session.started), ts_to_epoch(session.ended))
    inside = match_runs(calls, runs, window)
    return Facts(
        echolot_calls=calls,
        milestones=milestones(session, calls),
        hunts=hunts(session, cfg),
        cost=cost(session),
        top_outputs=top_outputs(session),
        instrumentation=instrumentation(session, cfg),
        gaps=gaps(session),
        entry=entry(session, calls),
        config=config_snapshot(cfg),
        runs=inside,
    )
