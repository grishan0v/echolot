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
