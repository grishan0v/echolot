#!/usr/bin/env python3
"""The device's state around a set: cooled before each repeat, held still
while they run, and put back afterwards (echolot/stabilize.py, #135).

The device answers from a script, as in test_collect_fake_adb.py. Its
answers are what an SM-A515F (Android 13) printed: `dumpsys battery` in
tenths of a degree, a thermal HAL with `AP`, `BAT`, `SKIN` and a `USB` that
reads 0.0, a fixed performance mode that the shell may switch and that moves
no frequency limit, and `cmd connectivity airplane-mode` that reads its state
back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import runner, stabilize  # noqa: E402
from echolot.runner import RunnerError  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_fake_adb import PACKAGE, SERIAL, FakeAdb  # noqa: E402

# `dumpsys thermalservice` on the SM-A515F, at rest.
THERMAL = """\
IsStatusOverride: false
Thermal Status: {status}
Cached temperatures:
\tTemperature{{mValue=30.1, mType=0, mName=AP, mStatus=0}}
\tTemperature{{mValue=27.1, mType=2, mName=BAT, mStatus=0}}
\tTemperature{{mValue=0.0, mType=4, mName=USB, mStatus=0}}
\tTemperature{{mValue=29.0, mType=3, mName=SKIN, mStatus=0}}
HAL Ready: true
HAL connection:
\tThermalHAL 2.0 connected: yes
Current temperatures from HAL:
\tTemperature{{mValue={ap}, mType=0, mName=AP, mStatus=0}}
\tTemperature{{mValue=27.1, mType=2, mName=BAT, mStatus=0}}
\tTemperature{{mValue=29.0, mType=3, mName=SKIN, mStatus=0}}
\tTemperature{{mValue=0.0, mType=4, mName=USB, mStatus=0}}
Current cooling devices from HAL:
Temperature static thresholds from HAL:
"""
BATTERY = "Current Battery Service state:\n  USB powered: true\n  level: 85\n  temperature: {tenths}\n"
LIMITS = "policy0 403000 1742000\npolicy4 936000 2314000\n"


def shell(args) -> str:
    """What one call asked the device's shell, without `adb -s <serial> shell`."""
    args = list(args)
    if args[:2] == ["adb", "-s"] and args[3:4] == ["shell"]:
        return " ".join(args[4:])
    return " ".join(args)


class Phone:
    """The device's answers to what stabilize asks, in order, and every
    question kept. `temps` is what the sensor reads, one value a reading;
    the last one stays."""

    def __init__(self, temps=(30.1,), airplane="disabled", refuse: str | None = None,
                 limits_after: str = LIMITS):
        self.temps = list(temps)
        self.airplane = airplane
        self.refuse = refuse
        self.limits = LIMITS
        self.limits_after = limits_after
        self.asked: list[str] = []

    def _temp(self) -> float:
        return self.temps.pop(0) if len(self.temps) > 1 else self.temps[0]

    def __call__(self, args, stdin=None, timeout=120) -> str:
        text = shell(args)
        self.asked.append(text)
        if self.refuse and self.refuse in text:
            raise RunnerError(f"adb shell {text}\nError: {self.refuse} refused")
        if text == "dumpsys battery":
            return BATTERY.format(tenths=int(round(self._temp() * 10)))
        if text == "dumpsys thermalservice":
            return THERMAL.format(status=0, ap=self._temp())
        if text.startswith("for p in /sys/devices/system/cpu/cpufreq"):
            return self.limits
        if text == "cmd power set-fixed-performance-mode-enabled true":
            self.limits = self.limits_after
            return ""
        if text == "cmd power set-fixed-performance-mode-enabled false":
            self.limits = LIMITS
            return ""
        if text == "cmd connectivity airplane-mode":
            return self.airplane + "\n"
        if text.startswith("cmd connectivity airplane-mode "):
            self.airplane = "enabled" if text.endswith("enable") else "disabled"
            return ""
        if text == "settings get system screen_brightness_mode":
            return "1\n"
        if text == "settings get system screen_brightness":
            return "39\n"
        return ""


