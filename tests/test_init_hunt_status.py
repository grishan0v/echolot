"""`init`, `hunt` and `status` in the cases they got wrong: `-c` from a
subdirectory, `init` for the plugin and Codex, the self-check `init` just
ran, the plugin's door, a second conclusion, the binary local.yml names, a
report that does not read, and a layer whose SKILL.md is gone.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import hunt as hunt_mod  # noqa: E402
from echolot import main as main_mod  # noqa: E402
from echolot import recorder, state  # noqa: E402
from tests.support import check  # noqa: E402

CONFIG = ("project:\n  package: com.example.app\n  process: com.example.app\n"
          "scenario:\n  name: coldStart\n")


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main_mod.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    traces = root / ".echolot" / "traces"
    traces.mkdir(parents=True)
    for i in range(2):
        (traces / f"coldStart_iter00{i}.perfetto-trace").write_bytes(b"x")
    return root


def test_hunt_c_from_a_subdirectory_is_the_project_s(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path / "p")
    monkeypatch.chdir(project)
    _run("hunt", "cold start got slower")
    (project / "bench").mkdir()
    monkeypatch.chdir(project / "bench")
    code, _, _ = _run("hunt", "-c", "../echolot.yml", "scroll got slow")
    h = hunt_mod.load(project)
    check("the project's investigation is the new one",
          code == 0 and h["question"] == "scroll got slow" and h["n"] == 2, h)
    check("none opened in the subdirectory", not (project / "bench" / ".echolot").exists())
    check("and the traces were set aside",
          not list((project / ".echolot" / "traces").glob("*.perfetto-trace")))


def test_status_c_reads_the_project_it_names(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path / "p")
    monkeypatch.chdir(project)
    _run("hunt", "cold start got slower")
    (project / ".echolot" / "traces" / "coldStart_iter000.perfetto-trace").write_bytes(b"x")
    build = project / "bench" / "build"
    build.mkdir(parents=True)
    monkeypatch.chdir(build)
    _, out, _ = _run("status", "-c", "../../echolot.yml")
    check("its investigation and its traces",
          "cold start got slower" in out and "1 in .echolot/traces" in out
          and str(project) in out, out)


def test_init_for_the_plugin_checks_and_says_what_next(tmp_path: Path, monkeypatch) -> None:
    ran = []

    def fake(args, info, **kw):
        ran.append(kw.get("project"))
        recorder.note(checks=3, failed=[])
        return 0, True
    monkeypatch.setattr(main_mod, "_doctor_quiet", fake)
    code, out, _ = _run("init", "--into", str(tmp_path), "--for", "plugin", "--no-input")
    check("the self-check ran", code == 0 and ran == [tmp_path], ran)
    check("and the next line is there", "\nnext  " in out, out[-400:])
    ran.clear()
    _run("init", "--into", str(tmp_path), "--for", "plugin", "--no-input", "--no-doctor")
    check("which --no-doctor skips", ran == [])


def test_init_s_own_self_check_decides_the_next_line(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / recorder.LOG_FILE
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({"ts": "2026-10-01T10:00:00+00:00", "cmd": "doctor", "exit": 1,
                               "facts": {"checks": 0, "failed": ["the self-check did not run"]}})
                   + "\n", encoding="utf-8")

    def fake(args, info, **kw):
        recorder.note(checks=5, failed=[])
        return 0, True
    monkeypatch.setattr(main_mod, "_doctor_quiet", fake)
    _, out, _ = _run("init", "--into", str(tmp_path), "--for", "claude", "--no-input")
    nxt = out.rsplit("next  ", 1)[-1]
    check("not back to doctor after a pass", not nxt.startswith("echolot doctor"), nxt)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-10-01T11:00:00+00:00", "cmd": "init", "exit": 0,
                            "facts": {"checks": 5, "failed": []}}) + "\n")
    check("and init's line counts as the last self-check",
          state.project_state(tmp_path)["last_doctor"]["cmd"] == "init")


def test_hunt_names_the_plugin_s_door(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path)
    _run("init", "--into", str(tmp_path), "--for", "plugin", "--no-input", "--no-doctor")
    monkeypatch.chdir(tmp_path)
    _, _, err = _run("hunt", "cold start got slower")
    check("the plugin's skill", "the plugin's echolot skill" in err, err[-400:])


def test_a_second_conclusion_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _run("hunt", "cold start slow")
    _run("hunt", "--done", "first answer")
    code, _, err = _run("hunt", "--done", "second answer")
    check("refused", code == 1 and "already concluded" in err, err)
    check("and the first answer kept", hunt_mod.load(tmp_path)["conclusion"] == "first answer")
    touched = []
    monkeypatch.setattr(hunt_mod, "touch", lambda project: touched.append(project))
    _run("hunt", "--resume")
    check("a concluded one is not resumed", touched == [])


def test_status_names_the_binary_local_yml_names(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path)
    fake = tmp_path / "tp"
    fake.write_text("#!/bin/sh\necho 'Perfetto v99.0-custom'\n", encoding="utf-8")
    fake.chmod(0o755)
    (tmp_path / "local.yml").write_text(f"toolchain:\n  tp_binary: {fake}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _, out, _ = _run("status")
    check("the custom binary", "Perfetto v99.0-custom" in out.splitlines()[0], out[:200])


def test_a_report_that_does_not_read_is_said(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path)
    out_dir = tmp_path / ".echolot" / "out"
    out_dir.mkdir(parents=True)
    (out_dir / "report.json").write_text("{ truncated", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _, out, _ = _run("status")
    check("not none yet", "does not read" in out and "none yet" not in out, out)


def test_files_equal_to_the_template_are_current_without_skill_md(tmp_path: Path) -> None:
    _run("init", "--into", str(tmp_path), "--for", "claude", "--no-input", "--no-doctor")
    (tmp_path / ".claude" / "skills" / "echolot" / "SKILL.md").unlink()
    _, out, _ = _run("init", "--into", str(tmp_path), "--for", "claude", "--no-input",
                     "--no-doctor")
    check("none called differs", "differs" not in out and "init --all" not in out, out)
