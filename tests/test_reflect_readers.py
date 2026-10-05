"""What `reflect`'s readers take from a transcript or the run log, in the rows
they used to drop, double or misplace.

A message typed while the agent was busy, a question Codex rejected, a
subagent's brief, a long run beside short ones, an inline sidechain, a
compaction summary, a multi-line argument, and a project whose transcripts
never used echolot.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.reflect import claude_code, codex, facts, from_log  # noqa: E402
from echolot.reflect.model import MAIN, Session, SubAgent, Turn  # noqa: E402
from tests.support import check  # noqa: E402


def _transcript(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "s.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _user(ts: str, text: str, **extra) -> dict:
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text},
            **extra}


def _assistant(ts: str, text: str, out: int, mid: str, **extra) -> dict:
    return {"type": "assistant", "timestamp": ts, **extra, "message": {
        "id": mid, "model": "claude-x", "role": "assistant",
        "content": [{"type": "thinking", "thinking": "…"}, {"type": "text", "text": text}],
        "usage": {"input_tokens": 1, "output_tokens": out}}}


def test_a_message_typed_while_the_agent_was_busy_is_read(tmp_path: Path) -> None:
    s = claude_code.read_session(_transcript(tmp_path, [
        _user("2026-09-01T12:00:00.000Z", "find why cold start regressed"),
        {"type": "attachment", "timestamp": "2026-09-01T12:00:07.000Z",
         "attachment": {"type": "queued_command", "commandMode": "prompt",
                        "prompt": "also check the list screen"}},
    ]))
    prompts = [t.text for t in s.turns if t.role == "user" and t.kind == "text"]
    check("the queued message is a prompt",
          prompts == ["find why cold start regressed", "also check the list screen"], prompts)


def test_a_compaction_summary_is_no_prompt(tmp_path: Path) -> None:
    s = claude_code.read_session(_transcript(tmp_path, [
        _user("2026-09-01T12:00:00.000Z", "hunt it"),
        _user("2026-09-01T13:00:00.000Z",
              "This session is being continued from a previous conversation. "
              "<command-name>/echolot</command-name>",
              isCompactSummary=True, isVisibleInTranscriptOnly=True),
    ]))
    check("one human prompt, and no slash command out of the summary",
          [t.text for t in s.turns if t.role == "user"] == ["hunt it"], s.turns)


def test_inline_sidechain_rows_are_not_the_main_context(tmp_path: Path) -> None:
    s = claude_code.read_session(_transcript(tmp_path, [
        _assistant("2026-09-01T12:00:00.000Z", "main one", 10, "m1"),
        _assistant("2026-09-01T12:00:01.000Z", "main two", 10, "m2"),
        _assistant("2026-09-01T12:00:02.000Z", "sidechain says last", 5000, "s1",
                   isSidechain=True),
    ]))
    check("the main context's tokens are its own", s.usage.output == 20, s.usage)
    check("and so are its thinking blocks and its last words",
          s.thinking_blocks == 2 and s.final_text == "main two",
          (s.thinking_blocks, s.final_text))
    inline = next(a for a in s.subagents if a.id == "inline")
    check("the sidechain's are kept apart",
          inline.usage.output == 5000 and inline.final_text == "sidechain says last", inline)


def test_a_question_codex_rejected_was_not_asked() -> None:
    s = Session(id="c", agent="codex")
    one = codex._Thread(s, MAIN, None)
    rows = [
        ("function_call", {"name": "request_user_input_async", "call_id": "a",
                           "arguments": json.dumps({"questions": [
                               {"title": "Scenario", "question": "Which regressed?"}]})}),
        ("function_call_output", {"call_id": "a", "output":
                                  "failed to parse function arguments: unknown field `question`"}),
        ("function_call", {"name": "request_user_input_async", "call_id": "b",
                           "arguments": json.dumps({"questions": [{"title": "Which regressed?"}]})}),
        ("function_call_output", {"call_id": "b", "output": '{"accepted": true}'}),
    ]
    for kind, payload in rows:
        one.row({"type": "response_item", "timestamp": "2026-09-01T12:00:00.000Z",
                 "payload": {"type": kind, **payload}})
    one.finish()
    check("one question asked, the accepted one", len(s.asks) == 1, s.asks)
    check("and the rejected call is an error",
          [c.is_error for c in s.calls] == [True, False], [c.is_error for c in s.calls])


def test_a_subagent_s_brief_is_not_a_user_turn() -> None:
    s = Session(id="s", agent="claude-code")
    s.turns = [Turn(ts="2026-09-01T12:00:00.000Z", role="user", text="hunt it"),
               Turn(ts="2026-09-01T12:00:01.000Z", role="user", text="brief", agent="sub:a"),
               Turn(ts="2026-09-01T12:00:02.000Z", role="user", text="brief", agent="sub:b")]
    s.subagents = [SubAgent(id="a"), SubAgent(id="b")]
    check("one human turn", facts.cost(s)["user_turns"] == 1, facts.cost(s))


def _entry(ts: str, cmd: str, ms: int = 500, argv: list[str] | None = None) -> dict:
    return {"ts": ts, "cmd": cmd, "argv": argv or [cmd], "exit": 0, "ms": ms}


def test_a_long_run_holds_its_sitting_together() -> None:
    runs = [_entry("2026-09-01T10:00:00+00:00", "doctor"),
            _entry("2026-09-01T10:00:30+00:00", "collect", ms=45 * 60 * 1000),
            _entry("2026-09-01T10:01:00+00:00", "status"),
            _entry("2026-09-01T10:46:00+00:00", "analyze")]
    got = from_log.sittings(runs)
    check("one sitting", [len(s) for s in got] == [4], [len(s) for s in got])


def test_a_multi_line_argument_is_one_call(tmp_path: Path) -> None:
    log = tmp_path / recorder.LOG_FILE
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps(_entry(
        "2026-09-01T10:00:00+00:00", "hunt",
        argv=["hunt", "--done", "Place: Store.kt:40\nEvidence: echolot analyze showed 212 ms"]))
        + "\n", encoding="utf-8")
    ref = from_log.list_sessions(tmp_path)[0]
    calls = facts.echolot_calls(from_log.read_session(ref))
    check("hunt, and no analyze out of its conclusion", [c.sub for c in calls] == ["hunt"],
          [c.sub for c in calls])


def test_transcripts_that_never_used_echolot_leave_the_log_to_speak(
        tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "app"
    log = project / recorder.LOG_FILE
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps(_entry("2026-09-01T10:00:00+00:00", "doctor", argv=["doctor", "-q"]))
                   + "\n" + json.dumps(_entry("2026-09-01T10:01:00+00:00", "analyze",
                                              argv=["analyze", "t.perfetto-trace"])) + "\n",
                   encoding="utf-8")
    transcripts = tmp_path / "claude"
    transcripts.mkdir()
    (transcripts / "s.jsonl").write_text(json.dumps(_user("2026-09-01T09:00:00.000Z",
                                                          "fix the README")) + "\n",
                                         encoding="utf-8")
    monkeypatch.chdir(project)
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["reflect", "--last", "--transcripts", str(transcripts)])
    check("the run log is read, and said to be", code == 0
          and "reading .echolot/log/runs.jsonl instead" in err.getvalue(), err.getvalue())
