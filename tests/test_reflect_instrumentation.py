"""`reflect` on the instrumentation an agent placed, and the config it wrote.

A marker placed by absolute path, a cleanup grep read backwards, a grep
dropped for the word echolot before it, `loop.max_rounds` taken for a
threshold, markers the main context wrote by Write or the shell, a workflow
taken for a config, thresholds compared by bare key, a timeline with another
prefix, and a note saved as instrumentation.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.config import Config  # noqa: E402
from echolot.reflect import facts as facts_mod  # noqa: E402
from echolot.reflect import signals as signals_mod  # noqa: E402
from echolot.reflect.model import MAIN, Call, Session, SubAgent  # noqa: E402
from tests.support import check  # noqa: E402

T0 = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
EXAMPLE = (Path(__file__).resolve().parent.parent / "echolot.yml.example").read_text(
    encoding="utf-8")


def _ts(s: float) -> str:
    return (T0 + timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _session(calls: list[Call], cwd: str = "/work/app") -> Session:
    s = Session(id="s", agent="claude-code", cwd=cwd, started=_ts(0), ended=_ts(600))
    s.calls = calls
    return s


def _bash(i: int, cmd: str, out: str = "", agent: str = MAIN) -> Call:
    return Call(id=f"b{i}", ts=_ts(i * 10), tool="Bash", input={"command": cmd},
                agent=agent, command=cmd, output_head=out)


def _edit(i: int, path: str, old: str, new: str, agent: str = MAIN) -> Call:
    return Call(id=f"e{i}", ts=_ts(i * 10), tool="Edit", path=path, agent=agent,
                input={"file_path": path, "old_string": old, "new_string": new})


def _write(i: int, path: str, content: str, agent: str = MAIN) -> Call:
    return Call(id=f"w{i}", ts=_ts(i * 10), tool="Write", path=path, agent=agent,
                input={"file_path": path, "content": content})


def test_a_shell_edit_by_absolute_path_is_found() -> None:
    s = _session([_bash(1, "sed -i '' 's/work()/trace(\"AGENTTMP_load\") { work() }/' "
                           "/work/app/app/src/main/java/com/example/Store.kt")])
    got = facts_mod.instrumentation(s, None)
    check("the file is the relative one", list(got["files"])
          == ["app/src/main/java/com/example/Store.kt"], got["files"])


def test_the_cleanup_grep_is_read_the_right_way_round() -> None:
    check("a path from grep -l is a marker still there",
          facts_mod._grep_verdict("app/src/main/java/com/example/Store.kt\n", "AGENTTMP_",
                                  "grep -rl AGENTTMP_ app/src") is False)
    for out in ("exit: 1\n", "grep exit: 1\n", "exit:1\n", "EXIT:1\n", "exit code: 1\n"):
        check(f"{out.strip()!r} is clean", facts_mod._grep_verdict(out, "AGENTTMP_") is True)
    check("and exit: 0 is not", facts_mod._grep_verdict("exit: 0\n", "AGENTTMP_") is False)


def test_a_grep_after_the_word_echolot_still_counts() -> None:
    s = _session([
        _edit(1, "/work/app/app/src/Store.kt", "work()", 'trace("AGENTTMP_load") { work() }'),
        _edit(2, "/work/app/app/src/Store.kt", 'trace("AGENTTMP_load") { work() }', "work()"),
        _bash(3, "cat echolot.yml; grep -rn AGENTTMP_ app/src | wc -l", "0\n"),
    ])
    got = facts_mod.instrumentation(s, None)
    check("the grep is the cleanup grep", got["grep_calls_total"] == 1
          and got["cleanup_grep_clean"] is True, got)
    s = _session([_bash(1, "echolot names t.perfetto-trace | grep AGENTTMP_", "AGENTTMP_x 3\n")])
    check("while one fed by echolot is not",
          facts_mod.instrumentation(s, None)["grep_calls_total"] == 0)


def test_loop_max_rounds_is_not_a_threshold() -> None:
    check("nothing to compare in the loop section",
          signals_mod._threshold_values("loop:\n  max_rounds: 5\n") == {})
    s = _session([Call(id="e", ts=_ts(10), tool="Edit", path="/work/app/echolot.yml",
                       input={"file_path": "/work/app/echolot.yml",
                              "old_string": "loop:\n  max_rounds: 3",
                              "new_string": "loop:\n  max_rounds: 5"})])
    sig = signals_mod.thresholds_by_hand(s, facts_mod.gather(s, None, []), None)
    check("and raising it is no hand-tuned threshold", sig is None, sig)


def test_thresholds_are_compared_per_detector() -> None:
    values = signals_mod._threshold_values(EXAMPLE)
    check("each detector's max_total_ms is its own",
          values.get("gc_pressure.max_total_ms") == "120"
          and values.get("binder_txn.max_total_ms") == "50", values)
    raised = EXAMPLE.replace("max_total_ms: 120", "max_total_ms: 300", 1)
    check("so a change to one of them shows",
          signals_mod._threshold_values(raised)["gc_pressure.max_total_ms"] == "300")


def test_the_main_context_s_markers_by_write_and_shell_count(tmp_path: Path) -> None:
    s = _session([
        _bash(1, "sed -i '' 's/a()/trace(\"AGENTTMP_a\") { a() }/' app/src/A.kt"),
        _write(2, "/work/app/app/src/B.kt", 'fun b() = trace("AGENTTMP_b") { work() }'),
        _bash(3, "./gradlew :benchmark:connectedBenchmarkAndroidTest"),
    ])
    s.subagents = [SubAgent(id="p", type="perf-hunter", started=_ts(100), ended=_ts(200))]
    sig = signals_mod.loop_in_main_context(s, facts_mod.gather(s, None, []), None)
    check("the loop ran in the main context", sig is not None and sig.severity == "warn", sig)


def test_a_workflow_is_not_a_config() -> None:
    s = _session([
        _bash(1, "echolot doctor -q"),
        _write(2, "/work/app/.github/workflows/perf.yml", "on: schedule\n"),
        _bash(3, "echolot analyze .echolot/traces/a.perfetto-trace -c echolot.yml"),
    ])
    sig = signals_mod.config_bypassed(s, facts_mod.gather(s, None, []), None)
    check("no bypass for a file nobody passed to echolot", sig is None, sig)
    s = _session([
        _bash(1, "echolot doctor -q"),
        _write(2, "/tmp/frames.yml", "detectors: {}\n"),
        _bash(3, "echolot analyze .echolot/traces/a.perfetto-trace -c /tmp/frames.yml"),
    ])
    check("while one that was is",
          signals_mod.config_bypassed(s, facts_mod.gather(s, None, []), None) is not None)


def test_the_timeline_takes_the_prefix_and_the_earliest_marker() -> None:
    s = _session([
        _edit(4, "/work/app/app/src/Main.kt", "go()", 'trace("TMPX_main") { go() }'),
        _edit(1, "/work/app/app/src/Sub.kt", "do()", 'trace("TMPX_sub") { do() }',
              agent="sub:p"),
    ])
    cfg = Config({"instrumentation": {"temp_prefix": "TMPX_"}})
    marks = [m for m in facts_mod.gather(s, cfg, []).milestones
             if m["label"].startswith("temporary instrumentation")]
    check("the subagent's earlier marker, under the project's prefix",
          len(marks) == 1 and marks[0]["ts"].startswith(_ts(10)[:19]), marks)


def test_a_note_is_not_an_instrumentation_edit() -> None:
    note = _write(1, "notes/before.json", "{}")
    check("a note is not instrumentation", facts_mod.activity_of(note) != "instrumentation edit")
    marker = _edit(2, "app/src/Store.kt", "work()", 'trace("AGENTTMP_load") { work() }')
    check("a marker is", facts_mod.activity_of(marker) == "instrumentation edit")
