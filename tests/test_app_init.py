"""`app_init` — what ran after the Application was created, before the first Activity.

The fixture's shape is pinned by the self-check: `makeApplication`, then 91 ms
the platform does not trace, with androidx.startup's Initializers, a library's
section and ART's classes inside, and 48 ms in no section at all. What the
fixture cannot hold at the same time are the other two shapes the detector has
to answer for: a stretch too short to matter, and a trace with no
`makeApplication` to start the stretch from. Both are built here from the
fixture with one thing changed.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import fixture, selftest  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402


def _analysed(tmp_path: Path, overrides: dict | None = None) -> dict:
    trace = tmp_path / "f.perfetto-trace"
    trace.write_bytes(fixture.build())
    return analyze_trace(trace, Config(selftest.FIXTURE_CONFIG),
                         cli_overrides=overrides)


def _rows(report: dict) -> dict[str, dict]:
    det = next(d for d in report["detectors"] if d["id"] == "app_init")
    check("app_init ran without an error", det["error"] is None, det["error"])
    return {r["location"]: r for r in det["rows"]}


def _without(name: str) -> dict:
    """The fixture's slices with every slice of that name taken out."""
    def strip(nodes):
        return [(n, start, dur, strip(kids)) for n, start, dur, kids in nodes
                if n != name]
    slices = copy.deepcopy(fixture.SLICES)
    slices[fixture.TID_MAIN] = strip(slices[fixture.TID_MAIN])
    return slices


def test_a_short_stretch_is_silent(tmp_path: Path) -> None:
    """91 ms against a bar of 100: silence, which has to mean checked and short."""
    report = _analysed(tmp_path, {"app_init": {"min_stretch_ms": 100}})
    check("no rows under the bar", _rows(report) == {}, _rows(report))
    check("and not among the detectors that fired",
          "app_init" not in report["summary"]["fired_ids"],
          report["summary"]["fired_ids"])

    report = _analysed(tmp_path, {"app_init": {"min_stretch_ms": 91}})
    check("the same stretch at a bar of 91 fires", _rows(report), report)


def test_without_make_application_the_stretch_is_all_of_it(
        tmp_path: Path, monkeypatch) -> None:
    """An older platform, or a trace cut differently: nothing marks the start.

    The stretch is then all of `bindApplication`, and the row nobody named
    has to say so — it now holds the platform's part as well, and reading it
    as the providers alone would put the platform's time on the app.
    """
    monkeypatch.setattr(fixture, "SLICES", _without("makeApplication"))
    got = _rows(_analysed(tmp_path))
    rest = got.get("(no section)")
    check("the row nobody named is there", rest is not None, sorted(got))
    # 98 ms of bindApplication, less the sections in it: Startup 16,
    # Firebase 22, two classes of 1 ms and a 3 ms transaction.
    check("and is all of bindApplication less its sections",
          rest["total_ms"] == 55.0, rest)
    check("saying that the trace has no makeApplication",
          "no makeApplication" in rest["detail"], rest["detail"])
    check("the named rows do not change",
          got["Firebase"]["total_ms"] == 22.0
          and got["WorkManagerInitializer"]["total_ms"] == 9.0, got)


def test_a_cold_start_without_bind_application_is_silent(
        tmp_path: Path, monkeypatch) -> None:
    """A warm start never binds the Application, and has nothing to say here."""
    monkeypatch.setattr(fixture, "SLICES", _without("bindApplication"))
    check("no bindApplication, no rows", _rows(_analysed(tmp_path)) == {})


def _nested() -> dict:
    """The fixture with two Initializers that `WorkManagerInitializer` depends
    on, run inside its section, and a class ART initialized inside it."""
    def put(nodes):
        out = []
        for n, start, dur, kids in nodes:
            if n == "WorkManagerInitializer":
                kids = [("LogsInitializer", 111.5, 3, [
                            ("ConfigInitializer", 112, 1, [])]),
                        ("Lcom/example/app/Logs;", 115, 0.5, []),
                        ("ReportingInitializer", 116, 2, [])]
            out.append((n, start, dur, put(kids)))
        return out
    slices = copy.deepcopy(fixture.SLICES)
    slices[fixture.TID_MAIN] = put(slices[fixture.TID_MAIN])
    return slices


def test_an_initializer_inside_another_is_a_row_of_its_own(
        tmp_path: Path, monkeypatch) -> None:
    """#512: an Initializer that another depends on runs inside the other's
    section, and had no row wherever it was nested."""
    monkeypatch.setattr(fixture, "SLICES", _nested())
    got = _rows(_analysed(tmp_path))
    check("both children and the grandchild are rows",
          {"LogsInitializer", "ReportingInitializer", "ConfigInitializer"} <= set(got),
          sorted(got))
    logs = got["LogsInitializer"]
    check("a child's total, and its own time without its own child",
          logs["total_ms"] == 3.0 and logs["self_ms"] == 2.0, logs)
    check("and says what it ran inside",
          "inside WorkManagerInitializer" in logs["detail"], logs["detail"])
    parent = got["WorkManagerInitializer"]
    # 9 ms, less 3 and 2 of the Initializers in it. The class ART initialized
    # inside it stays in its own time, as before.
    check("the parent's self time leaves its Initializers out",
          parent["total_ms"] == 9.0 and parent["self_ms"] == 4.0, parent)
    check("a class initialized inside an Initializer is not pulled out of it",
          "Lcom/example/app/Logs;" not in got, sorted(got))


def test_rows_of_a_flat_startup_have_self_equal_to_total(tmp_path: Path) -> None:
    got = _rows(_analysed(tmp_path))
    check("the shipped fixture has no nesting, so self is total",
          all(r["self_ms"] == r["total_ms"] for r in got.values()), got)


def test_a_merged_max_is_the_largest_repeat() -> None:
    """#512: `Max 508` over runs of ~30, ~510, ~510 and ~550 ms."""
    from echolot import report as report_mod
    from tests.test_report import one, row
    reps = [one([row("TelemetryInitializer", count=1, self_ms=ms, total_ms=ms, max_ms=ms)],
                det_id="app_init") for ms in (30.0, 508.0, 512.0, 550.0)]
    got = report_mod.aggregate(reps)["detectors"][0]["rows"][0]
    check("Max is the largest value seen, the median stays the median",
          got["max_ms"] == 550.0 and got["total_ms"] == 510.0, got)
