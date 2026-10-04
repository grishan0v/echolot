"""Reader for Codex sessions.

Codex keeps each thread as JSONL under
`~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<thread id>.jsonl`, under
CODEX_HOME when that is set. A subagent is a thread of its own, in a file of
its own, and its first line names its parent. A hunt's loop runs in one, so
without this reader a hunt in Codex was reflected on from the run log alone:
echolot's commands and their exit codes, and nothing of what the model read,
ran around them, spent, or did in the subagent (#193).

The format is not documented, and this reader treats it the way the Claude
Code reader treats Claude's: every field optional, unknown rows skipped,
anything surprising in `Session.notes`. What it relies on, as Codex CLI
0.159.2 wrote it:

    row.type, row.timestamp, row.ordinal
    session_meta          id, cwd, cli_version, git.branch, source —
                          {"subagent": {"thread_spawn": {parent_thread_id}}}
                          for a subagent — agent_path, agent_nickname,
                          subagent_history_start_ordinal
    turn_context          model
    event_msg item_completed, by item.type:
        UserMessage       content [{text}]
        AgentMessage      content [{text}], phase commentary | final_answer
        CommandExecution  command [shell, -lc, script], exit_code,
                          aggregated_output, duration {secs, nanos}
        FileChange        changes {path: {type add|update|delete, unified_diff}}
        SubAgentActivity  kind started, agent_thread_id, agent_path
        Reasoning         one per reasoning block
    event_msg task_complete   last_agent_message
    response_item function_call   spawn_agent, wait_agent,
                          request_user_input_async {questions}
    token_usage_record    usage per response_id: input_tokens (the cached
                          ones included), cached_input_tokens,
                          cache_write_input_tokens, output_tokens

Two things it cannot read, and says so. A subagent's file opens with a copy
of its parent's history, up to `subagent_history_start_ordinal`; only what
comes after is the subagent's own. And the message the parent hands a
subagent is encrypted on disk, in both files, so a Codex session carries no
brief (`BRIEFS`), and the check on what the brief said is reported as not
checked rather than as a brief with nothing in it.

A subagent is the hunt's loop — `perf-hunter`, in the Claude Code reader's
words — when it ran `echolot guide loop`, which is what the hunt skill
tells it to read first, or when its name says hunt.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .claude_code import COMMAND_LIMIT, FINAL_TEXT_LIMIT, echolot_subcommands, involves_echolot
from .model import (
    ASKS,
    MAIN,
    SUBAGENTS,
    TOOLS,
    TURNS,
    USAGE,
    Ask,
    Call,
    Session,
    SubAgent,
    Turn,
    Usage,
    clip,
    epoch_to_ts,
    ts_to_epoch,
)

AGENT_NAME = "codex"
__all__ = ["AGENT_NAME", "SessionRef", "echolot_subcommands", "involves_echolot",
           "list_sessions", "read_session", "sessions_root"]

_THREAD_ID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
_SHELLS = {"sh", "bash", "zsh", "dash", "fish"}
# Collaboration tools under the names the rest of reflect already reads:
# a subagent being started or waited on, and a question put to the human.
_TOOL_NAMES = {"spawn_agent": "Agent", "wait_agent": "Agent",
               "request_user_input_async": "AskUserQuestion"}


@dataclass
class SessionRef:
    id: str
    path: Path
    mtime: float
    size: int


def sessions_root(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _meta(path: Path) -> dict[str, Any]:
    """The thread's `session_meta`, which Codex writes first."""
    for row in _rows(path):
        if row.get("type") == "session_meta" and isinstance(row.get("payload"), dict):
            return row["payload"]
        break
    return {}


def _parent(meta: dict[str, Any]) -> str | None:
    source = meta.get("source")
    if isinstance(source, dict):
        spawn = (source.get("subagent") or {}).get("thread_spawn") or {}
        if spawn.get("parent_thread_id"):
            return str(spawn["parent_thread_id"])
    return meta.get("parent_thread_id") or None


def _same_dir(a: str | None, b: Path) -> bool:
    if not a:
        return False
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return False


def list_sessions(project: Path, root: Path | None = None) -> list[SessionRef]:
    """The main threads Codex ran in this project, newest first.

    A subagent's thread is read with its parent's, never listed on its own.
    """
    base = root or sessions_root()
    if not base.is_dir():
        return []
    refs = []
    for p in base.rglob("rollout-*.jsonl"):
        meta = _meta(p)
        if not meta or _parent(meta) or not _same_dir(meta.get("cwd"), project):
            continue
        st = p.stat()
        refs.append(SessionRef(str(meta.get("id") or p.stem), p, st.st_mtime, st.st_size))
    refs.sort(key=lambda r: r.mtime, reverse=True)
    return refs


