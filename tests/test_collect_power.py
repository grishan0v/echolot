#!/usr/bin/env python3
"""The phone's power around a set, and the app after a gradle round (#524).

The doctor checks in echolot/selftest.py hold the rule: a phone not charging
under `runner.min_battery` stops the set, above it gets one line, a change
between repeats is said where it was found, and a gradle round that
uninstalled the app says so. These are the edges around it, on the same fake
adb as the other collect tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import power, recording, runner, screen  # noqa: E402
from echolot.selftest import _powered  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_fake_adb import (  # noqa: E402
    INSTALLED, PACKAGE, SERIAL, WRITES_TWO, FakeAdb, old_set, wrapper)


@pytest.fixture
def adb(monkeypatch) -> FakeAdb:
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    return fake


def test_the_power_as_the_read_says_it() -> None:
    s = screen.parse(_powered(False, 49))
    check("not charging, its level, saver off",
          (s.powered, s.level, s.saver) == (False, 49, False), s)
    check("said in words", power.phrase(s) == "not charging, 49%", power.phrase(s))
    check("saver on", screen.parse(_powered(True, 80, saver="1")).saver is True)
    check("a phone that never had saver on answers null, and that is off",
          screen.parse(_powered(True, 80, saver="null")).saver is False)
    check("a `settings` that printed nothing is unknown, not off",
          screen.parse("low_power=\n").saver is None)
    check("and the dumps' own lines are not taken for it",
          screen.parse("  mLowPowerModeSetting=true\n").saver is None)
    check("one adb call reads the screen and the power", screen.READ.startswith(
        "dumpsys power; dumpsys window policy;") and "low_power" in screen.READ)


@pytest.mark.parametrize("value", [-1, 101, "25%", True, None])
def test_a_floor_that_is_not_a_percentage_stops_before_anything(tmp_path, adb, value):
    with pytest.raises(runner.RunnerError) as e:
        runner.collect(PACKAGE, tmp_path / "traces", 1, section={"min_battery": value},
                       name="startup", log=lambda m: None)
    check("named, with what it takes", "runner.min_battery" in str(e.value)
          and "0 to 100" in str(e.value), str(e.value))
    check("before adb is asked anything", adb.calls == [], adb.kinds())


def test_a_charging_phone_under_the_floor_is_recorded(tmp_path, adb):
    """Its level rises; Macrobenchmark's floor, charging or not, is its own."""
    adb.script["screen"] = [_powered(True, 10)]
    said: list[str] = []
    made = runner.collect(PACKAGE, tmp_path / "traces", 1, section={}, name="startup",
                          log=said.append)
    check("recorded, and nothing said of the power",
          len(made) == 1 and not any("power" in s for s in said), said)


def test_battery_saver_alone_gets_its_line(tmp_path, adb):
    adb.script["screen"] = [_powered(True, 80, saver="1")]
    said: list[str] = []
    made = runner.collect(PACKAGE, tmp_path / "traces", 1, section={}, name="startup",
                          log=said.append)
    lines = [s for s in said if "power" in s]
    check("one line, naming saver", len(lines) == 1 and "battery saver on" in lines[0], said)
    check("and the account keeps it",
          recording.read(made[0]["path"]).get("battery_saver") == "on")


def test_under_the_floor_the_previous_set_stays(tmp_path, adb):
    out = tmp_path / "traces"
    before = old_set(out)
    adb.script["screen"] = [_powered(False, 12)]
    with pytest.raises(runner.RunnerError) as e:
        runner.collect(PACKAGE, out, 3, section={}, name="startup", log=lambda m: None)
    check("the gist names the level and the knob", "12%" in e.value.gist
          and "runner.min_battery" in e.value.gist, e.value.gist)
    check("nothing recorded", "perfetto" not in adb.kinds(), adb.kinds())
    check("and the set from before is where it was",
          sorted(p.name for p in out.iterdir()) == before, list(out.iterdir()))


def test_an_app_that_is_not_installed_names_the_gradle_round(tmp_path, adb):
    adb.script["installed"] = [""]
    with pytest.raises(runner.RunnerError) as e:
        runner.collect(PACKAGE, tmp_path / "traces", 1, section={}, name="startup",
                       log=lambda m: None)
    check("one likely cause said", "gradle-mode round uninstalls" in str(e.value),
          str(e.value))


# --- gradle mode ---------------------------------------------------------------

GRADLE = {"mode": "gradle", "gradle": "./fake-gradlew",
          "gradle_task": ":benchmark:connectedBenchmarkAndroidTest"}


def _gradle(tmp_path: Path, said: list[str], device: str | None = None) -> list[dict]:
    root = tmp_path / "app"
    wrapper(root, WRITES_TWO)
    return runner.collect(PACKAGE, tmp_path / "traces", 1, name="startup", log=said.append,
                          device=device, section={**GRADLE, "project_root": str(root)})


def test_gradle_mode_without_adb_on_path_runs_as_before(tmp_path, monkeypatch):
    """Gradle finds its own adb; echolot's is not needed, and its absence is
    a phone not asked. The guard in conftest answers as no adb at all."""
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    said: list[str] = []
    made = _gradle(tmp_path, said)
    check("both traces, nothing said of the phone", len(made) == 2
          and not any("power" in s or "installed" in s for s in said), said)
    check("the account says nothing of the power it did not read",
          recording.read(made[0]["path"]) == {"runner": "gradle"},
          recording.read(made[0]["path"]))


def test_gradle_mode_asks_the_one_phone_attached_and_keeps_its_power(tmp_path, adb):
    adb.script["screen"] = [_powered(False, 60)]
    adb.script["installed"] = [INSTALLED, ""]
    said: list[str] = []
    made = _gradle(tmp_path, said)
    check("the phone is the one adb lists", adb.calls[1][:3] == ["adb", "-s", SERIAL],
          adb.calls)
    check("its power said before the task, and the uninstall after it",
          [s.split(":")[0] for s in said if "[!]" in s]
          == ["  [!] power", f"  [!] {PACKAGE} is no longer installed on {SERIAL}"], said)
    check("each trace keeps how the phone was powered when the task began",
          all(recording.read(r["path"]).get("power") == "not charging" for r in made))


def test_gradle_mode_stops_under_the_floor_before_the_task(tmp_path, adb):
    adb.script["screen"] = [_powered(False, 15)]
    with pytest.raises(runner.RunnerError):
        _gradle(tmp_path, [], device=SERIAL)
    check("gradle never ran", not (tmp_path / "app" / "serial.txt").exists())


def test_gradle_mode_with_several_phones_and_none_named_asks_none(tmp_path, adb):
    adb.listing = (f"List of devices attached\n{SERIAL}\tdevice\n"
                   "emulator-5554\tdevice\n\n")
    said: list[str] = []
    _gradle(tmp_path, said)
    check("only the listing", adb.kinds() == ["devices"], adb.kinds())
