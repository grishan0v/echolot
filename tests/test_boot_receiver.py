"""The app's `BOOT_COMPLETED` receiver inside a window is the reset's work (#910).

On Android 15 `am force-stop` leaves a package stopped, and its next start
takes it out of that state: system_server sends `PACKAGE_UNSTOPPED`, and the
app is sent `BOOT_COMPLETED`. In 25 of 25 Wikipedia cold starts on an
SM-F711B the main thread ran `broadcastReceiveComp:
android.intent.action.BOOT_COMPLETED` between `bindApplication` and the first
activity, 2.1–6.1 ms, and neither `collect` nor the report said so. A user's
cold start after the system reclaimed the process runs none of it.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, recorder  # noqa: E402
from echolot import render as render_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import _boot_said, _scenario_ran, main  # noqa: E402
from tests.support import check  # noqa: E402

APP = "com.example.app"
LAUNCH = f"""\
project:
  package: {APP}
  process: {APP}
scenario:
  name: startup
  window: launch
"""
ANCHORED = f"""\
project:
  package: {APP}
  process: {APP}
scenario:
  name: startup
  start: {{name: bindApplication}}
  end: {{name: activityResume}}
"""


def _traces(root: Path, n: int, boot=True) -> list[str]:
    out = []
    for i in range(n):
        path = root / f"t/s_iter{i:03d}.perfetto-trace"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fixture.build(boot=boot, frames=bool(i % 2)))
        out.append(str(path))
    return out


def _analyze(tmp_path: Path, config: str, traces: list[str]) -> tuple[dict, str, str]:
    (tmp_path / "echolot.yml").write_text(config, encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["analyze", "-c", str(tmp_path / "echolot.yml"),
                     "-o", str(tmp_path / "out"), *traces])
    assert code == 0, err.getvalue()[-800:]
    return (json.loads((tmp_path / "out" / "report.json").read_text()),
            (tmp_path / "out" / "report.md").read_text(), out.getvalue())


def test_the_report_names_the_receiver_with_its_time_over_the_set(tmp_path: Path) -> None:
    rep, md, overview = _analyze(tmp_path, LAUNCH, _traces(tmp_path, 2))
    rows = rep["window"]["boot_receivers"]
    check("report.json: the receiver, its median, least, most, and in how many repeats",
          rows == [{"name": fixture.BOOT_RECEIVER, "ms": fixture.BOOT_MS,
                    "ms_min": fixture.BOOT_MS, "ms_max": fixture.BOOT_MS, "runs": "2/2"}], rows)
    said = (f"> ⚠️ The main thread ran the app's `BOOT_COMPLETED` receiver inside the "
            f"window: `{fixture.BOOT_RECEIVER}` {fixture.BOOT_MS} ms, in 2 of 2 repeats. "
            f"On Android 15 `am force-stop` leaves an app stopped")
    check("report.md says it, as the reset's work", said in md
          and "it is the reset's work, not the start's" in md, md[:2000])
    check("and so does the overview analyze prints outside a terminal",
          said in overview and said in render_mod.overview(rep), overview[:1500])


def test_an_anchored_window_that_holds_it_says_it_too(tmp_path: Path) -> None:
    """A command scenario cut from `bindApplication` after a force-stop holds the
    same receiver: the line belongs to any window, not to launch ones alone."""
    rep, md, _ = _analyze(tmp_path, ANCHORED, _traces(tmp_path, 1))
    check("one repeat, its time", rep["window"]["boot_receivers"] == [
        {"name": fixture.BOOT_RECEIVER, "count": 1, "ms": fixture.BOOT_MS}],
        rep["window"]["boot_receivers"])
    check("and the line, with no count of repeats",
          f"`{fixture.BOOT_RECEIVER}` {fixture.BOOT_MS} ms. On Android 15" in md, md[:2000])


def test_a_window_without_it_says_nothing(tmp_path: Path) -> None:
    rep, md, _ = _analyze(tmp_path, LAUNCH, _traces(tmp_path, 2, boot=False))
    check("no rows, no line", rep["window"]["boot_receivers"] is None
          and "BOOT_COMPLETED" not in md, rep["window"].get("boot_receivers"))


def test_collect_says_it_under_the_first_repeat(tmp_path: Path) -> None:
    """The first trace's look (#536) reads the window `analyze` would cut, and
    the receiver is in it: said once, with its time, under repeat 1."""
    trace = tmp_path / "s_iter000.perfetto-trace"
    trace.write_bytes(fixture.build(boot=True))
    stop, note = _scenario_ran(Config({"project": {"package": APP, "process": APP},
                                       "scenario": {"name": "s", "window": "launch"}}), trace)
    check("the round goes on", stop is None)
    check("and the note names the broadcast and its time",
          note == _boot_said([{"name": fixture.BOOT_RECEIVER, "count": 1,
                               "ms": fixture.BOOT_MS}])
          and note.startswith("repeat 1 ran the app's boot receiver on its main thread "
                              "inside the window (`android.intent.action.BOOT_COMPLETED` "
                              "4.5 ms): on Android 15 a force-stop leaves the app stopped"),
          note)
    plain = tmp_path / "plain.perfetto-trace"
    plain.write_bytes(fixture.build())
    check("a first repeat without it gets no note", _scenario_ran(
        Config({"project": {"package": APP, "process": APP},
                "scenario": {"name": "s", "window": "launch"}}), plain) == (None, None))
