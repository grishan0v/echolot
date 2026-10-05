"""`calibrate` over the demo app's five cold starts.

The statistic stands for one run however many runs it is given, the section
it prints can replace the config's whole without turning anything back on or
resetting what the config set, and a detector that failed is not called rare.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import demo  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import main as cli  # noqa: E402
from echolot.tp import Detector  # noqa: E402
from tests.support import check  # noqa: E402

TUNED = """\
detectors:
  binder_txn: {min_txn_ms: 30, skip_glob: "*oneway*"}
  main_thread_outlier: {factor: 6}
  anr_risk: false
  frame_jank: false
"""


@pytest.fixture(scope="module")
def app(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("demo")
    demo.write(root)
    return root


def _calibrate(app: Path, n: int, config: str = "echolot.yml") -> tuple[int, str, str]:
    traces = sorted(str(p) for p in app.glob("coldStart_iter*.perfetto-trace"))[:n]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli(["calibrate", *traces, "-c", str(app / config)])
    return code, out.getvalue(), err.getvalue()


def _section(text: str) -> dict:
    return yaml.safe_load(text[text.index("detectors:"):])


def test_the_threshold_does_not_grow_with_the_repeats(app: Path) -> None:
    got = {n: _section(_calibrate(app, n)[1])["detectors"]["main_thread_block"]
           ["min_slice_ms"] for n in (1, 3, 5)}
    check("one run's top10, whatever the number of runs",
          len(set(got.values())) == 1, got)


def test_the_section_replaces_the_config_s_whole(app: Path) -> None:
    (app / "tuned.yml").write_text(
        (app / "echolot.yml").read_text(encoding="utf-8") + TUNED, encoding="utf-8")
    code, out, _ = _calibrate(app, 5, "tuned.yml")
    check("it ran", code == 0, out)
    section = _section(out)["detectors"]
    check("a detector turned off stays off",
          section["anr_risk"] is False and section["frame_jank"] is False, section)
    pasted = dict(yaml.safe_load((app / "echolot.yml").read_text(encoding="utf-8")),
                  detectors=section)
    cfg = Config(pasted)
    check("pasted in place of the old one, nothing is turned back on",
          cfg.disabled_detectors == {"anr_risk", "frame_jank"}, cfg.disabled_detectors)
    check("and nothing the config set is reset",
          cfg.detector_overrides["binder_txn"] == {"min_txn_ms": 30, "skip_glob": "*oneway*"}
          and cfg.detector_overrides["main_thread_outlier"] == {"factor": 6},
          cfg.detector_overrides)


def test_a_detector_that_failed_is_not_rare(app: Path, monkeypatch) -> None:
    render_open = Detector.render_open

    def broken(self, overrides=None):
        sql = render_open(self, overrides)
        return sql + " BROKEN" if self.id == "main_thread_block" else sql

    monkeypatch.setattr(Detector, "render_open", broken)
    code, out, err = _calibrate(app, 3)
    check("the failure is on stderr", err.count("[!] main_thread_block on") == 3, err)
    check("the section says it failed, not that the sample was small",
          "min_slice_ms: kept the default (16) — the detector failed on every trace"
          in out and "main_thread_block (3 of 3)" in out, out)
    check("and the run is not called a success", code == 1, code)
