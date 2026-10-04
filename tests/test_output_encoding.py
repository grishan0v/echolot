#!/usr/bin/env python3
"""What echolot prints reaches a pipe whole, whatever the pipe's encoding.

On Windows, Python 3.10–3.14 encode redirected output in the ANSI code page —
cp1252 and its neighbours — and only 3.15 makes UTF-8 the default (PEP 686).
Agents and CI read this tool through a pipe and nowhere else, and three
commands died there on the first character cp1252 has no room for:

- `init` on the `↑` of a .gitignore it had just updated — with .gitignore
  and .echolot/hosts.json written, exit 1, and no layer installed;
- `echolot` and `doctor -q` on the `→` of a stale layer.

PYTHONIOENCODING=cp1252 puts that encoding on a child's streams on any OS,
which is how CI on Linux gets to run the Windows case. Each case is its own
process, because the encoding is settled when the interpreter starts. The
output is read back as UTF-8: the arrows arriving as themselves, and not as
`?`, is the choice `main` made, and it is checked here too. Bytes that are
not UTF-8 are replaced on the way in rather than raised on, so that a run
which went out in cp1252 fails on what it said rather than inside
`subprocess`.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import main as main_mod  # noqa: E402
from tests.support import check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LAYER_FILE = Path(".claude") / "skills" / "echolot" / "SKILL.md"


def cli(cwd: Path, *argv: str, encoding: str | None = "cp1252",
        timeout: int = 300) -> subprocess.CompletedProcess:
    """`python -m echolot.main …` from this checkout, its streams in `encoding`."""
    env = dict(os.environ, ECHOLOT_NO_RECORD="1", PYTHONPATH=str(ROOT))
    env.pop("PYTHONIOENCODING", None)
    if encoding:
        env["PYTHONIOENCODING"] = encoding
    return subprocess.run([sys.executable, "-m", "echolot.main", *argv],
                          cwd=cwd, env=env, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def unbroken(done: subprocess.CompletedProcess) -> str:
    said = done.stdout + done.stderr
    check("exit 0", done.returncode == 0, f"exit {done.returncode}\n{said[-1500:]}")
    check("no UnicodeEncodeError", "UnicodeEncodeError" not in said, said[-1500:])
    check("no traceback", "Traceback" not in said, said[-1500:])
    return done.stdout


def git_project(root: Path) -> Path:
    """A git root with a .gitignore of its own, which is what `init` updates.

    `.git` as a bare directory is what `init` looks for; git itself is not
    needed to make one.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir()
    (root / ".gitignore").write_text("build/\n", encoding="utf-8")
    return root


def stale_project(root: Path) -> Path:
    """A project whose layer went in and then lost a file: `STALE — 1 missing → …`."""
    git_project(root)
    done = cli(root, "init", "--no-input", "--no-doctor", encoding=None)
    assert done.returncode == 0, done.stdout + done.stderr
    (root / ".claude" / "commands" / "echolot-setup.md").unlink()
    return root


def test_init_installs_the_layer_through_a_cp1252_pipe(tmp_path):
    project = git_project(tmp_path / "app")
    out = unbroken(cli(project, "init", "--no-input", "--no-doctor"))
    check("the layer went in", (project / LAYER_FILE).is_file(), out)
    # The character it died on, arriving as itself: UTF-8 went out, and not
    # a replacement that would have turned it into `?`.
    check("the .gitignore line keeps its arrow", "↑ .gitignore" in out, out)


def test_status_reads_a_stale_layer_through_a_cp1252_pipe(tmp_path):
    project = stale_project(tmp_path / "app")
    out = unbroken(cli(project))
    check("the layer is said to be stale", "STALE" in out, out)
    check("with the arrow to the fix", "→ `echolot init`" in out, out)


def test_doctor_q_reads_a_stale_layer_through_a_cp1252_pipe(tmp_path):
    """One real run, self-check included — about fifteen seconds."""
    project = stale_project(tmp_path / "app")
    out = unbroken(cli(project, "doctor", "-q"))
    lines = out.splitlines()
    check("three lines", len(lines) == 3, out)
    check("the layer line keeps its arrow",
          lines[1].startswith("layer: STALE") and "→" in lines[1], out)
    check("and the tally comes after it", lines[2].startswith("self-check:")
          and lines[2].endswith("passed"), out)


# --- the function itself, on streams made here ------------------------------

def _stream(encoding: str | None) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict")


@pytest.mark.parametrize("name", ["stdout", "stderr", "stdin"])
def test_a_stream_that_cannot_carry_the_symbols_becomes_utf8(monkeypatch, name):
    stream = _stream("cp1252")
    monkeypatch.setattr(sys, name, stream)
    main_mod._streams_that_carry_the_output()
    check(f"{name} is UTF-8 now", stream.encoding == "utf-8", stream.encoding)
    wanted = "replace" if name == "stdin" else "backslashreplace"
    check(f"and cannot raise: {wanted}", stream.errors == wanted, stream.errors)


def test_a_stream_that_can_carry_them_keeps_its_encoding(monkeypatch):
    stream = _stream("utf-16")
    monkeypatch.setattr(sys, "stdout", stream)
    main_mod._streams_that_carry_the_output()
    check("the encoding it had", stream.encoding == "utf-16", stream.encoding)
    # Strict, it raised on a lone surrogate: a file name that is not UTF-8.
    check("and no strict handler", stream.errors == "backslashreplace", stream.errors)


def test_a_stream_that_is_not_a_real_one_is_left_alone(monkeypatch):
    """A StringIO the self-check swaps in, and a stdin that is closed."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stdin", None)
    main_mod._streams_that_carry_the_output()
    print("→ still printable", file=sys.stdout)
