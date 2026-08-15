"""The normalised session: the one shape every reader must produce.

Deliberately small. Whatever an agent records on disk — JSONL, SQLite, a
markdown log — a reader boils it down to turns, tool calls, questions to the
human, token usage and subagents. Signals and rendering never look further
than this file.

Timestamps are ISO-8601 strings in UTC, as they arrive; ordering and gaps are
computed with `ts_to_epoch`. Text fields are already truncated by the reader:
the model holds evidence, not the transcript.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

MAIN = "main"  # `agent` value for the top-level context


@dataclass
class Turn:
    ts: str
    role: str                 # "user" | "assistant"
    text: str                 # truncated
    agent: str = MAIN
    kind: str = "text"        # "text" | "slash" | "interrupt" | "notification"
    command: str | None = None    # slash command name, when kind == "slash"
    args: str | None = None       # its arguments


@dataclass
class Call:
    id: str
    ts: str
    tool: str                 # Bash, Edit, Read, Agent, AskUserQuestion, ...
    input: dict[str, Any]     # the tool's input, strings truncated
    agent: str = MAIN
    is_error: bool = False
    output_chars: int = 0
    output_head: str = ""     # first lines of the result, for error tails
    duration_s: float | None = None
    # Convenience views filled by the reader:
    command: str | None = None    # Bash: the command text (truncated)
    path: str | None = None       # Edit/Write/Read: the file path


@dataclass
class Ask:
    ts: str
    question: str
    options: list[str] = field(default_factory=list)
    recommended: str | None = None
    chosen: str | None = None
    answered_after_s: float | None = None
    agent: str = MAIN


@dataclass
class Usage:
    input: int = 0
    cache_read: int = 0
    cache_create: int = 0
    output: int = 0
    messages: int = 0     # distinct API responses counted

    def add(self, other: "Usage") -> None:
        self.input += other.input
        self.cache_read += other.cache_read
        self.cache_create += other.cache_create
        self.output += other.output
        self.messages += other.messages


@dataclass
class SubAgent:
    id: str
    type: str | None = None       # e.g. "perf-hunter"
    description: str | None = None
    prompt: str = ""              # what the main context handed down
    started: str | None = None
    ended: str | None = None
    final_text: str = ""          # what came back up
    usage: Usage = field(default_factory=Usage)
    thinking_blocks: int = 0
    source: str | None = None     # path of its transcript, if separate


@dataclass
class Session:
    id: str
    agent: str                    # "claude-code" — the reader's name
    cwd: str | None = None
    model: str | None = None
    agent_version: str | None = None
    git_branch: str | None = None
    started: str | None = None
    ended: str | None = None
    turns: list[Turn] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)      # main + subagents
    asks: list[Ask] = field(default_factory=list)
    subagents: list[SubAgent] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)          # main context only
    thinking_blocks: int = 0
    skills_loaded: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)      # files read
    notes: list[str] = field(default_factory=list)        # reader caveats

    # -- helpers used by signals and rendering ---------------------------

    def calls_of(self, agent: str) -> list[Call]:
        return [c for c in self.calls if c.agent == agent]

    def bash(self, agent: str | None = None) -> list[Call]:
        return [c for c in self.calls
                if c.tool == "Bash" and (agent is None or c.agent == agent)]

    def edits(self, agent: str | None = None) -> list[Call]:
        return [c for c in self.calls
                if c.tool in ("Edit", "Write", "MultiEdit")
                and (agent is None or c.agent == agent)]

    def duration_s(self) -> float | None:
        if not self.started or not self.ended:
            return None
        return ts_to_epoch(self.ended) - ts_to_epoch(self.started)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ts_to_epoch(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def epoch_to_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def clip(text: Any, limit: int = 400) -> str:
    """Truncate for evidence. Never returns None."""
    if text is None:
        return ""
    s = str(text)
    return s if len(s) <= limit else s[:limit] + "…"
