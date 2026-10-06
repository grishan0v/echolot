#!/usr/bin/env python3
"""Rules the agent texts state, held to the tool they describe (#274).

Each of these was an instruction an agent followed to a lost round or a dirty
tree: a marker form the module could not compile, a cleanup check that looked
at one module, a round recorded after the last analysis, a setup question
about numbers no command prints.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import mark  # noqa: E402
from tests.support import check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOOP = ROOT / "echolot" / "claude" / "agents" / "perf-hunter.md"
GUIDE_HUNT = ROOT / "echolot" / "guide" / "hunt.md"
HUNT_COMMAND = ROOT / "echolot" / "claude" / "commands" / "echolot-hunt.md"
SETUP_COMMAND = ROOT / "echolot" / "claude" / "commands" / "echolot-setup.md"
PLUGIN = ROOT / "plugins" / "echolot" / "skills"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    return " ".join(_read(path).split())


def test_the_marker_the_loop_writes_by_hand_is_what_mark_remove_deletes():
    """The form needs no dependency, is the same in Java, and every line of it
    goes with `echolot mark --remove`, beside the ones `--apply` wrote."""
    text = _read(LOOP)
    block = re.search(r"```kotlin\n(android\.os\.Trace\.beginSection.*?)```", text, re.S)
    check("the loop gives the framework's form", block, text)
    lines = [ln for ln in block.group(1).splitlines() if ln.strip() and ln.strip() != "…"]
    check("three lines around the body", len(lines) == 3, lines)
    check("each one is a line --remove deletes",
          all(mark.is_applied_line(ln) for ln in lines), lines)
    check("and the begin carries the prefix", "AGENTTMP_" in lines[0], lines)
    check("androidx.tracing is no longer the form to copy",
          'androidx.tracing.trace("AGENTTMP_' not in text, text)


def test_the_loop_keeps_suspension_points_out_of_a_section():
    flat = _flat(LOOP)
    check("a rule about suspend calls",
          "Never put a suspension point inside a section" in flat, flat)


def test_the_loop_stops_before_recording_a_round_nobody_analyses():
    text = _read(LOOP)
    limit = text.find("round == loop.max_rounds")
    record = text.find("re-record, round += 1")
    check("the limit is tested", limit >= 0, text)
    check("before the re-record", 0 <= limit < record, (limit, record))
    layer_doc = _read(ROOT / "docs" / "agent-layer.md")
    check("and the same in docs/agent-layer.md",
          0 <= layer_doc.find("round == loop.max_rounds") < layer_doc.find("re-record"),
          layer_doc)


def test_the_loop_says_bare_compare_takes_the_last_two_analyze_runs():
    flat = _flat(LOOP)
    check("the last two analyze runs", "last two `analyze` runs" in flat, flat)
    check("and how to pick the rounds after an extra one",
          "echolot hunt --show <n>" in flat, flat)


def test_every_cleanup_check_covers_the_checkout_and_the_untagged_half():
    """Markers may go into every path of `instrumentation.allowed`, and the
    `endSection()` half of a pair carries the tag and not the prefix."""
    grep = "grep -rn --include='*.kt' --include='*.java' -e AGENTTMP_ -e 'echolot:mark' ."
    for path in (LOOP, GUIDE_HUNT, HUNT_COMMAND):
        check(f"{path.name} gives the whole check", grep in _flat(path), path)
    for path in [*(ROOT / "echolot").rglob("*.md"), *(ROOT / "echolot").rglob("*.py"),
                 *PLUGIN.rglob("*.md")]:
        check(f"{path.relative_to(ROOT)} greps no single source root",
              "<source_root>" not in _read(path), path)


def test_a_hunt_takes_out_what_the_last_one_left_before_recording():
    for path in (GUIDE_HUNT, PLUGIN / "echolot-hunt" / "SKILL.md"):
        flat = _flat(path)
        check(f"{path.name} says to remove leftovers with mark --remove",
              "echolot mark --remove" in flat, flat)


def test_the_door_keeps_a_stale_layer_out_of_a_plugin_project():
    """`init --all` without `--for` keeps the saved choice and the layer, so
    Claude Code loads its skills beside the plugin's."""
    row = next(ln for ln in _read(PLUGIN / "echolot" / "SKILL.md").splitlines()
               if ln.startswith("| `init-force`"))
    check("init-force runs init for the plugin", "echolot init --for plugin" in row, row)
    check("and not init --all", "init --all" not in row, row)


