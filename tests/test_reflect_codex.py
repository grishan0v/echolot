#!/usr/bin/env python3
"""`reflect` over a Codex session, subagents included (#193).

Once echolot shipped for Codex, hunts ran there, and `reflect` read them from
the run log alone: echolot's own commands and exit codes, and nothing of what
the model read, ran around them, spent, or did in the subagent. The first
thing the spike wanted from it was one of those: the main thread read the
loop's whole guide before handing the loop down.

The session here is built the way Codex CLI 0.159.2 wrote one: a main thread
and a subagent in files of their own under `sessions/YYYY/MM/DD/`, the
subagent's file opening with a copy of its parent's history, the brief
encrypted, commands as `CommandExecution` items and edits as `FileChange`.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path

import pytest

from echolot.main import main
from echolot.reflect import claude_code, codex
from tests.support import check

MAIN_ID = "01a0f1dc-2468-7e00-ac87-cdc28953fd66"
SUB_ID = "01a0f1dd-bcf0-7b30-8aa3-57920ac6eecf"
CONCLUSION = """Place: app/src/main/java/com/example/Startup.kt:40
Evidence: main_thread_block 212 ms, AGENTTMP_load_catalog 198 ms
Mechanism: the catalog is parsed on the main thread (inferred)
Suggestion: parse it off the main thread
Confidence: medium — one round of markers
Ruled out: gc_pressure, 12 ms
Also measured: AGENTTMP_load_catalog 198 ms
Cleanup: temporary instrumentation removed"""


def _row(ordinal: int, ts: str, kind: str, payload: dict) -> dict:
    return {"timestamp": f"2026-09-30T10:{ts}Z", "ordinal": ordinal, "type": kind,
            "payload": payload}


def _item(ordinal: int, ts: str, item: dict) -> dict:
    return _row(ordinal, ts, "event_msg", {"type": "item_completed", "item": item})


def _command(ordinal: int, ts: str, script: str, code: int = 0, out: str = "") -> dict:
    return _item(ordinal, ts, {
        "type": "CommandExecution", "id": f"exec-{ordinal}",
        "command": ["/bin/zsh", "-lc", script], "exit_code": code,
        "aggregated_output": out, "status": "completed" if code == 0 else "failed",
        "duration": {"secs": 1, "nanos": 500_000_000}})


def _patch(ordinal: int, ts: str, path: str, diff: str) -> dict:
    return _item(ordinal, ts, {"type": "FileChange", "id": f"patch-{ordinal}",
                               "status": "completed",
                               "changes": {path: {"type": "update", "unified_diff": diff}}})


def _usage(ordinal: int, ts: str, response: str, inp: int, cached: int, out: int) -> dict:
    return _row(ordinal, ts, "token_usage_record", {
        "response_id": response,
        "usage": {"input_tokens": inp, "cached_input_tokens": cached,
                  "cache_write_input_tokens": 0, "output_tokens": out}})


def _write(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _session(home: Path, project: Path,
             loop_guide_in_main: bool = True) -> tuple[Path, Path]:
    """A hunt in Codex: the main thread and the loop's subagent."""
    day = home / "sessions" / "2026" / "09" / "30"
    source = str(project / "app" / "src" / "main" / "java" / "com" / "example" / "Startup.kt")
    main_rows = [
        _row(0, "00:00.000", "session_meta", {
            "id": MAIN_ID, "cwd": str(project), "cli_version": "0.159.2",
            "git": {"branch": "master"}, "source": "exec"}),
        _row(1, "00:00.100", "turn_context", {"model": "gpt-6-luna", "cwd": str(project)}),
        _item(2, "00:01.000", {"type": "UserMessage",
                               "content": [{"type": "text",
                                            "text": "Cold start got slower. Where does the time go?"}]}),
        _command(3, "00:05.000", "echolot doctor -q", out="self-check: 148 of 148 passed"),
        _row(4, "00:08.000", "response_item", {
            "type": "function_call", "name": "request_user_input_async", "call_id": "ask-1",
            "arguments": json.dumps({"questions": [{
                "title": "What changed before the slowdown?",
                "options": [{"label": "A commit"}, {"label": "Unknown"}]}]})}),
        _command(5, "00:10.000", 'echolot hunt "cold start got slower"'),
        # The whole loop guide, read by the main thread: what the spike saw.
        _command(6, "00:12.000", "echolot guide loop", out="x" * 17933),
        _row(7, "00:13.000", "response_item", {
            "type": "function_call", "name": "spawn_agent", "namespace": "collaboration",
            "call_id": "spawn-1",
            "arguments": json.dumps({"task_name": "trace_hunt", "fork_turns": "none",
                                     "message": "gAAAAAB-encrypted"})}),
        _item(8, "00:13.500", {"type": "SubAgentActivity", "kind": "started",
                               "agent_thread_id": SUB_ID, "agent_path": "/root/trace_hunt"}),
        _row(9, "00:14.000", "response_item", {
            "type": "function_call", "name": "wait_agent", "namespace": "collaboration",
            "call_id": "wait-1", "arguments": json.dumps({"timeout_ms": 3600000})}),
        _command(10, "03:00.000", 'echolot hunt --done "Place: Startup.kt:40"'),
        _item(11, "03:05.000", {"type": "AgentMessage", "phase": "final_answer",
                                "content": [{"type": "Text", "text": "The catalog parse."}]}),
        _row(12, "03:05.100", "event_msg", {"type": "task_complete",
                                            "last_agent_message": "The catalog parse."}),
        _usage(13, "03:05.200", "resp-1", inp=20000, cached=15000, out=800),
    ]
    sub_rows = [
        _row(0, "00:13.600", "session_meta", {
            "id": SUB_ID, "cwd": str(project), "cli_version": "0.159.2",
            "source": {"subagent": {"thread_spawn": {
                "parent_thread_id": MAIN_ID, "depth": 1, "agent_path": "/root/trace_hunt"}}},
            "agent_path": "/root/trace_hunt", "parent_thread_id": MAIN_ID,
            "subagent_history_start_ordinal": 3}),
        # The parent's history, copied: not the subagent's own.
        _command(1, "00:05.000", "echolot doctor -q"),
        _item(2, "00:01.000", {"type": "UserMessage", "content": [{"type": "text",
                                                                  "text": "copied prompt"}]}),
        _row(3, "00:13.700", "response_item", {
            "type": "agent_message", "author": "/root", "recipient": "/root/trace_hunt",
            "content": [{"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\n"},
                        {"type": "encrypted_content", "encrypted_content": "gAAAAAB"}]}),
        _command(4, "00:15.000", "echolot guide loop", out="x" * 17933),
        _command(5, "00:20.000", "echolot analyze .echolot/traces/a.perfetto-trace -c echolot.yml"),
        _patch(6, "00:40.000", source,
               "@@ -39,2 +39,3 @@\n fun start() {\n+    trace(\"AGENTTMP_load_catalog\") {\n     load()\n"),
        _item(7, "00:41.000", {"type": "Reasoning", "summary_text": []}),
        _command(8, "01:00.000", "echolot collect -c echolot.yml -n 3"),
        _command(9, "02:00.000", "echolot analyze .echolot/traces/b.perfetto-trace -c echolot.yml"),
        _patch(10, "02:30.000", source,
               "@@ -39,3 +39,2 @@\n fun start() {\n-    trace(\"AGENTTMP_load_catalog\") {\n     load()\n"),
        _command(11, "02:40.000", "grep -rn AGENTTMP_ app/src/main", code=1),
        _item(12, "02:50.000", {"type": "AgentMessage", "phase": "final_answer",
                                "content": [{"type": "Text", "text": CONCLUSION}]}),
        _row(13, "02:50.100", "event_msg", {"type": "task_complete",
                                            "last_agent_message": CONCLUSION}),
        _usage(14, "02:50.200", "resp-9", inp=50000, cached=40000, out=3000),
    ]
    if not loop_guide_in_main:
        main_rows = [r for r in main_rows
                     if (r["payload"].get("item") or {}).get("command")
                     != ["/bin/zsh", "-lc", "echolot guide loop"]]
    main_file = _write(day / f"rollout-2026-09-30T13-00-00-{MAIN_ID}.jsonl", main_rows)
    sub_file = _write(day / f"rollout-2026-09-30T13-00-13-{SUB_ID}.jsonl", sub_rows)
    return main_file, sub_file


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    p = tmp_path / "app-project"
    p.mkdir()
    (p / "echolot.yml").write_text(
        "project: {package: com.example.app}\n"
        "scenario: {name: coldStart}\n"
        "instrumentation: {allowed: [app/src/main]}\n", encoding="utf-8")
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")
    # No Claude Code transcripts from the machine running this.
    monkeypatch.setattr(claude_code, "PROJECTS_ROOT", tmp_path / "claude-projects")
    _session(home, p)
    return p


