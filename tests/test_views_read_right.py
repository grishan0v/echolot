"""What `report`, `probe`, `names`, `calibrate` and `domains` print is what
they do, and none of it reads as a different fact (#515)."""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, recorder  # noqa: E402
from echolot import main as main_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402

APP = "com.example.app"
CONFIG = f"project:\n  package: {APP}\n  process: {APP}\n"


@pytest.fixture(scope="module")
def trace(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("views") / "fixture.perfetto-trace"
    p.write_bytes(fixture.build())
    return p


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


# --- 1. report after analyze -o ----------------------------------------------

def test_report_names_the_report_analyze_wrote_elsewhere(tmp_path: Path, demo_report,
                                                         monkeypatch) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    elsewhere = tmp_path / "out" / "r1" / "report.json"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text(json.dumps(demo_report), encoding="utf-8")
    log = tmp_path / recorder.LOG_FILE
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({"cmd": "analyze", "exit": 0,
                               "facts": {"report": str(elsewhere)}}) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, _, err = _run("report")
    check("it says where the last analyze wrote, and the line to read it",
          code == 2 and "The last `analyze` wrote out/r1/report.json" in err
          and "`echolot report out/r1/report.json`" in err, err)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        main(["report", "--help"])
    check("and the help says what the default is",
          "analyze -o is named here" in " ".join(out.getvalue().split()), out.getvalue())


# --- 2. report --window --------------------------------------------------------

def test_the_window_view_is_the_windows_numbers_not_the_header_again(demo_report) -> None:
    view = report_mod.window_view(demo_report)
    head = report_mod._header_lines(demo_report)
    repeated = [ln for ln in view.splitlines() if ln.strip() and ln in head]
    check("no line of the overview's header", repeated == [], repeated)
    for word in ("pid 12903", "on the trace's clock", "Frames: 19 (repeats", "sleeping 553.6 ms"):
        check(f"says {word!r}", word in view, view)
    check("and leaves the device to the overview", "Device:" not in view, view)


def test_the_startup_names_each_plain_reason_once() -> None:
    """`R` and `R+` are both "waiting for a CPU": one item in the window view,
    as in report.md, not two of the same name (#822)."""
    startup = {"type": "cold", "dur_ms": 100.0,
               "reasons": {"inflate": 1.79, "R": 1.62, "D": 1.58, "R+": 0.7,
                           "verify_class": 0.03}}
    view = report_mod.window_view({"window": {"startup": startup}})
    line = next(ln for ln in view.splitlines() if ln.startswith("Startup:"))
    check("once, the two added", line.count("waiting for a CPU") == 1
          and "waiting for a CPU 2.32 ms" in line, line)
    check("longest first", line.index("waiting for a CPU") < line.index("inflate")
          < line.index("blocked in the kernel"), line)
    text = "\n".join(report_mod._startup_lines(startup))
    check("and report.md says it once too", text.count("waiting for a CPU") == 1, text)


# --- 3. runnable_starvation ----------------------------------------------------

def test_runnable_starvation_does_not_repeat_its_state_column() -> None:
    sql = (main_mod.DETECTOR_DIR / "runnable_starvation.sql").read_text(encoding="utf-8")
    check("no `'state ' ||` in detail", "'state ' || s.state" not in sql, sql[-600:])


# --- 4. frame_jank -------------------------------------------------------------

def test_frame_jank_says_what_max_and_longest_are() -> None:
    rep = {"detectors": [{"id": "frame_jank", "title": "Frame jank", "rows": [
        {"location": "Buffer Stuffing", "count": 3, "total_ms": 1600.0, "max_ms": 1572.0,
         "detail": "Buffer Stuffing · 3 of 40 frames · longest 89.1 ms"}]}]}
    view = report_mod.detector_view(rep, "frame_jank", top=5)
    check("a legend under the table", "Evidence:" in view and "past each frame's deadline" in view, view)


# --- 5, 6. probe ----------------------------------------------------------------

def test_probe_without_a_process_says_how_to_see_the_app(trace: Path, tmp_path: Path,
                                                         monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    code, out, _ = _run("probe", str(trace))
    check("the hint", code == 0 and "--process <a name from the table>" in out and "-c" in out, out[-400:])
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    code, out, err = _run("probe", str(trace))
    check("with a config here, its process", code == 0 and "## Threads of process com.example.app" in out
          and "the process from echolot.yml" in err, err[-300:])
    code, _, err = _run("probe", str(trace), "-c", "nowhere.yml")
    check("and a -c that is not there is said", code == 2 and "-c nowhere.yml: no such file" in err, err)


def test_probe_leaves_lock_waits_out_of_the_anchor_candidates(trace: Path) -> None:
    code, out, _ = _run("probe", str(trace), "--process", APP)
    section = out.split("anchor candidates", 1)[-1]
    check("no contention slice", code == 0 and "contention" not in section, section[:600])


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def query(self, _sql):
        return self.rows


def test_probe_folds_a_number_that_differs_in_every_recording(capsys) -> None:
    main_mod._anchor_candidates(_Rows([
        {"name": "Choreographer#doFrame 8538821", "thread": "main", "n": 1, "dur": 55_000_000},
        {"name": "Choreographer#doFrame 8538822", "thread": "main", "n": 1, "dur": 12_000_000},
        {"name": "AppStart", "thread": "main", "n": 1, "dur": 40_000_000},
    ]), APP)
    out = capsys.readouterr().out
    check("one row, as the glob to write", out.count("Choreographer#doFrame *") == 1
          and "8538821" not in out, out)
    check("with what a `*` means", "A `*` stands for a number" in out, out)


# --- 7, 8, 10. names -------------------------------------------------------------

def test_names_json_lists_every_family_unless_top_is_given(trace: Path) -> None:
    _, out, _ = _run("names", str(trace), "--process", APP, "--json", "--min-ms", "0")
    sections = json.loads(out)["sections"]
    check("shown is the total", all(s["shown"] == s["total"] for s in sections),
          [(s["title"], s["shown"], s["total"]) for s in sections])
    _, out, _ = _run("names", str(trace), "--process", APP, "--json", "--top", "1")
    check("--top still cuts", all(s["shown"] <= 1 for s in json.loads(out)["sections"]))


def test_names_keeps_five_threads_in_a_cell_even_whole(capsys) -> None:
    threads = {f"worker-{i}" for i in range(58)}
    main_mod._families_table([("Fam", {"threads": threads, "n": 1, "ns": 1e6, "dets": set(),
                                        "skips": set()})], wide=True)
    out = capsys.readouterr().out
    check("five and the rest counted", "+53 more" in out and "worker-9" not in out, out)


def test_names_grep_finds_a_zero_ms_slice(trace: Path) -> None:
    _, out, _ = _run("names", str(trace), "--process", APP, "--grep", "Async.mark")
    check("listed with no --min-ms", "Async.mark" in out.split("_Only families", 1)[-1], out[-600:])
    _, out, _ = _run("names", str(trace), "--process", APP, "--grep", "Async.mark", "--min-ms", "1")
    check("a --min-ms given still applies", "matching `Async.mark`: 0 of" in out, out[-600:])


# --- 9. --help ---------------------------------------------------------------------

@pytest.mark.parametrize("verb, words", [
    ("calibrate", ["known to be healthy", "does not edit the config", "--config CONFIG the project config",
                   ]),
    ("domains", ["Kotlin and Java sources", "Reads no trace"]),
])
def test_calibrate_and_domains_say_what_they_do(verb: str, words: list[str]) -> None:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        main([verb, "--help"])
    text = " ".join(out.getvalue().split())
    for w in words:
        check(f"{verb} --help says {w!r}", w in text, text[:800])
