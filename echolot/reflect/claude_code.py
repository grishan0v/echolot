"""Reader for Claude Code transcripts.

Claude Code keeps every session as JSONL under
`~/.claude/projects/<slug>/<session-id>.jsonl`, where the slug is the working
directory with `/` turned into `-`. Subagents (perf-hunter among them) get a
transcript of their own under `<session-id>/subagents/agent-<id>.jsonl`.

The format is not documented and this reader treats it as such: every field
is optional, unknown rows are skipped, and anything surprising lands in
`Session.notes` rather than in an exception. What it relies on:

    row.type            "user" | "assistant" | "attachment" | ...
    row.timestamp       ISO-8601
    row.message.content str, or a list of blocks:
                        text | thinking | tool_use{id,name,input}
                        | tool_result{tool_use_id,content,is_error}
    row.message.usage   token counts — repeated on every row of one API
                        response, so it is de-duplicated by message.id
    row.toolUseResult   structured result: AskUserQuestion answers,
                        Agent's agentId, Bash stdout/stderr
    row.isSidechain     older layout: subagent rows inline in the main file

Slash commands arrive as user text wrapped in <command-name>…</command-name>;
a subagent's return value as a <task-notification> whose <task-id> is the
agentId; a loaded skill as a user text block starting with
"Base directory for this skill:".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .model import MAIN, Ask, Call, Session, SubAgent, Turn, Usage, clip, ts_to_epoch

AGENT_NAME = "claude-code"
PROJECTS_ROOT = Path.home() / ".claude" / "projects"
# The subagent's conclusion is kept nearly whole: the six fields are checked
# against it, and Cleanup and Confidence come last. A 4000-character clip
# once cut them off and reported them missing. Rendering clips it again.
FINAL_TEXT_LIMIT = 12000
# A shell command is kept long enough to see what a python heredoc does at
# its end — `open(p, 'w').write(t)` forty lines down is the config being
# written, and a 2000-character clip lost it.
COMMAND_LIMIT = 8000

_ECHOLOT_CALL = re.compile(r"(?:^|[\s;&|(`$])echolot\s+([a-z-]+)")
# MCP tools that run a shell command: the name says so, or the input does.
_SHELL_TOOL = re.compile(
    r"(?:^|__)(?:ctx_execute|ctx_batch_execute|bash|shell|sh|terminal|"
    r"execute_command|run_command|run_shell_command|execute)$", re.I)
_SLASH = re.compile(r"<command-name>\s*(/[^<\s]+)\s*</command-name>")
_SLASH_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_TASK_ID = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")
_TASK_RESULT = re.compile(r"<result>(.*?)</result>", re.S)
_ANSWER_PAIR = re.compile(r'"((?:[^"\\]|\\.)*)"="((?:[^"\\]|\\.)*)"')
_SKILL_DIR = re.compile(r"Base directory for this skill:\s*(\S+)")
_RECOMMENDED = re.compile(r"recommend|рекоменд", re.I)


# ---------------------------------------------------------------- discovery

@dataclass
class SessionRef:
    id: str
    path: Path
    mtime: float
    size: int


def slug_candidates(cwd: Path) -> list[str]:
    """The directory name Claude Code derives from a working directory.

    Observed: `/` → `-`. Some builds also fold every other non-alphanumeric
    character, so both spellings are tried, exact one first.
    """
    raw = str(cwd.resolve())
    first = raw.replace("/", "-")
    second = re.sub(r"[^A-Za-z0-9-]", "-", raw)
    return [first] if first == second else [first, second]


def project_dir(cwd: Path, root: Path | None = None) -> Path | None:
    base = root or PROJECTS_ROOT
    for name in slug_candidates(cwd):
        p = base / name
        if p.is_dir():
            return p
    return None


def list_sessions(pdir: Path) -> list[SessionRef]:
    refs = []
    for p in pdir.glob("*.jsonl"):
        st = p.stat()
        refs.append(SessionRef(p.stem, p, st.st_mtime, st.st_size))
    refs.sort(key=lambda r: r.mtime, reverse=True)
    return refs


def echolot_subcommands(session: Session) -> list[str]:
    """Every `echolot <sub>` seen in Bash calls, in order."""
    out = []
    for c in session.bash():
        for m in _ECHOLOT_CALL.finditer(c.command or ""):
            out.append(m.group(1))
    return out


def involves_echolot(session: Session) -> bool:
    """A session worth reflecting on: it used the tool for real work.

    `reflect` itself does not count — otherwise the session that runs the
    reflection is always the newest candidate.
    """
    subs = [s for s in echolot_subcommands(session) if s != "reflect"]
    if subs:
        return True
    if any(t.kind == "slash" and (t.command or "").startswith("/echolot")
           and t.command != "/echolot-reflect" for t in session.turns):
        return True
    return any(s.type == "perf-hunter" for s in session.subagents)


# ------------------------------------------------------------------ reading

def read_session(path: Path) -> Session:
    session = Session(id=path.stem, agent=AGENT_NAME)
    session.sources.append(str(path))
    pending_agents: dict[str, SubAgent] = {}       # tool_use id → placeholder
    _parse_rows(_rows(path), session, MAIN, None, pending_agents)

    # Subagent transcripts live beside the main file, one per agent.
    sub_dir = path.with_suffix("") / "subagents"
    if sub_dir.is_dir():
        for sub_path in sorted(sub_dir.glob("agent-*.jsonl")):
            agent_id = sub_path.stem[len("agent-"):]
            sub = _find_subagent(session, agent_id)
            if sub is None:
                sub = SubAgent(id=agent_id)
                session.subagents.append(sub)
                session.notes.append(
                    f"subagent {agent_id} has a transcript but no launching "
                    f"Agent call in the main file")
            sub.source = str(sub_path)
            session.sources.append(str(sub_path))
            _parse_rows(_rows(sub_path), session, f"sub:{agent_id}", sub, {})

    if session.calls or session.turns:
        stamps = [c.ts for c in session.calls] + [t.ts for t in session.turns]
        stamps = [s for s in stamps if s]
        if stamps:
            session.started = min(stamps, key=ts_to_epoch)
            session.ended = max(stamps, key=ts_to_epoch)
    for sub in session.subagents:
        own = [c.ts for c in session.calls if c.agent == f"sub:{sub.id}"]
        if own:
            sub.started = sub.started or min(own, key=ts_to_epoch)
            last = max(own, key=ts_to_epoch)
            if not sub.ended or ts_to_epoch(last) > ts_to_epoch(sub.ended):
                sub.ended = last
    return session


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


def _find_subagent(session: Session, agent_id: str) -> SubAgent | None:
    for s in session.subagents:
        if s.id == agent_id:
            return s
    return None


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _text_of(content: Any) -> str:
    return "\n".join(
        str(b.get("text", "")) for b in _blocks(content) if b.get("type") == "text"
    )


def _clip_input(inp: Any, limit: int = 1200) -> dict[str, Any]:
    if not isinstance(inp, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in inp.items():
        if isinstance(v, str):
            out[k] = clip(v, limit)
        elif isinstance(v, (list, dict)):
            s = json.dumps(v, ensure_ascii=False)
            out[k] = v if len(s) <= limit else clip(s, limit)
        else:
            out[k] = v
    return out


def _usage_of(msg: dict[str, Any]) -> Usage:
    u = msg.get("usage") or {}
    return Usage(
        input=int(u.get("input_tokens") or 0),
        cache_read=int(u.get("cache_read_input_tokens") or 0),
        cache_create=int(u.get("cache_creation_input_tokens") or 0),
        output=int(u.get("output_tokens") or 0),
        messages=1,
    )


def _parse_rows(rows: Iterable[dict[str, Any]], session: Session, agent: str,
                sub: SubAgent | None, pending_agents: dict[str, SubAgent]) -> None:
    """One pass over one transcript file. Fills the session in place."""
    open_calls: dict[str, Call] = {}
    open_asks: dict[str, list[dict[str, Any]]] = {}
    # One API response is written as several rows (thinking, text, each
    # tool_use) and the usage on them grows as the response streams: the last
    # row carries the final numbers. Per message id, keep the maximum of each
    # counter and add them up once at the end.
    per_msg: dict[str, Usage] = {}
    last_assistant_text = ""

    for row in rows:
        rtype = row.get("type")
        ts = row.get("timestamp") or ""
        if row.get("isSidechain") and agent == MAIN:
            # Older layout: subagent rows inline. Group them under one label
            # so that they do not pollute the main context's numbers.
            row_agent = "sub:inline"
        else:
            row_agent = agent

        if session.cwd is None and row.get("cwd"):
            session.cwd = row["cwd"]
        if session.agent_version is None and row.get("version"):
            session.agent_version = row["version"]
        # Claude Code writes "HEAD" for a directory that is not a git
        # repository (or a detached head); neither is a branch name.
        if session.git_branch is None and row.get("gitBranch") \
                and row["gitBranch"] != "HEAD":
            session.git_branch = row["gitBranch"]

        msg = row.get("message") or {}
        content = msg.get("content")

        if rtype == "assistant":
            model = msg.get("model")
            # Claude Code injects synthetic assistant rows (model "<synthetic>")
            # for its own bookkeeping; the real model is on the others.
            if session.model is None and model and not str(model).startswith("<"):
                session.model = model
            mid = msg.get("id") or row.get("requestId") or row.get("uuid")
            if mid:
                u = _usage_of(msg)
                prev = per_msg.get(mid)
                if prev is None:
                    per_msg[mid] = u
                else:
                    prev.input = max(prev.input, u.input)
                    prev.cache_read = max(prev.cache_read, u.cache_read)
                    prev.cache_create = max(prev.cache_create, u.cache_create)
                    prev.output = max(prev.output, u.output)
            for b in _blocks(content):
                bt = b.get("type")
                if bt == "thinking":
                    if sub:
                        sub.thinking_blocks += 1
                    else:
                        session.thinking_blocks += 1
                elif bt == "text":
                    text = str(b.get("text") or "")
                    if text.strip():
                        last_assistant_text = text
                        session.turns.append(Turn(
                            ts=ts, role="assistant", text=clip(text, 600),
                            agent=row_agent))
                elif bt == "tool_use":
                    call = _call_from_use(b, ts, row_agent)
                    session.calls.append(call)
                    open_calls[call.id] = call
                    if call.tool == "AskUserQuestion":
                        qs = (b.get("input") or {}).get("questions") or []
                        open_asks[call.id] = [q for q in qs if isinstance(q, dict)]
                    elif call.tool == "Agent":
                        inp = b.get("input") or {}
                        placeholder = SubAgent(
                            id=call.id,   # replaced by agentId on result
                            type=inp.get("subagent_type"),
                            description=inp.get("description"),
                            prompt=str(inp.get("prompt") or ""),
                            started=ts,
                        )
                        pending_agents[call.id] = placeholder
                        session.subagents.append(placeholder)
                    elif call.tool == "Skill":
                        name = (b.get("input") or {}).get("skill")
                        if name and name not in session.skills_loaded:
                            session.skills_loaded.append(str(name))
            continue

        if rtype != "user":
            continue

        if isinstance(content, str):
            _user_text(content, ts, row, session, row_agent, pending_agents)
            continue

        for b in _blocks(content):
            bt = b.get("type")
            if bt == "text":
                _user_text(str(b.get("text") or ""), ts, row, session,
                           row_agent, pending_agents)
            elif bt == "tool_result":
                use_id = b.get("tool_use_id")
                call = open_calls.get(use_id)
                text = _result_text(b.get("content"))
                if call is None:
                    continue
                call.is_error = bool(b.get("is_error"))
                call.output_chars = len(text)
                call.output_head = clip(text, 300)
                if ts and call.ts:
                    call.duration_s = round(
                        max(0.0, ts_to_epoch(ts) - ts_to_epoch(call.ts)), 1)
                tr = row.get("toolUseResult")
                if use_id in open_asks:
                    session.asks.extend(_asks_from(
                        open_asks.pop(use_id), call, ts, text, tr, row_agent))
                elif call.tool == "Agent":
                    _agent_result(call, text, tr, ts, session, pending_agents)

    target = sub.usage if sub else session.usage
    for u in per_msg.values():
        target.add(u)
    if sub is not None and last_assistant_text:
        sub.final_text = clip(last_assistant_text, FINAL_TEXT_LIMIT)


def _call_from_use(block: dict[str, Any], ts: str, agent: str) -> Call:
    name = str(block.get("name") or "?")
    raw = block.get("input") or {}
    call = Call(id=str(block.get("id") or ""), ts=ts, tool=name,
                input=_clip_input(raw), agent=agent)
    if name == "Bash" and isinstance(raw, dict):
        call.command = clip(raw.get("command"), COMMAND_LIMIT)
    elif isinstance(raw, dict) and _SHELL_TOOL.search(name):
        # Shell through an MCP tool (a context-saving plugin, a sandbox): the
        # same command, a different envelope. Without this the gradle runs and
        # the mv of a trace directory in one session were invisible — they
        # went through such a tool, and every shell-reading signal missed them.
        code = raw.get("command") if isinstance(raw.get("command"), str) else None
        if code is None and str(raw.get("language") or "").lower() in ("shell", "bash", "sh"):
            code = raw.get("code")
        if isinstance(code, str) and code.strip():
            call.command = clip(code, COMMAND_LIMIT)
    if isinstance(raw, dict):
        p = raw.get("file_path") or raw.get("path") or raw.get("notebook_path")
        if p:
            call.path = str(p)
    return call


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(str(b.get("text") or ""))
        return "\n".join(parts)
    return ""


def _user_text(text: str, ts: str, row: dict[str, Any], session: Session,
               agent: str, pending_agents: dict[str, SubAgent]) -> None:
    if not text.strip():
        return
    m = _SLASH.search(text)
    if m:
        args = _SLASH_ARGS.search(text)
        session.turns.append(Turn(
            ts=ts, role="user", text="", agent=agent, kind="slash",
            command=m.group(1),
            args=(args.group(1).strip() if args else None) or None))
        return
    if text.startswith("<task-notification>"):
        session.turns.append(Turn(ts=ts, role="user", text=clip(text, 300),
                                  agent=agent, kind="notification"))
        tid = _TASK_ID.search(text)
        res = _TASK_RESULT.search(text)
        if tid:
            sub = _find_subagent(session, tid.group(1))
            if sub is not None:
                if res:
                    sub.final_text = clip(res.group(1).strip(), FINAL_TEXT_LIMIT)
                sub.ended = ts
        return
    m = _SKILL_DIR.search(text)
    if m:
        name = Path(m.group(1)).name
        if name not in session.skills_loaded:
            session.skills_loaded.append(name)
        return
    if "[Request interrupted by user" in text:
        session.turns.append(Turn(ts=ts, role="user", text=clip(text, 120),
                                  agent=agent, kind="interrupt"))
        return
    if row.get("isMeta"):
        return
    if text.startswith("<local-command"):
        return
    session.turns.append(Turn(ts=ts, role="user", text=clip(text, 600),
                              agent=agent))


def _asks_from(questions: list[dict[str, Any]], call: Call, ts: str,
               text: str, tr: Any, agent: str) -> list[Ask]:
    answers: dict[str, str] = {}
    if isinstance(tr, dict) and isinstance(tr.get("answers"), dict):
        answers = {str(k): str(v) for k, v in tr["answers"].items()}
    if not answers:
        for m in _ANSWER_PAIR.finditer(text):
            answers[m.group(1)] = m.group(2)
    after = None
    if ts and call.ts:
        after = round(max(0.0, ts_to_epoch(ts) - ts_to_epoch(call.ts)), 1)
    out = []
    for q in questions:
        qtext = str(q.get("question") or "")
        labels = [str(o.get("label") or "") for o in (q.get("options") or [])
                  if isinstance(o, dict)]
        rec = next((l for l in labels if _RECOMMENDED.search(l)), None)
        out.append(Ask(ts=call.ts, question=clip(qtext, 300), options=labels,
                       recommended=rec, chosen=answers.get(qtext),
                       answered_after_s=after, agent=agent))
    return out


def _agent_result(call: Call, text: str, tr: Any, ts: str, session: Session,
                  pending_agents: dict[str, SubAgent]) -> None:
    sub = pending_agents.pop(call.id, None)
    if sub is None:
        return
    if isinstance(tr, dict) and tr.get("agentId"):
        sub.id = str(tr["agentId"])
        if tr.get("status") and "async" not in str(tr["status"]):
            sub.final_text = clip(text, FINAL_TEXT_LIMIT)
            sub.ended = ts
    else:
        # A synchronous agent: the return value is the tool result itself and
        # the transcript, if any, is inline (isSidechain rows).
        sub.final_text = clip(text, FINAL_TEXT_LIMIT)
        sub.ended = ts
