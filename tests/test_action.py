#!/usr/bin/env python3
"""The GitHub Action: action.yml, and the glue it runs, action/ci.py.

The whole action runs in .github/workflows/action.yml on the demo app's
traces, on every pull request. These are the parts that need no runner:
which files a `traces` input names, how the baseline is found among a
branch's runs, what the summary and the comment say, and the rules action.yml
keeps so that an input can never become a command.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import stat
import sys
import zipfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import __version__  # noqa: E402
from tests.support import check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ACTION = ROOT / "action.yml"

_spec = importlib.util.spec_from_file_location("echolot_action_ci", ROOT / "action" / "ci.py")
ci = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ci
_spec.loader.exec_module(ci)


# --- the traces -------------------------------------------------------------

def test_a_traces_input_names_each_file_once_in_the_order_given(tmp_path: Path) -> None:
    out = tmp_path / "build" / "outputs" / "run"
    out.mkdir(parents=True)
    for name in ("Startup_iter001.perfetto-trace", "Startup_iter000.perfetto-trace",
                 "Scroll_iter000.perfetto-trace"):
        (out / name).write_bytes(b"")
    (tmp_path / "one.perfetto-trace").write_bytes(b"")

    files, missed = ci.expand(
        "one.perfetto-trace\n\n  build/**/Startup_iter*.perfetto-trace  \none.perfetto-trace\n",
        tmp_path)
    check("a path, then a glob's files sorted, and nothing twice",
          [f.name for f in files] == ["one.perfetto-trace", "Startup_iter000.perfetto-trace",
                                      "Startup_iter001.perfetto-trace"], files)
    check("and every line named something", missed == [], missed)

    files, missed = ci.expand("build/**/Missing_*.perfetto-trace\nbuild\nnope.trace", tmp_path)
    check("a glob that matches nothing, a directory and a missing file are each named",
          files == [] and missed == ["build/**/Missing_*.perfetto-trace", "build", "nope.trace"],
          missed)


def test_analyze_stops_on_a_line_that_names_no_trace(tmp_path: Path, capsys) -> None:
    (tmp_path / "bench").mkdir()
    code = ci.main(["analyze", "--echolot", "/bin/false", "--config", "echolot.yml",
                    "--out", str(tmp_path / "out"), "--traces", "bench", "--root", str(tmp_path)])
    said = capsys.readouterr().out
    check("the step fails, before echolot is called", code == 1, said)
    check("and says the directory needs the traces of one test named in it",
          "`bench` names no trace — it is a directory" in said, said)


# --- the baseline -----------------------------------------------------------

def _archive(**files: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class FakeGitHub:
    """The REST calls the action makes, answered from a table."""

    def __init__(self, routes: dict, downloads: dict | None = None,
                 fail: Exception | None = None) -> None:
        self.routes, self.downloads, self.fail = routes, downloads or {}, fail
        self.sent: list[tuple[str, str, dict]] = []
        self.asked: list[str] = []

    def get(self, path: str):
        self.asked.append(path)
        if self.fail is not None:
            raise self.fail
        return self.routes[path.split("?")[0]]

    def download(self, url: str) -> bytes:
        return self.downloads[url]

    def send(self, method: str, path: str, payload: dict):
        self.sent.append((method, path, payload))
        return {}


RUNS = "/repos/o/r/actions/workflows/nightly.yml/runs"


def _artifacts(run: int) -> str:
    return f"/repos/o/r/actions/runs/{run}/artifacts"


def test_the_baseline_is_the_newest_good_run_on_the_branch_that_kept_a_report() -> None:
    report = json.dumps({"detectors": []}).encode()
    gh = FakeGitHub({
        RUNS: {"workflow_runs": [
            {"id": 10, "event": "schedule"},                  # this run
            {"id": 9, "event": "pull_request"},               # a fork's `main`
            {"id": 8, "event": "schedule"},                   # kept it, expired
            {"id": 7, "event": "push"},                       # kept something else
            {"id": 6, "event": "schedule", "run_number": 41, "html_url": "u/6"},
        ]},
        _artifacts(8): {"artifacts": [{"name": "echolot-report", "expired": True,
                                       "archive_download_url": "z/8"}]},
        _artifacts(7): {"artifacts": [{"name": "echolot-report", "expired": False,
                                       "archive_download_url": "z/7"}]},
        _artifacts(6): {"artifacts": [{"name": "echolot-report", "expired": False,
                                       "archive_download_url": "z/6"}]},
    }, downloads={"z/7": _archive(**{"comparison.json": b"{}"}),
                  "z/6": _archive(**{"report.json": report, "report.md": b"#"})})

    found, note = ci.find_baseline(gh, repo="o/r", workflow="nightly.yml", branch="main",
                                   artifact="echolot-report", current_run=10)
    check("run 41, passing over this run, a pull request's, an expired artifact "
          "and an archive with no report in it",
          found is not None and found.run["id"] == 6 and found.report == report and note == "",
          (found, note))
    check("the branch and the result are asked for, not filtered here",
          "branch=main&status=success" in gh.asked[0], gh.asked[0])
    check("never this run's own artifacts, nor a pull request's",
          not any(a.startswith(_artifacts(10)) or a.startswith(_artifacts(9))
                  for a in gh.asked), gh.asked)


def test_nothing_to_compare_against_is_a_note_never_a_failure() -> None:
    nothing, note = ci.find_baseline(FakeGitHub({RUNS: {"workflow_runs": []}}), repo="o/r",
                                     workflow="nightly.yml", branch="main",
                                     artifact="echolot-report", current_run=1)
    check("no good run yet", nothing is None and "has succeeded yet" in note, note)

    kept_none = FakeGitHub({RUNS: {"workflow_runs": [{"id": 5, "event": "push"}]},
                            _artifacts(5): {"artifacts": []}})
    nothing, note = ci.find_baseline(kept_none, repo="o/r", workflow="nightly.yml",
                                     branch="main", artifact="echolot-report", current_run=1)
    check("runs, and none kept a report under that name",
          nothing is None and "kept a report as `echolot-report`" in note, note)

    for code, words in ((403, "it needs `actions: read`"),
                        (404, "`baseline-workflow` is a file name")):
        refused = FakeGitHub({}, fail=ci.HTTPFailure(code, "refused"))
        nothing, note = ci.find_baseline(refused, repo="o/r", workflow="nightly.yml",
                                         branch="main", artifact="a", current_run=1)
        check(f"HTTP {code} says what to change", nothing is None and words in note, note)


def test_the_workflow_is_named_by_its_file() -> None:
    check("from the ref GitHub gives the job",
          ci.workflow_file("o/r/.github/workflows/nightly.yml@refs/heads/main") == "nightly.yml")


# --- the comparison ---------------------------------------------------------

def _echolot(tmp_path: Path) -> str:
    """This checkout's echolot as one executable, which is what the step is given."""
    exe = tmp_path / "echolot"
    exe.write_text(f'#!/bin/sh\nPYTHONPATH="{ROOT}" exec "{sys.executable}" -m echolot "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def test_compare_runs_against_the_baseline_and_says_how_many_rows_moved(
        tmp_path: Path, demo_report, demo_changed_report, monkeypatch) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "report.json").write_text(json.dumps(demo_changed_report))
    (tmp_path / "base.json").write_text(json.dumps(demo_report))
    (tmp_path / "echolot.yml").write_text("project:\n  process: com.example.app\n")
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))

    code = ci.main(["compare", "--echolot", _echolot(tmp_path),
                    "--config", str(tmp_path / "echolot.yml"), "--out", str(out),
                    "--baseline", str(tmp_path / "base.json")])
    said = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
    check("the lock's two rows moved, and the step passed", code == 0 and said["moved"] == "2",
          said)
    check("the comparison and the baseline it was made against stay with the report",
          (out / "comparison.md").is_file() and (out / "baseline" / "report.json").is_file()
          and said["baseline"] == str(out / "baseline" / "report.json"), sorted(os.listdir(out)))
    check("and its header names them the way they sit in the artifact",
          "Before  `baseline/report.json`" in (out / "comparison.md").read_text(), "")