# ------------------------------------------------------------------ reading

def read_session(path: Path) -> Session:
    meta = _meta(path)
    session = Session(id=str(meta.get("id") or path.stem), agent=AGENT_NAME,
                      carries=[TURNS, TOOLS, ASKS, SUBAGENTS, USAGE])
    session.cwd = meta.get("cwd")
    session.agent_version = meta.get("cli_version")
    git = meta.get("git")
    if isinstance(git, dict) and git.get("branch"):
        session.git_branch = str(git["branch"])
    session.sources.append(str(path))
    _parse(path, session, MAIN, None, 0)

    # Subagents, by the thread id each start names; their own subagents
    # after them, the same way. Anywhere under `sessions/`: a hunt that ran
    # past midnight has its subagent in the next day's folder.
    root = next((d for d in path.parents if d.name == "sessions"), path.parent)
    by_id = {m.group(1): p for p in root.rglob("rollout-*.jsonl")
             if (m := _THREAD_ID.search(p.name))}
    done: set[str] = set()
    for sub in session.subagents:   # grows while it is walked
        if sub.id in done:
            continue
        done.add(sub.id)
        sub_path = by_id.get(sub.id)
        if sub_path is None:
            session.notes.append(f"subagent {sub.id} was started, and its thread "
                                 f"is not among Codex's sessions")
            continue
        sub_meta = _meta(sub_path)
        sub.source = str(sub_path)
        session.sources.append(str(sub_path))
        _parse(sub_path, session, f"sub:{sub.id}", sub,
               int(sub_meta.get("subagent_history_start_ordinal") or 0))
        sub.type = _kind(session, sub, sub_meta)
    if session.subagents:
        session.notes.append("what the main thread handed each subagent is encrypted "
                             "in Codex's files, so the brief is not checked")

    stamps = [c.ts for c in session.calls] + [t.ts for t in session.turns]
    stamps = [s for s in stamps if s]
    if stamps:
        session.started = min(stamps, key=ts_to_epoch)
        session.ended = max(stamps, key=ts_to_epoch)
    return session


def _kind(session: Session, sub: SubAgent, meta: dict[str, Any]) -> str:
    """`perf-hunter` for the hunt's loop; otherwise the name Codex gave it."""
    ran = " ".join(c.command or "" for c in session.bash(f"sub:{sub.id}"))
    name = str(meta.get("agent_path") or sub.description or "")
    if re.search(r"echolot\s+guide\s+loop\b", ran) or "hunt" in name.lower():
        return "perf-hunter"
    return name.rsplit("/", 1)[-1] or "subagent"


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content
                         if isinstance(b, dict) and b.get("text"))
    return ""


def _ms(value: Any) -> str | None:
    return epoch_to_ts(value / 1000) if isinstance(value, (int, float)) and value > 0 else None


def _script(command: Any) -> str:
    """The shell script a command ran: `zsh -lc '<script>'` is the script."""
    if isinstance(command, str):
        return command
    if not isinstance(command, list) or not command:
        return ""
    words = [str(w) for w in command]
    if len(words) >= 3 and Path(words[0]).name in _SHELLS and words[1] in ("-lc", "-c"):
        return words[2]
    return " ".join(words)


def _diff_sides(diff: str) -> tuple[str, str]:
    """A unified diff's before and after, context lines on both sides."""
    old, new = [], []
    for line in diff.splitlines():
        if line.startswith(("@@", "---", "+++")):
            continue
        if line.startswith("-"):
            old.append(line[1:])
        elif line.startswith("+"):
            new.append(line[1:])
        else:
            text = line[1:] if line.startswith(" ") else line
            old.append(text)
            new.append(text)
    return "\n".join(old), "\n".join(new)


def _parse(path: Path, session: Session, agent: str, sub: SubAgent | None,
           start: int) -> None:
    """One thread's own rows into the session: from `start` on."""
    thread = _Thread(session, agent, sub)
    for row in _rows(path):
        ordinal = row.get("ordinal")
        if isinstance(ordinal, int) and ordinal < start:
            continue
        thread.row(row)
    thread.finish()


