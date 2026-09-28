#!/usr/bin/env python3
"""The first run says what it downloads, and a download that fails is a sentence.

trace_processor is not in the wheel. The perfetto package pins one build per
OS and CPU, and the first command that opens a trace fetches it with curl
into ~/.local/share/perfetto/prebuilts. That used to happen inside perfetto,
and a first run showed it:

- `Downloading <url>` went to stdout, in front of the first `names --json`;
- offline, every command that opens a trace ended in a CalledProcessError
  traceback quoting a curl command line, and without curl in an Errno 2;
- `doctor` tried twice, once for the path it shows and again for the
  self-check, and said "could not run" with the same command line;
- nothing said what was being fetched, how big it was, where it went, that
  curl was needed, or what to do behind a proxy or with no network at all.

HOME points at an empty directory throughout, so the cache is empty whatever
this machine holds, and a fake curl stands first on PATH, so nothing here can
reach the network: one that fails the way an offline machine does, one that
is not there at all, and one that hands over the binary this machine already
has — the hash then matches, and the download goes through perfetto's own
check for real. The CLI runs as its own process, the way a person and an
agent run it, so "no traceback" means none anywhere in what it printed.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import __version__, recorder, tp  # noqa: E402
from tests.support import check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

CONFIG = """\
project:
  package: com.example.app
  process: com.example.app
scenario:
  name: fixture
  start: {name: AppStart}
  end: {name: Screen.firstFrame}
"""

# How a machine with no network answers, as far as curl can tell: a name
# that does not resolve.
OFFLINE = ('echo "curl: (6) Could not resolve host: commondatastorage.googleapis.com" >&2\n'
           "exit 6\n")

PIN = tp.pinned_build()
needs_a_pin = pytest.mark.skipif(PIN is None, reason="the pin has no build for this machine")


def fake_curl(bin_dir: Path, log: Path, body: str) -> Path:
    """A `curl` that writes down every call in `log`, then does `body`."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    curl = bin_dir / "curl"
    curl.write_text(f'#!/bin/sh\necho "$*" >> {shlex.quote(str(log))}\n{body}',
                    encoding="utf-8")
    curl.chmod(0o755)
    return bin_dir


def calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def cli(cwd: Path, home: Path, path: str, *argv: str, record: bool = False,
        module: str = "echolot.main") -> subprocess.CompletedProcess:
    """`python -m <module> …` from this checkout, with its own HOME and PATH."""
    env = dict(os.environ, HOME=str(home), PATH=path, PYTHONPATH=str(ROOT),
               ECHOLOT_NO_RECORD="1")
    if record:
        env.pop("ECHOLOT_NO_RECORD")
    home.mkdir(parents=True, exist_ok=True)
    return subprocess.run([sys.executable, "-m", module, *argv], cwd=cwd,
                          env=env, capture_output=True, encoding="utf-8",
                          timeout=300)


@pytest.fixture(scope="module")
def trace(tmp_path_factory) -> Path:
    """The self-check's synthetic trace: something real for `names` to open."""
    path = tmp_path_factory.mktemp("trace") / "fixture.perfetto-trace"
    done = subprocess.run([sys.executable, "-m", "echolot.fixture", str(path)],
                          capture_output=True, text=True,
                          env=dict(os.environ, ECHOLOT_NO_RECORD="1",
                                   PYTHONPATH=str(ROOT)))
    assert done.returncode == 0, done.stderr[-400:]
    return path