class Clock:
    """Time that moves only when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s

    def __call__(self) -> float:
        return self.now


def settled(section, phone, log=None, clock=None) -> stabilize.Settled:
    clock = clock or Clock()
    return stabilize.Settled(section, SERIAL, run=phone,
                             log=log.append if log is not None else (lambda m: None),
                             sleep=clock.sleep, clock=clock)


# --- what the section may say ------------------------------------------------

def test_cooldown_is_off_unless_set_and_refused_when_it_cannot_be_meant() -> None:
    check("absent or false is off", stabilize.cooldown_of({}) is None
          and stabilize.cooldown_of({"cooldown": False}) is None)
    check("a temperature, battery and ten minutes unless said",
          stabilize.cooldown_of({"cooldown": {"max_c": 33}}) == stabilize.Cooldown(33.0))
    check("a sensor of the thermal HAL, and a timeout",
          stabilize.cooldown_of({"cooldown": {"max_c": 34, "sensor": "cpu", "timeout_s": 120}})
          == stabilize.Cooldown(34.0, "cpu", 120))
    for value, said in ((True, "is not a setting"), (36, "is not a setting"),
                        ({"max_c": 33, "poll_s": 5}, "no key 'poll_s'"),
                        ({"max_c": "33C"}, "is not a temperature"),
                        ({"max_c": True}, "is not a temperature"),
                        ({"max_c": 150}, "is not a temperature"),
                        ({"max_c": 33, "sensor": "BIG"}, "'BIG' is not one of battery, cpu"),
                        ({"max_c": 33, "timeout_s": "10m"}, "not a number of seconds")):
        with pytest.raises(RunnerError, match=said):
            stabilize.cooldown_of({"cooldown": value})


def test_the_state_to_hold_is_only_what_is_set() -> None:
    check("nothing asked, nothing held", stabilize.knobs_of({}) == {}
          and not stabilize.wanted({"fixed_performance": False}))
    check("each one as written",
          stabilize.knobs_of({"fixed_performance": True, "airplane_mode": True, "brightness": 0})
          == {"fixed_performance": True, "airplane_mode": True, "brightness": 0})
    for section, said in (({"airplane_mode": "yes"}, "is not true or false"),
                          ({"brightness": 300}, "is not a screen level"),
                          ({"brightness": True}, "is not a screen level"),
                          ({"brightness": 12.5}, "is not a screen level")):
        with pytest.raises(RunnerError, match=said):
            stabilize.knobs_of(section)


# --- reading the device -------------------------------------------------------

def test_the_readings_as_the_device_prints_them() -> None:
    check("the battery in tenths of a degree",
          stabilize.battery_c(BATTERY.format(tenths=273)) == 27.3)
    hal = stabilize.hal_temperatures(THERMAL.format(status=0, ap=30.1))
    check("the HAL's current readings, the USB's 0.0 left out",
          hal == [(0, "AP", 30.1), (2, "BAT", 27.1), (3, "SKIN", 29.0)], hal)
    cached = "Thermal Status: 2\nCached temperatures:\n\tTemperature{mValue=41.5, mType=0, mName=cpu0, mStatus=1}\nHAL Ready: true\n"
    check("the cached ones when the HAL lists none, and the status",
          stabilize.hal_temperatures(cached) == [(0, "cpu0", 41.5)]
          and stabilize.thermal_status(cached) == 2)
    check("nothing to read is nothing", stabilize.battery_c("level: 85") is None
          and stabilize.hal_temperatures("") == [])


# --- around a set -------------------------------------------------------------

def test_a_repeat_waits_until_the_sensor_is_cool_enough() -> None:
    phone, clock, said = Phone(temps=(31.0, 41.0, 38.0, 33.5)), Clock(), []
    with settled({"cooldown": {"max_c": 34, "sensor": "cpu"}}, phone, said, clock) as s:
        waited = s.cool()
    check("asked until it read 33.5, ten seconds apart",
          clock.slept == [10, 10] and waited == 20, (clock.slept, waited))
    check("and said so", "  cooling: cpu 41.0 °C, waiting for 34" in said
          and "  cooled to 33.5 °C in 20s" in said, said)
    check("a cool device does not wait", settled({"cooldown": {"max_c": 34}}, Phone()).cool() == 0)


def test_a_device_that_does_not_cool_is_recorded_as_it_is() -> None:
    phone, clock, said = Phone(temps=(40.0,)), Clock(), []
    with settled({"cooldown": {"max_c": 34, "timeout_s": 25}}, phone, said, clock) as s:
        waited = s.cool()
    check("it gives up at the timeout, no later", waited == 25 and clock.slept == [10, 10, 5],
          (waited, clock.slept))
    check("and says the report will show the heat",
          any("still 40.0 °C after 25s; recording anyway" in line for line in said), said)


def test_a_sensor_the_device_lacks_stops_the_set_before_anything_changes() -> None:
    phone = Phone()
    with pytest.raises(RunnerError, match="has no `gpu` sensor: it reports battery, cpu, skin"):
        with settled({"cooldown": {"max_c": 34, "sensor": "gpu"}, "brightness": 128}, phone):
            pass
    check("nothing was switched", not any(q.startswith(("settings put", "cmd"))
                                          for q in phone.asked), phone.asked)


def test_what_is_changed_is_put_back_in_reverse_whatever_ends_the_set() -> None:
    phone, said = Phone(), []
    section = {"fixed_performance": True, "airplane_mode": True, "brightness": 128}
    with pytest.raises(RuntimeError):
        with settled(section, phone, said):
            changed = [q for q in phone.asked if q.startswith(("settings put", "cmd"))]
            raise RuntimeError("the recording failed")
    check("on the way in: the mode, the radio, the screen",
          changed == ["cmd power set-fixed-performance-mode-enabled true",
                      "cmd connectivity airplane-mode",
                      "cmd connectivity airplane-mode enable",
                      "settings put system screen_brightness_mode 0",
                      "settings put system screen_brightness 128"], changed)
    check("on the way out, in reverse, to what they were",
          phone.asked[-4:] == ["settings put system screen_brightness 39",
                               "settings put system screen_brightness_mode 1",
                               "cmd connectivity airplane-mode disable",
                               "cmd power set-fixed-performance-mode-enabled false"],
          phone.asked[-6:])
    check("each one said", sum(line.startswith("  put back: ") for line in said) == 3, said)
    check("the brightness said with what it was",
          "  screen brightness 128, auto-brightness off (was 39, auto)" in said, said)


def test_a_mode_the_device_takes_and_does_not_honour_is_called_so() -> None:
    said = []
    with settled({"fixed_performance": True}, Phone(), said):
        pass
    check("the limits did not move, and the line says the clock still follows the load",
          any("kept every frequency limit as it was (policy0 403–1742 MHz, policy4 "
              "936–2314 MHz)" in line for line in said), said)
    said = []
    with settled({"fixed_performance": True},
                 Phone(limits_after="policy0 1248000 1248000\npolicy4 1690000 1690000\n"), said):
        pass
    check("limits that moved are said as they now stand",
          "  fixed performance mode on: policy0 1248–1248 MHz, policy4 1690–1690 MHz" in said,
          said)


def test_a_change_the_device_refuses_is_said_and_the_set_goes_on() -> None:
    phone, said = Phone(refuse="airplane-mode enable"), []
    with settled({"airplane_mode": True, "brightness": 64}, phone, said):
        pass
    check("said, and not put back since it never happened",
          any(line.startswith("  [!] airplane mode: the device refused it") for line in said)
          and "cmd connectivity airplane-mode disable" not in phone.asked, said)
    check("the rest still applied and restored",
          "settings put system screen_brightness 64" in phone.asked
          and "  put back: screen brightness" in said, said)


def test_a_radio_already_off_is_left_as_it_was() -> None:
    phone, said = Phone(airplane="enabled"), []
    with settled({"airplane_mode": True}, phone, said):
        pass
    check("not switched, and not switched back",
          "  airplane mode: already on, and left as it is" in said
          and not any("airplane-mode e" in q or "airplane-mode d" in q for q in phone.asked),
          phone.asked)


# --- in collect ----------------------------------------------------------------

class SettlingAdb(FakeAdb):
    """FakeAdb, with a phone behind it for what stabilize asks."""

    def __init__(self, phone: Phone, **kw) -> None:
        super().__init__(**kw)
        self.phone = phone

    def __call__(self, args, stdin=None, timeout=120) -> str:
        text = shell(args)
        if text.startswith(("dumpsys", "settings", "cmd power", "cmd connectivity", "for p in")):
            self.calls.append(list(args))
            self.stdin.append(stdin)
            return self.phone(args, stdin, timeout)
        return super().__call__(args, stdin, timeout)


def test_collect_cools_before_each_repeat_and_puts_the_screen_back(tmp_path, monkeypatch) -> None:
    phone = Phone(temps=(30.0, 30.0, 36.0, 33.0, 30.0))
    adb = SettlingAdb(phone)
    monkeypatch.setattr(runner, "_run", adb)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    clock = Clock()
    monkeypatch.setattr(stabilize.time, "sleep", clock.sleep)
    monkeypatch.setattr(stabilize.time, "monotonic", clock)
    said: list[str] = []
    runner.collect(PACKAGE, tmp_path / "traces", 3,
                   section={"cooldown": {"max_c": 34}, "brightness": 100},
                   name="startup", log=said.append)
    asked = phone.asked
    check("the screen set before the first repeat, and back after the last",
          asked.index("settings put system screen_brightness 100") < asked.index(
              "dumpsys battery", 2)
          and asked[-2:] == ["settings put system screen_brightness 39",
                             "settings put system screen_brightness_mode 1"], asked)
    kinds = [shell(c) for c in adb.calls]
    stops = [i for i, k in enumerate(kinds) if "am force-stop" in k]
    # The screen is read after the wait, which can outlast it (screen.py).
    check("a reading before each repeat's stop, then the screen",
          all(kinds[i - 2] == "dumpsys battery"
              and kinds[i - 1] == "dumpsys power; dumpsys window policy" for i in stops)
          and len(stops) == 3, kinds)
    check("the warm one waited", clock.slept == [10] and "  cooled to 33.0 °C in 10s" in said,
          (clock.slept, said))


def test_a_collect_that_fails_still_puts_the_device_back(tmp_path, monkeypatch) -> None:
    phone = Phone()
    adb = SettlingAdb(phone, fail="pull")
    monkeypatch.setattr(runner, "_run", adb)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    with pytest.raises(RunnerError, match="pull refused"):
        runner.collect(PACKAGE, tmp_path / "traces", 2, section={"airplane_mode": True},
                       name="startup", log=lambda m: None)
    check("the radio came back on", phone.asked[-1] == "cmd connectivity airplane-mode disable"
          and phone.airplane == "disabled", phone.asked)


def test_a_typo_stops_collect_before_the_device_is_asked(tmp_path, monkeypatch) -> None:
    adb = SettlingAdb(Phone())
    monkeypatch.setattr(runner, "_run", adb)
    with pytest.raises(RunnerError, match="runner.cooldown.max_c"):
        runner.collect(PACKAGE, tmp_path / "traces", 2, section={"cooldown": {"max_c": "hot"}},
                       name="startup", log=lambda m: None)
    check("not even adb devices", adb.calls == [], adb.calls)


def test_status_says_a_collect_is_cooling_between_repeats() -> None:
    from echolot import state
    told: list[dict] = []
    phone, clock = Phone(temps=(31.0, 40.0, 33.0)), Clock()
    with stabilize.Settled({"cooldown": {"max_c": 34, "sensor": "cpu"}}, SERIAL, run=phone,
                           notify=lambda **kw: told.append(kw), sleep=clock.sleep,
                           clock=clock) as s:
        s.cool()
    check("the progress is told what the wait waits for, then that it is over",
          told == [{"cooling": "cpu 40.0 °C, waiting for 34"}, {"cooling": None}], told)
    line = state.collect_line({"collect": {"status": "running", "scenario": "startup",
                                           "iterations": 8, "done": 2, "started": None,
                                           "cooling": told[0]["cooling"]}})
    check("and status says so", "2/8 iterations, cooling: cpu 40.0 °C, waiting for 34" in line,
          line)


def test_gradle_mode_cools_once_before_the_task_and_hands_it_the_device(tmp_path,
                                                                        monkeypatch) -> None:
    """The macrobenchmark runs its repeats back to back, so the wait comes
    before the task; the device is picked first, since it has to be known to
    be held still, and gradle gets the same one."""
    from tests.test_collect_fake_adb import WRITES_TWO, wrapper
    root = tmp_path / "app"
    wrapper(root, WRITES_TWO)
    phone = Phone(temps=(30.0, 37.0, 33.0))
    adb = SettlingAdb(phone)
    monkeypatch.setattr(runner, "_run", adb)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    clock = Clock()
    monkeypatch.setattr(stabilize.time, "sleep", clock.sleep)
    monkeypatch.setattr(stabilize.time, "monotonic", clock)
    said: list[str] = []
    results = runner.collect(PACKAGE, tmp_path / "traces", 5, name="startup", log=said.append,
                             section={"mode": "gradle", "gradle": "./fake-gradlew",
                                      "gradle_task": ":benchmark:connectedBenchmarkAndroidTest",
                                      "project_root": str(root),
                                      "cooldown": {"max_c": 34, "sensor": "cpu"}})
    check("both traces collected", len(results) == 2, results)
    check("the device picked first, and handed to gradle",
          shell(adb.calls[0]) == "adb devices"
          and (root / "serial.txt").read_text() == SERIAL, adb.calls[:2])
    check("one wait, before the task, and said to be the only one",
          clock.slept == [10]
          and "  cooldown: before the task only; the macrobenchmark runs its repeats back "
              "to back" in said, (clock.slept, said))


def test_a_knob_the_device_cannot_read_is_skipped_and_an_interrupt_puts_back_the_rest() -> None:
    phone, said = Phone(refuse="cmd connectivity airplane-mode"), []
    with settled({"airplane_mode": True, "brightness": 64}, phone, said):
        pass
    check("an Android without the command: said, and the screen still held",
          any(line.startswith("  [!] airplane mode: ") for line in said)
          and "settings put system screen_brightness 64" in phone.asked, said)

    class Interrupted(Phone):
        def __call__(self, args, stdin=None, timeout=120):
            if shell(args) == "cmd connectivity airplane-mode":
                raise KeyboardInterrupt
            return super().__call__(args, stdin, timeout)

    phone = Interrupted()
    with pytest.raises(KeyboardInterrupt):
        with settled({"fixed_performance": True, "airplane_mode": True}, phone):
            pass
    check("what was switched before the interrupt is switched back",
          phone.asked[-1] == "cmd power set-fixed-performance-mode-enabled false", phone.asked)
