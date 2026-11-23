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


# --- two versions of echolot (#507) ------------------------------------------

def test_a_report_names_the_echolot_that_wrote_it(demo_report) -> None:
    from echolot import __version__
    from echolot.main import DETECTOR_DIR
    from echolot.tp import load_detectors
    shipped = {d.id: d.sha for d in load_detectors(DETECTOR_DIR)}
    check("toolchain.echolot is this version", demo_report["toolchain"].get("echolot") == __version__,
          demo_report["toolchain"])
    check("and every detector carries the fingerprint of its query",
          all(d.get("sha") == shipped[d["id"]] for d in demo_report["detectors"]),
          [(d["id"], d.get("sha")) for d in demo_report["detectors"]])


def test_a_fingerprint_ignores_comments_and_follows_the_query() -> None:
    from echolot.tp import logic_sha
    check("a reworded comment keeps it",
          logic_sha("-- @plain_why: one\nSELECT 1 -- why\n") == logic_sha("-- @plain_why: two\n\nSELECT 1\n"))
    check("a changed query does not", logic_sha("SELECT 1") != logic_sha("SELECT 2"))


def _older(report: dict, *, version: str | None, sha_of: tuple[str, ...] = ()) -> dict:
    """The same report as another echolot would have written it."""
    old = copy.deepcopy(report)
    if version is None:
        old["toolchain"].pop("echolot", None)
        for d in old["detectors"]:
            d.pop("sha", None)
    else:
        old["toolchain"]["echolot"] = version
    for d in old["detectors"]:
        if d["id"] in sha_of:
            d["sha"] = "000000000000"
    return old


def _compare(tmp_path: Path, before: dict, after: dict) -> dict:
    (tmp_path / "b.json").write_text(json.dumps(before), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps(after), encoding="utf-8")
    code, _, err = _run("compare", str(tmp_path / "b.json"), str(tmp_path / "a.json"),
                        "-o", str(tmp_path / "cmp"))
    check("compare ran", code == 0, err[-500:])
    return json.loads((tmp_path / "cmp" / "comparison.json").read_text(encoding="utf-8"))


def test_two_versions_are_said_and_a_changed_detector_has_no_holds(tmp_path: Path, demo_report,
                                                                    demo_changed_report) -> None:
    """#507: 0.7.0 against 0.17.0 gave `holds: yes` to moves that were the tool's."""
    fired = next(d["id"] for d in demo_changed_report["detectors"] if d["rows"])
    before = _older(demo_report, version="0.7.0", sha_of=(fired,))
    cmp = _compare(tmp_path, before, demo_changed_report)
    said = {w["id"]: w["text"] for w in cmp["warnings"]}
    check("the two versions are named", "echolot 0.7.0 before" in said.get("versions", ""), said)
    check("and the detector whose query differs", f"`{fired}`" in said.get("logic", ""), said)
    mine = [r for r in cmp["rows"] if r["detector"] == fired]
    check("its rows carry no Holds", mine and all(r["holds"] is None for r in mine), mine)
    others = [r for r in cmp["rows"] if r["detector"] != fired and r["holds"] is not None]
    check("while the others keep theirs", others or not [r for r in cmp["rows"] if r["detector"] != fired],
          cmp["rows"][:3])


def test_a_report_from_before_the_fingerprint_takes_every_holds(tmp_path: Path, demo_report,
                                                                 demo_changed_report) -> None:
    cmp = _compare(tmp_path, _older(demo_report, version=None), demo_changed_report)
    said = {w["id"]: w["text"] for w in cmp["warnings"]}
    check("an older echolot is named", "older than 0.18.0" in said.get("versions", ""), said)
    check("and no row holds", all(r["holds"] is None for r in cmp["rows"]), cmp["rows"][:3])


def test_one_report_against_itself_is_the_same_recordings(tmp_path: Path, demo_report) -> None:
    cmp = _compare(tmp_path, demo_report, copy.deepcopy(demo_report))
    said = {w["id"]: w["text"] for w in cmp["warnings"]}
    check("said", "same recordings" in said.get("same-recordings", ""), said)
    check("no version line between one version", "versions" not in said and "logic" not in said, said)


def test_status_and_report_say_an_older_echolot(tmp_path: Path, demo_report, monkeypatch) -> None:
    project = _project(tmp_path, _older(demo_report, version="0.7.0"))
    monkeypatch.chdir(project)
    _, out, _ = _run("status")
    line = next((ln for ln in out.splitlines() if ln.startswith("report")), "")
    check("status", "written by echolot 0.7.0, analyse again" in line, out)
    _, out, _ = _run("report")
    check("report", "Written by echolot 0.7.0" in out and "analyze` again" in out, out[:800])
