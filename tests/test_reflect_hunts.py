"""`reflect` on what is a hunt, a round, a recording and a reflection.

Built as sessions rather than transcripts: each case is a handful of shell
lines and turns, read the way both readers hand them to the facts and
signals. The question is the same each time — did reflect take something for
work, a round, a re-record or a hunt that it is not.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.reflect import claude_code, from_log  # noqa: E402
from echolot.reflect import facts as facts_mod  # noqa: E402
from echolot.reflect import signals as signals_mod  # noqa: E402
from echolot.reflect.model import MAIN, Call, Session, SubAgent, Turn  # noqa: E402
from tests.support import check  # noqa: E402

T0 = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _session(lines: list[str], agent: str = MAIN, *,
             turns: list[Turn] | None = None,
             subagents: list[SubAgent] | None = None) -> Session:
    s = Session(id="s", agent="claude-code", started=_ts(0),
                ended=_ts(len(lines) * 10 + 10))
    for i, line in enumerate(lines):
        s.calls.append(Call(id=f"c{i}", ts=_ts(i * 10 + 1), tool="Bash",
                            input={"command": line}, agent=agent, command=line))
    s.turns = turns or []
    s.subagents = subagents or []
    return s


def _signals(s: Session) -> dict[str, signals_mod.Signal]:
    f = facts_mod.gather(s, None, [])
    return {sig.id: sig for sig in signals_mod.run(s, f, None)}


def test_a_reflection_is_not_work() -> None:
    reflecting = _session(
        ["echolot guide reflect", "echolot reflect --last"],
        turns=[Turn(ts=_ts(0), role="user", text="", kind="slash",
                    command="/echolot", args="reflect")])
    check("/echolot reflect, guide reflect and reflect are a reflection",
          not claude_code.involves_echolot(reflecting))
    plugin = _session(["echolot guide reflect", "echolot reflect --last"],
                      turns=[Turn(ts=_ts(0), role="user", text="", kind="slash",
                                  command="/echolot:echolot-reflect")])
    check("so is the reflect command under the plugin's prefix",
          not claude_code.involves_echolot(plugin))
    working = _session(["echolot doctor -q", "echolot analyze t.perfetto-trace"])
    check("a session that analyzed is work", claude_code.involves_echolot(working))
    hunting = _session([], turns=[Turn(ts=_ts(0), role="user", text="",
                                       kind="slash", command="/echolot",
                                       args="hunt cold start")])
    check("and /echolot with another argument is too",
          claude_code.involves_echolot(hunting))

    sitting = _session(["echolot guide reflect", "echolot reflect --last"])
    for c in sitting.calls:
        c.input["sub"] = c.command.split()[1]
    check("the run log reads a sitting of guide reflect and reflect the same way",
          not from_log.involves_echolot(sitting))


def test_a_launch_is_not_a_recording() -> None:
    s = _session(["echolot doctor -q",
                  "echolot analyze .echolot/traces/base/*.perfetto-trace -c echolot.yml",
                  "adb shell am start -W -n com.example.app/.MainActivity"])
    sigs = _signals(s)
    check("am start loses no baseline", "baseline_lost" not in sigs,
          sigs.get("baseline_lost"))
    check("and is no capture around echolot", "bypass_tools" not in sigs,
          sigs.get("bypass_tools"))


def test_the_config_is_read_the_way_a_shell_passes_it() -> None:
    cases = {
        'echolot analyze t.perfetto-trace -c "../Android Projects/app/echolot.yml"':
            "../Android Projects/app/echolot.yml",
        'CFG=echolot.yml; echolot analyze t.perfetto-trace -c "$CFG"': "echolot.yml",
        'echolot analyze t.perfetto-trace -c "$UNSET"': None,
        "echolot analyze t.perfetto-trace --config=draft/other.yml": "draft/other.yml",
        "echolot analyze t.perfetto-trace -cdraft/other.yml": "draft/other.yml",
        "echolot analyze t.perfetto-trace -c echolot.yml": "echolot.yml",
    }
    for line, want in cases.items():
        got = [c.config for c in facts_mod.echolot_calls(_session([line]))
               if c.sub == "analyze"]
        check(f"{line!r} names {want!r}", got == [want], got)

    own = _session(["echolot doctor -q",
                    'echolot analyze t.perfetto-trace -c "../Android Projects/app/echolot.yml"'])
    check("the project's own config behind a space is no bypass",
          "config_bypassed" not in _signals(own))
    other = _session(["echolot doctor -q",
                      "echolot analyze t.perfetto-trace --config=draft/other.yml"])
    check("another written as --config= is",
          "config_bypassed" in _signals(other))


def _sub(sid: str, kind: str) -> SubAgent:
    return SubAgent(id=sid, type=kind, description=kind,
                    started=_ts(1), ended=_ts(200))


def test_only_the_loop_is_a_hunt() -> None:
    reads = [f"sed -n 1,80p app/src/main/java/com/example/F{i}.kt" for i in range(12)]
    s = _session(["echolot doctor -q"], subagents=[_sub("e1", "Explore")])
    s.calls += _session(reads, agent="sub:e1").calls
    hunts = facts_mod.hunts(s, None)
    check("an Explore helper is no hunt", hunts == [], hunts)
    sigs = _signals(s)
    check("so it gets no hunt signals",
          not {"conclusion_shape", "agent_prompt_gaps", "code_read_by_hand"}
          & set(sigs), sorted(sigs))
    check("and does not stand for the loop staying in a subagent",
          "loop_in_main_context" not in sigs, sigs.get("loop_in_main_context"))

    s = _session(["echolot doctor -q"],
                 subagents=[_sub("e1", "Explore"), _sub("g1", "general-purpose")])
    s.calls += _session(["echolot guide loop", "echolot analyze t.perfetto-trace"],
                        agent="sub:g1").calls
    check("a subagent of any type that ran guide loop is the hunt",
          [h["id"] for h in facts_mod.hunts(s, None)] == ["g1"],
          facts_mod.hunts(s, None))


def test_a_round_written_on_one_line_is_a_round() -> None:
    line = ("echolot collect -c echolot.yml -n 5 && "
            "echolot analyze .echolot/traces/coldStart_iter*.perfetto-trace -c echolot.yml")
    s = _session(["echolot doctor -q"], subagents=[_sub("p1", "perf-hunter")])
    s.calls += _session([line] * 5, agent="sub:p1").calls
    h = facts_mod.hunts(s, None)[0]
    check("five such lines are five rounds",
          (h["rounds"], h["re_records"], h["analyze_calls"]) == (5, 5, 5), h)

    s = _session(["echolot analyze out/*.perfetto-trace -c echolot.yml",
                  "./gradlew :app:connectedBenchmarkAndroidTest && "
                  "echolot analyze out/*.perfetto-trace -c echolot.yml"])
    check("a re-record before an analyze on one line is checked for a lost baseline",
          "baseline_lost" in _signals(s))
