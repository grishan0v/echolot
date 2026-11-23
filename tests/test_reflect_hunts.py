"""`reflect` on what is a hunt, a round, a recording and a reflection.

Built as sessions rather than transcripts: each case is a handful of shell
lines and turns, read the way both readers hand them to the facts and
signals. The question is the same each time — did reflect take something for
work, a round, a re-record or a hunt that it is not.
"""

from __future__ import annotations

import json
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


# ---------------------------------------------------------- what came back
#
# Rows as Claude Code writes them, read by the reader itself: the answer's
# carriers are in the transcript's shape, not in the model's.

CONCLUSION = ("Place: Store.kt:40\nEvidence: main_thread_block 212 ms\n"
              "Mechanism: sync read on the main thread (inferred)\nSuggestion: move it\n"
              "Confidence: medium — one round of markers\n"
              "Ruled out: gc_pressure, 8 ms\nAlso measured: AGENTTMP_read 41 ms\n"
              "Cleanup: temporary instrumentation removed")
BRIEF = ("Find the cold start regression. Traces: .echolot/traces/*.perfetto-trace. "
         "Return Place / Evidence / Mechanism / Suggestion / Confidence / Ruled out / "
         "Also measured / Cleanup)")
AGENT_ID = "a1b2c3"


def _row(kind: str, at: float, content, **extra) -> dict:
    return {"type": kind, "timestamp": _ts(at), "cwd": "/work/app", **extra,
            "message": {"id": f"m{at}", "role": kind, "content": content}}


def _agent_call(at: float, use_id: str) -> dict:
    return _row("assistant", at, [{"type": "tool_use", "id": use_id, "name": "Agent", "input": {
        "subagent_type": "perf-hunter", "description": "Find the cause", "prompt": BRIEF}}])


def _launched(at: float, use_id: str) -> dict:
    return _row("user", at, [{"type": "tool_result", "tool_use_id": use_id,
                              "content": "Async agent launched"}],
                toolUseResult={"isAsync": True, "status": "async_launched",
                               "agentId": AGENT_ID})


def _waiting(at: float) -> dict:
    return _row("user", at,
                f"<task-notification>\n<task-id>{AGENT_ID}</task-id>\n"
                f"<result>This agent has not reported yet: it is waiting on its own "
                f"background work and will deliver its report through "
                f"SubagentHandback when that finishes.</result>\n</task-notification>")


def _read(tmp_path: Path, main: list[dict], sub: list[dict] | None) -> Session:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "s.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in main), encoding="utf-8")
    if sub is not None:
        d = tmp_path / "s" / "subagents"
        d.mkdir(parents=True)
        (d / f"agent-{AGENT_ID}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in sub), encoding="utf-8")
    return claude_code.read_session(path)


def _sub_rows(handback: bool) -> list[dict]:
    rows = [_row("user", 2, BRIEF),
            _row("assistant", 3, [{"type": "tool_use", "id": "g1", "name": "Bash",
                                   "input": {"command": "echolot guide loop"}}]),
            _row("assistant", 1500, [{"type": "text", "text":
                 "Waiting for the clean build to install so the phone is back in its "
                 "round-0 state."}])]
    if handback:
        rows.append(_row("assistant", 1560, [{
            "type": "tool_use", "id": "h1", "name": "SubagentHandback",
            "input": {"message": CONCLUSION}}]))
    return rows


def _shape(s: Session):
    f = facts_mod.gather(s, None, [])
    return f, signals_mod.conclusion_shape(s, f, None)


def test_a_hand_back_is_the_answer_wherever_it_arrives(tmp_path: Path) -> None:
    main = [_agent_call(1, "t1"), _launched(1, "t1"), _waiting(600), _waiting(1200)]
    s = _read(tmp_path / "own", main, _sub_rows(handback=True))
    f, sig = _shape(s)
    check("the subagent's own SubagentHandback is what it returned",
          sig.severity == "ok" and f.hunts[0]["final_text"] == CONCLUSION,
          (sig.rows, f.hunts[0]["final_text"][:80]))

    back = _row("user", 1561, f'<agent-message from="{AGENT_ID}">[Subagent hand-back] '
                              f"{CONCLUSION}</agent-message>")
    s = _read(tmp_path / "main", [*main, back], _sub_rows(handback=False))
    f, sig = _shape(s)
    check("so is the hand-back as the main transcript received it",
          sig.severity == "ok" and f.hunts[0]["final_text"] == CONCLUSION
          and f.hunts[0]["confidence"] == "medium — one round of markers",
          (sig.rows, f.hunts[0]["final_text"][:80]))

    s = _read(tmp_path / "none", main, _sub_rows(handback=False))
    f, sig = _shape(s)
    check("with neither, there is no answer — not the 'not reported yet' text, "
          "nor the progress line before it",
          sig.rows == [{"agent": "perf-hunter", "missing": "no return value at all"}],
          (sig.rows, f.hunts[0]["final_text"][:80]))


def test_a_refused_agent_call_is_no_run_of_the_subagent(tmp_path: Path) -> None:
    refused = _row("user", 2, [{"type": "tool_result", "tool_use_id": "t0", "is_error": True,
                                "content": "A hook changed this call's input after auto mode "
                                           f"reviewed it. Input was: {BRIEF}"}])
    main = [_agent_call(1, "t0"), refused, _agent_call(5, "t1"), _launched(5, "t1"),
            _waiting(600)]
    s = _read(tmp_path, main, _sub_rows(handback=True))
    f, sig = _shape(s)
    check("one subagent, the one that ran",
          [h["id"] for h in f.hunts] == [AGENT_ID], [h["id"] for h in f.hunts])
    check("judged on its own conclusion", sig.severity == "ok", sig.rows)
    friction = signals_mod.env_friction(s, f, None)
    check("and the refusal is still the host's friction",
          friction is not None and any(r["kind"] == "hook redirected the call"
                                       for r in friction.rows), friction)


def test_a_field_is_a_heading_not_a_word_in_a_sentence() -> None:
    s = _session(["echolot guide loop"], agent="sub:p1",
                 subagents=[_sub("p1", "perf-hunter")])
    s.subagents[0].final_text = (
        "Place: Store.kt:40\nEvidence: 212 ms\nMechanism: sync read\nSuggestion: move it\n"
        "I have little Confidence in the mechanism; it was not measured.\n"
        "Ruled out: nothing else was checked\nAlso measured: none\nCleanup: none was added")
    h = facts_mod.hunts(s, None)[0]
    check("Confidence mentioned in a sentence is no Confidence field",
          h["conclusion_fields"]["confidence"] is False and h["confidence"] is None, h)
    s.subagents[0].final_text = BRIEF
    h = facts_mod.hunts(s, None)[0]
    check("and the brief's list of the fields is none of them",
          not any(h["conclusion_fields"].values()) and h["confidence"] is None, h)
    s.subagents[0].final_text = ("## Place\nStore.kt:40\n**Confidence:** high — two rounds\n"
                                 "- Ruled out: gc\n")
    h = facts_mod.hunts(s, None)[0]
    check("headings in markdown are fields",
          h["conclusion_fields"]["place"] and h["conclusion_fields"]["ruled_out"]
          and h["confidence"] == "high — two rounds", h)
