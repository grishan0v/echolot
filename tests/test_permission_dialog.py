#!/usr/bin/env python3
"""A launch the system's permission dialog took (#810).

A debug build that asks for `POST_NOTIFICATIONS` before its first frame, on an
SM-A515F with Android 13, never granted: the dialog comes up over the app,
and the platform's launch ends on it. `am start -W` names the permission
controller's `GrantPermissionsActivity`, and the trace's launch is
`com.google.android.permissioncontroller`'s. `collect` said the app went on
to another activity by itself, and `analyze` with `window: launch` said the
trace held no launch of the app and sent a person who had recorded with
`runner.mode: launch` to set `runner.mode: launch`. After `pm grant` the same
build gave its launch window.

`collect` names the dialog under the first repeat it took and at the end of
the set, with the command that grants the permission; `analyze` names it from
the trace's launches, or from what `collect` kept beside the trace.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder, recording, runner  # noqa: E402
from echolot import launch as launch_mod  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from echolot.config import Config, ConfigError  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.selftest import BEFORE_FIRST_FRAME, main_thread_trace  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_fake_adb import (  # noqa: E402
    ACTIVITY, AM_START, CONFIG, PACKAGE, SERIAL, FakeAdb, kind)

CONTROLLER = "com.google.android.permissioncontroller"
DIALOG = f"{CONTROLLER}/com.android.permissioncontroller.permission.ui.GrantPermissionsActivity"
NOTIFICATIONS = "android.permission.POST_NOTIFICATIONS"
GRANT = f"`adb shell pm grant {PACKAGE} {NOTIFICATIONS}`"

# `dumpsys package` of an app on an SM-A515F (Android 13), cut to the
# sections read, with a second user whose list repeats the first's.
DUMP = f"""\
Packages:
  Package [{PACKAGE}] (2c4e3f1):
    requested permissions:
      android.permission.INTERNET
      android.permission.CAMERA
      {NOTIFICATIONS}
    install permissions:
      android.permission.INTERNET: granted=true
      android.permission.FOREGROUND_SERVICE: granted=false
    User 0: ceDataInode=4321 installed=true hidden=false suspended=false stopped=false
      gids=[3003]
      runtime permissions:
        android.permission.CAMERA: granted=false, flags=[ USER_SENSITIVE_WHEN_GRANTED]
        {NOTIFICATIONS}: granted=false, flags=[ USER_SENSITIVE_WHEN_GRANTED]
        android.permission.ACCESS_FINE_LOCATION: granted=true, flags=[ USER_SET]
      disabledComponents:
        {PACKAGE}.Unused
    User 10: ceDataInode=0 installed=true hidden=false suspended=false stopped=true
      runtime permissions:
        android.permission.CAMERA: granted=false, flags=[ USER_SENSITIVE_WHEN_GRANTED]
