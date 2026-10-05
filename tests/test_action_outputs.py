"""The action in the cases it reported wrong: a row that appeared, the doctor
step's config and exit 2, a second call in one job, a baseline the token cannot
read, and a comparison that failed after the baseline was found.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402
from tests.test_action import ACTION, RUNS, FakeGitHub, _artifacts, _echolot, ci  # noqa: E402


def _step(name: str) -> dict:
    steps = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]
    return next(s for s in steps if s.get("name") == name)


def test_a_row_that_appeared_is_an_output_of_its_own(tmp_path: Path, demo_report,
                                                     monkeypatch) -> None:
    base = copy.deepcopy(demo_report)
    detector = next(d for d in base["detectors"] if d.get("rows"))
    detector["rows"] = detector["rows"][1:]
    out = tmp_path / "out"
    out.mkdir()
    (out / "report.json").write_text(json.dumps(demo_report))
    (tmp_path / "base.json").write_text(json.dumps(base))
    (tmp_path / "echolot.yml").write_text("project:\n  process: com.example.app\n")
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    ci.main(["compare", "--echolot", _echolot(tmp_path), "--config",
             str(tmp_path / "echolot.yml"), "--out", str(out),
             "--baseline", str(tmp_path / "base.json")])
    said = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
    check("nothing moved, one appeared",
          said["moved"] == "0" and said["appeared"] == "1" and said["vanished"] == "0", said)
    declared = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["outputs"]
    check("and the action passes both on", {"appeared", "vanished"} <= set(declared), declared)


def _doctor(tmp_path: Path, exit_code: int) -> subprocess.CompletedProcess:
    fake = tmp_path / "echolot"
    fake.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/argv"\nexit {exit_code}\n')
    fake.chmod(0o755)
    return subprocess.run(["bash", "-c", _step("echolot doctor")["run"]],
                          env={**os.environ, "ECHOLOT": str(fake), "CONFIG": "app/echolot.yml"},
                          capture_output=True, text=True, check=False)


def test_the_doctor_step_reads_the_config_and_tells_a_download_from_a_failure(
        tmp_path: Path) -> None:
    ran = _doctor(tmp_path, 0)
    check("given the config analyze reads",
          ran.returncode == 0 and (tmp_path / "argv").read_text().split() == [
              "doctor", "-q", "-c", "app/echolot.yml"], (tmp_path / "argv").read_text())
    check("which the step declares", _step("echolot doctor")["env"].get("CONFIG")
          == "${{ inputs.config }}")
    ran = _doctor(tmp_path, 2)
    check("exit 2 is a download that failed",
          ran.returncode == 2 and "could not be downloaded" in ran.stdout
          and "does not compute traces correctly" not in ran.stdout, ran.stdout)
    ran = _doctor(tmp_path, 1)
    check("and 1 a runner that computes wrong",
          ran.returncode == 1 and "does not compute traces correctly" in ran.stdout, ran.stdout)


def test_a_second_call_in_one_job_gets_its_own_virtualenv(tmp_path: Path) -> None:
    script = _step("Install echolot")["run"]
    head = script.split("\nif ", 1)[0]
    # Only the lines that name the virtualenv are run: the rest installs.
    check("the script names its virtualenv before it installs",
          "VENV=" in head and "pip" not in head and "venv \"" not in head, script)

    def venv(action_path: str) -> str:
        return subprocess.run(["bash", "-c", head + '\necho "$VENV"'], capture_output=True,
                              text=True, check=True, cwd=tmp_path,
                              env={**os.environ, "PYTHON": sys.executable,
                                   "TEMP_ROOT": str(tmp_path),
                                   "GITHUB_ACTION_PATH": action_path}).stdout.strip()

    first, second = venv("/actions/echolot/v0.10.0"), venv("/actions/echolot/main")
    check("another ref, another virtualenv", first != second and first.startswith(str(tmp_path)),
          (first, second))
    check("the same ref, the same one", venv("/actions/echolot/main") == second)


def test_a_baseline_the_token_cannot_read_is_a_warning(capsys) -> None:
    for code in (401, 403, 404, 502):
        ci.find_baseline(FakeGitHub({}, fail=ci.HTTPFailure(code, "refused")), repo="o/r",
                         workflow="nightly.yml", branch="main", artifact="a", current_run=1)
        check(f"HTTP {code} annotated", "::warning" in capsys.readouterr().out)
    ci.find_baseline(FakeGitHub({RUNS: {"workflow_runs": []}}), repo="o/r",
                     workflow="nightly.yml", branch="main", artifact="a", current_run=1)
    check("while no good run yet is a plain line", "::warning" not in capsys.readouterr().out)
    broken = FakeGitHub({RUNS: {"workflow_runs": [{"id": 5, "event": "push"}]},
                         _artifacts(5): {"artifacts": [{"name": "a", "expired": False,
                                                        "archive_download_url": "u"}]}},
                        downloads={"u": b"not a zip"})
    ci.find_baseline(broken, repo="o/r", workflow="nightly.yml", branch="main",
                     artifact="a", current_run=1)
    check("and an artifact that is no archive is annotated too",
          "is not a zip archive" in capsys.readouterr().out)


def test_a_comparison_that_failed_is_not_a_baseline_missing(tmp_path: Path) -> None:
    page = ci.page(tmp_path, artifact="echolot-report", branch="main", note="", run="41",
                   url="https://example/run/6", whole=False,
                   failed="`echolot compare` exited 2")
    check("it says the comparison failed",
          "The comparison with the report run [41](https://example/run/6) kept failed" in page
          and "no baseline was found" not in page, page)
    page = ci.page(tmp_path, artifact="echolot-report", branch="main", note="", run="",
                   url="", whole=False)
    check("and with none, what a run on the branch has to keep",
          "a run on `main` has to keep one first" in page
          and "a later run compares against it" not in page, page)
