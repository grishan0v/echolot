#!/usr/bin/env python3
"""The last places where the tool said more than it did, before 0.8.0.

- `init` closed on "the self-check failed (see above)", and `next` said "the
  last self-check failed", for a self-check that never ran as well. #118
  logs one as `checks: 0` beside one entry in `failed`, and since #126
  `echolot`'s doctor line says it did not run.
- With no investigation open, `echolot` promised "the next hunt opens one"
  while echolot.yml did not load — which is when `hunt "<q>"` refuses, since
  #126.
- A download of the pinned trace_processor that failed left its file in the
  cache. perfetto fetches into `<binary>.<number>.tmp` and gives the file the
  pinned name only once the hash matches, with a new number for every
  attempt, so each failure left one more: part of the binary, or a whole
  file that is not the pin.

Wherever a download is attempted here, HOME is an empty directory and PATH
holds a fake `curl` and nothing else, so the real one cannot run.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from echolot import hunt as hunt_mod
from echolot import recorder, selftest, state, tp
from echolot.main import ASSERTS_SKIPPED, NOT_RUN, main
from tests.support import check

ROOT = Path(__file__).resolve().parent.parent
CONFIG = """\
project:
  package: com.example.app
  process: com.example.app
scenario:
  name: coldStart
"""
# `true` is not a setting under a detector, so the whole config is refused
# on load — the shape #121 made fatal.
BROKEN = CONFIG + "detectors:\n  frame_jank: true\n"

PIN = tp.pinned_build()
needs_a_pin = pytest.mark.skipif(PIN is None, reason="the pin has no build for this machine")


def run(*argv: str) -> tuple[int, str, str]:
    """A command, with what it printed on each stream."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def project(monkeypatch, tmp_path) -> Path:
    """An empty project directory to run from, and the recorder put back after.

    `main` points the recorder at the project it runs in, and that global
    would otherwise outlive the test.
    """
    root = tmp_path / "app"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(recorder, "_root", None)
    return root


def fake_curl(bin_dir: Path, body: str) -> str:
    """A PATH with one program on it: a `curl` that does `body`."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    curl = bin_dir / "curl"
    curl.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    curl.chmod(0o755)
    return str(bin_dir)


def writes(text: str, then: int, wrote: Path) -> str:
    """A curl body: `text` into the file after `-o`, that file's name into
    `wrote`, then exit `then`. Shell builtins only — PATH has nothing else."""
    return ('while [ $# -gt 0 ]; do\n'
            '  if [ "$1" = "-o" ]; then\n'
            f'    printf %s {shlex.quote(text)} > "$2"\n'
            f'    echo "$2" >> {shlex.quote(str(wrote))}\n'
            '  fi\n'
            '  shift\n'
            'done\n'
            f'exit {then}\n')


# --- `next`: a self-check that never ran did not fail -----------------------

def _next_after(root: Path, facts: dict) -> str:
    """The `next` step over a run log whose last doctor noted `facts`."""
    log = root / recorder.LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({
        "ts": "2026-09-28T10:00:00+00:00", "cmd": "doctor",
        "argv": ["doctor", "-q"], "exit": 1, "facts": facts,
    }) + "\n", encoding="utf-8")
    st = state.project_state(root)
    # An absent layer outranks a failed doctor in `next_kind`, and the layer
    # is not what this is about.
    st["layer_verdict"] = "current"
    check("routed back to doctor", state.next_kind(st) == "doctor", state.next_kind(st))
    return state.next_step(st)


@pytest.mark.parametrize("facts,said,unsaid", [
    ({"checks": 0, "failed": [NOT_RUN]}, "the last self-check did not run", "failed"),
    ({"checks": 143, "failed": ["a check"]}, "the last self-check failed", "did not run"),
], ids=["did-not-run", "failed"])
def test_next_says_whether_the_last_self_check_ran(project, facts, said, unsaid):
    """`checks: 0` and `NOT_RUN` is what `_record_not_run` writes."""
    step = _next_after(project, facts)
    check("the step is still doctor", step.startswith("echolot doctor —"), step)
    check(f"it says {said!r}", said in step, step)
    check(f"and not {unsaid!r}", unsaid not in step, step)


# --- init's closing line: the same words ------------------------------------

def _next_line(out: str) -> str:
    return next((ln for ln in out.splitlines() if ln.startswith("next")), "")


def _init(*flags: str) -> tuple[int, str, str]:
    """`echolot init` into the project, with its closing check."""
    code, out, err = run("init", "--no-input", "--for", "claude", *flags)
    return code, _next_line(out), out + err


def _did_not_run(line: str) -> None:
    check("the next line says the self-check did not run",
          "the self-check did not run" in line, line)
    check("and not that it failed", "failed" not in line, line)


@needs_a_pin
def test_init_without_trace_processor_says_the_self_check_did_not_run(
        project, monkeypatch, tmp_path):
    """The pin is not here and cannot be downloaded: nothing was checked."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", fake_curl(tmp_path / "bin", "exit 6\n"))
    monkeypatch.setattr(tp, "_FAILED", {})
    code, line, said = _init()
    check("exit 2", code == 2, f"exit {code}\n{said[-2000:]}")
    check("with the download's error", "could not be downloaded" in said, said[-2000:])
    _did_not_run(line)


