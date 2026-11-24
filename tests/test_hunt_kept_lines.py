"""The tagged lines `mark --remove` leaves, counted wherever echolot says what
is left in the code (#703).

An agent marked a call by hand: its `beginSection` on a line of its own, in
the shape `--apply` writes, and the end folded onto the call,
`try { parse() } finally { android.os.Trace.endSection() } // echolot:mark`.
`mark --remove` took the begin out and left the wrap, naming it. Everything
else counted by the prefix, which the wrap does not carry, and by the lines
`--remove` deletes, which it is not: the page, `status`, the cleanup check
of `hunt --verdict` and `hunt --done`, and the note when the next
investigation opens all said the code was clean, over an `endSection()` that
ends whatever section the app has open on that thread.

These cases hold each reader to the same count, with the place of each line,
before the removal and after it, and the count to what `--remove` leaves.

    python -m pytest tests/test_hunt_kept_lines.py
"""

from __future__ import annotations

from pathlib import Path

from echolot import hunt as hunt_mod
from echolot import mark as mark_mod
from echolot import state
from echolot import verdict as verdict_mod
from echolot.mark import Markers
from tests.support import check
from tests.test_hunt_verdict import CONFIG, ROUND_1, ROUND_2, VERDICT, hunt_with, run, write

SOURCE = "app/src/main/java/com/example/app/Store.kt"
# `--apply`'s block around `read()`, then a marker an agent wrote by hand
# around `parse()`: the begin a line of its own, the end on the call's line.
MARKED = ("package com.example.app\n\nfun load() {\n"
          '    android.os.Trace.beginSection("AGENTTMP_load") // echolot:mark\n'
          "    try { // echolot:mark\n"
          "        read()\n"
          "    } finally { android.os.Trace.endSection() } // echolot:mark\n"
          '    android.os.Trace.beginSection("AGENTTMP_parse") // echolot:mark\n'
          "    try { parse() } finally { android.os.Trace.endSection() } // echolot:mark\n"
          "}\n")
BEFORE = f"{SOURCE}:9"   # the wrap, among the lines --remove takes out
AFTER = f"{SOURCE}:5"    # the wrap once they are gone, where --remove names it


def marked(root: Path) -> Path:
    path = root / SOURCE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MARKED, encoding="utf-8")
    return path


def test_the_count_is_what_mark_remove_leaves(tmp_path: Path) -> None:
    marked(tmp_path)
    left = Markers(tmp_path).leftovers()
    check("both begins are markers --remove takes out", (left["markers"], left["removable"]) == (2, 2), left)
    check("with the three lines of the block and the hand begin", left["lines"] == 4, left)
    check("and the wrap is named by its place", left["kept"] == [BEFORE], left)

    _, kept = mark_mod.remove(tmp_path)
    check("--remove leaves the wrap and names it", [(rel, n) for rel, n, _ in kept] == [(SOURCE, 5)], kept)
    left = Markers(tmp_path).leftovers()
    check("then the wrap is all that is counted, where --remove named it",
          (left["markers"], left["removable"], left["lines"], left["kept"]) == (0, 0, 0, [AFTER]), left)
    check("the files of the markers and of the lines --remove takes out hold none of it",
          left["files"] == [], left)


def test_a_line_remove_leaves_is_one_rule_in_both(tmp_path: Path) -> None:
    """Every tagged line `--remove` does not delete is one it names, and one
    `leftovers` counts: prefixed or not, Java or Kotlin, CRLF or not."""
    rows = ['    android.os.Trace.beginSection("AGENTTMP_a"); work(); // echolot:mark',
            "    try { work() } finally { android.os.Trace.endSection() } // echolot:mark",
            "    android.os.Trace.endSection() // echolot:mark AppTheme {",
            "    work() // echolot:mark",
            '    android.os.Trace.beginSection("AGENTTMP_b") // echolot:mark',
            "    android.os.Trace.endSection(); // echolot:mark",
            "    try { // echolot:mark",
            "    } finally { android.os.Trace.endSection() } // echolot:mark"]
    path = tmp_path / "app/src/main/java/com/example/app/Feed.java"
    path.parent.mkdir(parents=True)
    path.write_bytes(("class Feed {\r\n" + "\r\n".join(rows) + "\r\n}\r\n").encode("utf-8"))
    rel = "app/src/main/java/com/example/app/Feed.java"
    check("the first four carry more than a marker, by the same rule",
          [mark_mod.is_kept_line(r) for r in rows] == [True] * 4 + [False] * 4)
    check("counted at their lines", Markers(tmp_path).leftovers()["kept"] == [f"{rel}:{n}" for n in (2, 3, 4, 5)])
    _, kept = mark_mod.remove(tmp_path)
    check("and the same ones are what --remove leaves", [n for _, n, _ in kept] == [2, 3, 4, 5], kept)
    check("still counted there", Markers(tmp_path).leftovers()["kept"] == [f"{rel}:{n}" for n in (2, 3, 4, 5)])


def test_status_says_they_go_by_hand_and_where(tmp_path: Path) -> None:
    marked(tmp_path)
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    hunt = {"n": 1, "status": "open", "question": "q"}
    said = hunt_mod.recap(hunt, state.project_state(tmp_path), tmp_path)
    check("the markers, on a line of their own",
          "  ! 2 AGENTTMP_ marker(s) still in 1 file(s) — `echolot mark --remove` takes out 2" in said, said)
    check("and the wrap, on its own, with its place",
          f"  ! 1 line(s) tagged `// echolot:mark` carry something besides the marker, so `echolot mark "
          f"--remove` leaves them — take the marker out by hand, keeping the code it wraps: {BEFORE}" in said, said)

    mark_mod.remove(tmp_path)
    said = hunt_mod.recap(hunt, state.project_state(tmp_path), tmp_path)
    notes = [line for line in said if line.startswith("  ! ")]
    check("after the removal the wrap is the one thing said, never nothing",
          len(notes) == 1 and notes[0].endswith(f"keeping the code it wraps: {AFTER}"), said)