def test_setup_asks_only_about_what_probe_prints_and_calibrates_collects_traces():
    text = _read(SETUP_COMMAND)
    check("no start times probe does not print", "@ 772 ms" not in text, text)
    check("the options carry max_ms", "max 867.65 ms" in text, text)
    check("calibrate reads the traces collect writes",
          "echolot calibrate .echolot/traces/<scenario>_iter*.perfetto-trace" in text, text)
    check("not a glob nothing matches", "run*.perfetto-trace" not in text, text)


def test_the_anr_guide_reads_the_lead_anr_prints():
    flat = _flat(ROOT / "echolot" / "guide" / "anr.md")
    check("the frames nearest to the app", "the frames nearest to the app" in flat, flat)
    check("the missing lock notes", "Who was holding what" in flat, flat)


# --- stale counts and keys (#275) -----------------------------------------------

REFS = ROOT / "echolot" / "claude" / "skills" / "echolot" / "references"


def test_the_loop_reads_calibration_with_cli_overrides_too():
    flat = _flat(LOOP)
    check("config+cli counts as calibrated", "`config` or `config+cli`" in flat, flat)


def test_the_hunt_texts_let_collect_set_the_last_round_aside():
    for path in (LOOP, HUNT_COMMAND):
        flat = _flat(path)
        check(f"{path.name}: collect sets the round aside",
              ".echolot/traces/<scenario>-<stamp>/" in flat, flat)
        check(f"{path.name}: no hand copy into a directory of your own",
              ".echolot/traces/<round>/" not in flat and ".echolot/traces/<label>/" not in flat,
              flat)


def test_the_report_example_runs_every_shipped_detector():
    from echolot.main import DETECTOR_DIR
    from echolot.tp import load_detectors
    shipped = len(load_detectors(DETECTOR_DIR))
    text = _read(REFS / "report.md")
    check("detectors_run is the shipped count",
          f'"detectors_run": {shipped},' in text, shipped)


def test_the_comparison_examples_carry_what_compare_writes():
    report_md, compare_md = _read(REFS / "report.md"), _read(ROOT / "docs" / "compare.md")
    for name, text in (("report.md", report_md), ("compare.md", compare_md)):
        check(f"{name}: the ratio as compare rounds it", '"ratio": 73.01' in text, name)
    check("report.md: values and runs on both sides",
          report_md.count('"values": [ … ], "count": 1, "runs": "5/5"') == 2, report_md)


def test_the_report_reference_warns_where_the_report_does():
    from echolot import report
    key = report.BUDGET_LABELS[0][0]
    quiet = report._budget_lines({"window_ms": 100.0, "accounted_pct": 97.0, key: 97.0})
    loud = report._budget_lines({"window_ms": 100.0, "accounted_pct": 94.0, key: 94.0})
    check("97% passes in silence and 94% does not", len(loud) > len(quiet), (quiet, loud))
    flat = _flat(REFS / "report.md")
    check("the reference says 95%", "The report warns below 95%" in flat, flat)


def test_the_jit_thread_is_named_the_way_art_names_it():
    for path in [*(ROOT / "echolot").rglob("*.md"), *(ROOT / "echolot").rglob("*.sql")]:
        check(f"{path.relative_to(ROOT)}: no hyphenated JIT thread",
              "jit-thread-pool" not in _read(path), path)


