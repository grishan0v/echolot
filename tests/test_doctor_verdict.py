#!/usr/bin/env python3
"""`doctor`'s verdict, held to saying something true about the machine.

Three ways it said something that was not true of the machine it ran on:

- Under `python -O` (or PYTHONOPTIMIZE) every self-check — an `assert` — was
  compiled out. A healthy machine read "2 of 143 FAIL", and a report with
  every detector emptied got past ten checks that catch it without the flag:
  the tally no longer depended on the pipeline.
- A self-check that could not start printed "could not run" and exited 1,
  but the run log recorded no failure. `echolot` then said "doctor 0s ago,
  passed", and `next` did not send anyone back to it.
- `toolchain.tp_binary` in a local.yml was what `analyze` ran on, while
  `doctor` self-checked the pinned binary.

The self-check is stubbed wherever the question is which binary it was
handed, or what was recorded when it failed: 143 checks over a
trace_processor that does not exist would answer neither.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from echolot import recorder, selftest, state
from echolot import report as report_mod
from echolot.config import Config
from echolot.main import ASSERTS_SKIPPED, NOT_RUN, _binary_origin, main

ROOT = Path(__file__).resolve().parent.parent
CUSTOM = "/opt/custom/trace_processor_shell"
CONFIG = "project:\n  package: com.example.app\n"
LOCAL = f"toolchain:\n  tp_binary: {CUSTOM}\n"
BOTH_DOCTORS = pytest.mark.parametrize(
    "argv", [["doctor", "-q"], ["doctor"]], ids=["quiet", "full"])


def _project(root: Path, config: str = CONFIG, local: str | None = None) -> None:
    (root / "echolot.yml").write_text(config, encoding="utf-8")
    if local is not None:
        (root / "local.yml").write_text(local, encoding="utf-8")


def _optimized(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    """`python -O -m echolot.main …` from this checkout, logging into `cwd`."""
    env = {k: v for k, v in os.environ.items() if k != "ECHOLOT_NO_RECORD"}
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run([sys.executable, "-O", "-m", "echolot.main", *argv],
                          cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=120)


@pytest.fixture
def handed(monkeypatch) -> list:
    """The binary each self-check was given, collected instead of run."""
    seen: list = []

    def run(tp_binary=None):
        seen.append(tp_binary)
        return [("a check that holds", None)]

    monkeypatch.setattr(selftest, "run", run)
    return seen


@pytest.fixture
def logged(monkeypatch, tmp_path) -> Path:
    """A project in tmp_path whose run log is actually written."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    # `main` points the recorder at the project; this puts it back after.
    monkeypatch.setattr(recorder, "_root", None)
    return tmp_path / recorder.LOG_FILE


# --- -O: a verdict that no longer depends on the checks ---------------------

@BOTH_DOCTORS
def test_under_python_O_doctor_refuses_before_a_single_check(tmp_path, argv):
    """The refusal is the whole output, and the log says nothing was checked.

    Exactly one line, so there is nowhere for an environment block or a tally
    to hide: a check that ran would have printed one. A subprocess because
    `-O` is a property of the interpreter, and only a fresh one can have it.
    """
    run = _optimized(tmp_path, *argv)
    assert run.returncode == 1, run.stdout + run.stderr
    assert run.stdout.strip() == ASSERTS_SKIPPED, run.stdout + run.stderr

    runs = recorder.read(tmp_path / recorder.LOG_FILE)
    assert [r.get("cmd") for r in runs] == ["doctor"], runs
    facts = runs[0].get("facts") or {}
    assert facts.get("checks") == 0 and facts.get("failed") == [NOT_RUN], runs[0]


def test_under_python_O_init_ends_in_the_same_refusal(tmp_path):
    """`init` closes with `doctor -q`, and must not close with a fake tally.

    The layer still goes in — a Python flag is no reason to leave a project
    without the skill — and the exit code says the environment was not
    checked.
    """
    run = _optimized(tmp_path, "init", "--no-input", "--for", "claude")
    assert run.returncode == 1, run.stdout + run.stderr
    assert ASSERTS_SKIPPED in run.stdout.splitlines(), run.stdout
    assert (tmp_path / ".claude").is_dir(), "the layer was not installed"


# --- a self-check that could not run ----------------------------------------

@BOTH_DOCTORS
def test_a_self_check_that_could_not_run_is_recorded_as_failed(
        tmp_path, monkeypatch, capsys, logged, argv):
    """The readers of the log act on `failed`; now there is one to act on.

    What a first run offline, or `--tp-binary` pointing at nothing, left
    behind: exit 1, "could not run" on the screen, and a log line with no
    `failed` in it — which `echolot` printed as "doctor 0s ago, passed".
    """
    def offline(tp_binary=None):
        raise RuntimeError("could not download trace_processor: offline")

    monkeypatch.setattr(selftest, "run", offline)
    assert main(argv) == 1
    assert "could not run" in capsys.readouterr().out

    doctors = [r for r in recorder.read(logged) if r.get("cmd") == "doctor"]
    assert len(doctors) == 1, doctors
    facts = doctors[0].get("facts") or {}
    assert facts.get("failed") == [NOT_RUN], doctors[0]
    assert facts.get("checks") == 0, doctors[0]
    assert "offline" in (doctors[0].get("error") or ""), doctors[0]

    # The state the way `echolot` and `/echolot` build it, from that log.
    st = state.project_state(tmp_path)
    assert st["last_doctor"] == doctors[0], st["last_doctor"]
    # An absent layer outranks everything else in `next_kind`, and the layer
    # is not what this is about: say it is installed and current.
    st["layer_verdict"] = "current"
    assert state.next_kind(st) == "doctor", state.next_kind(st)

    # And `echolot` itself, which is where "passed" was read off.
    assert main([]) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines()
                if ln.startswith("doctor"))
    assert "passed" not in line, line