def test_a_baseline_that_is_not_there_leaves_the_outputs_empty(tmp_path: Path, monkeypatch,
                                                                capsys) -> None:
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    code = ci.main(["compare", "--echolot", "/bin/false", "--config", "echolot.yml",
                    "--out", str(tmp_path), "--baseline", str(tmp_path / "gone.json")])
    check("a warning, and the step passes", code == 0
          and "::warning" in capsys.readouterr().out)
    check("with nothing to compare", outputs.read_text()
          == "baseline=\ncomparison=\nmoved=\nappeared=\nvanished=\nfailed=\n",
          outputs.read_text())


# --- what a person reads ----------------------------------------------------

def test_the_summary_leads_with_the_comparison_and_keeps_the_report_below(tmp_path: Path) -> None:
    (tmp_path / "report.md").write_text("# Marker Report\n")
    page = ci.page(tmp_path, artifact="echolot-report", branch="main", note="no good run",
                   run="", url="", whole=True)
    check("without a comparison, why, and the report itself",
          "Nothing to compare against: no good run." in page and "# Marker Report" in page, page)

    (tmp_path / "comparison.md").write_text("# Comparison\n")
    page = ci.page(tmp_path, artifact="echolot-report", branch="main", note="", run="41",
                   url="https://example/run/6", whole=True)
    check("with one, where the baseline came from, then the comparison",
          "the report run [41](https://example/run/6) kept on `main`" in page
          and page.index("# Comparison") < page.index("<details>"), page)
    short = ci.page(tmp_path, artifact="echolot-report", branch="main", note="", run="41",
                    url="https://example/run/6", whole=False)
    check("and the comment leaves the report to the summary", "Marker Report" not in short, short)


