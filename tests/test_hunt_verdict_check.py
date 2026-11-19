"""The verdict checked on its own: `hunt --verdict <file>` with no `--done`.

The agent that wrote the file has the evidence in front of it, so it is the one
that can put a refusal right, and it can only do that if it can find the
refusal before it returns. The check is the one closing makes and nothing else:
it records nothing and the investigation stays open, because closing is not the
agent's. These cases pin that it is the same check, that it leaves no trace in
the investigation, and that it says the same things the closing says.

    python -m pytest tests/test_hunt_verdict_check.py
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from echolot import hunt as hunt_mod
from tests.support import check
from tests.test_hunt_verdict import (
    ROUND_1,
    ROUND_2,
    VERDICT,
    hunt_with,
    leave_markers,
    run,
    write,
)


def snapshot(project: Path) -> dict[str, bytes]:
    """Every file under `.echolot` but the run log, which every command writes."""
    root = project / ".echolot"
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and p.relative_to(root).parts[0] != "log"}


def problem_lines(err: str) -> list[str]:
    """The entries of a refusal: the indented lines under its heading."""
    return [ln for ln in err.splitlines() if ln.startswith("  ") and ln.strip()]


def test_a_sound_verdict_would_be_accepted_and_nothing_is_recorded(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    before = snapshot(p)
    code, out, err = run(p, "hunt", "--verdict", write(p, VERDICT))
    check("exit 0", code == 0, err)
    check("says it would be accepted, and how much it looked at",
          "would be accepted" in out and "1 cause(s)" in out and "3 row(s)" in out, out)
    check("says nothing was recorded and that it is open",
          "Nothing was recorded" in out and "still open" in out, out)
    check("and does not suggest closing, which the agent reading it must not do",
          "--done" not in out and "close" not in out, out)
    check("not one file under .echolot changed", snapshot(p) == before)
    h = hunt_mod.load(p)
    check("still open, with no verdict and no conclusion",
          h["status"] == "open" and h.get("conclusion") is None
          and "verdict" not in h and "verdict_check" not in h and "closed_at" not in h, h)
    code, _, err = run(p, "hunt", "--done", "--verdict", "verdict.json")
    check("and the same file then closes it, as it was",
          code == 0 and hunt_mod.load(p)["verdict"] == VERDICT, err)


def test_a_verdict_that_would_be_refused_has_every_entry_named_and_exits_two(
        tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    bad = copy.deepcopy(VERDICT)
    bad["causes"][0]["evidence"][1]["report"] = 7
    bad["causes"][0]["evidence"][0]["location"] = "not_a_row"
    bad["ruled_out"] = ["fine", ""]
    before = snapshot(p)
    code, out, err = run(p, "hunt", "--verdict", write(p, bad))
    check("exit 2", code == 2, (code, out, err))
    check("it says would, and how many", "would be refused" in err and "3 problem(s)" in err, err)
    for want in ("causes[0].evidence[0]", "not_a_row", "causes[0].evidence[1]: report 7",
                 "ruled_out[1]"):
        check(f"names {want}", want in err, err)
    check("and says nothing was recorded and the investigation is open",
          "Nothing was recorded" in err and "still open" in err, err)
    check("nothing was written", snapshot(p) == before)
    check("and nothing concluded", "concluded" not in out, out)


def test_the_check_and_the_closing_refuse_the_same_entries_in_the_same_words(
        tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    bad = copy.deepcopy(VERDICT)
    bad["inside"]["report"] = 9
    bad["causes"][0]["expected_ms"] = [400, 300]
    bad["suggestions"] = [{"fix": ""}]
    name = write(p, bad)
    _, _, checked = run(p, "hunt", "--verdict", name)
    _, _, closed = run(p, "hunt", "--done", "--verdict", name)
    check("three entries, the same ones",
          len(problem_lines(checked)) == 3 and problem_lines(checked) == problem_lines(closed),
          (checked, closed))
    check("and the heading is the only difference",
          "would be refused" in checked and "was refused" in closed, (checked, closed))


def test_a_check_can_read_the_verdict_from_stdin(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    code, out, err = run(p, "hunt", "--verdict", "-", stdin=json.dumps(VERDICT))
    check("accepted", code == 0 and "would be accepted" in out, err)
    code, _, err = run(p, "hunt", "--verdict", "-", stdin="{not json")
    check("and refused there too", code == 2 and "not JSON" in err, err)
    check("the investigation is open throughout", hunt_mod.load(p)["status"] == "open")


def test_a_file_that_is_not_there_or_not_json_exits_two_and_leaves_the_hunt_alone(
        tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1)
    before = snapshot(p)
    code, _, err = run(p, "hunt", "--verdict", "no-such-file.json")
    check("missing", code == 2 and "cannot read the verdict" in err, err)
    code, _, err = run(p, "hunt", "--verdict", write(p, "Store.fill, confidence high"))
    check("prose", code == 2 and "not JSON" in err, err)
    check("untouched", snapshot(p) == before)


def test_a_check_needs_an_open_investigation_and_says_so_as_closing_does(
        tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    code, _, err = run(empty, "hunt", "--verdict", "-", stdin="{}")
    check("no hunt", code == 1 and "no investigation is open" in err, err)

    p = hunt_with(tmp_path / "closed", ROUND_1, ROUND_2)
    run(p, "hunt", "--done", "by hand")
    before = snapshot(p)
    code, _, err = run(p, "hunt", "--verdict", write(p, VERDICT))
    check("a concluded one", code == 1 and "already concluded" in err, err)
    check("and it stays as it was", snapshot(p) == before)


def test_the_markers_in_the_sources_are_counted_and_never_a_reason_to_refuse(
        tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    leave_markers(p)
    before = snapshot(p)
    code, _, err = run(p, "hunt", "--verdict", write(p, VERDICT))
    check("still accepted", code == 0, err)
    check("and said where the agent reads it, while it can still act on it",
          "1 AGENTTMP_ marker(s) still in" in err and "the verdict says removed" in err
          and "closing records both" in err, err)
    check("nothing recorded", snapshot(p) == before)

    q = hunt_with(tmp_path / "clean", ROUND_1, ROUND_2)
    code, out, err = run(q, "hunt", "--verdict", write(q, VERDICT))
    check("a clean tree says so and has nothing to add",
          code == 0 and "no temporary markers in the sources" in out
          and "the verdict says" not in err, (out, err))


def test_a_claim_of_left_markers_that_are_not_there_is_said_in_both_modes(
        tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    claim: dict[str, Any] = {**VERDICT, "cleanup": "left"}
    code, _, err = run(p, "hunt", "--verdict", write(p, claim))
    check("the check says so", code == 0 and "the verdict says left" in err
          and "closing records both" in err, err)
    code, _, err = run(p, "hunt", "--done", "--verdict", "verdict.json")
    check("and so does the closing", code == 0
          and "the verdict says left; the record holds both" in err, err)
    check("and the record holds both halves",
          hunt_mod.load(p)["verdict_check"]["cleanup"]["agrees"] is False)


def test_the_hunt_a_check_ran_in_is_the_hunt_that_closes_later(tmp_path: Path) -> None:
    """A check changes nothing about the investigation: `--resume` has nothing
    to catch up on, and the recap reads it as untouched."""
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    before = hunt_mod.load(p)
    run(p, "hunt", "--verdict", write(p, VERDICT))
    check("the record is the record", hunt_mod.load(p) == before)