# Rows read whatever their payload's type; the others are told apart by it.
_WHOLE_ROWS = ("turn_context", "token_usage_record")


class _Thread:
    """One thread's file read row by row, and what stays open between rows.

    Shaped like the Claude Code reader's pass: a method for each kind of row
    and of item, and this class for the state they share. A question is
    answered rows after it is asked, and the last thing the agent said is
    known only at the end.
    """

    def __init__(self, session: Session, agent: str, sub: SubAgent | None) -> None:
        self.session = session
        self.agent = agent
        self.sub = sub
        self.usage: dict[str, Usage] = {}
        self.asks: dict[str, Call] = {}
        # A call's questions wait for its answer: Codex rejects many of them
        # with a parse error, and the agent asks again with other fields.
        self.waiting: dict[str, list[Ask]] = {}
        self.last_text = ""
        self.final_text = ""

    def row(self, row: dict[str, Any]) -> None:
        ts = str(row.get("timestamp") or "")
        rtype = row.get("type")
        p = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        kind = str(rtype) if rtype in _WHOLE_ROWS else f"{rtype}:{p.get('type')}"
        read = _ROWS.get(kind)
        if read is not None:
            read(self, p, ts)

    def _turn_context(self, p: dict[str, Any], ts: str) -> None:
        if self.session.model is None and p.get("model"):
            self.session.model = str(p["model"])

    def _token_usage(self, p: dict[str, Any], ts: str) -> None:
        u = p.get("usage") or {}
        cached = int(u.get("cached_input_tokens") or 0)
        self.usage[str(p.get("response_id") or len(self.usage))] = Usage(
            input=max(0, int(u.get("input_tokens") or 0) - cached),
            cache_read=cached,
            cache_create=int(u.get("cache_write_input_tokens") or 0),
            output=int(u.get("output_tokens") or 0),
            messages=1)

    def _function_call(self, p: dict[str, Any], ts: str) -> None:
        name = str(p.get("name") or "?")
        args = _arguments(p.get("arguments"))
        call = Call(id=str(p.get("call_id") or ""), ts=ts,
                    tool=_TOOL_NAMES.get(name, name),
                    input={"codex_tool": name, **{k: clip(v, 300) if isinstance(v, str)
                                                  else v for k, v in args.items()}},
                    agent=self.agent)
        self.session.calls.append(call)
        if name == "request_user_input_async":
            self.asks[call.id] = call
            self.waiting[call.id] = _questions(args, ts, self.agent)

    def _function_output(self, p: dict[str, Any], ts: str) -> None:
        call = self.asks.get(str(p.get("call_id") or ""))
        if call is None:
            return
        raw = p.get("output")
        out = raw if isinstance(raw, str) else _text(raw)
        call.output_chars, call.output_head = len(out), clip(out, 300)
        # Only a call Codex accepted put a question to the human. A rejected
        # one stayed in the report as a question asked, once per attempt, and
        # was no error for `env_friction` to see.
        asked = self.waiting.pop(call.id, [])
        if _accepted(out):
            self.session.asks.extend(asked)
        else:
            call.is_error = True

    def _task_complete(self, p: dict[str, Any], ts: str) -> None:
        if p.get("last_agent_message"):
            self.final_text = str(p["last_agent_message"])
        if self.sub is not None:
            self.sub.ended = ts or self.sub.ended

    def _item_completed(self, p: dict[str, Any], ts: str) -> None:
        item = p.get("item") if isinstance(p.get("item"), dict) else {}
        started = _ms(p.get("started_at_ms")) or ts
        read = _ITEMS.get(item.get("type"))
        said = read(self, item, started, ts) if read is not None else None
        if said:
            self.last_text = said
            if item.get("phase") == "final_answer":
                self.final_text = said

    # --- one completed item each; a message returns what the agent said ---

    def _user_message(self, item: dict[str, Any], started: str, ts: str) -> None:
        text = _text(item.get("content"))
        if text.strip():
            self.session.turns.append(Turn(ts=ts, role="user", text=clip(text, 600),
                                           agent=self.agent))

    def _agent_message(self, item: dict[str, Any], started: str, ts: str) -> str | None:
        text = _text(item.get("content"))
        if not text.strip():
            return None
        self.session.turns.append(Turn(ts=ts, role="assistant", text=clip(text, 600),
                                       agent=self.agent))
        return text

    def _reasoning(self, item: dict[str, Any], started: str, ts: str) -> None:
        if self.sub is not None:
            self.sub.thinking_blocks += 1
        else:
            self.session.thinking_blocks += 1

    def _command(self, item: dict[str, Any], started: str, ts: str) -> None:
        script = _script(item.get("command"))
        out = str(item.get("aggregated_output") or "")
        code = item.get("exit_code")
        # Claude Code's shell tool leads a failure with "Exit code N", and
        # the facts read the code off that line; the same line here keeps
        # one parser for both.
        head = f"Exit code {code}\n{out}" if isinstance(code, int) and code else out
        self.session.calls.append(Call(
            id=str(item.get("id") or ""), ts=started, tool="Bash",
            input={"command": clip(script, 1200)}, agent=self.agent,
            is_error=isinstance(code, int) and code != 0,
            output_chars=len(out), output_head=clip(head, 300),
            duration_s=_seconds(item.get("duration") or {}),
            command=clip(script, COMMAND_LIMIT)))

    def _file_change(self, item: dict[str, Any], started: str, ts: str) -> None:
        for file, change in (item.get("changes") or {}).items():
            if isinstance(change, dict):
                self.session.calls.append(_edit(file, change, item, started, self.agent))

    def _subagent_activity(self, item: dict[str, Any], started: str, ts: str) -> None:
        if item.get("kind") != "started":
            return
        tid = str(item.get("agent_thread_id") or "")
        if tid and not any(s.id == tid for s in self.session.subagents):
            self.session.subagents.append(SubAgent(
                id=tid, description=item.get("agent_path"), started=ts))

    def finish(self) -> None:
        # Asked and never answered before the file ends: the question stands.
        for asked in self.waiting.values():
            self.session.asks.extend(asked)
        target = self.sub.usage if self.sub is not None else self.session.usage
        for u in self.usage.values():
            target.add(u)
        said = clip(self.final_text or self.last_text, FINAL_TEXT_LIMIT)
        if self.sub is not None:
            self.sub.final_text = said
        elif said:
            self.session.final_text = said


