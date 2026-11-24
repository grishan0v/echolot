"""Which runs write a scenario's latest files: `report.*` and `comparison.*`.

The start page shows the latest report, its next check sets it as "before",
and its "After the fix" is the scenario's `comparison.*`. An agent's
`analyze --defaults` replaced the latest report and became that "before"
(#922); a `compare` of two reports named by hand wrote over the page's
comparison (#923). One rule now decides both: `main._not_the_latest`.
"""

from __future__ import annotations

import contextlib
import io
import json

import pytest

from echolot import demo, recorder
from echolot.main import main
from tests.support import check


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    traces = demo.write(tmp_path)
    (tmp_path / "echolot.yml").write_text(demo.CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, _, err = run("analyze", str(traces[0]))
    assert code == 0, err[-400:]
    return tmp_path, str(traces[0])


@pytest.mark.parametrize("flags", [["--defaults"], ["--set", "main_thread_block.min_slice_ms=100000"]],
                         ids=["defaults", "set"])
def test_a_look_goes_beside_the_latest_report(project, flags):
    root, trace = project
    out = root / ".echolot" / "out"
    latest = (out / "report.json").read_bytes()
    code, _, err = run("analyze", trace, *flags)
    check("analyzed", code == 0, err[-400:])
    check("the latest report is the one made on the config's thresholds",
          (out / "report.json").read_bytes() == latest)
    look = json.loads((out / "look" / "report.json").read_text(encoding="utf-8"))
    check("the look is beside it", look["config"]["defaults"] == (flags[0] == "--defaults")
          and bool(look["config"]["set"]) == (flags[0] == "--set"), look["config"])
    check("and stderr says where it went and why",
          "the report goes to .echolot/out/look/, and the latest report stays" in err, err[-600:])


def test_a_look_inside_an_investigation_is_filed_there_and_nowhere_else(project):
    root, trace = project
    out = root / ".echolot" / "out"
    latest = (out / "report.json").read_bytes()
    run("hunt", "cold start slow")
    code, _, err = run("analyze", trace, "--defaults", "--hunt", "1")
    check("analyzed", code == 0, err[-400:])
    filed = sorted((root / ".echolot" / "hunts" / "1" / "reports").glob("*.json"))
    check("filed under the investigation", len(filed) == 1
          and json.loads(filed[0].read_text(encoding="utf-8"))["config"]["defaults"], filed)
    check("and the latest report is untouched", (out / "report.json").read_bytes() == latest)


def test_o_puts_a_look_where_it_says(project):
    root, trace = project
    code, _, err = run("analyze", trace, "--defaults", "-o", ".echolot/out")
    check("analyzed", code == 0, err[-400:])
    rep = json.loads((root / ".echolot" / "out" / "report.json").read_text(encoding="utf-8"))
    check("-o is obeyed", rep["config"]["defaults"] and not (root / ".echolot/out/look").exists())


def test_two_reports_named_leave_the_scenarios_comparison_alone(project):
    root, _ = project
    out = root / ".echolot" / "out"
    (out / "before.json").write_bytes((out / "report.json").read_bytes())
    code, _, err = run("compare", ".echolot/out/before.json")
    check("one report named: the page's form writes the comparison", code == 0
          and (out / "comparison.json").is_file(), err[-400:])
    page = (out / "comparison.json").read_bytes()

    code, text, err = run("compare", ".echolot/out/report.json", ".echolot/out/report.json")
    check("two named: printed", code == 0 and text.strip(), err[-400:])
    check("and the scenario's comparison is left as it was",
          (out / "comparison.json").read_bytes() == page
          and "both reports named, so nothing was written over the scenario's comparison.*" in err,
          err[-400:])

    code, _, err = run("compare", ".echolot/out/before.json", ".echolot/out/report.json",
                       "-o", "mine")
    check("with -o, written there", code == 0 and (root / "mine" / "comparison.json").is_file()
          and (out / "comparison.json").read_bytes() == page, err[-400:])
