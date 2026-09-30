#!/usr/bin/env python3
"""A value a person confirmed stays theirs, inside a hunt too (#196).

Setup marks a block `_source: confirmed_by_user` when a person chose it: the
end of the scenario, most often, the moment they call the app ready. In a
live Codex session the hunt found that end matched nothing, took step 2's
"fix the config" at its word and rewrote it, and every report after that
measured a window nobody had agreed to. The session said so once, after the
report on the new window was made.

What is held here: the loop's text leaves such a value to the person; an
investigation records the confirmed values when it opens; `analyze` inside it
says which one no longer holds, before the report, and logs it; `echolot
hunt` names the value in its drift line; and `reflect` raises it from the log
alone, since the session where it happened had no transcript to read.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from echolot import hunt as hunt_mod
from echolot import layer, recorder
from echolot.config import Config
from echolot.main import _warn_confirmed_changed, main
from tests.support import check

CONFIG = """\
project:
  package: com.example.app
  process: com.example.app
scenario:
  name: coldStart
  start:
    name: bindApplication
    _source: derived
    _evidence: "probe: top slices"
  end:
    name: "reportFullyDrawn() for ComponentActivity"
    _source: confirmed_by_user
    _evidence: "probe: the app is ready"
"""


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")
    return tmp_path


def _hunt(project: Path, question: str = "startup got slower") -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = main(["hunt", question])
    check("hunt opens", code == 0, out.getvalue())
    return out.getvalue()


def _edit_end(project: Path, name: str = "activityResume") -> None:
    path = project / "echolot.yml"
    path.write_text(path.read_text(encoding="utf-8").replace(
        '"reportFullyDrawn() for ComponentActivity"', f'"{name}"'), encoding="utf-8")


# --- the loop's text -------------------------------------------------------

def test_the_loop_leaves_a_confirmed_value_to_the_person():
    """What every host's subagent reads: `echolot guide loop` prints it."""
    loop = " ".join(layer.guide_text(layer.guide_topics()["loop"]).split())
    check("step 2 names the mark", "_source: confirmed_by_user" in loop)
    check("says whose it is", "a person chose it, and it is theirs" in loop, loop)
    check("and what to do instead of changing it",
          "exit with that as the conclusion" in loop and "echolot probe" in loop)
    check("derived and default stay the agent's",
          "`derived` or `default` is yours to fix" in loop)


# --- what the config says was confirmed -----------------------------------

def test_the_confirmed_values_are_read_by_their_keys(project):
    confirmed = Config.load(project / "echolot.yml").confirmed()
    check("the end a person chose, without its provenance",
          confirmed == {"scenario.end.name": "reportFullyDrawn() for ComponentActivity"},
          confirmed)


def test_a_config_with_nothing_confirmed_holds_nothing(tmp_path):
    (tmp_path / "echolot.yml").write_text(
        "project: {package: a.b}\nscenario: {name: s, end: {name: x, _source: derived}}\n",
        encoding="utf-8")
    check("nothing", Config.load(tmp_path / "echolot.yml").confirmed() == {})


# --- the investigation remembers ------------------------------------------------

def test_an_investigation_records_what_was_confirmed_when_it_opened(project):
    _hunt(project)
    h = hunt_mod.load(project)
    check("recorded", h["confirmed"] == {
        "scenario.end.name": "reportFullyDrawn() for ComponentActivity"}, h)


def test_analyze_says_a_confirmed_value_changed_before_the_report(project, capsys):
    _hunt(project)
    _edit_end(project)
    changed = _warn_confirmed_changed(project, Config.load(project / "echolot.yml"))
    err = " ".join(capsys.readouterr().err.split())
    check("one value, both sides", changed == [{
        "field": "scenario.end.name",
        "was": "reportFullyDrawn() for ComponentActivity", "now": "activityResume"}],
        changed)
    check("said with both values",
          "scenario.end.name changed since this investigation opened" in err
          and "'reportFullyDrawn() for ComponentActivity'" in err
          and "'activityResume'" in err, err)
    check("and whose it is to change", "The value is theirs to change: ask them." in err, err)


def test_unchanged_or_outside_an_investigation_nothing_is_said(project, capsys):
    cfg = Config.load(project / "echolot.yml")
    check("no investigation, nothing to hold it to",
          _warn_confirmed_changed(project, cfg) == [])
    _hunt(project)
    capsys.readouterr()
    check("unchanged", _warn_confirmed_changed(project, cfg) == [])
    check("and silent", capsys.readouterr().err == "")


def test_the_run_log_carries_it(project, monkeypatch):
    """What `reflect` reads where there is no transcript."""
    _hunt(project)
    _edit_end(project)
    monkeypatch.setattr(recorder, "_facts", {})
    with contextlib.redirect_stderr(io.StringIO()):
        _warn_confirmed_changed(project, Config.load(project / "echolot.yml"))
    noted = recorder._facts.get("confirmed_changed")
    check("noted as a fact of the run", noted and noted[0]["now"] == "activityResume", noted)


def test_hunt_names_the_value_where_it_said_the_file_changed(project):
    _hunt(project)
    _edit_end(project)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        main(["hunt"])
    said = " ".join(out.getvalue().split())
    check("the value, not only the file",
          "a value a person confirmed changed since this investigation opened: "
          "scenario.end.name 'reportFullyDrawn() for ComponentActivity' → "
          "'activityResume'" in said, said)


# --- reflect, from the log alone ----------------------------------------------

def _line(ts: str, cmd: str, argv: list[str], **extra) -> dict:
    return {"ts": ts, "cmd": cmd, "argv": argv, "cwd": "/p", "exit": 0,
            "ms": 1000, "version": "0.9.0", **extra}


def test_reflect_raises_it_without_a_transcript(tmp_path):
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    moved = [{"field": "scenario.end.name",
              "was": "reportFullyDrawn() for ComponentActivity", "now": "activityResume"}]
    rows = [
        _line("2026-09-30T10:00:00+00:00", "hunt", ["hunt", "slower"]),
        _line("2026-09-30T10:01:00+00:00", "analyze", ["analyze", "a", "-c", "echolot.yml"]),
        _line("2026-09-30T10:03:00+00:00", "analyze", ["analyze", "a", "-c", "echolot.yml"],
              facts={"confirmed_changed": moved}),
        _line("2026-09-30T10:05:00+00:00", "analyze", ["analyze", "b", "-c", "echolot.yml"],
              facts={"confirmed_changed": moved}),
    ]
    log = tmp_path / recorder.LOG_FILE
    log.parent.mkdir(parents=True)
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    done = subprocess.run(
        [sys.executable, "-m", "echolot.main", "reflect", "--last", "--from-log",
         "--project", str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path,
        env=dict(os.environ, ECHOLOT_NO_RECORD="1"))
    check("reflect exits 0", done.returncode == 0, done.stderr[-400:])
    report = json.loads(next((tmp_path / ".echolot" / "reflect").glob("*.json"))
                        .read_text(encoding="utf-8"))
    signal = next((s for s in report["signals"] if s["id"] == "confirmed_changed"), None)
    check("raised as a warning", signal and signal["severity"] == "warn", report["signals"])
    check("one row for the one change, with the reports it touched",
          len(signal["rows"]) == 1 and signal["rows"][0]["reports"] == 2
          and signal["rows"][0]["now"] == "activityResume", signal["rows"])
    check("not listed as unchecked",
          "confirmed_changed" not in report["summary"]["skipped_ids"])