def test_the_comment_is_one_per_artifact_and_edited_in_place() -> None:
    tag = ci.marker("echolot-report")
    gh = FakeGitHub({"/repos/o/r/issues/5/comments": [
        {"id": 1, "body": "a person's comment"},
        {"id": 2, "body": f"{tag}\nthe last push's comparison"},
    ]})
    check("edited", ci.upsert_comment(gh, "o/r", 5, "echolot-report", "new") == "updated"
          and gh.sent == [("PATCH", "/repos/o/r/issues/comments/2", {"body": f"{tag}\nnew"})],
          gh.sent)

    gh = FakeGitHub({"/repos/o/r/issues/5/comments": [{"id": 1, "body": "a person's comment"}]})
    ci.upsert_comment(gh, "o/r", 5, "echolot-report", "x" * (ci.COMMENT_LIMIT + 10))
    method, path, payload = gh.sent[0]
    check("posted when there is none, and cut to fit",
          method == "POST" and path == "/repos/o/r/issues/5/comments"
          and len(payload["body"]) < ci.COMMENT_LIMIT + 200
          and payload["body"].endswith("in the job summary."), (method, path))


# --- action.yml -------------------------------------------------------------

def _action() -> dict:
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def test_every_action_the_action_uses_is_pinned_to_a_commit() -> None:
    """The rule the workflows keep: a tag can be moved, a commit cannot."""
    lines = [line.strip() for line in ACTION.read_text(encoding="utf-8").splitlines()
             if re.match(r"\s*(- )?uses:", line)]
    bad = [line for line in lines
           if not re.fullmatch(r"(- )?uses: [\w./-]+@[0-9a-f]{40}  # v[\d.]+", line)]
    check("every `uses:` names a commit, with its version beside it", lines and not bad, bad)


def test_an_input_reaches_a_script_as_data() -> None:
    """`${{ inputs.traces }}` written into a script would run a crafted glob as shell."""
    steps = _action()["runs"]["steps"]
    inline = [s.get("name") for s in steps if "${{" in s.get("run", "")]
    check("no expression inside a `run:`; inputs go through `env`", inline == [], inline)


def test_the_inputs_declared_are_the_inputs_used() -> None:
    text = ACTION.read_text(encoding="utf-8")
    declared = set(_action()["inputs"])
    used = set(re.findall(r"inputs\.([\w-]+)", text))
    check("every input is used, and nothing uses an input that is not there",
          declared == used, (sorted(declared - used), sorted(used - declared)))
    undescribed = [k for k, v in _action()["inputs"].items() if not v.get("description")]
    check("and each says what it is", undescribed == [], undescribed)


def test_every_step_of_the_glue_exists() -> None:
    named = set(re.findall(r'action/ci\.py" (\w+)', ACTION.read_text(encoding="utf-8")))
    sub = next(a for a in ci.parser()._actions if isinstance(a, argparse._SubParsersAction))
    check("each `ci.py <step>` action.yml runs is one ci.py has, and none is left over",
          named == set(sub.choices), (sorted(named), sorted(sub.choices)))


# 0.10.0 is the last release without action.yml in its tag. Until the version
# moves past it, the documents call the action at `main`; from then on at the
# tag of the version in the code, so a release that bumps the version without
# the documents fails here, as the plugin's marketplace entry does.
LAST_WITHOUT_ACTION = (0, 10, 0)


def test_the_documents_call_the_action_at_a_ref_that_has_it() -> None:
    version = tuple(int(p) for p in __version__.split(".")[:3])
    want = "main" if version <= LAST_WITHOUT_ACTION else f"v{__version__}"
    found = {(doc, ref) for doc in ("README.md", "docs/compare.md")
             for ref in re.findall(r"uses: grishan0v/echolot@([\w.]+)",
                                   (ROOT / doc).read_text(encoding="utf-8"))}
    check("the README and docs/compare.md show the action", {d for d, _ in found}
          == {"README.md", "docs/compare.md"}, found)
    check(f"at `{want}`", {r for _, r in found} == {want}, found)
