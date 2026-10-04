"""Choosing the process and listing its names, in the shapes they went wrong:
a file with no trace data, a mask from `--process`, a process with no thread
slices, another process's JIT frames, a detector turned off, an async section
with a keyword in its name, a marker on a nameless thread, and `names --json`
on a process with no slices.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest
import yaml
from perfetto.protos.perfetto.trace import perfetto_trace_pb2 as pb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, recorder, selftest  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace, main  # noqa: E402
from tests.support import check  # noqa: E402


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _config(root: Path, **changes) -> Path:
    raw = {**selftest.FIXTURE_CONFIG, **changes}
    path = root / "echolot.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def trace(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("fx") / "fx.perfetto-trace"
    path.write_bytes(fixture.build())
    return path


def test_a_file_with_no_trace_data_is_said_so(tmp_path: Path) -> None:
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schema": 1, "detectors": []}), encoding="utf-8")
    code, _, err = _run("analyze", str(old), "-c", str(_config(tmp_path)))
    check("no trace data, and no config blamed",
          code == 2 and "old.json holds no process with a name" in err
          and "project.process" not in err, err)
    code, _, err = _run("names", str(old))
    check("under names too, with the file named",
          code == 2 and "old.json holds no process" in err and "try" not in err, err)


def test_the_mask_s_source_is_the_one_named(trace: Path, tmp_path: Path) -> None:
    code, _, err = _run("names", str(trace), "--process", "com.typo*")
    check("a flag's mask is the flag's",
          code == 2 and "matches --process 'com.typo*'" in err
          and "try --process '*com.typo'" in err and "project.process" not in err, err)
    cfg = _config(tmp_path, project={"package": "com.example.app", "process": "com.*"})
    _, _, err = _run("analyze", str(trace), "-c", str(cfg), "-o", str(tmp_path / "out"))
    check("and analyze, which has no flag, points at the config",
          "Narrow it with project.process in the config" in err and "--process" not in err, err)


def test_a_process_with_no_thread_slices_is_a_sentence(trace: Path, tmp_path: Path) -> None:
    cfg = _config(tmp_path, project={"package": "com.example.app",
                                     "process": "*surfaceflinger"})
    code, _, err = _run("analyze", str(trace), "-c", str(cfg), "-o", str(tmp_path / "out"))
    check("analyze", code == 2 and "has no thread slices" in err and "Traceback" not in err,
          err[-500:])
    code, _, err = _run("calibrate", str(trace), "-c", str(cfg))
    check("and calibrate", code == 2 and "has no thread slices" in err, err[-500:])


def test_another_process_s_jit_frames_are_not_the_app_s(tmp_path: Path, monkeypatch) -> None:
    jit = "/memfd:jit-cache (deleted)"
    monkeypatch.setitem(fixture.SAMPLE_STACKS, "other_jit",
                        [(f"com.other.app.Thing.m{i}", jit) for i in range(100)])
    monkeypatch.setattr(fixture, "SAMPLES", [
        *fixture.SAMPLES,
        *[(9, fixture.OTHER_PID, fixture.OTHER_PID, at, "other_jit") for at in (310, 410)]])
    path = tmp_path / "t.perfetto-trace"
    path.write_bytes(fixture.build(sampling="minified"))
    names = analyze_trace(path, Config(selftest.FIXTURE_CONFIG))["environment"]["sampling"]["names"]
    check("the app's eight, all minified", names == {"methods": 8, "minified": 8}, names)


def test_a_detector_turned_off_sees_nothing(trace: Path, tmp_path: Path) -> None:
    cfg = _config(tmp_path, detectors={"gc_pressure": False})
    code, out, _ = _run("names", str(trace), "-c", str(cfg), "--json")
    data = json.loads(out)
    credited = {d for s in data["sections"] for f in s["families"] for d in f["detectors"]}
    missed = [m["family"] for m in data["missed"]]
    check("no mask is credited to it", code == 0 and "gc_pressure" not in credited, credited)
    check("and its names are missed",
          any("waitWhileAllocatingLocked" in m for m in missed), missed)


def test_an_async_section_is_not_missed_by_a_mask(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(fixture, "ASYNC_SLICES",
                        [*fixture.ASYNC_SLICES, ("LoadingContent", 300, 380, 3)])
    path = tmp_path / "t.perfetto-trace"
    path.write_bytes(fixture.build())
    code, out, _ = _run("names", str(path), "-c", str(_config(tmp_path)), "--json")
    data = json.loads(out)
    where = [s["title"] for s in data["sections"]
             for f in s["families"] if f["family"] == "LoadingContent"]
    check("under everything else", code == 0 and where == ["Everything else"], where)
    check("and not missed", "LoadingContent" not in [m["family"] for m in data["missed"]])


def test_a_marker_on_a_nameless_thread_is_measured(tmp_path: Path) -> None:
    # One ftrace print from tid 4300 of the app, which no packet names: no
    # process tree entry, no sched_switch with its comm.
    extra = pb.Trace()
    bundle = extra.packet.add().ftrace_events
    bundle.cpu = 7
    for at, buf in ((400, f"B|{fixture.APP_PID}|AGENTTMP_nameless\n"),
                    (420, f"E|{fixture.APP_PID}\n")):
        event = bundle.event.add()
        event.timestamp, event.pid = fixture.ms(at), 4300
        event.print.buf = buf
    path = tmp_path / "t.perfetto-trace"
    path.write_bytes(fixture.build() + extra.SerializeToString())
    rep = analyze_trace(path, Config(selftest.FIXTURE_CONFIG))
    rows = [r for r in rep["markers"]["rows"] if r["location"] == "AGENTTMP_nameless"]
    check("one row, on tid 4300", len(rows) == 1 and rows[0]["detail"] == "tid 4300", rows)


def test_names_json_is_json_when_the_process_has_no_slices(trace: Path) -> None:
    code, out, _ = _run("names", str(trace), "--process", "*surfaceflinger", "--json")
    data = json.loads(out)
    check("the usual object, empty", code == 0 and data["sections"] == []
          and data["missed"] == [] and data["async_sections"] == 0, data)
