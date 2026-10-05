#!/usr/bin/env python3
"""What a person types wrong is a sentence and exit 2, never a traceback.

A traceback out of the CLI is a bug in echolot, and not a private one:
`reflect` reads the run log, finds the exception and files it as exactly
that. Every case below ended in one, and none was a bug in anything but the
answer — a file that is not a trace, a glob that YAML reads as an alias,
`true` under a detector, a section written as a list or as one word, a
config naming no package, a unit written into a number.

Each runs as its own process, the way a person and an agent both run the
tool, so that "no Traceback anywhere in the output" means exactly that. An
`adb` that only complains stands first on PATH: none of these may get as far
as a device, and if one did, it would say so here rather than ask a real one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.main import DETECTOR_DIR, parse_set  # noqa: E402
from echolot.tp import load_detectors  # noqa: E402
from tests.support import check  # noqa: E402

CONFIG = """\
project:
  package: com.example.app
  process: com.example.app
scenario:
  name: fixture
  start: {name: AppStart}
  end: {name: Screen.firstFrame}
"""

# What a capture cut short or a log saved under the wrong name looks like to
# trace_processor: bytes of no format it knows.
NOT_A_TRACE = b"this is a build log that was saved under the wrong name\n" * 20


def cli(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    fake = cwd / "bin"
    fake.mkdir(exist_ok=True)
    adb = fake / "adb"
    adb.write_text("#!/bin/sh\necho 'a real adb was asked' >&2\nexit 1\n", encoding="utf-8")
    adb.chmod(0o755)
    env = dict(os.environ, ECHOLOT_NO_RECORD="1",
               PATH=f"{fake}{os.pathsep}{os.environ.get('PATH', '')}")
    return subprocess.run([sys.executable, "-m", "echolot.main", *argv],
                          capture_output=True, text=True, cwd=cwd, env=env,
                          timeout=300)


def refused(done: subprocess.CompletedProcess, *words: str) -> None:
    said = done.stdout + done.stderr
    check("exit 2", done.returncode == 2, f"exit {done.returncode}\n{said[-1500:]}")
    check("no traceback", "Traceback" not in said, said[-1500:])
    lines = [ln for ln in done.stderr.splitlines()
             if ln.startswith(("error:", "config error:", "collection error:"))]
    check("an error line", lines, done.stderr[-800:])
    for w in words:
        check(f"which says {w!r}", w in done.stderr, done.stderr[-800:])
    check("and no device was asked", "a real adb was asked" not in said, said[-800:])


TRUE_UNDER_A_DETECTOR = CONFIG + "detectors:\n  frame_jank: true\n"
DETECTORS_AS_A_LIST = CONFIG + "detectors:\n  - frame_jank\n  - gc_pressure\n"

CASES = [
    # A file that is there and is not a trace, through every verb that opens one.
    ("analyze-not-a-trace", CONFIG, ["analyze", "bad.perfetto-trace"],
     ["bad.perfetto-trace", "cannot read this file"]),
    ("probe-not-a-trace", CONFIG, ["probe", "bad.perfetto-trace"],
     ["bad.perfetto-trace", "cannot read this file"]),
    ("names-not-a-trace", CONFIG, ["names", "bad.perfetto-trace"],
     ["bad.perfetto-trace", "cannot read this file"]),
    ("calibrate-not-a-trace", CONFIG, ["calibrate", "bad.perfetto-trace"],
     ["bad.perfetto-trace", "cannot read this file"]),
    # A glob where a number goes: YAML reads `*16*` as an alias and used to
    # raise from inside `--set`; now it is a value, refused for its kind.
    ("set-a-glob-for-a-number", CONFIG,
     ["analyze", "bad.perfetto-trace", "--set", "main_thread_block.min_slice_ms=*16*"],
     ["min_slice_ms", "must be a number"]),
    ("analyze-true-under-a-detector", TRUE_UNDER_A_DETECTOR,
     ["analyze", "bad.perfetto-trace"],
     ["detectors.frame_jank: true is not a setting", "frame_jank: false"]),
    ("calibrate-true-under-a-detector", TRUE_UNDER_A_DETECTOR,
     ["calibrate", "bad.perfetto-trace"],
     ["detectors.frame_jank: true is not a setting"]),
    ("calibrate-detectors-as-a-list", DETECTORS_AS_A_LIST,
     ["calibrate", "bad.perfetto-trace"],
     ["detectors section must be a mapping", "got a list"]),
    ("collect-runner-as-one-word", CONFIG + "runner: gradle\n", ["collect"],
     ["runner section must be a mapping", "mode: gradle"]),
    ("collect-without-a-package", "scenario:\n  name: coldStart\n", ["collect"],
     ["neither project.process nor project.package"]),
    ("collect-iterations-as-a-word", CONFIG + "runner:\n  iterations: five\n", ["collect"],
     ["runner.iterations", "five"]),
    ("collect-duration-with-a-unit", CONFIG + "runner:\n  duration_ms: 12s\n", ["collect"],
     ["runner.duration_ms", "12s"]),
    ("collect-timeout-with-a-unit",
     CONFIG + "runner:\n  mode: gradle\n  gradle_task: x\n  timeout_s: 1h\n", ["collect"],
     ["runner.timeout_s", "1h"]),
]


@pytest.mark.parametrize("config, argv, words", [c[1:] for c in CASES],
                         ids=[c[0] for c in CASES])
def test_bad_input_is_a_sentence_and_exit_2(tmp_path, config, argv, words):
    (tmp_path / "echolot.yml").write_text(config, encoding="utf-8")
    (tmp_path / "bad.perfetto-trace").write_bytes(NOT_A_TRACE)
    refused(cli(tmp_path, *argv), *words)


@pytest.fixture(scope="module")
def fixture_trace(tmp_path_factory) -> Path:
    """The self-check's synthetic trace, built once for the two runs below."""
    path = tmp_path_factory.mktemp("fixture") / "fixture.perfetto-trace"
    done = subprocess.run([sys.executable, "-m", "echolot.fixture", str(path)],
                          capture_output=True, text=True,
                          env=dict(os.environ, ECHOLOT_NO_RECORD="1"))
    assert done.returncode == 0, done.stderr[-400:]
    return path


