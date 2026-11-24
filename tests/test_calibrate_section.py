"""`calibrate` over the demo app's five cold starts.

The statistic stands for one run however many runs it is given, the section
it prints can replace the config's whole without turning anything back on or
resetting what the config set, a detector that failed is not called rare, a
healthy tail below the shipped default keeps the default unless asked, and
the section is tried on the runs it came from.

The demo's cold starts feed one threshold only, `main_thread_block`'s, which
the shipped detector no longer calibrates (#509): the tests put that line
back, as a detector of the project's own could declare it.
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
from echolot import main as main_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import main as cli  # noqa: E402
from echolot.tp import Calibration, Detector, load_detectors  # noqa: E402
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


@pytest.fixture(autouse=True)
def block_calibrated(monkeypatch):
    """`main_thread_block` with the `@calibrate` line it shipped with."""
    def load(path):
        out = load_detectors(path)
        for d in out:
            if d.id == "main_thread_block":
                d.calibrations = [Calibration("min_slice_ms", "top", 10, "self_ms", 1.5)]
        return out
    monkeypatch.setattr(main_mod, "load_detectors", load)


def _calibrate(app: Path, n: int, config: str = "echolot.yml",
               *flags: str) -> tuple[int, str, str]:
    traces = sorted(str(p) for p in app.glob("coldStart_iter*.perfetto-trace"))[:n]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli(["calibrate", *traces, "-c", str(app / config), *flags])
    return code, out.getvalue(), err.getvalue()


def _section(text: str) -> dict:
    return yaml.safe_load(text[text.index("detectors:"):])


def test_the_threshold_does_not_grow_with_the_repeats(app: Path) -> None:
    got = {n: _section(_calibrate(app, n, "echolot.yml", "--below-defaults")[1])
           ["detectors"]["main_thread_block"]["min_slice_ms"] for n in (1, 3, 5)}
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
    render = Detector.render

    def broken(self, overrides=None):
        sql, resolved = render(self, overrides)
        return (sql + " BROKEN" if self.id == "main_thread_block" else sql), resolved

    monkeypatch.setattr(Detector, "render", broken)
    code, out, err = _calibrate(app, 3)
    check("the failure is on stderr", err.count("[!] main_thread_block on") == 3, err)
    check("the section says it failed, not that the sample was small",
          "min_slice_ms: kept the default (16) — the detector failed on every trace"
          in out and "main_thread_block (3 of 3)" in out, out)
    check("and the run is not called a success", code == 1, code)


def test_the_shipped_main_thread_block_is_not_calibrated() -> None:
    shipped = {d.id: d for d in load_detectors(main_mod.DETECTOR_DIR)}
    check("where the main thread's time went has a frame for its bar, not a tail",
          shipped["main_thread_block"].calibrations == [], shipped["main_thread_block"])


def test_below_the_default_the_default_is_kept(app: Path) -> None:
    code, out, _ = _calibrate(app, 5)
    check("it ran", code == 0, out)
    check("the line keeps the default and says how far below the tail was",
          "# min_slice_ms: kept the default (16) — the healthy runs' tail gives 4.7 "
          "(top10(self_ms)=3.1 × 1.5), 3× lower" in out, out)
    check("so the pasted section leaves the default in effect",
          "min_slice_ms" not in (_section(out)["detectors"].get("main_thread_block") or {}),
          _section(out))
    check("and the summary says so, with the flag that takes the lower number",
          "kept at the default although the healthy runs' tail\n# is lower: 1" in out
          and "--below-defaults" in out, out)
    check("nothing moved, so there is nothing to try", "analyze would report" not in out, out)


def test_below_defaults_takes_the_tail_and_tries_it(app: Path) -> None:
    code, out, _ = _calibrate(app, 5, "echolot.yml", "--below-defaults")
    check("it ran", code == 0, out)
    check("the lower number, marked as below the default",
          "min_slice_ms: 4.7    # top10(self_ms)=3.1 × 1.5" in out
          and "3× below the default 16" in out, out)
    tried = out[out.index("analyze would report:"):]
    line = next(x for x in tried.splitlines() if "main_thread_block" in x)
    after, now = (int(w) for w in line.replace("row(s),", "").replace("now", "").split()[2:])
    check("the section is tried on the same runs: more rows than with the default",
          after > now > 0, line)
    check("and only the detector it moved", tried.count("row(s), now") == 1, tried)