def _run(project: Path, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    here = Path.cwd()
    os.chdir(project)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(list(argv))
    finally:
        os.chdir(here)
    return code, out.getvalue()


# --- the reader --------------------------------------------------------------

def test_only_the_projects_main_threads_are_listed(project, tmp_path):
    refs = codex.list_sessions(project)
    check("the main thread, and not the subagent's", [r.id for r in refs] == [MAIN_ID], refs)
    check("nothing for another directory", codex.list_sessions(tmp_path) == [])


def test_the_main_thread_reads_as_a_session(project):
    s = codex.read_session(codex.list_sessions(project)[0].path)
    check("the agent, its version, the model and the branch",
          (s.agent, s.agent_version, s.model, s.git_branch)
          == ("codex", "0.159.2", "gpt-6-luna", "master"), s)
    check("the prompt", s.turns[0].text.startswith("Cold start got slower"))
    check("the main thread's commands",
          [c.command for c in s.bash("main")][:3]
          == ["echolot doctor -q", 'echolot hunt "cold start got slower"', "echolot guide loop"])
    check("a question to the human", s.asks and s.asks[0].question.startswith(
        "What changed") and s.asks[0].options == ["A commit", "Unknown"], s.asks)
    check("tokens: cached apart from the rest", (s.usage.input, s.usage.cache_read,
                                                  s.usage.output) == (5000, 15000, 800))
    check("its last word", s.final_text == "The catalog parse.")


def test_the_subagent_is_the_loop_and_only_its_own_rows_count(project):
    s = codex.read_session(codex.list_sessions(project)[0].path)
    check("one subagent, the loop", len(s.subagents) == 1
          and s.subagents[0].type == "perf-hunter", s.subagents)
    own = s.bash(f"sub:{SUB_ID}")
    check("the parent's history is not the subagent's",
          "echolot doctor -q" not in [c.command for c in own], [c.command for c in own])
    check("the conclusion came back", s.subagents[0].final_text == CONCLUSION)
    check("a failure keeps its exit code",
          own[-1].is_error and own[-1].output_head.startswith("Exit code 1"))
    edits = s.edits(f"sub:{SUB_ID}")
    check("the patches as edits, the marker in and then out",
          len(edits) == 2 and "AGENTTMP_" in edits[0].input["new_string"]
          and "AGENTTMP_" in edits[1].input["old_string"]
          and "AGENTTMP_" not in edits[1].input["new_string"], edits)
    check("the brief is said to be unreadable",
          any("encrypted" in n for n in s.notes), s.notes)


# --- reflect, end to end ---------------------------------------------------------

def test_reflect_last_reads_the_codex_session(project):
    code, said = _run(project, "reflect", "--last", "--project", str(project))
    check("reflect exits 0", code == 0, said[-600:])
    check("no fallback to the log", "reading .echolot/log" not in said, said[:300])
    report = json.loads((project / ".echolot" / "reflect" / f"{MAIN_ID[:8]}.json")
                        .read_text(encoding="utf-8"))
    hunt = report["hunts"][0]
    check("the hunt: two rounds, the whole conclusion", hunt["rounds"] == 2
          and all(hunt["conclusion_fields"].values()), hunt)
    by_id = {s["id"]: s for s in report["signals"]}
    check("the loop stayed in the subagent",
          by_id["loop_in_main_context"]["severity"] == "ok", by_id.get("loop_in_main_context"))
    check("the brief is not checked, and says why",
          by_id["agent_prompt_gaps"]["severity"] == "skip"
          and "what the main context handed the subagent"
          in by_id["agent_prompt_gaps"]["why"], by_id.get("agent_prompt_gaps"))
    hogs = by_id.get("context_hogs") or {}
    check("the guide the main thread read is among the large outputs",
          any(r.get("agent") == "main" and "guide loop" in str(r.get("what"))
              for r in hogs.get("rows") or []), hogs)


def test_list_names_the_agent_and_puts_the_newest_first(project, tmp_path):
    """A Claude Code session beside the Codex one, older: both listed, newest first."""
    slug = claude_code.slug_candidates(project)[0]
    older = tmp_path / "claude-projects" / slug / "c1aude00-0000-0000-0000-000000000000.jsonl"
    _write(older, [
        {"type": "assistant", "timestamp": "2026-09-29T09:00:00Z", "cwd": str(project),
         "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                  "input": {"command": "echolot doctor -q"}}]}},
        {"type": "user", "timestamp": "2026-09-29T09:00:05Z",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                  "content": "ok"}]}}])
    os.utime(older, (1_000_000_000, 1_000_000_000))
    code, said = _run(project, "reflect", "--list", "--project", str(project))
    lines = said.splitlines()
    check("the agent column", lines[0].split()[:2] == ["session", "agent"], said)
    check("Codex first, then Claude Code",
          [ln.split()[1] for ln in lines[1:3]] == ["codex", "claude-code"], said)


