#!/usr/bin/env python3
"""A scenario's anchor that no code writes is said before anything is recorded (#500).

The case behind it: an end anchor a temporary build had written two months
earlier, in no file of the code any more. `echolot` called the config sound,
`collect -n 5` recorded five traces, and only `analyze` said the window was
the whole trace, while the trace held the same moment under the name the
code writes today. Each test is a scratch project, its sources and its
echolot.yml; nothing here needs a phone.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anchors, recorder, runner  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_fake_adb import ACTIVITY, PACKAGE, FakeAdb  # noqa: E402

MARKS = "app/src/main/java/com/example/app/Marks.kt"
CODE = """package com.example.app

object Marks {
    const val LIST_SHOWN = "list_shown"
}

class ListScreen {
    fun shown() {
        Trace.beginAsyncSection(Marks.LIST_SHOWN, 0)
        val title = "list"
        val key = "screen_${kind}_ready"
    }
}
"""


def _project(root: Path, scenario: str, code: str = CODE, more: str = "") -> Path:
    (root / MARKS).parent.mkdir(parents=True, exist_ok=True)
    (root / MARKS).write_text(code, encoding="utf-8")
    path = root / "echolot.yml"
    path.write_text(f"project: {{package: {PACKAGE}}}\n{scenario}{more}", encoding="utf-8")
    return path


def _notes(root: Path, scenario: str, **kw) -> list[anchors.Note]:
    return anchors.check(Config.load(_project(root, scenario)), **kw)


def _one(end: str, start: str = "bindApplication") -> str:
    return f"scenario:\n  name: coldStart\n  start: {{name: \"{start}\"}}\n  end: {{name: \"{end}\"}}\n"


# --- what the code holds -------------------------------------------------------

def test_an_anchor_the_code_no_longer_writes_names_the_one_it_does(tmp_path: Path) -> None:
    notes = _notes(tmp_path, _one("list_shown_mark"))
    check("one note, the end's", [(n.key, n.glob) for n in notes] == [("end", "list_shown_mark")],
          notes)
    note = notes[0]
    check("the code's near name, with where it is", note.kind == "near"
          and note.near == (("list_shown", f"{MARKS}:4"),), note)
    check("said as the code's", note.text() ==
          f"end anchor `list_shown_mark` is in no Kotlin or Java string here; the code has "
          f"`list_shown` ({MARKS}:4)", note.text())


@pytest.mark.parametrize("end", [
    "list_shown",               # the constant's value, written through `Marks.LIST_SHOWN`
    "list_shown*",              # a glob it matches
    "screen_home_ready",        # put together at run time from `"screen_${kind}_ready"`
])
def test_a_name_the_code_holds_or_puts_together_is_not_said(tmp_path: Path, end: str) -> None:
    check(f"{end}: nothing said", _notes(tmp_path, _one(end)) == [], end)


@pytest.mark.parametrize("anchor", [
    "bindApplication", "activityStart", "activityResume", "Choreographer#doFrame*",
    "performCreate:com.example.app.MainActivity", "reportFullyDrawn*", "RV Scroll",
    "Compose:recompose", "Startup", "traversal",
])
def test_the_platforms_sections_are_in_no_code_and_never_said(tmp_path: Path, anchor: str) -> None:
    check(f"{anchor}: nothing said", _notes(tmp_path, _one(anchor, start=anchor)) == [], anchor)


def test_the_first_word_alone_does_not_hold_a_name(tmp_path: Path) -> None:
    """`"list"` is in the code: it starts every string the check reads, and
    makes no `list_ready` of it."""
    notes = _notes(tmp_path, _one("list_ready"))
    check("said", [n.glob for n in notes] == ["list_ready"], notes)


def test_a_name_with_nothing_near_it_is_asked_about_not_ruled_on(tmp_path: Path) -> None:
    notes = _notes(tmp_path, _one("catalog_ready"))
    check("unknown: a library's or gone", [n.kind for n in notes] == ["unknown"]
          and not notes[0].sure, notes)
    check("said as a question", notes[0].text().endswith(
        "nor one of the platform's sections echolot knows: a library's, or a marker "
        "the code no longer writes?"), notes[0].text())


def test_a_recording_that_held_it_settles_the_question(tmp_path: Path) -> None:
    """A library's section the list does not know is asked about until a report
    of the scenario has found it; a name the code renamed is said all the same."""
    found = {"coldStart": {"catalog_ready", "list_shown_mark"}}
    check("unknown, found: not said", _notes(tmp_path, _one("catalog_ready"), found=found) == [])
    check("near, found: said", [n.glob for n in _notes(tmp_path, _one("list_shown_mark"),
                                                       found=found)] == ["list_shown_mark"])


def test_a_temporary_marker_gone_from_the_code_is_said_as_one(tmp_path: Path) -> None:
    notes = _notes(tmp_path, _one("AGENTTMP_list_loaded"))
    check("temporary, and sure", [n.kind for n in notes] == ["temporary"] and notes[0].sure, notes)
    check("said so", "a temporary marker no code here puts in any more" in notes[0].text(),
          notes[0].text())


def test_scans_question_mark_is_said_whatever_the_code(tmp_path: Path) -> None:
    notes = _notes(tmp_path, _one("?"))
    check("unpicked", [(n.key, n.kind) for n in notes] == [("end", "unpicked")], notes)
    (tmp_path / MARKS).unlink()
    check("with no code here too", [n.kind for n in anchors.check(
        Config.load(tmp_path / "echolot.yml"))] == ["unpicked"])


def test_a_config_away_from_the_code_says_nothing_of_names(tmp_path: Path) -> None:
    (tmp_path / "echolot.yml").write_text(f"project: {{package: {PACKAGE}}}\n"
                                          + _one("catalog_ready"), encoding="utf-8")
    check("no code: nothing", anchors.check(Config.load(tmp_path / "echolot.yml")) == [])


def test_every_scenario_is_read_and_a_launch_window_has_nothing_to_check(tmp_path: Path) -> None:
    notes = _notes(tmp_path, "scenarios:\n"
                             "  cold: {window: launch}\n"
                             "  list: {start: bindApplication, end: list_shown_mark}\n")
    check("the list's alone, named", [(n.scenario, n.key) for n in notes] == [("list", "end")]
          and notes[0].text(named=True).startswith("list's end anchor"), notes)


def test_the_near_names_are_alike_by_words_then_spelling() -> None:
    names = ["list_shown", "list_loaded", "Screen.firstFrame", "Screen.loaded", "feed_shown"]
    check("list_shown for list_shown_mark", anchors.nearest("list_shown_mark", names)
          == ["list_shown"], anchors.nearest("list_shown_mark", names))
    check("Screen.firstFrame for Screen.firstFrameMark",
          anchors.nearest("Screen.firstFrameMark", names) == ["Screen.firstFrame"])
    check("nothing alike, nothing offered", anchors.nearest("catalog_ready", names) == [])


# --- where it is said ----------------------------------------------------------

def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _lines(out: str, key: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith(key)]


def test_status_says_it_on_a_line_of_its_own(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path, _one("list_shown_mark"))
    monkeypatch.chdir(tmp_path)
    _, out, _ = _run("status")
    check("one anchor line, marked", _lines(out, "anchor") == [
        f"anchor  ! end anchor `list_shown_mark` is in no Kotlin or Java string here; the code "
        f"has `list_shown` ({MARKS}:4) — if that is the one, put it in echolot.yml"], out)


def test_status_gives_the_command_that_settles_a_question(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path, _one("catalog_ready"))
    monkeypatch.chdir(tmp_path)
    _, out, _ = _run("status")
    check("one recording, then probe", any(
        "`echolot collect -c echolot.yml -n 1`, then `echolot probe` on its trace, says which"
        in ln for ln in _lines(out, "anchor")), out)


def test_a_report_that_found_the_name_quiets_status(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path, _one("catalog_ready"))
    out_dir = tmp_path / ".echolot" / "out"
    out_dir.mkdir(parents=True)
    (out_dir / "report.json").write_text(json.dumps({
        "generated_at": "2026-11-24T10:00:00Z", "summary": {}, "config": {}, "traces": ["a"],
        "window": {"start_anchor": {"glob": "bindApplication", "matches": 1},
                   "end_anchor": {"glob": "catalog_ready", "matches": 1}}}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _, out, _ = _run("status")
    check("no anchor line", _lines(out, "anchor") == [], out)


def test_next_picks_scans_question_mark_with_one_recording(tmp_path: Path, monkeypatch) -> None:
    """Five recordings before the window has an end was the step `next` gave."""
    _project(tmp_path, _one("?"))
    monkeypatch.chdir(tmp_path)
    _, out, _ = _run("status")
    check("said", _lines(out, "anchor") == [
        "anchor  ! end anchor is `?`, the question `scan` left in the draft: no slice is "
        "named that"], out)
    nxt = _lines(out, "next")
    check("and the step is one recording and probe", len(nxt) == 1 and
          "pick the end anchor first: `echolot collect -c echolot.yml -n 1`, then `echolot "
          "probe` on its trace, and put the slice that ends the scenario in echolot.yml; "
          "then " in nxt[0] and "-n 5" not in nxt[0], nxt)
    traces = tmp_path / ".echolot" / "traces"
    traces.mkdir(parents=True)
    (traces / "coldStart_iter000.perfetto-trace").write_bytes(b"x")
    _, out, _ = _run("status")
    check("with a trace there, probe that one", any(
        "pick the end anchor first: `echolot probe .echolot/traces/coldStart_iter000."
        "perfetto-trace`" in ln for ln in _lines(out, "next")), out)


def test_collect_says_it_before_the_first_repeat_and_records(tmp_path: Path, monkeypatch) -> None:
    """A warning, not a stop: the set is the scenario's whatever the anchor is
    called, and `analyze` reads it again once the name is right."""
    _project(tmp_path, _one("list_shown_mark"),
             more=f"runner:\n  iterations: 1\n  activity: {ACTIVITY}\n")
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    monkeypatch.chdir(tmp_path)
    code, _, err = _run("collect")
    said = (f"[!] end anchor `list_shown_mark` is in no Kotlin or Java string here; the code "
            f"has `list_shown` ({MARKS}:4) — the traces are the scenario's whatever it is "
            f"called: put the name right in echolot.yml before `analyze`")
    check("recorded", code == 0 and "perfetto" in fake.kinds(), (code, err[-800:]))
    check("said once, before the device was asked anything",
          err.count(said) == 1 and err.index(said) < err.index("device "), err[-1500:])


def test_domains_c_lists_the_maps_entries_no_code_holds(tmp_path: Path) -> None:
    path = _project(tmp_path, _one("list_shown"),
                    more="domains:\n  - {slice: list_shown, module: \":app\"}\n"
                         "  - {slice: list_shown_mark, module: \":app\"}\n"
                         "  - {slice: bindApplication, module: \":app\"}\n")
    code, out, _ = _run("domains", "--root", str(tmp_path), "-c", str(path))
    check("the one gone, with the near name, as a comment", code == 0 and out.splitlines()[-2:] == [
        "# In echolot.yml's domains: and in no Kotlin or Java string here — a library's, or a "
        "marker the code no longer writes:",
        f"#   list_shown_mark — the code has list_shown ({MARKS}:4)"], out[-600:])
    _, plain, _ = _run("domains", "--root", str(tmp_path))
    check("without -c, the map alone", "domains: and in no" not in plain, plain[-400:])
