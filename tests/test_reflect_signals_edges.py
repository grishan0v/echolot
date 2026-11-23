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
from echolot.reflect.model import MAIN, Call, Session, SubAgent, Turn  # noqa: E402
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


def _doctor_run(at: float, exit: int = 0, version: str = "0.17.0", **facts) -> dict:
    return {"ts": (T0 + timedelta(seconds=at)).isoformat(timespec="seconds"),
            "cmd": "doctor", "argv": ["doctor"], "exit": exit, "ms": 900,
            "version": version, **({"facts": facts} if facts else {})}


def test_a_passing_doctor_in_the_run_log_counts_before_the_analyze() -> None:
    s = _session([_bash(1000, "echolot"),
                  _bash(1010, "echolot analyze .echolot/traces/a.perfetto-trace")])

    def verdict(runs: list[dict]):
        return signals_mod.doctor_first(s, facts_mod.gather(s, None, runs), None)

    sig = verdict([_doctor_run(1010 - 15 * 60)])
    check("a doctor that passed 15 minutes before is the check the protocol asks for",
          sig.severity == "ok" and "15 min" in sig.title, sig.title)
    sig = verdict([_doctor_run(1010 - 30 * 3600)])
    check("one from 30 hours before is not", sig.severity == "warn", sig.title)
    sig = verdict([_doctor_run(1010 - 15 * 60, exit=1, failed=["adb"])])
    check("nor one that failed", sig.severity == "warn", sig.title)
    init = _doctor_run(1010 - 600, checks=148)
    init["cmd"] = "init"
    sig = verdict([init])
    check("the self-check inside init counts, as status reads it",
          sig.severity == "ok" and "init" in sig.title, sig.title)
    sig = verdict([_doctor_run(1010 + 60)])
    check("a doctor after the analyze does not", sig.severity == "warn", sig.title)


def _why(s: Session) -> list[tuple[int, str]]:
    return [(g["seconds"], g["why"]) for g in facts_mod.gaps(s, keep=None)]


def test_a_silence_is_labelled_by_what_the_session_was_waiting_on() -> None:
    bg = _bash(10, "echolot analyze .echolot/traces/*.perfetto-trace",
               out="Command running in background with ID: b7x2k9")
    bg.input["run_in_background"] = True
    s = _session([bg, _bash(20, "ls .echolot/out"), _bash(515, "cat .echolot/out/report.md")])
    s.turns = [Turn(ts=_ts(510), role="user", kind="notification",
                    text="<task-notification>\n<task-id>b7x2k9</task-id>\n<status>completed")]
    check("a background shell, until its notification",
          _why(s) == [(495, "waiting on a background task")], _why(s))

    agent = Call(id="a", ts=_ts(5), tool="Agent", input={"prompt": "hunt"},
                 output_head="Async agent launched")
    s = _session([agent, _bash(30, "git status"), _bash(983, "git diff")])
    s.subagents = [SubAgent(id="h1", type="perf-hunter", started=_ts(6), ended=_ts(980))]
    s.calls += [_bash(40, "echolot guide loop", agent="sub:h1"),
                _bash(60, "echolot analyze t.perfetto-trace", agent="sub:h1")]
    check("a subagent in the background, after a plain Bash",
          (953, "a subagent was running") in _why(s), _why(s))

    s = _session([_bash(10, "git push"), _bash(1456, "gh pr view 1")])
    s.turns = [Turn(ts=_ts(12), role="assistant", text="Pushed; over to you."),
               Turn(ts=_ts(1450), role="user", text="CI is green, go on")]
    check("the turn handed back, then a message",
          _why(s) == [(1446, "waiting for the human or another session")], _why(s))

    s = _session([_bash(10, "git push"), _bash(1456, "gh pr view 1")])
    check("and nothing outstanding stays unknown", _why(s) == [(1446, "unknown")], _why(s))


def test_an_exit_1_that_is_an_answer_is_no_friction() -> None:
    answers = ["diff a.json b.json", "cd out && cmp a b", "grep -rn AGENTTMP_ app/src",
               "LC_ALL=C rg -n foo src"]
    s = _session([_bash(i, cmd, out="Exit code 1\n", error=True)
                  for i, cmd in enumerate(answers)])
    sig = _sig(s, "env_friction")
    check("diff, cmp and grep saying no are answers", sig is None, sig and sig.rows)
    s = _session([_bash(1, "diff a b | head -5", out="Exit code 1\n", error=True),
                  _bash(2, "diff a b", out="Exit code 2\ndiff: a: No such file or directory",
                        error=True)])
    sig = _sig(s, "env_friction")
    check("a pipeline's exit is its last stage's, and diff's 2 is an error",
          sig is not None and sum(r["count"] for r in sig.rows) == 2, sig and sig.rows)