# --- the loop's guide, read where it should not be (#202) ----------------------

def _signals(project: Path) -> dict:
    code, said = _run(project, "reflect", "--last", "--project", str(project))
    check("reflect exits 0", code == 0, said[-600:])
    report = json.loads((project / ".echolot" / "reflect" / f"{MAIN_ID[:8]}.json")
                        .read_text(encoding="utf-8"))
    return {s["id"]: s for s in report["signals"]}


def test_the_loops_guide_read_in_the_main_thread_is_a_warning(project):
    sig = _signals(project)["guides_in_main"]
    check("a warning", sig["severity"] == "warn", sig)
    check("with the guide and what it cost",
          sig["rows"] == [{"ts": "10:00:12", "guide": "echolot guide loop", "chars": 17933}],
          sig["rows"])


def test_a_main_thread_that_leaves_it_to_the_subagent_passes(tmp_path, monkeypatch):
    p = tmp_path / "clean"
    p.mkdir()
    (p / "echolot.yml").write_text("project: {package: com.example.app}\n", encoding="utf-8")
    home = tmp_path / "codex-home-clean"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")
    monkeypatch.setattr(claude_code, "PROJECTS_ROOT", tmp_path / "claude-projects")
    _session(home, p, loop_guide_in_main=False)
    sig = _signals(p)["guides_in_main"]
    check("the check passes", sig["severity"] == "ok", sig)