"""
DUMPSYS = f"adb -s {SERIAL} shell dumpsys package {PACKAGE}"


def ended_on(component: str) -> str:
    return AM_START.format(activity=ACTIVITY, total=812, wait=840).replace(
        f"Activity: {ACTIVITY}", f"Activity: {component}")


# --- what the device says ----------------------------------------------------------

def test_the_ungranted_runtime_permissions_are_read_from_the_dump() -> None:
    check("the runtime ones without a grant, once each, in the order listed",
          runner.parse_ungranted(DUMP) == ["android.permission.CAMERA", NOTIFICATIONS],
          runner.parse_ungranted(DUMP))
    check("a device that says nothing gives none", runner.parse_ungranted("") == [])


def test_the_grant_names_the_notifications_permission_first() -> None:
    said = launch_mod.grant_said(PACKAGE, ["android.permission.CAMERA", NOTIFICATIONS])
    check("the command for the one an app asks at its start",
          said.startswith(f"grant it once before recording, {GRANT}"), said)
    check("and the others it holds ungranted, as candidates",
          "`android.permission.CAMERA` ungranted too; the dialog asks for one of them"
          in said and said.endswith("or tap Allow on the phone"), said)
    unknown = launch_mod.grant_said(PACKAGE)
    check("without the list: the usual one, and how to list the rest",
          f"`adb shell pm grant {PACKAGE} <permission>`" in unknown
          and NOTIFICATIONS in unknown and f"`adb shell dumpsys package {PACKAGE}`" in unknown,
          unknown)


@pytest.mark.parametrize("name, dialog", [
    (DIALOG, True), (CONTROLLER, True),
    ("com.android.permissioncontroller/.permission.ui.GrantPermissionsActivity", True),
    ("com.android.packageinstaller/.permission.ui.GrantPermissionsActivity", True),
    (ACTIVITY, False), ("", False), (None, False),
])
def test_the_dialog_is_known_by_its_package(name, dialog) -> None:
    check(f"{name!r}", launch_mod.permission_dialog(name) is dialog)


# --- collect -----------------------------------------------------------------------

def test_collect_says_the_dialog_took_the_launch(tmp_path, monkeypatch, capsys) -> None:
    fake = FakeAdb(script={"start": [ended_on(DIALOG)], DUMPSYS: [DUMP]})
    monkeypatch.setattr(runner, "_run", fake)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with recorder.isolated():
        code = main(["collect"])
    _, err = capsys.readouterr()
    lines = err.splitlines()
    first = next(i for i, s in enumerate(lines) if "[1/3]" in s)
    under = lines[first + 1].strip()
    check("exit 0: the round is recorded", code == 0, err)
    check("a line under repeat 1 says the dialog took the launch",
          under.startswith(f"[!] a permission dialog took the launch: it ended on "
                           f"{CONTROLLER}'s GrantPermissionsActivity, which came up over "
                           f"{PACKAGE} before its first frame"), under)
    check("with the command that grants it", GRANT in under, under)
    check("and only there, with nothing of an activity the app went on to",
          err.count("a permission dialog took the launch") == 1 and "by itself" not in err,
          err)
    check("the end of the set says it of every launch",
          f"[!] A permission dialog took every launch of the 3: each ended on {CONTROLLER}'s"
          in err and GRANT in err, err)
    check("the device was asked once", fake.kinds().count(DUMPSYS) == 1, fake.kinds())
    traces = tmp_path / ".echolot" / "traces"
    kept = [runner.launch_record(traces / f"startup_iter{i:03d}.perfetto-trace")
            for i in range(3)]
    check("every repeat's record keeps where it ended and what the app lacked",
          all(k["activity"] == DIALOG and k["ungranted"] == ["android.permission.CAMERA",
                                                              NOTIFICATIONS] for k in kept),
          kept)


def test_a_launch_of_the_app_asks_nothing_of_its_permissions(tmp_path, monkeypatch) -> None:
    fake = FakeAdb(script={DUMPSYS: [DUMP]})
    monkeypatch.setattr(runner, "_run", fake)
    made = runner.collect(PACKAGE, tmp_path / "t", 2, section={}, name="startup",
                          log=lambda m: None)
    check("no dump read", DUMPSYS not in fake.kinds(), fake.kinds())
    check("and nothing kept", "ungranted" not in runner.launch_record(made[0]["path"]))


def test_a_set_the_dialog_took_in_part_says_what_the_dialog_is(tmp_path, monkeypatch) -> None:
    """Allow tapped during the set: the first repeat ended on the dialog."""
    fake = FakeAdb(script={"start": [ended_on(DIALOG), ended_on(ACTIVITY)], DUMPSYS: [DUMP]})
    monkeypatch.setattr(runner, "_run", fake)
    made = runner.collect(PACKAGE, tmp_path / "t", 3, section={}, name="startup",
                          log=lambda m: None)
    end = runner.activities_said(made) or ""
    check("the repeat is named, and the dialog for what it is",
          "GrantPermissionsActivity in repeat 1" in end
          and "is the system's permission dialog" in end and "by itself" not in end, end)


# --- analyze -----------------------------------------------------------------------

LAUNCH = {"project": {"package": PACKAGE, "process": PACKAGE},
          "scenario": {"name": "startup", "window": "launch"}}


def _cold_start(path: Path, completed_as: str) -> Path:
    """A cold start of the app at 100 ms, whose launch the platform closed at
    400 as `completed_as`'s: the app's own, or the dialog's that came up over it."""
    return main_thread_trace(path, BEFORE_FIRST_FRAME, launched_as=completed_as)


def test_analyze_names_the_dialog_that_took_the_launch(tmp_path: Path) -> None:
    trace = _cold_start(tmp_path / "startup_iter000.perfetto-trace", CONTROLLER)
    with pytest.raises(launch_mod.NoLaunch) as e:
        analyze_trace(trace, Config(LAUNCH))
    said = str(e.value)
    check("the trace's launch is the dialog's, said as such",
          said.startswith(f"no launch of {PACKAGE} in trace startup_iter000.perfetto-trace: "
                          f"its launch is {CONTROLLER}'s. A permission dialog came up over "
                          f"the app before its first frame"), said)
    check("the fix is the grant, and the runner is not blamed",
          f"`adb shell pm grant {PACKAGE} <permission>`" in said and "runner.mode" not in said,
          said)
    check("a ConfigError, as every window that cannot be cut is",
          isinstance(e.value, ConfigError))
    recording.write(trace, {"runner": "launch", "launch": {"activity": DIALOG,
                                                           "ungranted": [NOTIFICATIONS]}})
    with pytest.raises(launch_mod.NoLaunch) as e:
        analyze_trace(trace, Config(LAUNCH))
    check("with collect's record beside it, the permission it lacked is named",
          GRANT in str(e.value), e.value)


