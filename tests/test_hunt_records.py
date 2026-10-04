"""The investigation's records and the run log, where they used to lose or mix
things: a reused number, a question asked mid-collect, a project's own marker
prefix, a half-written file, times in the wrong zone, and a run log line lost
to the config or to Ctrl-C.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import hunt as hunt_mod  # noqa: E402
from echolot import recorder, state  # noqa: E402
from tests.support import check  # noqa: E402


def test_a_number_whose_reports_are_still_filed_is_not_reused(tmp_path: Path) -> None:
    first = hunt_mod.open_new(tmp_path, "scroll got slower")
    (tmp_path / ".echolot" / "hunts" / str(first["n"]) / "reports").mkdir(parents=True)
    hunt_mod.path(tmp_path).write_text("", encoding="utf-8")
    second = hunt_mod.open_new(tmp_path, "cold start got slower")
    check("the next number, not the one whose reports are on disk",
          second["n"] == first["n"] + 1, (first["n"], second["n"]))


def _old(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def test_no_resume_question_while_the_investigation_collects() -> None:
    hunt = {"status": "open", "question": "cold start", "touched_at": _old(40),
            "opened_at": _old(60)}
    st = {"traces": {"count": 5}, "report": None, "hunt": hunt,
          "collect": {"status": "running"}}
    check("a collect in flight is the investigation working",
          not hunt_mod.needs_choice(hunt, st))
    st["collect"] = {"status": "done"}
    check("once it is done, an old investigation is asked about again",
          hunt_mod.needs_choice(hunt, st))


def test_leftover_markers_are_found_by_the_config_s_prefix(tmp_path: Path) -> None:
    src = tmp_path / "app/src/main/java/com/example/app/Store.kt"
    src.parent.mkdir(parents=True)
    src.write_text('fun load() {\n    android.os.Trace.beginSection("PERF_load") // echolot:mark\n'
                   '    android.os.Trace.endSection() // echolot:mark\n}\n', encoding="utf-8")
    (tmp_path / "echolot.yml").write_text(
        "project:\n  process: com.example.app\nscenario:\n  name: coldStart\n"
        "instrumentation:\n  temp_prefix: PERF_\n", encoding="utf-8")
    st = state.project_state(tmp_path)
    check("the state carries the prefix", st["config"]["temp_prefix"] == "PERF_", st["config"])
    hunt = {"n": 1, "status": "open", "question": "q", "opened_at": _old(5),
            "touched_at": _old(5)}
    lines = hunt_mod.recap(hunt, st, tmp_path)
    check("and the recap counts the marker under it",
          any("PERF_ marker(s) still in" in line for line in lines), lines)


def test_a_record_is_written_whole_or_not_at_all(tmp_path: Path, monkeypatch) -> None:
    hunt_mod.save(tmp_path, {"n": 1, "status": "open", "question": "kept"})

    write = Path.write_text

    def cut(self, data, encoding=None):
        # Half of it reaches the disk, as when the process is killed mid-write.
        write(self, data[:len(data) // 2], encoding=encoding)
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "write_text", cut)
    try:
        hunt_mod.save(tmp_path, {"n": 1, "status": "open", "question": "new"})
    except KeyboardInterrupt:
        pass
    monkeypatch.undo()
    check("an interrupted write leaves the record as it was",
          (hunt_mod.load(tmp_path) or {}).get("question") == "kept", hunt_mod.load(tmp_path))


def test_times_are_shown_in_local_time() -> None:
    utc = "2026-10-04T07:27:00+00:00"
    local = datetime.fromisoformat(utc).astimezone().strftime("%Y-%m-%d %H:%M")
    check("the stored UTC, in this machine's clock", hunt_mod._when(utc) == local,
          (hunt_mod._when(utc), local))
    check("and a dash for nothing", hunt_mod._when(None) == "—")


def test_the_run_log_keeps_its_line_when_the_config_is_a_directory(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    (tmp_path / "cfgdir").mkdir()
    args = type("A", (), {"cmd": "status", "config": str(tmp_path / "cfgdir")})()
    with recorder.isolated():
        recorder.at(tmp_path)
        recorder.record(args, ["status"], 0.0, exit_code=1)
        runs = recorder.read(tmp_path / recorder.LOG_FILE)
    check("the line is there, its stamp without a hash",
          len(runs) == 1 and runs[0]["config"]["sha"] is None, runs)


def test_ctrl_c_is_logged_as_an_interruption(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    args = type("A", (), {"cmd": "collect", "config": None})()
    with recorder.isolated():
        recorder.at(tmp_path)
        recorder.record(args, ["collect"], 0.0, exit_code=1, error=KeyboardInterrupt())
        runs = recorder.read(tmp_path / recorder.LOG_FILE)
    check("exit 130, as the shell says, and no traceback",
          runs and runs[0]["exit"] == 130 and runs[0]["error"] == "interrupted", runs)


def test_a_live_process_is_asked_about_without_a_signal() -> None:
    check("this process is alive", state._alive(os.getpid()))
    check("a pid that is not is not", not state._alive(2 ** 22 + 12345))
    check("and the check lives in one place", "os.kill(pid, 0)" not in json.dumps(
        Path(state.__file__).read_text(encoding="utf-8").split("def _alive")[0]))