def test_one_bad_file_among_the_repeats_is_named(tmp_path, fixture_trace):
    """It used to abort the whole multi-trace analyze with a traceback that
    named no file, so finding the one to leave out meant bisecting by hand."""
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "cut-short.perfetto-trace").write_bytes(NOT_A_TRACE)
    done = cli(tmp_path, "analyze", str(fixture_trace), "cut-short.perfetto-trace")
    refused(done, "cut-short.perfetto-trace", "leave it out")


def test_a_mask_is_taken_as_typed(tmp_path, fixture_trace):
    """`*GC*` is a glob: the same shape as the shipped `*GC`, and a value."""
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    done = cli(tmp_path, "analyze", str(fixture_trace),
               "--set", "gc_pressure.name_glob=*GC*")
    check("exit 0", done.returncode == 0, done.stderr[-800:])
    report = json.loads((tmp_path / ".echolot" / "out" / "report.json").read_text(encoding="utf-8"))
    gc = next(d for d in report["detectors"] if d["id"] == "gc_pressure")
    check("the mask reached the detector as typed",
          gc["params"]["name_glob"] == "*GC*" and gc["params_source"] == "cli", gc["params"])


def test_what_set_hands_the_detectors():
    """The rule, without a process: a mask is kept as typed, a number is read.

    A string parameter keeps the text whenever YAML would make it anything
    else — an alias error, a list, a boolean — and a numeric one is read as
    YAML, so `16` and `4.5` arrive as numbers the way they do from the file.
    """
    got = parse_set(["gc_pressure.name_glob=*GC*",
                     "binder_txn.skip_glob=*async*",
                     "gc_pressure.name_glob_alt=[Ww]",
                     "monitor_contention.name_glob=on",
                     "main_thread_block.min_slice_ms=16",
                     "gc_pressure.max_total_ms=4.5",
                     "binder_txn.min_txn_ms=*10*"],
                    load_detectors(DETECTOR_DIR))
    check("globs as typed",
          got["gc_pressure"] == {"name_glob": "*GC*", "name_glob_alt": "[Ww]",
                                 "max_total_ms": 4.5}
          and got["binder_txn"]["skip_glob"] == "*async*"
          and got["monitor_contention"]["name_glob"] == "on", got)
    check("numbers as numbers", got["main_thread_block"]["min_slice_ms"] == 16, got)
    check("and a glob where a number goes is kept for `check` to refuse",
          got["binder_txn"]["min_txn_ms"] == "*10*", got)


@pytest.mark.parametrize("verb", ["compare", "report"])
def test_a_file_that_is_no_report_is_a_sentence(tmp_path, verb):
    """A trace where its report belongs, and JSON that is not an object."""
    (tmp_path / "trace.perfetto-trace").write_bytes(bytes([0x0a, 0xae, 0xff, 0x01]) * 64)
    (tmp_path / "arr.json").write_text("[]", encoding="utf-8")
    for name in ("trace.perfetto-trace", "arr.json"):
        argv = [verb, name] + (["arr.json"] if verb == "compare" else [])
        refused(cli(tmp_path, *argv), name, "not a Marker Report")
