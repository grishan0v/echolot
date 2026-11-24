"""A start `collect` stopped the app for and found warm is left out of a set
of cold ones (#523).

One such repeat in a set of five made `Repeat windows diverged 3.7x`, an
anchor missing in 1 of 5, and the header's pid the warm run's. `collect`
keeps beside each trace how its start went (#619), and `analyze` reads that
rather than guessing from the trace.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, recorder, recording  # noqa: E402
from echolot.main import _cold_only, main  # noqa: E402
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

COLD = {"repeat": 1, "attempt": 1, "state": "COLD", "total_time_ms": 610, "cold": True,
        "notes": []}
WARM = {"repeat": 2, "attempt": 1, "state": "WARM", "total_time_ms": 295, "cold": False,
        "notes": ["[!] not a cold start: com.example.app was running again before the "
                  "recording began (pid 4243)"]}


def _trace(root: Path, name: str, launch: dict | None, **build) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fixture.build(**build))
    if launch is not None:
        recording.write(path, {"runner": "launch", "reset_policy": "force-stop",
                               "launch": launch})
    return str(path)


def test_only_a_trace_collect_found_warm_after_its_stop_is_left_out(tmp_path: Path) -> None:
    cold = _trace(tmp_path, "s_iter000.perfetto-trace", COLD)
    warm = _trace(tmp_path, "s_iter001-warm.perfetto-trace", WARM)
    old = _trace(tmp_path, "s_iter002.perfetto-trace", None)
    unjudged = _trace(tmp_path, "s_iter003.perfetto-trace", {**COLD, "cold": None})
    kept, left = _cold_only([cold, warm, old, unjudged])
    check("the cold one, one recorded before collect kept this, and one with nothing "
          "to judge by are read", kept == [cold, old, unjudged], kept)
    check("the warm one is left out, with its state",
          left == [{"trace": warm, "state": "WARM"}], left)


def test_a_set_of_warm_starts_alone_is_read_as_given(tmp_path: Path) -> None:
    """The recordings `collect` set aside, given on purpose: there is no cold
    start to read instead."""
    warm = [_trace(tmp_path, f"s_iter00{i}-warm.perfetto-trace", WARM) for i in range(2)]
    check("both read, nothing left out", _cold_only(warm) == (warm, []))


def test_analyze_leaves_the_warm_start_out_and_says_which(tmp_path: Path) -> None:
    a = _trace(tmp_path, "t/s_iter000.perfetto-trace", COLD)
    b = _trace(tmp_path, "t/s_iter001.perfetto-trace", COLD, frames=False)
    warm = _trace(tmp_path, "t/s_iter001-warm.perfetto-trace", WARM, stuffed=True)
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    err = io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(err):
        code = main(["analyze", "-c", str(tmp_path / "echolot.yml"),
                     "-o", str(tmp_path / "out"), a, warm, b])
    assert code == 0, err.getvalue()[-800:]
    rep = json.loads((tmp_path / "out" / "report.json").read_text())
    md = (tmp_path / "out" / "report.md").read_text()
    check("the medians are over the two cold starts", rep["traces"] == [a, b], rep["traces"])
    check("the report keeps which was left out, and its state",
          rep["not_cold"] == [{"trace": warm, "state": "WARM"}], rep.get("not_cold"))
    said = ("Left out: `s_iter001-warm.perfetto-trace` (WARM). `collect` stopped the app "
            "before it and found its process running again by the start")
    check("report.md says it under the runs", said in md
          and md.index("Runs: **2**") < md.index(said), md[:900])
    check("and so does the run, before the report", said in err.getvalue(), err.getvalue())
