"""Which two reports a bare `compare` takes inside an open investigation (#613).

Every `analyze` inside an open investigation files a report, a look at part of
a round included. Taken as the last two, round 2 was held against a single-run
look at one of its traces: no spread, an empty Holds column, and a header that
read like two rounds. A look is now left out, and the pair is named.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import hunt as hunt_mod  # noqa: E402
from echolot import recorder  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_compare import FIXTURE_CONFIG, det, report, row  # noqa: E402


def _investigation(root: Path) -> Path:
    (root / "echolot.yml").write_text(FIXTURE_CONFIG, encoding="utf-8")
    out = root / ".echolot" / "out"
    out.mkdir(parents=True, exist_ok=True)
    hunt_mod.open_new(root, "scroll janks", scenario="fixture")
    return out


def _file(root: Path, out: Path, value: float, traces: list[str], **config) -> None:
    """One analyze: the latest report, and its copy filed under the hunt."""
    body = report([det("d", [row("A", value)])], runs=len(traces),
                  config={"sha": "aaaa", "defaults": False, **config})
    body["traces"] = traces if len(traces) > 1 else None
    body["trace"] = traces[0]
    (out / "report.json").write_text(json.dumps(body), encoding="utf-8")
    (out / "report.md").write_text("#", encoding="utf-8")
    hunt_mod.record_report(root, out)


def _compare(root: Path, monkeypatch) -> tuple[float | None, str]:
    monkeypatch.chdir(root)
    err = io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(err):
        code = main(["compare"])
    if code != 0:
        return None, err.getvalue()
    body = json.loads((root / ".echolot" / "out" / "comparison.json").read_text())
    return body["rows"][0]["delta_ms"], err.getvalue()


def _set(tag: str, n: int = 5) -> list[str]:
    return [f"traces/{tag}/iter{i:03d}.perfetto-trace" for i in range(1, n + 1)]


def test_a_look_at_one_run_is_not_the_after_side(tmp_path: Path, monkeypatch) -> None:
    out = _investigation(tmp_path)
    _file(tmp_path, out, 100.0, _set("good"))
    _file(tmp_path, out, 400.0, _set("bad"))
    _file(tmp_path, out, 900.0, _set("bad")[:1])
    delta, err = _compare(tmp_path, monkeypatch)
    check("round 1 against round 2", delta == 300.0, err[-400:])
    check("and the pair is named, with the look left out",
          "reports/001.json against" in err and "reports/002.json" in err
          and "003.json" in err and "looks rather than rounds" in err, err[-400:])


def test_a_look_with_other_thresholds_is_left_out(tmp_path: Path, monkeypatch) -> None:
    out = _investigation(tmp_path)
    _file(tmp_path, out, 100.0, _set("good"))
    _file(tmp_path, out, 400.0, _set("bad"))
    _file(tmp_path, out, 900.0, _set("bad"), set={"main_thread_block": {"min_slice_ms": 4}})
    _file(tmp_path, out, 950.0, _set("bad"), defaults=True)
    delta, err = _compare(tmp_path, monkeypatch)
    check("--set and --defaults looks are not rounds", delta == 300.0, err[-400:])


def test_a_second_analyze_of_the_same_traces_is_not_a_round(tmp_path: Path,
                                                            monkeypatch) -> None:
    out = _investigation(tmp_path)
    _file(tmp_path, out, 100.0, _set("good"))
    _file(tmp_path, out, 400.0, _set("bad"))
    _file(tmp_path, out, 400.0, _set("bad"))
    delta, _ = _compare(tmp_path, monkeypatch)
    check("a round is not held against its own copy", delta == 300.0)


def test_the_next_full_round_counts_again(tmp_path: Path, monkeypatch) -> None:
    out = _investigation(tmp_path)
    _file(tmp_path, out, 100.0, _set("good"))
    _file(tmp_path, out, 400.0, _set("bad"))
    _file(tmp_path, out, 900.0, _set("bad")[:1])
    _file(tmp_path, out, 150.0, _set("fixed"))
    delta, err = _compare(tmp_path, monkeypatch)
    check("round 2 against round 3, past the look", delta == -250.0
          and "002.json against" in err and "004.json" in err, err[-400:])


def test_two_reports_that_are_not_rounds_are_still_compared(tmp_path: Path,
                                                            monkeypatch) -> None:
    out = _investigation(tmp_path)
    _file(tmp_path, out, 100.0, _set("good"))
    _file(tmp_path, out, 400.0, _set("good")[:1])
    delta, _ = _compare(tmp_path, monkeypatch)
    check("with one round only, the last two reports, as before", delta == 300.0)