@pytest.fixture
def project(tmp_path) -> Path:
    """A directory with a config, for the commands that want one."""
    root = tmp_path / "app"
    root.mkdir()
    (root / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    return root


def announced(stderr: str, home: Path) -> None:
    """The notice before the download: what, how big, from where, into where."""
    check("the download is announced", "is not on this machine yet" in stderr, stderr)
    check("with its size", PIN.size_text in stderr, stderr)
    check("where it comes from", PIN.url in stderr, stderr)
    where = home / ".local" / "share" / "perfetto" / "prebuilts" / PIN.path.name
    check("and where it goes", str(where) in stderr, stderr)


# --- a download that fails ---------------------------------------------------

EVERY_VERB = [
    ("names", ["names", "{trace}"]),
    ("probe", ["probe", "{trace}"]),
    ("analyze", ["analyze", "{trace}"]),
    ("calibrate", ["calibrate", "{trace}"]),
    ("doctor", ["doctor"]),
    ("doctor-q", ["doctor", "-q"]),
    ("init", ["init", "--no-input"]),
]


@needs_a_pin
@pytest.mark.parametrize("argv", [c[1] for c in EVERY_VERB], ids=[c[0] for c in EVERY_VERB])
def test_offline_every_verb_says_why_and_exits_2_after_one_attempt(
        tmp_path, project, trace, argv):
    log = tmp_path / "curl.log"
    bin_dir = fake_curl(tmp_path / "bin", log, OFFLINE)
    home = tmp_path / "home"
    done = cli(project, home, f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
               *[a.format(trace=trace) for a in argv])
    said = done.stdout + done.stderr

    check("exit 2", done.returncode == 2, f"exit {done.returncode}\n{said[-2000:]}")
    check("no traceback", "Traceback" not in said, said[-2000:])
    check("one attempt", len(calls(log)) == 1, calls(log))
    announced(done.stderr, home)
    errors = [ln for ln in done.stderr.splitlines() if ln.startswith("error:")]
    check("one error line", len(errors) == 1, done.stderr[-2000:])
    check("naming what failed and why", errors and errors[0].startswith(
        f"error: trace_processor {PIN.version} could not be downloaded: curl "
        f"exited with status 6 — the server's name did not resolve"), errors)
    for way in ("HTTPS_PROXY", "curl, installed and on PATH", PIN.path.name):
        check(f"and the way round it: {way}", way in done.stderr, done.stderr[-2000:])
    check("perfetto's own line stays off stdout", "Downloading" not in done.stdout,
          done.stdout)
    check("nothing half-downloaded under the real name",
          not (home / ".local" / "share" / "perfetto" / "prebuilts"
               / PIN.path.name).exists())


@needs_a_pin
def test_a_doctor_with_no_trace_processor_is_logged_as_not_run(tmp_path, project):
    """So that `echolot` shows the last doctor as failed, and routes back to it."""
    from echolot.main import NOT_RUN

    bin_dir = fake_curl(tmp_path / "bin", tmp_path / "curl.log", OFFLINE)
    done = cli(project, tmp_path / "home",
               f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
               "doctor", "-q", record=True)
    assert done.returncode == 2, done.stdout + done.stderr
    runs = recorder.read(project / recorder.LOG_FILE)
    check("one line in the run log", len(runs) == 1, runs)
    facts = runs[0].get("facts") or {}
    check("a self-check that did not run",
          facts.get("checks") == 0 and facts.get("failed") == [NOT_RUN], runs[0])
    check("and why", "could not be downloaded" in (runs[0].get("error") or ""), runs[0])


@needs_a_pin
def test_without_curl_the_error_says_so(tmp_path, project, trace):
    """PATH holds nothing at all, so there is no curl to find — not even the real one."""
    empty = tmp_path / "empty"
    empty.mkdir()
    done = cli(project, tmp_path / "home", str(empty), "names", str(trace))
    said = done.stdout + done.stderr
    check("exit 2", done.returncode == 2, said[-2000:])
    check("no traceback", "Traceback" not in said, said[-2000:])
    check("the cause is curl", "curl is not installed, or not on PATH" in done.stderr,
          done.stderr[-2000:])


@needs_a_pin
def test_one_attempt_per_process_whoever_asks_again(tmp_path, monkeypatch):
    """doctor asks for the path, then the self-check asks again; analyze asks
    once per trace. A second ask after a failure gets the same error, and
    curl is not run again."""
    log = tmp_path / "curl.log"
    bin_dir = fake_curl(tmp_path / "bin", log, OFFLINE)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    said = []
    for _ in range(2):
        with pytest.raises(tp.ToolchainError) as e:
            tp.resolve_binary_path()
        said.append(str(e.value))
    check("one attempt", len(calls(log)) == 1, calls(log))
    check("the same sentence twice", said[0] == said[1], said)


@needs_a_pin
def test_a_page_in_place_of_the_binary_is_refused_by_its_hash(tmp_path, monkeypatch):
    """A captive portal or a proxy answers with a page of its own and curl
    exits 0. perfetto's hash check is what catches it, and its words for that
    are what the error recognises — so they are exercised here, not quoted."""
    portal = ('while [ $# -gt 0 ]; do\n'
              '  if [ "$1" = "-o" ]; then echo "<html>log in first</html>" > "$2"; fi\n'
              '  shift\n'
              'done\n')
    bin_dir = fake_curl(tmp_path / "bin", tmp_path / "curl.log", portal)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    with pytest.raises(tp.ToolchainError) as e:
        tp.resolve_binary_path()
    check("the hash is named as the cause", "its SHA-256 differs" in str(e.value), e.value)
    check("and the page was not kept under the pinned name",
          not tp.pinned_build().path.exists())


@pytest.mark.parametrize("error, says", [
    (subprocess.CalledProcessError(99, ["curl"]), "curl exited with status 99"),
    (PermissionError(13, "Permission denied", "/home/you/.local/share/perfetto"),
     "[Errno 13] Permission denied: '/home/you/.local/share/perfetto'"),
], ids=["an-exit-status-with-no-gloss", "a-cache-that-cannot-be-written"])
def test_any_other_cause_is_said_as_it_came(error, says):
    check("said as it came", tp._cause(error) == says, tp._cause(error))


def test_a_machine_the_pin_has_no_build_for_is_a_sentence(monkeypatch):
    monkeypatch.setattr(tp.platform, "machine", lambda: "riscv64")
    with pytest.raises(tp.ToolchainError) as e:
        tp.resolve_binary_path()
    check("which says so", "no build for this machine" in str(e.value), e.value)
    check("and what to use instead", "--tp-binary" in str(e.value), e.value)


def test_a_named_binary_that_is_not_there_is_a_sentence(tmp_path, trace):
    """`--tp-binary` with a typo. perfetto refused it with a bare Exception,
    which came out of `names` as a traceback."""
    from echolot.config import ConfigError

    with pytest.raises(ConfigError) as e:
        tp.TraceSession(trace, str(tmp_path / "trace_processor_shell"))
    check("which names the path", f"no trace_processor at {tmp_path}" in str(e.value),
          e.value)
    check("and is not taken for a download that failed",
          not isinstance(e.value, tp.ToolchainError), type(e.value))


# --- a download that goes through --------------------------------------------

@needs_a_pin
def test_a_first_names_json_is_json_and_the_notice_is_on_stderr(tmp_path, trace):
    real = PIN.path
    if not real.is_file():
        pytest.skip(f"no cached trace_processor at {real} to hand the fake curl")
    log = tmp_path / "curl.log"
    serves = ('while [ $# -gt 0 ]; do\n'
              f'  if [ "$1" = "-o" ]; then exec cp {shlex.quote(str(real))} "$2"; fi\n'
              '  shift\n'
              'done\n'
              'exit 2\n')
    bin_dir = fake_curl(tmp_path / "bin", log, serves)
    home = tmp_path / "home"
    path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    done = cli(tmp_path, home, path, "names", "--json", str(trace))
    assert done.returncode == 0, done.stdout + done.stderr
    try:
        inventory = json.loads(done.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(f"stdout is not JSON ({e}):\n{done.stdout[:600]}") from e
    check("and it is the inventory", inventory.get("process") == "com.example.app",
          inventory)
    announced(done.stderr, home)
    check("one download", len(calls(log)) == 1, calls(log))
    landed = home / ".local" / "share" / "perfetto" / "prebuilts" / PIN.path.name
    check("under the pinned name", landed.is_file(), landed)

    again = cli(tmp_path, home, path, "names", "--json", str(trace))
    assert again.returncode == 0, again.stdout + again.stderr
    check("the second run finds it", len(calls(log)) == 1, calls(log))
    check("and says nothing about it", "is not on this machine yet" not in again.stderr,
          again.stderr)


@needs_a_pin
def test_the_destination_announced_is_the_one_perfetto_writes(tmp_path, monkeypatch):
    """`pinned_build` works the path out the way perfetto does, so that "is it
    here" and "where it will go" can be said before a download; if a perfetto
    release changed its naming, the notice would name a file nobody writes.

    perfetto returns early for a file that is already there, so one is put
    where echolot expects it. A failing curl stands first on PATH in case the
    two disagree: then perfetto would look elsewhere and try to download, and
    this has to fail rather than reach the network.
    """
    _, download_or_get_cached = tp._perfetto_prebuilts()
    bin_dir = fake_curl(tmp_path / "bin", tmp_path / "curl.log", OFFLINE)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    pin = tp.pinned_build()
    pin.path.parent.mkdir(parents=True)
    pin.path.write_bytes(b"")
    got = download_or_get_cached(file_name=pin.file_name, url=pin.url, sha256=pin.sha256)
    check("the same path", Path(got) == pin.path, f"{got} != {pin.path}")
    check("and no download", calls(tmp_path / "curl.log") == [])


@pytest.mark.skipif(sys.platform == "win32", reason="the sample is a POSIX path")
def test_the_samples_in_determinism_md_are_what_the_tool_prints(monkeypatch):
    """docs/determinism.md shows the notice and the error for an Intel Mac
    with no network. A sample that drifts from the tool sends a reader
    looking for words it no longer prints, so both are rebuilt here from the
    manifest for that machine and must appear in the page as they are."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(tp.platform, "machine", lambda: "x86_64")
    monkeypatch.setenv("HOME", "/Users/you")
    pin = tp.pinned_build()
    page = (ROOT / "docs" / "determinism.md").read_text(encoding="utf-8")
    offline = tp._cause(subprocess.CalledProcessError(6, ["curl"]))
    for what, text in (("the notice", tp._notice(pin)),
                       ("the error", f"error: {tp._cannot_fetch(pin, offline)}")):
        check(f"{what} is in the page as the tool prints it", text in page, text)


# --- --version, and python -m echolot ------------------------------------------

@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_names_the_pin_without_downloading_or_logging(tmp_path, flag):
    """Offline and on a first run as well: the pin is read off the manifest.

    With the run log switched on, because a `--version` must not leave a
    line in it — nor a .echolot/ in whatever directory it was typed.
    """
    log = tmp_path / "curl.log"
    bin_dir = fake_curl(tmp_path / "bin", log, OFFLINE)
    done = cli(tmp_path, tmp_path / "home",
               f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", flag, record=True)
    assert done.returncode == 0, done.stdout + done.stderr
    info = tp.toolchain_info()
    check("one line, the one doctor -q opens with", done.stdout ==
          f"echolot {__version__} · trace_processor {info['trace_processor']} · "
          f"perfetto {info['perfetto_package']} · python "
          f"{platform.python_version()}\n", done.stdout)
    check("nothing downloaded", calls(log) == [], calls(log))
    check("nothing recorded", not (tmp_path / ".echolot").exists())


def test_python_m_echolot_is_the_cli(tmp_path):
    done = cli(tmp_path, tmp_path / "home", os.environ.get("PATH", ""), "--help",
               module="echolot")
    assert done.returncode == 0, done.stdout + done.stderr
    check("the help of the CLI", done.stdout.startswith("usage: echolot"), done.stdout[:300])
    check("with the command table", "Yours:" in done.stdout, done.stdout[:600])