def test_the_door_names_both_upgrades_the_tool_names():
    from echolot import layer
    flat = _flat(PLUGIN / "echolot" / "SKILL.md")
    for command in re.findall(r"`([^`]+)`", layer.UPGRADE):
        check(f"the door names {command}", command in flat, flat)


def test_the_guide_counts_its_warnings_and_names_both_readers():
    text = _read(ROOT / "echolot" / "guide" / "overview.md")
    check("no count that went stale", "Three things" not in text, text)
    finding = text.split("## From a finding to the code", 1)[1].split("\n## ", 1)[0]
    check("the outlier paragraphs sit under From a finding to the code",
          "`main_thread_outlier` is not `main_thread_block` again" in finding, finding)
    check("reflect reads Codex too",
          "Claude Code and Codex have a reader" in " ".join(text.split()), text)


def test_the_manual_recording_stops_instead_of_pulling_a_stale_trace():
    text = _read(REFS / "collect.md")
    check("the old trace is deleted first",
          "adb shell rm -f /data/misc/perfetto-traces/t.pftrace" in text, text)
    check("a perfetto that fails stops the script", "< /tmp/trace.cfg || exit 1" in text, text)
    check("the package reaches atrace_apps", 'atrace_apps: "$PKG"' in text
          and "<<'EOF'" not in text, text)


# --- who init sets up, who runs the loop, what detectors need (#247) ------------

DETECTORS_MD = ROOT / "docs" / "detectors.md"


def test_the_sample_detector_would_pass_the_check_on_shipped_ones():
    from tests.test_in_rows import _LIMIT_TAIL
    sample = re.search(r"```sql\n(-- @id: my_detector.*?)```", _read(DETECTORS_MD), re.S)
    check("the page opens with a sample detector", sample, DETECTORS_MD)
    check("which ends in its LIMIT", _LIMIT_TAIL.search(sample.group(1)), sample.group(1))


def test_the_intervals_sample_stands_for_what_its_rows_count():
    """Whole slices for a row that counts whole slices, as gc_pressure does."""
    text = _read(DETECTORS_MD)
    block = re.search(r"```sql\n(ORDER BY .*?)```", text, re.S).group(1)
    gc = _read(ROOT / "echolot" / "sql" / "detectors" / "gc_pressure.sql")
    check("the sample's tail is gc_pressure's", "ORDER BY total_ms DESC" in block
          and "ORDER BY total_ms DESC" in gc, block)


def test_detectors_md_names_both_readers_of_claimed_name():
    flat = _flat(DETECTORS_MD)
    check("app_init reads _claimed_name too", "`repeated_work` and `app_init`" in flat, flat)
    check("no 'one detector with no mask'", "the one detector with no mask" not in flat, flat)
    check("the intervals item fails the build",
          "The first three things below fail the build" in flat, flat)


def test_the_main_thread_leaves_markers_to_the_loop():
    flat = _flat(GUIDE_HUNT)
    check("no mark --apply before the hand-off",
          "then `echolot mark --apply`, then one re-record" not in flat, flat)
    check("the brief carries it instead", "`Instrumentation: none`" in flat, flat)


def test_the_readme_hands_the_loop_to_a_subagent_where_the_host_can():
    flat = _flat(ROOT / "README.md")
    check("a plain init finds only agents the project has",
          "points only at agents whose files the project already has" in flat, flat)
    check("Codex without the plugin is set up by name", "echolot init --for agents,codex" in flat,
          flat)
    check("the main context only where no subagent can start",
          "where it cannot, the loop runs in your main context" in flat, flat)


def test_every_text_lists_what_stands_on_thread_state():
    for path in (ROOT / "docs" / "collecting.md", REFS / "collect.md",
                 ROOT / "echolot" / "runner.py"):
        flat = _flat(path)
        check(f"{path.name}: io_wait goes silent too", "io_wait" in flat, path)
