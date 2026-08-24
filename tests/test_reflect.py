#!/usr/bin/env python3
"""`echolot reflect` over a synthetic Claude Code session.

Builds a small transcript in the layout Claude Code uses — a main file plus a
subagent file — with known events planted in it, runs `reflect` over it, and
checks the answers: the reader must find every planted event, and each signal
must fire exactly where it was planted and stay quiet elsewhere.

Building the transcript and running reflect over it happens once for the file;
every check below reads the one report that produced.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from echolot import recorder  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402


def expect(ok: object, claim: str) -> None:
    """`check` with the claim second, as this file has always written it."""
    check(claim, ok)


T0 = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
SESSION = "abcdef12-0000-0000-0000-000000000000"
AGENT = "a1b2c3d4e5f6a7b8c"
MSG = [0]


def ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _base(t: float, cwd: str) -> dict:
    return {"timestamp": ts(t), "cwd": cwd, "version": "2.1.0", "gitBranch": "main",
            "sessionId": SESSION, "isSidechain": False}


def user_text(t, cwd, text, **extra):
    row = _base(t, cwd)
    row.update({"type": "user", "message": {"role": "user", "content": text}})
    row.update(extra)
    return row


def assistant(t, cwd, blocks, output_tokens=100, msg_id=None):
    row = _base(t, cwd)
    row.update({"type": "assistant", "message": {
        "id": msg_id or f"msg_{MSG[0]}", "model": "claude-fable-5", "role": "assistant",
        "content": blocks,
        "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000,
                  "output_tokens": output_tokens}}})
    return row


def tool_use(uid, name, inp):
    return {"type": "tool_use", "id": uid, "name": name, "input": inp}


def tool_result(t, cwd, uid, content, is_error=False, tur=None):
    row = _base(t, cwd)
    row.update({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": uid, "content": content,
         "is_error": is_error}]}})
    if tur is not None:
        row["toolUseResult"] = tur
    return row


def bash(rows, t, cwd, uid, command, output, is_error=False, dt=1.0):
    MSG[0] += 1
    rows.append(assistant(t, cwd, [tool_use(uid, "Bash", {"command": command})]))
    rows.append(tool_result(t + dt, cwd, uid, output, is_error))


def mcp_shell(rows, t, cwd, uid, code, output, dt=1.0):
    """A shell command through an MCP tool — same command, other envelope."""
    MSG[0] += 1
    rows.append(assistant(t, cwd, [tool_use(
        uid, "mcp__plugin_context-mode_context-mode__ctx_execute",
        {"language": "shell", "code": code})]))
    rows.append(tool_result(t + dt, cwd, uid, output))


def edit(rows, t, cwd, uid, path, old, new):
    MSG[0] += 1
    rows.append(assistant(t, cwd, [tool_use(uid, "Edit", {
        "file_path": path, "old_string": old, "new_string": new})]))
    rows.append(tool_result(t + 0.5, cwd, uid, "ok"))


def build(root: Path, project: Path) -> Path:
    cwd = str(project)
    slug = cwd.replace("/", "-")
    pdir = root / slug
    (pdir / SESSION / "subagents").mkdir(parents=True)

    # ------------------------------------------------------------ main
    m: list[dict] = []
    m.append(user_text(0, cwd, "<command-message>echolot</command-message>\n"
                                "<command-name>/echolot</command-name>"))
    m.append(user_text(1, cwd, [{"type": "text", "text":
             f"Base directory for this skill: {cwd}/.claude/skills/echolot\n\n# echolot"}]))
    m.append(user_text(2, cwd, "[Request interrupted by user]"))
    m.append(user_text(3, cwd, "<command-message>echolot-setup</command-message>\n"
                                "<command-name>/echolot-setup</command-name>"))
    # streaming rows of one response: usage grows, the last row is final
    MSG[0] += 1
    m.append(assistant(4, cwd, [{"type": "thinking", "thinking": "…"}], output_tokens=3))
    m.append(assistant(4.1, cwd, [{"type": "text", "text": "Checking the environment."}],
                       output_tokens=250))
    # `2>&1` is not an argument; the reader must strip it
    bash(m, 5, cwd, "tu_doctor", "echolot doctor 2>&1", "All 12 checks passed", dt=2.0)
    # a question with a recommended option, answered against the recommendation
    MSG[0] += 1
    m.append(assistant(10, cwd, [tool_use("tu_ask", "AskUserQuestion", {"questions": [{
        "question": "Which scenario?", "header": "Scenario",
        "options": [{"label": "Cold start (Recommended)"}, {"label": "Checkout"}]}]})]))
    m.append(tool_result(70, cwd, "tu_ask",
                         'Your questions have been answered: "Which scenario?"="Checkout"'))
    MSG[0] += 1
    m.append(assistant(80, cwd, [tool_use("tu_write", "Write", {
        "file_path": f"{cwd}/echolot.yml", "content": "project:\n  process: app\n"})]))
    m.append(tool_result(80.5, cwd, "tu_write", "ok"))
    # a Bash call that fails in the shell before echolot runs, then a retry
    bash(m, 90, cwd, "tu_cal1", 'cd "out/SM-A515F - 13" && echolot calibrate *.perfetto-trace -c echolot.yml',
         "Exit code 1\n(eval):cd:1: no such file or directory: out/SM-A515F - 13", is_error=True)
    bash(m, 95, cwd, "tu_cal2", 'echolot calibrate "out/SM-A515F - 13"/*.perfetto-trace -c echolot.yml',
         "thresholds written", dt=20.0)
    # calibrate's output applied to echolot.yml through a python heredoc: a
    # config write the reader must see, and a heredoc body that mentions
    # `echolot calibrate` without running it
    bash(m, 100, cwd, "tu_cfg_shell",
         "python3 - <<'PY'\n"
         "p='echolot.yml'; t=open(p).read()\n"
         "t += '# thresholds: `echolot calibrate` on 5 healthy runs\\ndetectors:\\n"
         "  main_thread_block:\\n    min_slice_ms: 40\\n'\n"
         "open(p,'w').write(t)\n"
         "PY\n"
         "echo written", "written")
    bash(m, 120, cwd, "tu_an1", "echolot analyze build/out/*.perfetto-trace -c echolot.yml",
         "# Marker Report\n…", dt=25.0)
    # the agent's own python fails after a clean analyze: a traceback in the
    # output that is not echolot's
    bash(m, 130, cwd, "tu_an_py",
         "echolot analyze build/out/*.perfetto-trace -c echolot.yml >/dev/null; "
         "python3 -c \"import json; r=json.load(open('.echolot/out/report.json')); r['detectors'].items()\"",
         "Traceback (most recent call last):\n  File \"<string>\", line 1, in <module>\n"
         "AttributeError: 'list' object has no attribute 'items'", dt=26.0)
    # Prose that mentions the tool is not a call. Before subcommands were
    # checked against the parser, `ran` and `without` arrived in the report as
    # subcommands of their own and the "By subcommand" line read like a
    # sentence — with the timeline and every signal measuring it.
    bash(m, 140, cwd, "tu_echo",
         'echo "echolot ran fine, and echolot without asking is the default"',
         "echolot ran fine, and echolot without asking is the default")
    # A document written through a heredoc, mentioning the two things the
    # command scanners look for. Nothing here ran: `trace_processor` and
    # `report.json` are inside the body, on their way into a file. On a real
    # session this shape produced 19 of 21 `trace_opened_directly` rows —
    # writing about the tool read as using it.
    bash(m, 142, cwd, "tu_notes",
         "cat > notes.md <<'MD'\n"
         "The pinned trace_processor is what makes two runs comparable.\n"
         "Read report.json rather than the markdown: json.load(open('report.json')).\n"
         "MD\necho written", "written")
    # And the documented example, which is not the project's config. The name
    # used to be matched as a substring, so writing this counted as editing
    # `echolot.yml` — with the thresholds inside it read as hand-tuning.
    bash(m, 145, cwd, "tu_example",
         "cat > echolot.yml.example <<'YML'\n"
         "detectors:\n  main_thread_block:\n    min_slice_ms: 40\n"
         "YML\necho written", "written")
    m.append(user_text(150, cwd, "<command-message>echolot-hunt</command-message>\n"
                                  "<command-name>/echolot-hunt</command-name>"))
    MSG[0] += 1
    m.append(assistant(160, cwd, [tool_use("tu_agent", "Agent", {
        "subagent_type": "perf-hunter", "description": "Find the cause",
        "prompt": "Localise the regression. Traces: out/*.perfetto-trace. "
                  "It was 3 s, now it is 7 s."})]))
    m.append(tool_result(161, cwd, "tu_agent", "launched", tur={
        "isAsync": True, "status": "async_launched", "agentId": AGENT}))
    m.append(user_text(900, cwd,
             f"<task-notification>\n<task-id>{AGENT}</task-id>\n<status>completed</status>\n"
             f"<result>Place: Foo.kt:12\nEvidence: main_thread_block 120 ms\n"
             f"Mechanism: sync IO\nSuggestion: move it\nConfidence: high\n"
             f"Also measured: AGENTTMP_read 41 ms · AGENTTMP_parse 12 ms\n"
             f"Cleanup: removed</result>\n</task-notification>"))
    m.append(user_text(950, cwd, "thanks, implement the fix"))
    # two analyze calls in one Bash line against a config written on the
    # spot: the first glob matches nothing (zsh skips that line, the tool
    # still exits 0 because `echo done` ran), the second runs
    bash(m, 960, cwd, "tu_an_pair",
         "sed 's/^detectors:/off:/' echolot.yml > /tmp/frames.yml\n"
         "echolot analyze out/before/*.perfetto-trace -c /tmp/frames.yml && cp .echolot/out/report.json /tmp/before.json\n"
         "echolot analyze out/after/*.perfetto-trace -c /tmp/frames.yml && cp .echolot/out/report.json /tmp/after.json\n"
         "echo done",
         "(eval):2: no matches found: out/before/*.perfetto-trace\ndone", dt=27.0)
    # a re-record through an MCP shell tool: the metrics json is copied, the
    # trace directory is renamed inside the build tree (gradle cleans it),
    # nothing copies the traces themselves → the baseline is lost
    mcp_shell(m, 970, cwd, "tu_mv_rec",
              'OUT="build/out"\n'
              'mkdir -p /tmp/base && cp "$OUT/metrics.json" /tmp/base/\n'
              'mv "$OUT" "${OUT}_before_fix"\n'
              "./gradlew :benchmark:connectedBenchmarkAndroidTest > /tmp/run.log 2>&1",
              "BUILD SUCCESSFUL", dt=600.0)
    bash(m, 1580, cwd, "tu_an_after", "echolot analyze build/out/*.perfetto-trace -c echolot.yml",
         "# Marker Report", dt=25.0)
    # and one done right: the traces are copied out before re-recording
    bash(m, 1610, cwd, "tu_keep",
         "mkdir -p .echolot/traces/before && cp build/out/*.perfetto-trace .echolot/traces/before/", "")
    bash(m, 1620, cwd, "tu_rec_ok", "./gradlew :benchmark:connectedBenchmarkAndroidTest",
         "BUILD SUCCESSFUL", dt=600.0)
    # Claude Code writes gitBranch "HEAD" outside a git repository; that is
    # not a branch name and must not be reported as one
    m[0]["gitBranch"] = "HEAD"
    (pdir / f"{SESSION}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in m) + "\n", encoding="utf-8")

    # -------------------------------------------------------- subagent
    s: list[dict] = []
    bash(s, 170, cwd, "su_doc", "echolot doctor", "All 12 checks passed", dt=2.0)
    bash(s, 175, cwd, "su_an1", "echolot analyze out/*.perfetto-trace -c echolot.yml",
         "# Marker Report", dt=25.0)
    bash(s, 205, cwd, "su_json1", "python3 -c \"import json; r=json.load(open('.echolot/out/report.json')); print(r['window'])\"",
         "{'duration_ms': 3000}")
    bash(s, 210, cwd, "su_json2", "python3 -c \"import json; r=json.load(open('.echolot/out/report.json')); print(r['summary'])\"",
         "{'fired': 1}")
    bash(s, 215, cwd, "su_help", "echolot names --help", "usage: echolot names …")
    # a config of its own, then analyze against it
    bash(s, 220, cwd, "su_yml", "cat > /tmp/hunt.yml <<'EOF'\nproject:\n  process: app\nEOF", "")
    bash(s, 225, cwd, "su_an2", "echolot analyze out/*.perfetto-trace -c /tmp/hunt.yml",
         "# Marker Report", dt=25.0)
    # the agent reads a source file whole before instrumenting: what feeds
    # the window, by activity, must show it
    bash(s, 298, cwd, "su_cat", f"cat {cwd}/app/src/main/kotlin/Foo.kt",
         "class Foo {\n" + "    fun load() { /* … */ }\n" * 120 + "}\n")
    # temporary instrumentation: one inside allowed, one outside, one unprefixed
    edit(s, 300, cwd, "su_e1", f"{cwd}/app/src/main/kotlin/Foo.kt",
         "fun load() {", 'fun load() { trace("AGENTTMP_load") {')
    edit(s, 305, cwd, "su_e2", f"{cwd}/benchmark/src/main/java/Bench.kt",
         "fun run() {", 'fun run() { trace("AGENTTMP_bench") {')
    edit(s, 310, cwd, "su_e3", f"{cwd}/app/src/main/kotlin/Bar.kt",
         "fun draw() {", 'fun draw() { trace("draw_frame") {')
    # and one edit made through the shell — a python heredoc, no Edit tool
    bash(s, 312, cwd, "su_e_sh",
         f"cd {cwd}/app/src/main/kotlin && python3 - <<'EOF'\n"
         "p='Baz.kt'; s=open(p).read()\n"
         "s=s.replace('fun init() {', 'fun init() { trace(\"AGENTTMP_init\") {')\n"
         "open(p,'w').write(s)\nEOF", "")
    # re-record, then analyze again: round two
    bash(s, 320, cwd, "su_rec", "./gradlew :benchmark:connectedBenchmarkAndroidTest", "BUILD SUCCESSFUL", dt=300.0)
    bash(s, 640, cwd, "su_an3", "echolot analyze out/*.perfetto-trace -c echolot.yml",
         "# Marker Report", dt=25.0)
    # cleanup: both prefixed edits reverted, then a grep
    edit(s, 700, cwd, "su_e4", f"{cwd}/app/src/main/kotlin/Foo.kt",
         'fun load() { trace("AGENTTMP_load") {', "fun load() {")
    edit(s, 705, cwd, "su_e5", f"{cwd}/benchmark/src/main/java/Bench.kt",
         'fun run() { trace("AGENTTMP_bench") {', "fun run() {")
    bash(s, 707, cwd, "su_e_sh2",
         f"cd {cwd}/app/src/main/kotlin && sed -i '' 's/ trace(\"AGENTTMP_init\") {{//' Baz.kt", "")
    bash(s, 710, cwd, "su_grep", "grep -rn AGENTTMP_ app/src benchmark/src | wc -l", "0")
    MSG[0] += 1
    # a long conclusion, Cleanup and Confidence past the fourth kilobyte, the
    # suggestion under a Russian heading: every field must still be found
    s.append(assistant(720, cwd, [{"type": "text", "text":
             "Place: Foo.kt:12\nEvidence: main_thread_block 120 ms\nMechanism: sync IO\n"
             + "the mechanism, at length: the file is read on the main thread\n" * 80
             + "## Что чинить\nmove it\nConfidence: high\n"
             + "Ещё измерено: AGENTTMP_read 41 ms\nCleanup: removed"}], output_tokens=400))
    (pdir / SESSION / "subagents" / f"agent-{AGENT}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in s) + "\n", encoding="utf-8")
    return pdir


@pytest.fixture(scope="module")
def reflected(tmp_path_factory):
    """One transcript, one reflect run, one report — read by every check below."""
    root = tmp_path_factory.mktemp("reflect")
    project = root / "app-project"
    project.mkdir()
    (project / "echolot.yml").write_text(
        "project:\n  process: app\n  source_root: app/src/main/kotlin\n"
        "scenario:\n  name: checkout\n"
        "loop:\n  max_rounds: 3\n"
        "instrumentation:\n  allowed: [\"app/src/main\"]\n  temp_prefix: AGENTTMP_\n",
        encoding="utf-8")
    transcripts = build(root / "projects", project)

    here = os.getcwd()
    os.chdir(project)
    try:
        # The report itself goes to stdout; here only the verdict matters.
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = main(["reflect", "--last", "--transcripts", str(transcripts),
                         "--project", str(project)])
    finally:
        os.chdir(here)
    check("reflect exits 0", code == 0, f"exit {code}")

    written = list((project / ".echolot" / "reflect").glob("*.json"))
    check("exactly one report was written", len(written) == 1, str(written))
    return json.loads(written[0].read_text(encoding="utf-8")), project


def test_the_reader_found_every_planted_event(reflected):
    """Transcript in, normalised session out, with nothing lost on the way."""
    report, _ = reflected
    src = report["source"]
    expect(src["model"] == "claude-fable-5", "model read from assistant rows")
    expect(src["agent_version"] == "2.1.0", "agent version read")
    expect(src["git_branch"] == "main", f"HEAD is not a branch: {src['git_branch']}")
    calls = report["echolot_calls"]
    expect(len(calls) == 13,
           f"13 echolot calls; prose about the tool is not one, got {len(calls)}")
    subs = sorted(c["sub"] for c in calls)
    expect(subs.count("analyze") == 8 and subs.count("doctor") == 2 and subs.count("names") == 1,
           f"subcommands: {subs}")
    # `echolot calibrate` inside a heredoc body is text, not a call
    expect(subs.count("calibrate") == 2, f"heredoc mention not counted as a call: {subs}")
    # the agent's own python traceback after a clean analyze is not echolot's
    py = next(c for c in calls if "r['detectors'].items()" in c["command"])
    expect(not py["traceback"] and py["exit"] == 0, f"inline python traceback not echolot's: {py}")
    doc = next(c for c in calls if c["sub"] == "doctor" and c["agent"] == "main")
    expect(doc["argv"] == "", f"redirect stripped from argv: {doc['argv']!r}")
    # one Bash line, two invocations: the skipped one has no exit and no
    # duration, the one that ran keeps the line's numbers and says it shares
    pair = [c for c in calls if c["config"] == "/tmp/frames.yml"]
    expect(len(pair) == 2 and all(c["shared"] == 2 for c in pair), f"pair of analyze calls: {pair}")
    skipped = [c for c in pair if not c["ran"]]
    ran = [c for c in pair if c["ran"]]
    expect(len(skipped) == 1 and "before" in skipped[0]["argv"] and skipped[0]["exit"] is None
           and skipped[0]["duration_s"] is None and skipped[0]["shell_error"],
           f"the glob that missed names the skipped call: {skipped}")
    expect(len(ran) == 1 and "after" in ran[0]["argv"] and ran[0]["exit"] == 0
           and ran[0]["duration_s"] == 27.0 and not ran[0]["shell_error"],
           f"the other one ran: {ran}")
    expect(len(report["questions"]) == 1, "one question")
    q = report["questions"][0]
    expect(q["recommended"] == "Cold start (Recommended)" and q["chosen"] == "Checkout",
           f"recommended/chosen: {q}")
    expect(q["answered_after_s"] == 60.0, f"answered after 60 s, got {q['answered_after_s']}")
    expect(len(report["hunts"]) == 1, "one subagent")
    h = report["hunts"][0]
    expect(h["type"] == "perf-hunter" and h["id"] == AGENT, f"subagent linked by agentId: {h['id']}")
    expect(h["rounds"] == 2 and h["re_records"] == 1, f"rounds/re-records: {h['rounds']}/{h['re_records']}")
    expect(all(h["conclusion_fields"].values()), f"conclusion fields: {h['conclusion_fields']}")
    expect(h["confidence"] == "high", f"confidence: {h['confidence']}")
    expect(h["prompt_mentions"] == {"traces": True, "regression": True, "since_change": False},
           f"prompt mentions: {h['prompt_mentions']}")
    expect(h["duration_s"] and 700 <= h["duration_s"] <= 760, f"subagent duration: {h['duration_s']}")
    e = report["entry"]
    expect(e["skills_loaded"] == ["echolot"], f"skills loaded: {e['skills_loaded']}")
    expect(e["interruptions"] == 1, "one interruption")
    expect([x["command"] for x in e["slash_commands"]] == ["/echolot", "/echolot-setup", "/echolot-hunt"],
           f"slash commands: {e['slash_commands']}")
    expect(e["slash_before_first_call"] == 2, f"slash commands before the first call: {e}")
    expect(e["slash_in_entry"] == 2, f"slash commands in the entry window: {e}")
    # streaming usage: max per message (250, not 3 and not 253), one count per
    # message id, main and subagent kept apart
    um = report["cost"]["usage_main"]
    expect(um["output"] == 250 + 100 * 17, f"main output tokens: {um['output']}")
    us = report["cost"]["usage_subagents"]
    expect(us["output"] == 400 + 100 * 18, f"subagent output tokens: {us['output']}")


def test_every_planted_signal_fired(reflected):
    """Each signal must fire exactly where the transcript plants its cause."""
    report, _ = reflected
    sig = {x["id"]: x for x in report["signals"]}
    h = report["hunts"][0]

    expect(sig.get("doctor_first", {}).get("severity") == "ok", "doctor_first ok")
    expect(sig.get("trace_opened_directly", {}).get("severity") == "ok",
           "a document about trace_processor is not an opened trace")
    # The transcript plants one genuine slicing — an inline python that loads
    # report.json — and one document that merely mentions it. Only the first
    # is a row.
    sliced = sig.get("report_sliced_by_hand", {}).get("rows", [])
    expect(len(sliced) == 3, f"the three real slicings, and not the document: {sliced}")
    expect(all("notes.md" not in str(r.get("command", "")) for r in sliced),
           f"a document about report.json is not report.json cut up: {sliced}")
    # One row, for the one write to the project's own config. The count is
    # what the claim rests on: `config_writes` records the name it searched
    # for rather than the path it found, so asserting that no row says
    # "example" would hold however wrong the matching got.
    by_hand = sig.get("thresholds_by_hand", {}).get("rows", [])
    expect(len(by_hand) == 1,
           f"echolot.yml.example is not the project's config: {by_hand}")
    expect(sig.get("loop_in_main_context", {}).get("severity") == "ok", "loop stayed in subagent")
    expect(sig.get("rounds_over_max", {}).get("severity") == "ok", "rounds within max")
    expect(sig.get("conclusion_shape", {}).get("severity") == "ok", "conclusion shape ok")
    x = sig.get("cleanup_balance")
    expect(x and x["severity"] == "ok" and "through the shell" in x["why"]
           and "found nothing" in x["why"], f"cleanup ok, shell edits counted: {x}")
    inst = report["instrumentation"]
    expect(inst["files"].get("app/src/main/kotlin/Baz.kt", {}).get("shell") == 2
           and inst["shell_edits"] == 2 and inst["cleanup_grep_clean"] is True,
           f"shell edits seen per file, grep verdict clean: {inst}")
    x = sig.get("edits_outside_allowed")
    expect(x and x["severity"] == "warn" and len(x["rows"]) == 2 and
           all("Bench.kt" in r["file"] for r in x["rows"]), f"edits outside allowed: {x}")
    x = sig.get("instrumentation_prefix")
    expect(x and x["severity"] == "warn" and x["rows"][0]["file"].endswith("Bar.kt"),
           f"unprefixed tracing call: {x}")
    x = sig.get("config_bypassed")
    expect(x and x["severity"] == "warn" and any(r["config"] == "/tmp/hunt.yml" for r in x["rows"]),
           f"config bypassed: {x}")
    rows = x["rows"] if x else []
    # the heredoc and the sed redirect are reported by the yaml path, not by
    # the first hundred characters of the command
    expect(sorted(r["config"] for r in rows if r["sub"] == "Bash redirect")
           == ["/tmp/frames.yml", "/tmp/hunt.yml"], f"configs written from Bash: {rows}")
    # the analyze that zsh skipped did not run on anything: one row, not two
    expect(sum(1 for r in rows if r["sub"] == "analyze" and r["config"] == "/tmp/frames.yml") == 1,
           f"skipped analyze not counted as a bypass: {rows}")
    x = sig.get("report_sliced_by_hand")
    expect(x and len(x["rows"]) == 3, f"report sliced by hand three times: {x}")
    x = sig.get("help_lookups")
    expect(x and len(x["rows"]) == 1, f"one help lookup: {x}")
    x = sig.get("bypass_tools")
    expect(x and x["rows"][0]["kind"] == "gradle", f"gradle bypass: {x}")
    # the gradle run through the MCP shell tool counts too: 1 in the subagent,
    # 2 in main (one MCP, one Bash)
    expect(x and x["rows"][0]["count"] == 3 and "main" in x["rows"][0]["agents"],
           f"MCP shell seen as shell: {x}")
    x = sig.get("baseline_lost")
    expect(x and x["severity"] == "warn" and len(x["rows"]) == 2, f"baseline lost twice: {x}")
    if x and len(x["rows"]) == 2:
        sub_row, main_row = x["rows"]
        expect(sub_row["agent"].startswith("sub:") and "no copy" in sub_row["note"],
               f"subagent re-recorded over the analyzed set: {sub_row}")
        expect(main_row["agent"] == "main" and "inside the build tree" in main_row["note"]
               and "build/out_before_fix" in main_row["note"]
               and main_row["traces_before"] == "build/out",
               f"main renamed the traces inside build/: {main_row}")
        # the re-record after `cp *.perfetto-trace .echolot/traces/before/` is not a row
        expect(all("12:27:00" not in r["ts"] for r in x["rows"]),
               f"a copied-out baseline is not a loss: {x['rows']}")
    x = sig.get("echolot_failures")
    expect(x and x["severity"] == "info" and x["rows"][0]["where"] == "shell",
           f"shell failure classified: {x}")
    # the skipped analyze is a shell failure too, even though the Bash tool
    # itself reported success
    expect(x and len(x["rows"]) == 2 and x["rows"][1]["sub"] == "analyze"
           and x["rows"][1]["where"] == "shell" and x["rows"][1]["exit"] is None,
           f"glob miss classified as shell failure: {x}")
    x = sig.get("retries")
    expect(x and x["rows"][0]["sub"] == "calibrate", f"calibrate retry: {x}")
    x = sig.get("agent_prompt_gaps")
    expect(x and x["rows"][0]["missing"] == "since_change", f"prompt gap: {x}")
    x = sig.get("entry_fumbling")
    expect(x is not None, "entry fumbling (an interruption in the entry window)")
    x = sig.get("long_gaps")
    expect(x and any(r["seconds"] >= 300 for r in x["rows"]), f"the gradle wait is a gap: {x}")
    # what fed the subagent's window: one source read, most of the chars,
    # before the first instrumentation edit
    w = h["window"]
    src = w["by_activity"].get("source reading") or {}
    expect(src.get("calls") == 1 and src.get("share", 0) >= 20
           and w["source_reads_before_first_edit"] == 1
           and w["by_activity"]["echolot"]["calls"] >= 4,
           f"window mix: {w}")
    x = sig.get("code_read_by_hand")
    expect(x and x["rows"][0]["source reads"] == 1 and x["rows"][0]["agent"] == "perf-hunter",
           f"code read by hand: {x}")

    # the config write through the python heredoc is seen: thresholds after
    # calibrate, from the shell
    x = sig.get("thresholds_by_hand")
    expect(x and x["severity"] == "info" and x["rows"][0]["tool"] == "shell",
           f"shell-written thresholds after calibrate: {x}")
    expect(any(m["label"] == "shell wrote echolot.yml" for m in report["timeline"]),
           "config write from the shell is a milestone")
    # the /tmp/hunt.yml analyze came after echolot.yml was written: a bypass,
    # not a setup draft
    x = sig.get("config_bypassed")
    expect(x and all(r.get("phase") == "after setup" for r in x["rows"]),
           f"config bypass phase: {x}")


def test_the_signals_with_nothing_planted_stayed_quiet(reflected):
    """Silence is a result too, and a signal that fires anyway is noise."""
    report, _ = reflected
    sig = {x["id"]: x for x in report["signals"]}

    for quiet in ("context_hogs", "env_friction"):
        expect(quiet not in sig, f"{quiet} must not fire: {sig.get(quiet)}")


def test_the_recorder_logged_the_reflect_run(reflected):
    """The tool's own flight recorder, which no transcript can vouch for."""
    _, project = reflected

    log = project / recorder.LOG_FILE
    runs = recorder.read(log)
    reflects = [r for r in runs if r.get("cmd") == "reflect"]
    check("a line for the reflect run itself", reflects, "none in the log")
    r = reflects[-1]
    check("the line carries exit, duration and argv",
          r.get("exit") == 0 and "ms" in r and "argv" in r, r)
    check("and the facts reflect attached to it",
          (r.get("facts") or {}).get("sessions") == 1, r.get("facts"))