def test_init_on_a_binary_that_is_not_there_says_the_self_check_did_not_run(
        project, tmp_path):
    """`--tp-binary` pointing at nothing: the self-check could not start."""
    code, line, said = _init("--tp-binary", str(tmp_path / "nowhere" / "trace_processor_shell"))
    check("exit 1", code == 1, f"exit {code}\n{said[-2000:]}")
    check("with why", "self-check: could not run" in said, said[-2000:])
    _did_not_run(line)


def test_init_under_python_O_says_the_self_check_did_not_run(tmp_path):
    """Refused before anything is checked. A subprocess: `-O` belongs to the
    interpreter, and only a fresh one can have it."""
    done = subprocess.run([sys.executable, "-O", "-m", "echolot.main", "init",
                           "--no-input", "--for", "claude"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=120,
                          env=dict(os.environ, PYTHONPATH=str(ROOT),
                                   ECHOLOT_NO_RECORD="1"))
    check("exit 1", done.returncode == 1, done.stdout + done.stderr)
    check("the refusal is said", ASSERTS_SKIPPED in done.stdout.splitlines(), done.stdout)
    _did_not_run(_next_line(done.stdout))


def test_init_keeps_failed_for_a_self_check_that_ran_and_failed(project, monkeypatch):
    def a_mismatch(tp_binary=None):
        return [("a check that holds", None), ("a check that moved", "5 != 6")]

    monkeypatch.setattr(selftest, "run", a_mismatch)
    code, line, said = _init()
    check("exit 1", code == 1, f"exit {code}\n{said[-2000:]}")
    check("the next line says it failed", "the self-check failed" in line, line)
    check("and not that it did not run", "did not run" not in line, line)


# --- the hunt line: no promise the config will not keep ---------------------

@pytest.mark.parametrize("config,promised", [
    (None, True),
    (CONFIG, True),
    (BROKEN, False),
], ids=["no-config", "a-config-that-loads", "a-config-that-does-not-load"])
def test_the_hunt_line_promises_only_what_hunt_does(project, config, promised):
    """The line is read against what `hunt "<q>"` then does, not against itself."""
    if config is not None:
        (project / "echolot.yml").write_text(config, encoding="utf-8")
    code, out, _ = run()
    assert code == 0, out
    line = next(ln for ln in out.splitlines() if ln.startswith("hunt"))
    check("no investigation is open", "none open" in line, line)
    says_it_opens = "the next hunt opens one" in line
    check("the promise is there or not", says_it_opens == promised, line)
    if not promised:
        check("and the line says what it waits on", "once echolot.yml loads" in line, line)

    code, _, err = run("hunt", "cold start 3s → 7s")
    opened = code == 0 and hunt_mod.load(project) is not None
    check("and hunt does what the line said", opened == promised,
          f"exit {code}, opened: {opened}\n{line}\n{err}")


# --- a failed download takes its own file with it ---------------------------

@pytest.fixture
def cache(tmp_path, monkeypatch) -> tuple[tp.PinnedBuild, dict[str, bytes]]:
    """perfetto's cache under an empty HOME, holding what no attempt may touch:
    an earlier run's leftover of this build, another build's binary and its
    download, and a file that is not perfetto's."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(tp, "_FAILED", {})
    pin = tp.pinned_build()
    root, ext = os.path.splitext(pin.file_name)
    other = f"{root}-{'0' * 16}{ext}"
    kept = {
        f"{pin.path.name}.77.tmp": b"an earlier run's leftover",
        other: b"another build's binary",
        f"{other}.4242.tmp": b"another build's download",
        "notes.txt": b"not perfetto's",
    }
    pin.path.parent.mkdir(parents=True)
    for name, data in kept.items():
        (pin.path.parent / name).write_bytes(data)
    return pin, kept


def _listing(pin: tp.PinnedBuild) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in pin.path.parent.iterdir()}


def _attempt(monkeypatch, tmp_path, body: str) -> str:
    """One download through `resolve_binary_path`, with `body` as curl."""
    monkeypatch.setenv("PATH", fake_curl(tmp_path / "bin", body))
    with pytest.raises(tp.ToolchainError) as e:
        tp.resolve_binary_path()
    return str(e.value)


@needs_a_pin
@pytest.mark.parametrize("text,then,cause", [
    ("the first half of a binary", 56, "curl exited with status 56"),
    ("<html>log in first</html>", 0, "its SHA-256 differs"),
], ids=["curl-broke-off", "hash-mismatch"])
def test_a_failed_download_takes_its_own_file_with_it(
        tmp_path, monkeypatch, capsys, cache, text, then, cause):
    pin, kept = cache
    wrote = tmp_path / "wrote"
    error = _attempt(monkeypatch, tmp_path, writes(text, then, wrote))
    check("the download failed as planted", cause in error, error)

    created = wrote.read_text(encoding="utf-8").split()
    check("curl did write a file, under perfetto's temporary name",
          len(created) == 1 and created[0].startswith(f"{pin.path}.")
          and created[0].endswith(".tmp"), created)
    check("which is gone", not Path(created[0]).exists(), created[0])
    check("everything else in the cache is as it was", _listing(pin) == kept,
          sorted(_listing(pin)))
    check("and nothing is under the pinned name", not pin.path.exists())
    said = capsys.readouterr()
    check("nothing is said about it: the notice, and the error raised",
          said.err == tp._notice(pin) + "\n" and said.out == "", said)


@needs_a_pin
def test_an_attempt_that_wrote_nothing_removes_nothing_and_says_nothing(
        tmp_path, monkeypatch, capsys, cache):
    """Offline, curl gives up before it creates the file."""
    pin, kept = cache
    error = _attempt(monkeypatch, tmp_path, "exit 6\n")
    offline = tp._cause(subprocess.CalledProcessError(6, ["curl"]))
    check("the error is the one it always was", error == tp._cannot_fetch(pin, offline),
          error)
    check("the cache is as it was", _listing(pin) == kept, sorted(_listing(pin)))
    said = capsys.readouterr()
    check("and nothing is said but the notice",
          said.err == tp._notice(pin) + "\n" and said.out == "", said)


@needs_a_pin
def test_only_this_builds_temporary_name_counts(cache):
    """The binary itself, and every name merely like the shape, are not it."""
    pin, _ = cache
    where = pin.path.parent
    pin.path.write_bytes(b"the binary")
    ours = where / f"{pin.path.name}.31337.tmp"
    for name in (ours.name, f"{pin.path.name}.tmp", f"{pin.path.name}.12a.tmp",
                 f"{pin.path.name}.12.tmp.part", f"x{pin.path.name}.12.tmp"):
        (where / name).write_bytes(b"")
    check("this build's `.<digits>.tmp` names and nothing else",
          tp._partials(pin) == {ours, where / f"{pin.path.name}.77.tmp"},
          sorted(p.name for p in tp._partials(pin)))