def test_one_sentence_names_five_places_and_counts_the_rest() -> None:
    kept = [f"a.kt:{n}" for n in range(1, 8)]
    note = hunt_mod.leftover_lines({"markers": 0, "removable": 0, "lines": 0, "files": [], "kept": kept,
                                    "prefix": "AGENTTMP_"})
    check("seven lines, five places, two more",
          note == ["7 line(s) tagged `// echolot:mark` carry something besides the marker, so `echolot mark "
                   "--remove` leaves them — take the marker out by hand, keeping the code it wraps: a.kt:1, "
                   "a.kt:2, a.kt:3, a.kt:4, a.kt:5 and 2 more"], note)
    check("a clean tree has nothing to say", hunt_mod.leftover_lines(
        {"markers": 0, "removable": 0, "lines": 0, "files": [], "kept": [], "prefix": "AGENTTMP_"}) == [])


def test_a_new_investigation_names_each_line_that_goes_by_hand(tmp_path: Path) -> None:
    marked(tmp_path)
    mark_mod.remove(tmp_path)
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    code, _, err = run(tmp_path, "hunt", "cold start 2.1 s")
    check("it opens", code == 0, err)
    check("and says the wrap is left, how it goes, and where",
          "[!] 1 line(s) tagged `// echolot:mark` left by the previous investigation carry something besides "
          "the marker, so `echolot mark --remove` leaves them. Take the marker out of each by hand, keeping "
          f"the code it wraps:\n    {AFTER}\n" in err, err)
    check("with no marker sentence over it, since none is there", "marker(s) left in" not in err, err)


def test_a_new_investigation_says_both_and_lists_twelve(tmp_path: Path) -> None:
    marked(tmp_path)
    many = tmp_path / "app/src/main/java/com/example/app/Feed.kt"
    many.write_text("fun feed() {\n" + "".join(
        f"    try {{ step{n}() }} finally {{ android.os.Trace.endSection() }} // echolot:mark\n"
        for n in range(13)) + "}\n", encoding="utf-8")
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    code, _, err = run(tmp_path, "hunt", "cold start 2.1 s")
    check("the markers, as before", code == 0 and "[!] 2 AGENTTMP_ marker(s) left in 1 file(s) by the previous "
          "investigation." in err and "`echolot mark --remove` takes out 2." in err, err)
    check("then the fourteen lines --remove leaves", "[!] 14 line(s) tagged `// echolot:mark` left by the "
          "previous investigation" in err, err)
    places = [line.strip() for line in err.splitlines() if line.startswith("    app/")]
    check("twelve by their place, in the order they are counted, the rest a count",
          places == Markers(tmp_path).leftovers()["kept"][:12] and "    … and 2 more" in err, (places, err))


def test_the_verdict_check_and_closing_name_them_and_record_them(tmp_path: Path) -> None:
    p = hunt_with(tmp_path, ROUND_1, ROUND_2)
    marked(p)
    mark_mod.remove(p)
    code, out, err = run(p, "hunt", "--verdict", write(p, VERDICT))
    check("the check still accepts", code == 0, err)
    check("and never says the sources are clean", "no temporary markers in the sources" not in out, out)
    check("it names the wrap and its place where the agent reads",
          "[!] 1 line(s) tagged `// echolot:mark` carry something besides the marker" in err
          and f"keeping the code it wraps: {AFTER}" in err, err)
    check("and holds it against the agent's word", "the verdict says removed; closing records both" in err, err)

    code, out, err = run(p, "hunt", "--done", "--verdict", write(p, VERDICT))
    check("closing accepts too, saying the same", code == 0 and f"keeping the code it wraps: {AFTER}" in err, err)
    cleanup = hunt_mod.load(p)["verdict_check"]["cleanup"]
    check("the record holds the wrap by its place", cleanup["found"]["kept"] == [AFTER], cleanup)
    check("and that the agent's word disagrees", cleanup["agrees"] is False, cleanup)

    code, out, err = run(p, "hunt", "--show", "1")
    check("looking back says it went by hand, and where", code == 0 and
          "cleanup: removed — echolot found 1 line(s) tagged `// echolot:mark` that `echolot mark --remove` "
          f"leaves, to take out by hand: {AFTER}" in out, out)


def test_the_agents_word_is_held_against_the_lines_remove_leaves() -> None:
    left = {"markers": 0, "lines": 0, "kept": ["a.kt:3"]}
    check("removed is not so", verdict_mod.cleanup_agrees("removed", left) is False)
    check("nor is none_added", verdict_mod.cleanup_agrees("none_added", left) is False)
    check("left is", verdict_mod.cleanup_agrees("left", left) is True)
    check("a record from before the lines were counted reads as it did",
          verdict_mod.cleanup_agrees("removed", {"markers": 0, "lines": 0}) is True)


def test_looking_back_says_both_kinds() -> None:
    check_ = {"cleanup": {"found": {"markers": 2, "removable": 2, "lines": 4, "files": [SOURCE],
                                    "kept": [BEFORE], "prefix": "AGENTTMP_"}}}
    check("the markers, then the line that goes by hand",
          verdict_mod._cleanup_line("removed", check_) == (
              f"cleanup: removed — echolot found 2 AGENTTMP_ marker(s) in 1 file(s) still in the sources, and 1 "
              f"line(s) tagged `// echolot:mark` that `echolot mark --remove` leaves, to take out by hand: {BEFORE}"))
