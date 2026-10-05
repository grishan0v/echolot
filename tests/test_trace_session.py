"""Opening a trace: the binary named, the path given, the script split, the
download interrupted.

Each case is a way a session used to fail with a traceback, leave a
trace_processor behind, or run another binary than the one named.
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, selftest, tp  # noqa: E402
from echolot.config import Config, ConfigError  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_last_loose_ends import cache, needs_a_pin  # noqa: E402,F401


def test_a_bare_binary_name_is_the_file_here_or_on_path(tmp_path: Path, monkeypatch) -> None:
    here = tmp_path / "here"
    here.mkdir()
    (here / "trace_processor_shell").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.chdir(here)
    check("the file in this directory, by its absolute path",
          tp.named_binary("trace_processor_shell")
          == str((here / "trace_processor_shell").resolve()))
    on_path = tmp_path / "bin"
    on_path.mkdir()
    shell = on_path / "tp_on_path"
    shell.write_text("#!/bin/sh\n", encoding="utf-8")
    shell.chmod(0o755)
    monkeypatch.setenv("PATH", str(on_path))
    check("a name that is no file here is looked up in PATH",
          tp.named_binary("tp_on_path") == str(shell))


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0,
                    reason="file modes, and root reads everything")
def test_an_unreadable_trace_is_refused_before_anything_starts(tmp_path: Path) -> None:
    trace = tmp_path / "locked.perfetto-trace"
    trace.write_bytes(fixture.build())
    trace.chmod(0)
    try:
        with pytest.raises(ConfigError, match="cannot read trace"):
            tp.TraceSession(trace)
    finally:
        trace.chmod(0o644)


def test_a_colon_in_a_relative_path_is_a_file_name(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "run12:00.perfetto-trace").write_bytes(fixture.build())
    monkeypatch.chdir(tmp_path)
    with tp.TraceSession("run12:00.perfetto-trace") as session:
        rows = session.query("SELECT COUNT(*) AS n FROM slice")
    check("it opens and reads", rows and rows[0]["n"] > 0, rows)


def test_a_semicolon_inside_a_string_ends_nothing(tmp_path: Path) -> None:
    check("one statement per `;` outside a literal", tp._split_statements(
        "-- a comment; with a semicolon\nSELECT 'Lcom/a/B;' AS x;\nSELECT 2;")
        == ["SELECT 'Lcom/a/B;' AS x", "SELECT 2"])
    config = copy.deepcopy(selftest.FIXTURE_CONFIG)
    config["scenario"]["end"] = {"name": "Lcom/example/app/Store;"}
    trace = tmp_path / "f.perfetto-trace"
    trace.write_bytes(fixture.build())
    rep = analyze_trace(trace, Config(config))
    check("an anchor with `;` in it is matched, not split",
          rep["window"]["end_anchor"]["matches"] == 1, rep["window"]["end_anchor"])


@needs_a_pin
def test_an_interrupted_download_takes_its_file_with_it(cache, monkeypatch) -> None:  # noqa: F811
    pin, kept = cache
    partial = pin.path.with_name(f"{pin.path.name}.123.tmp")

    def interrupted(**kw):
        partial.write_bytes(b"half a binary")
        raise KeyboardInterrupt

    manifest, _ = tp._perfetto_prebuilts()
    monkeypatch.setattr(tp, "_perfetto_prebuilts", lambda: (manifest, interrupted))
    with pytest.raises(KeyboardInterrupt):
        tp.resolve_binary_path()
    check("the file it wrote is gone", not partial.exists())
    check("and the interruption is not remembered as a failure",
          pin.path not in tp._FAILED, tp._FAILED)
    check("nothing else in the cache moved",
          {p.name: p.read_bytes() for p in pin.path.parent.iterdir()} == kept)