def test_echolot_is_a_call_only_where_the_shell_runs_it() -> None:
    strings = ['echo "next: echolot collect -n 5"',
               "sed -n '/### echolot report/p' notes.md",
               "gh issue view 529 -R grishan0v/echolot --json title,body",
               'gh issue list -R grishan0v/echolot --state all --search "reflect"',
               "grep -n 'echolot analyze' docs/reflect.md"]
    for line in strings:
        check(f"{line!r} runs no echolot", facts_mod.subcommands(line) == [],
              facts_mod.subcommands(line))
    calls = {
        "echolot collect -n 5": ["collect"],
        "~/.local/bin/echolot analyze a.perfetto-trace": ["analyze"],
        ".venv/bin/echolot report --plain": ["report"],
        "ECHOLOT_NO_RECORD=1 echolot doctor -q": ["doctor"],
        'N=$(echolot names --json | wc -l); echo "$(echolot status)"': ["names", "status"],
        "bash -c 'echolot analyze a.perfetto-trace'": ["analyze"],
        "python3 -m echolot analyze a.perfetto-trace": ["analyze"],
        'echo "step 2" && echolot collect -n 5 | tail -3': ["collect"],
    }
    for line, want in calls.items():
        check(f"{line!r} runs {want}", facts_mod.subcommands(line) == want,
              facts_mod.subcommands(line))
    s = _session([_bash(1, 'echo "… echolot collect …"', out="Exit code 2\n", error=True),
                  _bash(2, "echolot collect -n 5")])
    calls_found = facts_mod.echolot_calls(s)
    check("an echo's exit code is not echolot's",
          [(c.sub, c.exit) for c in calls_found] == [("collect", 0)],
          [(c.sub, c.exit) for c in calls_found])
    check("and a config read off a quoted path keeps its spaces",
          facts_mod.echolot_calls(_session([_bash(
              1, 'echolot analyze t.perfetto-trace -c "my app/echolot.yml"')]))[0].config
          == "my app/echolot.yml")


def test_a_trace_is_opened_by_what_runs_and_not_by_a_word_on_the_line() -> None:
    def rows(calls: list[Call]) -> list[str]:
        sig = _sig(_session(calls), "trace_opened_directly")
        return [r["how"] for r in sig.rows]

    check("a path that names trace_processor is no opening", rows([_bash(
        1, "ls ~/.local/pipx/venvs/echolot/lib/python3.14/site-packages/perfetto/"
           "trace_processor")]) == [])
    check("nor is copying the trace",
          rows([_bash(1, "cp .echolot/traces/a.perfetto-trace /tmp/keep/")]) == [])
    check("nor a note about trace_processor written through a heredoc", rows([_bash(
        1, "cat > notes.md <<'MD'\nfrom perfetto.trace_processor import TraceProcessor\nMD")]) == [])
    script = ("cat > verify2.py <<'EOF'\nfrom perfetto.trace_processor import TraceProcessor\n"
              "import sys\ntp = TraceProcessor(trace=sys.argv[1])\nEOF")
    check("a script written to open the trace, then run, is", rows([
        _bash(1, script),
        _bash(2, "python3 -I verify2.py .echolot/traces/coldStart_iter000.perfetto-trace")])
        == ["ran verify2.py, written to open the trace"])
    write = Call(id="w", ts=_ts(1), tool="Write", path="/work/app/verify.py", input={
        "file_path": "/work/app/verify.py",
        "content": "from perfetto.trace_processor import TraceProcessor\n"})
    check("so is one written by Write", rows([write, _bash(2, "python3 -I verify.py")])
          == ["ran verify.py, written to open the trace"])
    check("python fed through a heredoc is code that ran", rows([_bash(
        1, "python3 - <<'EOF'\nimport perfetto.trace_processor as tp\nEOF")])
        == ["code through a heredoc"])
    check("and strings on the trace file reads it",
          rows([_bash(1, "strings -n 8 .echolot/traces/a.perfetto-trace | grep track_event")])
          == ["strings on the trace file"])


def test_only_the_projects_own_config_is_written() -> None:
    def written(calls: list[Call]) -> list[str]:
        return [w["file"] for w in facts_mod.config_writes(_session(calls))]

    check("a copy into another directory is no write of the config", written([
        _bash(1, "mkdir -p /tmp/x && cp echolot.yml /tmp/x/echolot.yml"),
        _bash(2, "cp local.yml work-b/local.yml"),
        _bash(3, "cat > /tmp/x/echolot.yml <<'YML'\nscenario: {name: a}\nYML")]) == [])
    check("a Write of a copy is not either", written([Call(
        id="w", ts=_ts(1), tool="Write", path="/work/app/work-a/echolot.yml",
        input={"content": "scenario: {name: a}\n"})]) == [])
    check("the project's own is, however it is written", written([
        _bash(1, "cp /tmp/x/echolot.yml ."),
        _bash(2, "cat > echolot.yml <<'YML'\nscenario: {name: a}\nYML"),
        Call(id="w", ts=_ts(3), tool="Write", path="/work/app/local.yml",
             input={"content": "device: {serial: x}\n"})]) == ["echolot.yml", "echolot.yml",
                                                             "local.yml"])