def test_the_app_s_own_launch_is_still_the_window(tmp_path: Path) -> None:
    rep = analyze_trace(_cold_start(tmp_path / "own.perfetto-trace", PACKAGE), Config(LAUNCH))
    check("closed at 400 by the app's own launch",
          rep["window"]["defined_by"] == "launch"
          and rep["window"]["ts_end"] - rep["window"]["ts_start"] == 300 * 1_000_000,
          rep["window"])


def test_a_launch_mode_recording_with_no_launch_is_not_sent_to_launch_mode(
        tmp_path: Path) -> None:
    trace = tmp_path / "startup_iter001.perfetto-trace"
    trace.write_bytes(b"a trace")
    recording.write(trace, {"runner": "launch", "launch": {"activity": ACTIVITY}})

    class _Tp:
        def exec_script(self, sql: str) -> None:
            pass

        def query(self, sql: str) -> list[dict]:
            return [{"name": PACKAGE}] if "FROM _proc" in sql else []

    with pytest.raises(launch_mod.NoLaunch) as e:
        launch_mod.find(_Tp(), 1, str(trace))
    said = str(e.value)
    check("it says collect started the app, and not to set the mode it was in",
          "`collect` started the app inside this recording (`runner.mode: launch`)" in said
          and "does that" not in said and "permission" not in said, said)
    trace.with_name(trace.name + recording.SIDECAR_SUFFIX).unlink()
    with pytest.raises(launch_mod.NoLaunch) as e:
        launch_mod.find(_Tp(), 1, str(trace))
    check("a trace nobody says was recorded so keeps the advice",
          "`runner.mode: launch` does that" in str(e.value), e.value)


# --- collect stops a launch-window round the dialog took ---------------------------

def _round(tmp_path, monkeypatch, capsys, scenario: str) -> tuple[int, str, FakeAdb]:
    """`echolot collect` of three repeats whose every launch the dialog took,
    with `scenario` as the config's: the device hands over a real trace of it."""
    body = _cold_start(tmp_path / "dialog.perfetto-trace", CONTROLLER).read_bytes()
    fake = FakeAdb(script={"start": [ended_on(DIALOG)], DUMPSYS: [DUMP]})

    def run(args, stdin=None, timeout=120):
        answer = fake(args, stdin, timeout)
        if kind(list(args)) == "pull":
            Path(args[-1]).write_bytes(body)
        return answer
    monkeypatch.setattr(runner, "_run", run)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    (tmp_path / "echolot.yml").write_text(
        f"project:\n  package: {PACKAGE}\nscenario:\n  name: startup\n{scenario}"
        f"runner:\n  iterations: 3\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with recorder.isolated():
        code = main(["collect"])
    _, err = capsys.readouterr()
    return code, err, fake


def test_a_launch_window_round_stops_at_the_first_repeat_the_dialog_took(
        tmp_path, monkeypatch, capsys) -> None:
    code, err, fake = _round(tmp_path, monkeypatch, capsys, "  window: launch\n")
    check("exit 2, a collection error", code == 2 and "collection error: repeat 1 of 3: "
          f"no launch of {PACKAGE} in trace startup_iter000.perfetto-trace: its launch is "
          f"{CONTROLLER}'s. A permission dialog came up over the app" in err, err)
    check("naming the permission collect read", GRANT in err.split("collection error:")[1], err)
    check("nothing more was recorded", fake.kinds().count("perfetto") == 1, fake.kinds())
    check("and nothing kept", not list((tmp_path / ".echolot" / "traces").glob(
        "*.perfetto-trace")), list((tmp_path / ".echolot" / "traces").iterdir()))


def test_an_anchored_round_the_dialog_took_goes_on(tmp_path, monkeypatch, capsys) -> None:
    """`bindApplication` to `activityStart` closes before the dialog is drawn,
    and before the app's first frame (#849): measured as it is."""
    code, err, _ = _round(tmp_path, monkeypatch, capsys,
                          "  start: {name: bindApplication}\n  end: {name: activityStart}\n")
    check("three repeats, the dialog said under the first", code == 0
          and "[3/3]" in err and err.count("a permission dialog took the launch") == 1, err)
