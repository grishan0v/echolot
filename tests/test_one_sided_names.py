#!/usr/bin/env python3
"""Slice names in every run of one side and in no run of the other (#610).

`compare` pairs detector rows, and a slice no detector reads never reached
it. A busy loop planted on the main thread with no section of its own gave
no row on either side, and the one name only the slow build had — the JIT
compilation of the method with the loop in it — was found by agents who
set two sets of `echolot names` against each other in a script.

What is pinned here: how a name is folded so that two builds' traces share
it (`names.slice_across_builds`), what `analyze` keeps of the names
(`report._merge_names`), and which of them `compare` lists, where, and when
it says it did not look. The doctor check of the same claim runs the scene
`fixture.blind_main` draws.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import compare as compare_mod  # noqa: E402
from echolot import fixture, moves  # noqa: E402
from echolot import names as names_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.test_compare import compare, det, report, row  # noqa: E402

JIT = ("JIT compiling void com.example.app.feature.ListViewModel.prepare() "
       "(kind=Baseline) from /data/app/~~#/com.example.app-#/base.apk")


# --- folding -----------------------------------------------------------------

def test_a_name_is_folded_so_two_builds_share_it() -> None:
    fold = names_mod.slice_across_builds
    check("the install directory, picked anew at every install, is folded",
          fold(fixture.jit_name(fixture.JIT_NEW, fixture.INSTALL[0]))
          == fold(fixture.jit_name(fixture.JIT_NEW, fixture.INSTALL[1])) == JIT,
          fold(fixture.jit_name(fixture.JIT_NEW, fixture.INSTALL[0])))
    check("and so is the one Android 10 and below gave",
          fold("JIT compiling void a.B.c() from /data/app/com.example.app-Q2x8==/base.apk")
          == "JIT compiling void a.B.c() from /data/app/com.example.app-#/base.apk")
    check("a fence's number, which runtime_name leaves, is folded",
          fold("waiting for HWC release 313") == fold("waiting for HWC release 326"))
    check("so is a line number: a build that moved the code is not a new lock",
          fold("Lock contention on a monitor lock (owner tid: 4455) at void "
               "a.Store.save()(Store.kt:41)")
          == fold("Lock contention on a monitor lock (owner tid: 13533) at void "
                  "a.Store.save()(Store.kt:43)"))
    check("a name runtime_name folded first folds to the same",
          fold(names_mod.runtime_name("Lock contention on 0x7f3a2b"))
          == fold("Lock contention on 0x7f3a2b") == "Lock contention on 0x#")
    check("the project's own markers keep their digits",
          (fold("AGENTTMP_fill_v4", keep="AGENTTMP_"),
           fold("loadMenu_v2", names={"loadMenu_v2"}))
          == ("AGENTTMP_fill_v4", "loadMenu_v2"))


# --- what analyze keeps --------------------------------------------------------

def names(runs: list[dict[str, float]], thread: str = "Jit thread pool") -> dict:
    """A set's `slice_names`, from each run's names and how long each took."""
    reports = [{report_mod.NAMES_ALL: {
        name: {"total_ms": ms, "max_ms": ms, "threads": [thread]}
        for name, ms in run.items()}} for run in runs]
    out = report_mod._merge_names(reports)
    assert out is not None
    return out


def test_the_merge_keeps_the_steady_names_and_sees_every_one() -> None:
    section = names([{"steady": 4.0, "short": 0.2, "twice": 3.0, "once": 9.0},
                     {"steady": 6.0, "short": 0.2, "twice": 3.0},
                     {"steady": 5.0, "short": 0.2}])
    kept = {r["name"]: r for r in report_mod.names_rows(section)}
    check("kept: a name in all runs but one or more, a ms or more a run",
          sorted(kept) == ["steady", "twice"], sorted(kept))
    check("with its runs, its median a run, its longest and its thread",
          kept["steady"] == {"name": "steady", "runs": "3/3", "total_ms": 5.0,
                             "max_ms": 6.0, "threads": "Jit thread pool"},
          kept["steady"])
    seen = report_mod.names_seen(section)
    check("seen: every name any run held, whatever it took",
          seen == {report_mod.name_digest(n) for n in ("steady", "short", "twice", "once")}
          and (section["names"], section["kept"], section["runs"]) == (4, 2, 3), section)
    again = names([{"steady": 4.0, "short": 0.2, "twice": 3.0, "once": 9.0},
                   {"steady": 6.0, "short": 0.2, "twice": 3.0},
                   {"steady": 5.0, "short": 0.2}])
    check("the same names make the same section, byte for byte", again == section)


def test_a_set_with_a_repeat_without_names_has_none() -> None:
    reports = [{report_mod.NAMES_ALL: {"a": {"total_ms": 2.0, "max_ms": 2.0,
                                             "threads": ["main"]}}}, {}]
    check("a report written before the section existed is unknown, not empty",
          report_mod._merge_names(reports) is None)
    check("and the names of the one that had them are taken out all the same",
          report_mod.NAMES_ALL not in reports[0])


def test_a_set_read_back_and_merged_again_drops_the_section_of_one_run() -> None:
    one = report([det("x", [])], runs=1)
    one[report_mod.SLICE_NAMES] = names([{"a": 2.0}])
    merged = report_mod.aggregate([json.loads(json.dumps(one)) for _ in range(3)])
    check("a repeat's own section is of one run, not of the set",
          report_mod.SLICE_NAMES not in merged, merged.get(report_mod.SLICE_NAMES))


# --- what compare lists --------------------------------------------------------

def side(runs: list[dict[str, float]], detectors: list[dict] | None = None) -> dict:
    rep = report(detectors or [det("x", [])], runs=len(runs))
    rep[report_mod.SLICE_NAMES] = names(runs)
    return rep


def listed(cmp: dict, where: str) -> list[str]:
    return [r["name"] for r in cmp["names"][where]]


def test_a_name_in_every_run_of_one_side_and_none_of_the_other_is_listed() -> None:
    before = side([{"common": 30.0, "gone": 8.0}] * 3)
    after = side([{"common": 31.0, JIT: 6.0}] * 3)
    cmp = compare(before, after)
    check("the new compilation is listed after, the gone name before",
          (listed(cmp, "after"), listed(cmp, "before")) == ([JIT], ["gone"]), cmp["names"])
    new = cmp["names"]["after"][0]
    check("with its runs against the other side's, its time and its thread",
          (new["runs"], new["other"], new["total_ms"], new["threads"])
          == ("3/3", "0/3", 6.0, "Jit thread pool"), new)
    check("and counted in the summary",
          (cmp["summary"]["only_after"], cmp["summary"]["only_before"]) == (1, 1))
    text = compare_mod.to_markdown(cmp)
    check("the comparison has the section, and the header counts it",
          "## Names on one side only" in text
          and f"| {JIT} | after | 3/3 against 0/3 | 6 | 6 | Jit thread pool |" in text
          and "names only after **1** · only before **1**" in text, text)


def test_a_name_the_other_side_has_under_the_floor_is_not_listed() -> None:
    before = side([{"grown": 0.3}] * 3)
    after = side([{"grown": 6.0}] * 3)
    check("grown from 0.3 ms a run is not new: the other side had it",
          listed(compare(before, after), "after") == [])


def test_a_name_missing_from_a_run_is_listed_only_from_five_runs() -> None:
    three = compare(side([{}] * 3), side([{"x": 5.0}, {"x": 5.0}, {}]))
    check("2 of 3 runs is not every run", listed(three, "after") == [])
    five = compare(side([{}] * 5), side([{"x": 5.0}] * 4 + [{}]))
    check("4 of 5 is: all but one", listed(five, "after") == ["x"], five["names"])
    four = compare(side([{}] * 5), side([{"x": 5.0}] * 3 + [{}] * 2))
    check("3 of 5 is not", listed(four, "after") == [])


def test_too_few_runs_a_side_is_said_and_nothing_is_listed() -> None:
    cmp = compare(side([{}] * 2), side([{"x": 5.0}] * 3))
    check("with two runs before nothing is listed",
          cmp["names"]["too_few"] and listed(cmp, "after") == [], cmp["names"])
    text = compare_mod.to_markdown(cmp)
    check("and the comparison says it did not look, and why",
          "**Names on one side only:** not looked for with 2 run(s) before and 3 after; "
          "it takes 3 a side" in text, text)


def test_a_report_without_names_is_compared_without_the_section() -> None:
    cmp = compare(report([det("x", [])]), side([{"x": 5.0}] * 3))
    check("no names on one side: nothing to read the other against",
          cmp["names"] is None and cmp["summary"]["only_after"] == 0)
    text = compare_mod.to_markdown(cmp)
    check("and the comparison reads as it always did",
          "Names on one side only" not in text, text)
    check("--names says why there is nothing",
          "written before echolot kept the window's slice names" in compare_mod.names_view(cmp))


def test_a_name_the_table_has_as_new_is_left_out() -> None:
    before = side([{}] * 3, [det("slow_main", [])])
    after = side([{"loadAll": 40.0, JIT: 6.0}] * 3,
                 [det("slow_main", [row("loadAll", 40.0)])])
    cmp = compare(before, after)
    check("the table has loadAll as new; the names list only the compilation",
          {r["location"]: r["change"] for r in cmp["rows"]} == {"loadAll": "appeared"}
          and listed(cmp, "after") == [JIT], cmp["names"])


def test_the_comparison_shows_ten_a_side_and_names_the_rest() -> None:
    many = {f"new_{chr(97 + i)}": 20.0 - i for i in range(13)}
    cmp = compare(side([{}] * 3), side([many] * 3))
    text = compare_mod.to_markdown(cmp)
    check("ten of thirteen in the table",
          text.count(" | after | 3/3 against 0/3 |") == compare_mod.NAMES_SHOWN, text)
    check("and the rest said, with where to find them",
          "… and 3 more after: `compare --names` lists every one" in text, text)
    whole = compare_mod.names_view(cmp)
    check("--names lists all thirteen",
          whole.count(" | after | 3/3 against 0/3 |") == 13, whole)


def test_the_plain_reading_names_the_work_only_one_side_has() -> None:
    before = side([{"common": 30.0}] * 3)
    after = side([{"common": 30.0, JIT: 6.0}] * 3)
    cmp = compare(before, after)
    note = next((n for n in moves.read(before, after, cmp)["notes"] if n["id"] == "names"),
                None)
    check("a note says it, by name, with its time a run",
          note is not None and note["title"] == "Work only one side has."
          and f"In every run after and in none before: `{JIT}` (6 ms a run, "
              f"Jit thread pool)." in note["text"], note)


def _note(before: dict, after: dict) -> str:
    cmp = compare(before, after)
    return next(n for n in moves.read(before, after, cmp)["notes"] if n["id"] == "names")["text"]


def test_the_plain_reading_says_how_many_launches_a_name_was_in() -> None:
    """#851: from five runs a name may miss one, and the table says `4/5`.
    The plain reading said "In every launch after" of it all the same."""
    before = side([{"common": 30.0}] * 5)
    text = _note(before, side([{"common": 30.0, "wait": 2.0}] * 4 + [{"common": 30.0}]))
    check("all of them in 4 of 5: the sentence says so", text.startswith(
        "In 4 of 5 runs after and in none before: `wait` (2 ms a run, Jit thread pool)."), text)
    text = _note(before, side([{"common": 30.0, JIT: 6.0, "wait": 2.0}] * 4 + [{"common": 30.0, JIT: 6.0}]))
    check("one in every run and one in 4 of 5: both said, and the one that missed a run by name",
          text.startswith(f"In every run after, or all but one, and in none before: `{JIT}` (6 ms a run, "
                          "Jit thread pool), `wait` (2 ms a run in 4 of 5, Jit thread pool)."), text)
    text = _note(before, side([{"common": 30.0, JIT: 6.0}] * 5))
    check("every run is every run", text.startswith("In every run after and in none before:"), text)


def test_the_names_past_the_ones_said_count_too() -> None:
    """The names after the third are said as "and N more": what the sentence
    opens with is true of them as well."""
    every = {f"every_{c}": 20.0 - i for i, c in enumerate("abc")}
    text = _note(side([{}] * 5), side([{**every, "late_one": 2.0}] * 4 + [every]))
    check("three in every run said, a fourth in 4 of 5 behind `1 more`",
          text.startswith("In every run after, or all but one, and in none before:")
          and "in 4 of 5" not in text and ", and 1 more." in text, text)


# --- from traces -------------------------------------------------------------

CONFIG = {
    "project": {"package": fixture.APP_NAME, "process": fixture.APP_NAME},
    "scenario": {"name": "blind", "start": {"name": "bindApplication"},
                 "end": {"name": "Screen.firstFrame"}},
}


def _set(tmp_path: Path, loop: float, runs: range, install: str,
         jit: tuple[str, ...]) -> tuple[dict, list[Path]]:
    """Three starts of one build, merged the way `analyze` merges them."""
    paths, reports = [], []
    for run in runs:
        path = tmp_path / f"blind-{loop}-{run}.perfetto-trace"
        path.write_bytes(fixture.blind_main(loop, run=run, install=install, jit=jit))
        paths.append(path)
        reports.append(analyze_trace(path, Config(CONFIG)))
    return report_mod.aggregate(reports), paths


def test_the_compilation_behind_a_busy_loop_is_the_one_name_after(tmp_path: Path) -> None:
    before, _ = _set(tmp_path, 100, range(0, 3), fixture.INSTALL[0], (fixture.JIT_BOTH,))
    after, _ = _set(tmp_path, 300, range(10, 13), fixture.INSTALL[1],
                    (fixture.JIT_BOTH, fixture.JIT_NEW))
    cmp = compare(before, after, temp_prefix="AGENTTMP_")
    check("of every name in the window, only the method with the loop is after only: "
          "new vsyncs, fences, a pool's thread and another install are not",
          (listed(cmp, "after"), listed(cmp, "before")) == ([JIT], []), cmp["names"])
    check("and nothing of it went into the report but the section",
          report_mod.NAMES_ALL not in after and after[report_mod.SLICE_NAMES]["runs"] == 3)


def test_compare_names_prints_them_and_writes_them(tmp_path: Path, capsys, monkeypatch) -> None:
    before, _ = _set(tmp_path, 100, range(0, 3), fixture.INSTALL[0], (fixture.JIT_BOTH,))
    after, _ = _set(tmp_path, 300, range(10, 13), fixture.INSTALL[1],
                    (fixture.JIT_BOTH, fixture.JIT_NEW))
    for name, rep in (("before.json", before), ("after.json", after)):
        (tmp_path / name).write_text(report_mod.to_json(rep), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code = main(["compare", "before.json", "after.json", "--names", "-o", "out"])
    printed = capsys.readouterr().out
    check("--names prints the section and nothing of the table",
          code == 0 and "## Names on one side only" in printed and JIT in printed
          and "## What moved" not in printed, printed)
    written = json.loads((tmp_path / "out" / "comparison.json").read_text(encoding="utf-8"))
    check("and comparison.json has them under `names`",
          [r["name"] for r in written["names"]["after"]] == [JIT], written["names"])