_ROWS = {
    "turn_context": _Thread._turn_context,
    "token_usage_record": _Thread._token_usage,
    "response_item:function_call": _Thread._function_call,
    "response_item:function_call_output": _Thread._function_output,
    "event_msg:task_complete": _Thread._task_complete,
    "event_msg:item_completed": _Thread._item_completed,
}

_ITEMS = {
    "UserMessage": _Thread._user_message,
    "AgentMessage": _Thread._agent_message,
    "Reasoning": _Thread._reasoning,
    "CommandExecution": _Thread._command,
    "FileChange": _Thread._file_change,
    "SubAgentActivity": _Thread._subagent_activity,
}


def _arguments(raw: Any) -> dict[str, Any]:
    """A function call's arguments: a JSON object in a string, or nothing."""
    try:
        args = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return args if isinstance(args, dict) else {}


def _accepted(out: str) -> bool:
    """Whether Codex took a question call: its output is JSON saying so."""
    try:
        data = json.loads(out)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("accepted") is True


def _questions(args: dict[str, Any], ts: str, agent: str) -> list[Ask]:
    asks = []
    for q in args.get("questions") or []:
        if not isinstance(q, dict):
            continue
        labels = [str(o.get("label") or o) if isinstance(o, dict) else str(o)
                  for o in (q.get("options") or [])]
        asks.append(Ask(ts=ts, question=clip(q.get("question") or q.get("title") or "", 300),
                        options=labels, agent=agent))
    return asks


def _seconds(took: Any) -> float | None:
    if not isinstance(took, dict):
        return None
    return round(float(took.get("secs") or 0) + float(took.get("nanos") or 0) / 1e9, 1)


def _edit(file: str, change: dict[str, Any], item: dict[str, Any], started: str,
          agent: str) -> Call:
    """One file of a patch, as the Write or Edit call Claude Code would have made."""
    old, new = _diff_sides(str(change.get("unified_diff") or change.get("content") or ""))
    if change.get("type") == "add":
        tool, inp = "Write", {"file_path": file, "content": clip(new, 1200)}
    else:
        tool, inp = "Edit", {"file_path": file, "old_string": clip(old, 1200),
                             "new_string": clip(new, 1200)}
    return Call(id=str(item.get("id") or ""), ts=started, tool=tool, input=inp,
                agent=agent, path=str(file), is_error=item.get("status") == "failed")
