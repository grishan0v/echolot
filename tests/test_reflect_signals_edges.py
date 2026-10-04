"""`reflect`'s signals on calls they misfiled or miscounted.

A global option before the subcommand, a heredoc body, `--help` before the
real call, a doctor that failed and then passed, echolot's own
`--tp-binary`, a set kept under `.echolot/`, an `assemble…AndroidTest`
build, a call the harness refused, and totals cut to the list they show.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.reflect import facts as facts_mod  # noqa: E402
from echolot.reflect import signals as signals_mod  # noqa: E402
from echolot.reflect.model import MAIN, Call, Session, SubAgent  # noqa: E402
from tests.support import check  # noqa: E402

T0 = datetime(2026, 10, 4, 10, 0, 0, tzinfo=timezone.utc)


def _ts(s: float) -> str:
    return (T0 + timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _bash(at: float, cmd: str, out: str = "", agent: str = MAIN,
          error: bool = False, chars: int = 0) -> Call:
    return Call(id=f"c{at}", ts=_ts(at), tool="Bash", input={"command": cmd}, agent=agent,
                command=cmd, output_head=out, is_error=error, output_chars=chars)


def _session(calls: list[Call]) -> Session:
    s = Session(id="s", agent="claude-code", cwd="/work/app", started=_ts(0),
                ended=_ts(100000))
    s.calls = calls
    return s


def _sig(s: Session, name: str):
    return getattr(signals_mod, name)(s, facts_mod.gather(s, None, []), None)


def test_a_global_option_before_the_subcommand_is_not_help() -> None:
    cmd = "echolot --tp-binary bin/trace_processor_shell analyze out/a.perfetto-trace -c echolot.yml"
    calls = facts_mod.echolot_calls(_session([_bash(1, cmd)]))
    check("it is an analyze", [(c.sub, c.is_help) for c in calls] == [("analyze", False)],
          [(c.sub, c.is_help) for c in calls])
    check("subcommands agree", facts_mod.subcommands(cmd) == ["analyze"])
    calls = facts_mod.echolot_calls(_session([_bash(1, "echolot -V"), _bash(2, "echolot --version")]))
    check("and -V is the version, not a help lookup",
          [(c.sub, c.is_help) for c in calls] == [("version", False)] * 2,
          [(c.sub, c.is_help) for c in calls])


def test_a_heredoc_body_is_no_round() -> None:
    note = ("cat > notes.md <<'EOF'\nNext: echolot collect -c echolot.yml -n 5, then "
            "echolot analyze .echolot/traces/coldStart_iter000.perfetto-trace\nEOF")
    s = _session([_bash(1, "echolot doctor -q"),
                  _bash(2, "echolot analyze t.perfetto-trace -c echolot.yml", agent="sub:p"),
                  _bash(3, note, agent="sub:p"),
                  _bash(4, "echolot analyze --help", agent="sub:p"),
                  _bash(5, "echolot analyze t.perfetto-trace -c echolot.yml", agent="sub:p")])
    s.subagents = [SubAgent(id="p", type="perf-hunter", started=_ts(1), ended=_ts(9))]
    h = facts_mod.hunts(s, None)[0]
    check("one round, no re-record, two analyzes",
          (h["rounds"], h["re_records"], h["analyze_calls"]) == (1, 0, 2), h)


def test_doctor_first_reads_real_calls_and_the_last_doctor() -> None:
    s = _session([_bash(1, "echolot analyze --help"),
                  _bash(5, "echolot doctor -q"),
                  _bash(6, "echolot analyze .echolot/traces/a.perfetto-trace -c echolot.yml")])
    check("a --help first is not the first analyze", _sig(s, "doctor_first").severity == "ok")
    s = _session([_bash(1, "echolot doctor -q", "Exit code 1\nFAIL", error=True),
                  _bash(5, "echolot doctor -q"),
                  _bash(6, "echolot analyze .echolot/traces/a.perfetto-trace -c echolot.yml")])
    check("a doctor that failed and then passed is the protocol",
          _sig(s, "doctor_first").severity == "ok")


def test_echolot_s_own_tp_binary_is_not_opening_the_trace() -> None:
    s = _session([_bash(1, "echolot analyze --tp-binary tools/trace_processor_shell "
                           ".echolot/traces/a.perfetto-trace -c echolot.yml")])
    check("no breach for a call through echolot",
          _sig(s, "trace_opened_directly").severity == "ok")
    s = _session([_bash(1, "tools/trace_processor_shell .echolot/traces/a.perfetto-trace")])
    check("while the binary run by hand is one",
          _sig(s, "trace_opened_directly").severity == "warn")


def test_a_set_kept_under_echolot_is_not_lost() -> None:
    s = _session([_bash(1, "echolot analyze .echolot/traces/startup-20261001-1200/"
                           "a.perfetto-trace -c echolot.yml"),
                  _bash(2, "./gradlew :benchmark:connectedBenchmarkAndroidTest")])
    check("no baseline lost", _sig(s, "baseline_lost") is None)


def test_building_the_test_apk_is_no_capture() -> None:
    s = _session([_bash(1, "./gradlew :app:assembleBenchmarkAndroidTest")])
    check("assemble is a build", _sig(s, "bypass_tools") is None)
    s = _session([_bash(1, "./gradlew :benchmark:connectedBenchmarkAndroidTest")])
    check("connected is a capture", _sig(s, "bypass_tools") is not None)


def test_a_call_the_harness_refused_was_not_retried() -> None:
    s = _session([_bash(1, "echolot init", "Permission for this action was denied.", error=True),
                  _bash(31, "echolot init")])
    check("no retry after a refusal", _sig(s, "retries") is None)


def test_totals_are_counted_beyond_the_list() -> None:
    s = _session([Call(id=f"r{i}", ts=_ts(i * 3600), tool="Read", input={},
                       path=f"/work/app/f{i}.kt", output_chars=20000) for i in range(16)])
    gaps = _sig(s, "long_gaps")
    hogs = _sig(s, "context_hogs")
    check("fifteen silences", gaps.title.startswith("15 silence(s)"), gaps.title)
    check("sixteen large outputs", hogs.title.startswith("16 tool output(s)"), hogs.title)
