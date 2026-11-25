"""`compare` and `report` in the cases they read wrong: `-o` beside one report,
a build directory below the project, a comparison handed to `report`, a config
that does not load, and markers with no prefix configured.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402

CONFIG = "project:\n  package: com.example.app\n  process: com.example.app\n"


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _project(root: Path, report: dict) -> Path:
    (root / ".git").mkdir(parents=True)
    (root / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    out = root / ".echolot" / "out"
    out.mkdir(parents=True)
    (out / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (root / "old.json").write_text(json.dumps(report), encoding="utf-8")
    return root


def test_o_is_where_the_comparison_goes(tmp_path: Path, demo_report, monkeypatch) -> None:
    project = _project(tmp_path, demo_report)
    monkeypatch.chdir(project)
    code, _, err = _run("compare", "old.json", "-o", "cmp")
    check("the newer side is the latest report, and the output lands in cmp",
          code == 0 and (project / "cmp" / "comparison.md").is_file(), err[-400:])


def test_a_build_directory_finds_the_project(tmp_path: Path, demo_report, monkeypatch) -> None:
    project = _project(tmp_path, demo_report)
    build = project / "bench" / "build" / "outputs" / "x"
    build.mkdir(parents=True)
    monkeypatch.chdir(build)
    code, out, err = _run("report")
    check("report reads the project's latest", code == 0 and "nearest up the tree" in err
          and out.strip(), err[-400:])
    code, _, err = _run("compare", str(project / "old.json"))
    check("and compare writes beside it",
          code == 0 and (project / ".echolot" / "out" / "comparison.md").is_file(), err[-400:])


def test_a_comparison_handed_to_report_is_named_for_report(tmp_path: Path) -> None:
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps({"kind": "comparison", "rows": []}), encoding="utf-8")
    code, _, err = _run("report", str(path))
    check("give a report.json", code == 2 and "give a report.json" in err
          and "Compare two reports" not in err, err)


def test_a_config_that_does_not_load_is_said_so(tmp_path: Path, demo_report,
                                                monkeypatch) -> None:
    (tmp_path / "old.json").write_text(json.dumps(demo_report), encoding="utf-8")
    (tmp_path / "echolot.yml").write_text("project:\n  process: [unclosed\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, _, err = _run("compare", "old.json", "old.json")
    check("why nothing was written", code == 0 and "echolot.yml does not load" in err
          and "does not parse" in err and "no config found" not in err, err[-500:])


def test_markers_keep_their_digits_without_a_configured_prefix(tmp_path: Path, demo_report,
                                                               monkeypatch) -> None:
    def with_marker(name: str, ms: float) -> dict:
        rep = copy.deepcopy(demo_report)
        det = next(d for d in rep["detectors"] if d.get("rows"))
        row = {**det["rows"][0], "location": name}
        for key in det.get("value_columns") or []:
            row[key] = ms
        det["rows"] = [*det["rows"], row]
        return rep
    (tmp_path / "a.json").write_text(json.dumps(with_marker("AGENTTMP_fill_v4", 40)))
    (tmp_path / "b.json").write_text(json.dumps(with_marker("AGENTTMP_fill_v6", 120)))
    monkeypatch.chdir(tmp_path)
    code, out, _ = _run("compare", "a.json", "b.json")
    check("two markers, and the warning that names them",
          code == 0 and "AGENTTMP_fill_v#" not in out and "AGENTTMP_fill_v6" in out, out[:1500])


def _compare(tmp_path: Path, before: dict, after: dict) -> dict:
    (tmp_path / "b.json").write_text(json.dumps(before), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps(after), encoding="utf-8")
    code, _, err = _run("compare", str(tmp_path / "b.json"), str(tmp_path / "a.json"),
                        "-o", str(tmp_path / "cmp"))
    check("compare ran", code == 0, err[-500:])
    return json.loads((tmp_path / "cmp" / "comparison.json").read_text(encoding="utf-8"))


# --- header lines that read as something else (#585) -------------------------

def test_the_time_beside_each_side_is_when_its_report_was_written(tmp_path: Path,
                                                                  demo_report) -> None:
    """A bare UTC stamp read as when the side was recorded, in local time."""
    from datetime import datetime
    before = copy.deepcopy(demo_report)
    before["generated_at"] = "2026-11-22T12:26:39+00:00"
    _compare(tmp_path, before, demo_report)
    md = (tmp_path / "cmp" / "comparison.md").read_text(encoding="utf-8")
    line = next(ln for ln in md.splitlines() if ln.startswith("Before"))
    here = datetime.fromisoformat(before["generated_at"]).astimezone()
    zone = here.tzname() or here.strftime("%z")
    check("labelled, in local time, with its zone",
          f"report written {here:%Y-%m-%d %H:%M:%S} {zone}" in line and zone, line)


def test_the_windows_spread_is_named_as_the_repeats(demo_report) -> None:
    from echolot import report as report_mod
    from echolot import render as render_mod
    md = report_mod.to_markdown(demo_report)
    line = next(ln for ln in md.splitlines() if ln.startswith("Scenario window"))
    check("report.md", line.endswith("across repeats)"), line)
    head = "\n".join(render_mod._header_lines(demo_report))
    check("and the short header", "(repeats " in head and "· written " in head, head)
