"""Commands at their edges: a draft config outside the project, `mark` with
no config or one that does not load, an argument argparse refused, `init
--into` a missing directory, a file that cannot be read, an `anr --root`
typo, `mark --apply --json`, a refusal's reason in the run log, the help's
usage, and a pipe closed early.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder  # noqa: E402
from echolot.main import main, project_of  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_mark_edits import LAUNCHER, manifest, write  # noqa: E402

ACTIVITY = ("class MainActivity : ComponentActivity() {\n"
            "    override fun onCreate(savedInstanceState: Bundle?) {\n"
            "        super.onCreate(savedInstanceState)\n"
            "    }\n"
            "}\n")
GUARDED = ("project:\n  package: com.example.app\n"
           "instrumentation:\n  allowed: [\"feature/*/src/main\"]\n")


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def logged(tmp_path, monkeypatch):
    """The run log on, in a directory of the test's own."""
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    monkeypatch.chdir(tmp_path)

    def last() -> dict:
        lines = (tmp_path / recorder.LOG_FILE).read_text(encoding="utf-8").splitlines()
        return json.loads(lines[-1])
    return last


def _app(root: Path, config: str | None = GUARDED) -> Path:
    write(root, "app/src/main/AndroidManifest.xml", manifest(LAUNCHER))
    write(root, "app/src/main/kotlin/MainActivity.kt", ACTIVITY)
    if config is not None:
        write(root, "echolot.yml", config)
    return root


def test_a_draft_outside_the_project_logs_where_the_command_ran(tmp_path: Path,
                                                               monkeypatch) -> None:
    project, scratch = tmp_path / "proj", tmp_path / "scratch"
    (project / "build").mkdir(parents=True)
    scratch.mkdir()
    (scratch / "draft.yml").write_text("project: {package: a}\n", encoding="utf-8")
    (project / "echolot.yml").write_text("project: {package: a}\n", encoding="utf-8")
    monkeypatch.chdir(project)
    args = argparse.Namespace(config="../scratch/draft.yml")
    check("the draft's directory is no project", project_of(args) == Path.cwd())
    monkeypatch.chdir(project / "build")
    args = argparse.Namespace(config="../echolot.yml")
    check("while the project's own config, from below it, is",
          project_of(args) == project.resolve())
    (project / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(config="proj/echolot.yml")
    check("and so is a project's from beside it", project_of(args) == project.resolve())


def test_mark_finds_the_config_under_root_and_refuses_one_named_and_missing(
        tmp_path: Path, monkeypatch) -> None:
    project = _app(tmp_path / "proj")
    monkeypatch.chdir(tmp_path)
    code, out, _ = _run("mark", "--root", "proj", "--json")
    plan = json.loads(out)
    check("the guard applies from the parent directory",
          code == 0 and not any(p["applicable"] for p in plan["proposals"]), plan["proposals"])
    monkeypatch.chdir(project)
    code, _, err = _run("mark", "-c", "echolot.yaml")
    check("a config named and not there is refused", code == 2 and "echolot.yaml" in err, err)


def test_mark_stops_on_a_config_that_does_not_load(tmp_path: Path, monkeypatch) -> None:
    _app(tmp_path, GUARDED + "detectors:\n  frame_jank: true\n")
    monkeypatch.chdir(tmp_path)
    code, _, err = _run("mark", "--apply")
    check("exit 2, with why", code == 2 and "does not load" in err, err)
    check("and nothing written",
          "AGENTTMP_" not in (tmp_path / "app/src/main/kotlin/MainActivity.kt").read_text())


def test_mark_apply_json_is_one_document(tmp_path: Path, monkeypatch) -> None:
    _app(tmp_path, "project:\n  package: com.example.app\n")
    monkeypatch.chdir(tmp_path)
    code, out, _ = _run("mark", "--apply", "--json")
    data = json.loads(out)
    check("with what was applied in it",
          code == 0 and data["applied"] and data["applied"][0]["markers"], data.get("applied"))


def test_an_argument_error_is_logged(logged) -> None:
    code, _, _ = _run("analyze")
    entry = logged()
    check("exit 2 and its line", code == 2 and entry["cmd"] == "analyze"
          and entry["exit"] == 2 and "error" in entry.get("error", ""), entry)


def test_init_into_a_missing_directory_leaves_it_missing(tmp_path: Path, logged) -> None:
    code, _, _ = _run("init", "--into", "no-such-app", "--no-input")
    check("refused, and still missing", code == 2 and not (tmp_path / "no-such-app").exists())


def test_a_file_that_cannot_be_read_is_a_sentence(tmp_path: Path, logged) -> None:
    (tmp_path / "adir").mkdir()
    code, _, err = _run("report", "adir")
    check("a directory as a report", code == 2 and "error:" in err
          and "Traceback" not in err, err)
    (tmp_path / "echolot.yml").write_bytes(b"project:\n  package: caf\xe9\n")
    code, _, err = _run("hunt", "why", "is", "it", "slow")
    check("a config that is not UTF-8", code == 2 and "Traceback" not in err, err)


def test_analyze_checks_o_before_it_starts(tmp_path: Path, logged) -> None:
    (tmp_path / "echolot.yml").write_text("project: {package: a}\n", encoding="utf-8")
    (tmp_path / "afile").write_text("", encoding="utf-8")
    code, _, err = _run("analyze", "never-read.perfetto-trace", "-o", "afile")
    check("refused up front", code == 2 and "is a file, not a directory" in err, err)


def test_anr_with_a_mistyped_root_is_refused(tmp_path: Path) -> None:
    from tests.test_anr import DUMP
    report = tmp_path / "export.txt"
    report.write_text(DUMP, encoding="utf-8")
    code, _, err = _run("anr", str(report), "--root", str(tmp_path / "chekout"))
    check("no such directory", code == 2 and "no such directory" in err, err)


def test_a_refusal_keeps_its_reason_in_the_log(logged) -> None:
    code, _, _ = _run("scan", "--root", "nope")
    entry = logged()
    check("the sentence it printed", code == 2 and "no such directory" in entry.get("error", ""),
          entry)


def test_domains_root_reads_as_optional() -> None:
    _, out, _ = _run("--help")
    check("[--root <repo>]", "domains [--root <repo>]" in out, out[:3000])


def test_a_pipe_closed_early_is_no_crash(logged, monkeypatch) -> None:
    from echolot import main as main_mod

    def closed(args):
        raise BrokenPipeError(32, "Broken pipe")
    # The command itself meets the closed pipe; stdout stays a StringIO, so
    # pointing it at /dev/null cannot touch this process's own descriptor.
    monkeypatch.setattr(main_mod, "cmd_explain", closed)
    with recorder.isolated(), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        code = main(["explain"])
    entry = logged()
    check("141, and no traceback in the log",
          code == 141 and entry["exit"] == 141 and "error" not in entry, entry)
