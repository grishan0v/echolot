"""A window shorter than the sampling period still has the device's state (#617).

Temperature and free memory are sampled about once a second. A hot start or a
tap is shorter than that, and read only inside the window it held a sample
by chance: of six such windows, four said the state was "not recorded",
though the trace held it. Here no sample falls inside the fixture's window
[100, 1105] ms: one is 50 ms before it opens and one after it closes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, report, selftest  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402
from tests.support import check  # noqa: E402


def _report(tmp_path: Path, monkeypatch) -> dict:
    monkeypatch.setattr(fixture, "THERMAL", [(50, "cpu-therm", 45_000),
                                             (1200, "cpu-therm", 80_000)])
    monkeypatch.setattr(fixture, "SYS_STATS", [(50, 2_000_000, 1_000),
                                               (1200, 100_000, 9_999)])
    path = tmp_path / "short.perfetto-trace"
    path.write_bytes(fixture.build())
    return analyze_trace(path, Config(selftest.FIXTURE_CONFIG))


def test_the_last_sample_before_the_window_counts_for_it(tmp_path: Path, monkeypatch) -> None:
    env = _report(tmp_path, monkeypatch)["environment"]
    check("nothing is called missing", env["missing"] == [], env["missing"])
    check("the temperature that held over the window, not the one after it",
          env["thermal"]["max_celsius"] == 45.0, env["thermal"])
    check("and how old the reading was",
          env["thermal"]["sampled_before_ms"] == 50.0, env["thermal"])
    check("free memory the same way",
          env["memory"]["available_mb_min"] == 2048.0
          and env["memory"]["sampled_before_ms"] == 50.0, env["memory"])


def test_the_header_does_not_say_not_recorded(tmp_path: Path, monkeypatch) -> None:
    text = report.to_markdown(_report(tmp_path, monkeypatch))
    check("no line sends the reader to record again",
          "Device state not recorded" not in text, text[:1500])


def test_a_sample_inside_the_window_has_no_age(tmp_path: Path) -> None:
    path = tmp_path / "f.perfetto-trace"
    path.write_bytes(fixture.build())
    env = analyze_trace(path, Config(selftest.FIXTURE_CONFIG))["environment"]
    check("the fixture's own samples fall inside, so no age is given",
          "sampled_before_ms" not in env["thermal"]
          and "sampled_before_ms" not in env["memory"], env)