# --- the binary analyze would use -------------------------------------------

@pytest.mark.parametrize("argv,said", [
    (["doctor", "-q"], ["(custom binary from toolchain.tp_binary in local.yml)"]),
    (["doctor"], ["(from toolchain.tp_binary in local.yml)",
                  "custom binary, the pin in pyproject.toml is bypassed"]),
], ids=["quiet", "full"])
def test_doctor_checks_the_binary_local_yml_names_and_says_so(
        tmp_path, monkeypatch, capsys, handed, argv, said):
    """`analyze` ran on the binary local.yml named; doctor vouched for the pin."""
    _project(tmp_path, local=LOCAL)
    monkeypatch.chdir(tmp_path)

    assert main(argv) == 0
    out = capsys.readouterr().out
    assert handed == [CUSTOM], handed
    for words in said:
        assert words in out, f"{words!r} not in:\n{out}"
    # requirements.txt is `-e .`; the pin has only ever been in pyproject.toml.
    assert "requirements" not in out, out


def test_the_flag_still_outranks_local_yml(tmp_path, monkeypatch, capsys, handed):
    """The same order `analyze` uses: the flag, then the config, then the pin."""
    _project(tmp_path, local=LOCAL)
    monkeypatch.chdir(tmp_path)

    assert main(["doctor", "-q", "--tp-binary", "/opt/flag/tp"]) == 0
    assert handed == ["/opt/flag/tp"], handed
    assert "(custom binary from --tp-binary)" in capsys.readouterr().out


def test_the_file_named_is_the_one_that_holds_the_key(tmp_path):
    """local.yml is merged over echolot.yml, and either can hold the key.

    Here the committed config does and local.yml only carries a device, so
    pointing the reader at local.yml would send them to the wrong file.
    """
    _project(tmp_path, config=CONFIG + LOCAL, local="runner:\n  device: emulator-5554\n")
    cfg = Config.load(tmp_path / "echolot.yml")
    assert cfg.local_path is not None, "local.yml was not applied"
    assert _binary_origin("toolchain.tp_binary", cfg) == \
        f"toolchain.tp_binary in {tmp_path / 'echolot.yml'}"

    _project(tmp_path, local=LOCAL)
    cfg = Config.load(tmp_path / "echolot.yml")
    assert _binary_origin("toolchain.tp_binary", cfg) == \
        f"toolchain.tp_binary in {tmp_path / 'local.yml'}"


@pytest.mark.parametrize("config,local", [
    ("project:\n  package: [com.example.app\n", LOCAL),
    (CONFIG, LOCAL + "  [not yaml\n"),
], ids=["echolot.yml", "local.yml"])
def test_a_config_that_does_not_load_is_said_and_the_pin_is_checked(
        tmp_path, monkeypatch, capsys, handed, config, local):
    """A broken config is `analyze`'s error to stop on, not a reason to skip
    the check — but the binary it may name is unknown, and that is said."""
    _project(tmp_path, config=config, local=local)
    monkeypatch.chdir(tmp_path)

    assert main(["doctor", "-q"]) == 0
    err = capsys.readouterr().err
    assert handed == [None], handed
    assert "the config does not load" in err and "the pinned one" in err, err


# --- the report ---------------------------------------------------------------

def _fired(toolchain: dict) -> dict:
    """A report with one detector that fired: a quiet one ends before the footer."""
    return {
        "schema": 1, "generated_at": "2026-09-28T10:00:00+00:00",
        "trace": "t.perfetto-trace", "toolchain": toolchain,
        "window": {"process": "com.example.app", "duration_ms": 1000.0},
        "environment": {},
        "summary": {"detectors_run": 1, "detectors_fired": 1, "fired_ids": ["d"]},
        "detectors": [{"id": "d", "title": "d", "why": "", "params": {},
                       "params_source": "default", "error": None,
                       "rows": [{"location": "inflate", "count": 1, "total_ms": 86.0}]}],
    }


@pytest.mark.parametrize("toolchain,footer", [
    ({"trace_processor": "v48.0", "source": "toolchain.tp_binary", "binary": CUSTOM},
     "trace_processor v48.0 (custom binary from toolchain.tp_binary, pin bypassed)"),
    ({"trace_processor": "v48.0", "source": "--tp-binary", "binary": CUSTOM},
     "trace_processor v48.0 (custom binary from --tp-binary, pin bypassed)"),
    ({"trace_processor": None, "source": "toolchain.tp_binary", "binary": CUSTOM},
     "trace_processor unknown (custom binary from toolchain.tp_binary, pin bypassed)"),
    ({"trace_processor": "v56.1", "source": "pinned", "binary": None},
     "trace_processor v56.1"),
], ids=["local.yml", "flag", "no version", "pinned"])
def test_the_report_footer_names_whose_binary_ran(toolchain, footer):
    """Only `--tp-binary` was marked, so a local.yml binary read as the pin."""
    last = report_mod.to_markdown(_fired(toolchain)).splitlines()[-1]
    assert last == f"<sub>{footer}</sub>", last
