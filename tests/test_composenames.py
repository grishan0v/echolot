#!/usr/bin/env python3
"""`runner.compose_names`: a Compose app's Perfetto SDK switched on for a set
over adb, and off after it (echolot/composenames.py, #363).

The app answers from a script with what an SM-A515F printed for a Compose
app built with `runtime-tracing` and `tracing-perfetto` 1.0.1: the enable
broadcast without a library says `result=11` and the version it needs, the
same with the pushed library says `result=1`, and `am broadcast` prints both
on a `Broadcast completed` line.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import composenames, compare, report, runner  # noqa: E402
from echolot.runner import RunnerError  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_fake_adb import PACKAGE, SERIAL, FakeAdb  # noqa: E402
from tests.test_stabilize import shell  # noqa: E402

ENABLE = "am broadcast -a androidx.tracing.perfetto.action.ENABLE_TRACING_COLD_START"
DISABLE = "am broadcast -a androidx.tracing.perfetto.action.DISABLE_TRACING_COLD_START"
COMPLETED = 'Broadcast completed: result={code}, data="{{"exitCode":{code},"requiredVersion":"1.0.1"{extra}}}"'
MISSING = ',"message":"java.lang.UnsatisfiedLinkError: dlopen failed: library \\"libtracing_perfetto.so\\" not found"'
MEDIA = f"/sdcard/Android/media/{PACKAGE}"


def _aar(abis=("arm64-v8a",)) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for abi in abis:
            z.writestr(f"jni/{abi}/libtracing_perfetto.so", f"ELF for {abi}")
    return buf.getvalue()


@pytest.fixture
def caches(tmp_path, monkeypatch):
    """Gradle's cache with the 1.0.1 AAR in it, and an empty echolot cache."""
    gradle = tmp_path / "gradle"
    aar = (gradle / "caches/modules-2/files-2.1/androidx.tracing/tracing-perfetto-binary"
           / "1.0.1/58c6ede6/tracing-perfetto-binary-1.0.1.aar")
    aar.parent.mkdir(parents=True)
    aar.write_bytes(_aar())
    monkeypatch.setenv("GRADLE_USER_HOME", str(gradle))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path


class App:
    """The device and the app's tracing receiver, answering as on the A51."""

    def __init__(self, first: int = 11, second: int = 1, unanswered: bool = False,
                 installed: bool = True) -> None:
        self.first, self.second = first, second
        self.unanswered = unanswered
        self.installed = installed
        self.asked: list[str] = []

    def __call__(self, args, stdin=None, timeout=120) -> str:
        text = shell(args)
        self.asked.append(text)
        if text.startswith(ENABLE):
            code = self.second if "--es path" in text else self.first
            if code == 0:
                return "Broadcast completed: result=0\n"
            return COMPLETED.format(code=code, extra=MISSING if code == 11 else "") + "\n"
        if text.startswith(DISABLE):
            if self.unanswered:
                return "Broadcast completed: result=0\n"
            return COMPLETED.format(code=1, extra="") + "\n"
        if text == "getprop ro.product.cpu.abi":
            return "arm64-v8a\n"
        if text.startswith(f"pm path {PACKAGE}"):
            return f"package:/data/app/~~kQ1==/{PACKAGE}-x9A==/base.apk\n" if self.installed else ""
        return ""


def names(app: App, log=None) -> composenames.Names:
    return composenames.Names(PACKAGE, SERIAL, run=app,
                              log=log.append if log is not None else (lambda m: None))


# --- what the section may say, and what the app answers -------------------

def test_compose_names_is_off_unless_set_and_refused_when_it_cannot_be_meant() -> None:
    check("absent or false", not composenames.wanted({})
          and not composenames.wanted({"compose_names": False}))
    check("true", composenames.wanted({"compose_names": True}))
    with pytest.raises(RunnerError, match="is not true or false"):
        composenames.wanted({"compose_names": "yes"})


def test_the_receivers_answer_as_am_prints_it() -> None:
    check("success, with the version", composenames.answer(
        "Broadcasting: Intent { … }\n" + COMPLETED.format(code=1, extra=""))
        == (1, {"exitCode": 1, "requiredVersion": "1.0.1"}))
    code, data = composenames.answer(COMPLETED.format(code=11, extra=MISSING))
    check("no library: the version it needs, the message's quotes kept",
          code == 11 and data["requiredVersion"] == "1.0.1"
          and 'library "libtracing_perfetto.so" not found' in data["message"], data)
    check("nothing answered", composenames.answer("Broadcast completed: result=0") == (0, {}))
    check("extras after the data", composenames.answer(
        COMPLETED.format(code=2, extra="") + ", extras: Bundle[mParcelledData.dataSize=48]")[0] == 2)
    with pytest.raises(RunnerError, match="gave no answer"):
        composenames.answer("Error: Activity not started")


# --- the library ------------------------------------------------------------

def test_the_library_comes_from_gradles_cache_for_the_devices_abi(caches) -> None:
    lib = composenames.library("1.0.1", "arm64-v8a", log=lambda m: None)
    check("extracted from the AAR Gradle cached", lib.read_bytes() == b"ELF for arm64-v8a", lib)
    with pytest.raises(RunnerError, match="has no library for x86"):
        composenames.library("1.0.1", "x86", log=lambda m: None)


def test_the_library_is_fetched_from_maven_and_checked(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GRADLE_USER_HOME", str(tmp_path / "no-gradle"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    body = _aar()
    import hashlib
    served = {composenames.MAVEN.format(v="1.2.0"): body,
              composenames.MAVEN.format(v="1.2.0") + ".sha1":
                  hashlib.sha1(body).hexdigest().encode()}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(composenames.urllib.request, "urlopen",
                        lambda url, timeout=60: Response(served[url]))
    said: list[str] = []
    lib = composenames.library("1.2.0", "arm64-v8a", log=said.append)
    check("fetched, said so, and cached as an AAR beside echolot's own",
          lib.read_bytes() == b"ELF for arm64-v8a"
          and (tmp_path / "cache/echolot/tracing-perfetto-binary-1.2.0.aar").exists()
          and any("fetching tracing-perfetto-binary 1.2.0" in line for line in said), said)

    served[composenames.MAVEN.format(v="1.3.0")] = body
    served[composenames.MAVEN.format(v="1.3.0") + ".sha1"] = b"0" * 40
    with pytest.raises(RunnerError, match="arrived with SHA-1"):
        composenames.library("1.3.0", "arm64-v8a", log=lambda m: None)

    def offline(url, timeout=60):
        raise composenames.urllib.error.URLError("no network")

    monkeypatch.setattr(composenames.urllib.request, "urlopen", offline)
    with pytest.raises(RunnerError, match="could not be fetched .* put the AAR at"):
        composenames.library("1.4.0", "arm64-v8a", log=lambda m: None)


# --- around a set -------------------------------------------------------------

def test_an_app_without_its_library_gets_its_version_pushed_and_put_back(caches) -> None:
    app, said = App(), []
    with names(app, said):
        during = list(app.asked)
    check("asked the version, then the ABI, pushed, switched on, stopped the app",
          during[0].startswith(ENABLE) and "--es path" not in during[0]
          and during[1] == "getprop ro.product.cpu.abi"
          and during[2] == f"mkdir -p {MEDIA}"
          and " push " in during[3] and during[3].endswith(f"{MEDIA}/libtracing_perfetto.so")
          and during[4].startswith(ENABLE)
          and f"--es path {MEDIA}/libtracing_perfetto.so --es persistent true" in during[4]
          and during[5] == f"am force-stop {PACKAGE}", during)
    check("off after the set, and the library gone",
          app.asked[-2].startswith(DISABLE)
          and app.asked[-1] == f"rm -f {MEDIA}/libtracing_perfetto.so", app.asked[-3:])
    check("each step said", any("tracing-perfetto 1.0.1, its library for arm64-v8a pushed"
                                in line for line in said)
          and "  put back: composable names" in said, said)


def test_an_unanswered_switch_off_removes_the_config_itself(caches) -> None:
    """A locked phone's sleeping app answered the disable with `result=0` and
    kept its config, and would have loaded the SDK at every start after."""
    app = App(unanswered=True)
    with names(app):
        pass
    check("the config and the library both removed",
          f"rm -f {MEDIA}/libtracing_perfetto_startup.properties" in app.asked
          and app.asked[-1] == f"rm -f {MEDIA}/libtracing_perfetto.so", app.asked[-4:])


def test_already_on_is_left_on(caches) -> None:
    app = App(first=2)
    with names(app):
        pass
    check("nothing pushed, nothing switched off",
          not any(" push " in q or q.startswith(DISABLE) for q in app.asked), app.asked)


def test_an_app_that_carries_the_library_needs_none_pushed(caches) -> None:
    app = App(first=1)
    with names(app):
        pass
    check("switched on and off, nothing pushed",
          not any(" push " in q for q in app.asked)
          and app.asked[-1].startswith(DISABLE), app.asked)


@pytest.mark.parametrize("first, second, said", [
    (0, 1, "does not carry `androidx.compose.runtime:runtime-tracing`"),
    (11, 13, "refused the library's checksum"),
    (11, 12, "another version than the app's `tracing-perfetto`"),
])
def test_a_refusal_is_a_sentence_and_leaves_nothing_behind(caches, first, second, said) -> None:
    app = App(first=first, second=second)
    with pytest.raises(RunnerError, match=said.replace("`", ".").replace("(", ".").replace(")", ".")):
        with names(app):
            pass
    pushed = any(" push " in q for q in app.asked)
    check("a pushed library is removed again",
          not pushed or app.asked[-1] == f"rm -f {MEDIA}/libtracing_perfetto.so", app.asked)


def test_an_app_that_is_not_there_is_said_so_and_not_called_one_without_tracing(caches) -> None:
    """Nothing answers the broadcast for an app that is not installed either,
    and "it does not carry runtime-tracing" was said of one (#385)."""
    app = App(first=0, installed=False)
    with pytest.raises(RunnerError) as e:
        with names(app):
            pass
    said = str(e.value)
    check("not installed, on that device",
          f"{PACKAGE} is not installed on {SERIAL}" in said
          and "runtime-tracing" not in said, said)
    check("asked the device only after nothing answered",
          app.asked.index(f"pm path {PACKAGE} || true") > 0, app.asked)


# --- in collect ----------------------------------------------------------------

class NamingAdb(FakeAdb):
    def __init__(self, app: App, **kw) -> None:
        super().__init__(**kw)
        self.app = app

    def __call__(self, args, stdin=None, timeout=120) -> str:
        text = shell(args)
        if text.startswith(("am broadcast", "getprop", "mkdir", "rm -f /sdcard")) \
                or " push " in text:
            self.calls.append(list(args))
            self.stdin.append(stdin)
            return self.app(args, stdin, timeout)
        return super().__call__(args, stdin, timeout)


def test_collect_records_names_without_waiting_on_the_sdks_producers(tmp_path, monkeypatch,
                                                                      caches) -> None:
    adb = NamingAdb(App())
    monkeypatch.setattr(runner, "_run", adb)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    slept: list[float] = []
    import time
    monkeypatch.setattr(time, "sleep", slept.append)
    runner.collect(PACKAGE, tmp_path / "traces", 2, section={"compose_names": True},
                   name="startup", log=lambda m: None)
    starts = [c for c in adb.calls if "perfetto" in " ".join(c) and "-c" in c]
    check("the recording asks for Compose's track events",
          all('name: "track_event"' in (adb.stdin[adb.calls.index(c)] or "")
              and 'enabled_categories: "rendering"' in adb.stdin[adb.calls.index(c)]
              for c in starts) and len(starts) == 2, starts)
    check("and detaches at once, the launch two seconds after",
          all("--background" in c and "--background-wait" not in c for c in starts)
          and slept == [runner.TRACK_EVENT_START_S] * 2, (starts, slept))
    check("switched off after the set, the library removed",
          shell(adb.calls[-2]).startswith(DISABLE)
          and shell(adb.calls[-1]) == f"rm -f {MEDIA}/libtracing_perfetto.so",
          [shell(c) for c in adb.calls[-3:]])


def test_without_names_the_recording_is_what_it_was(tmp_path, monkeypatch) -> None:
    adb = FakeAdb()
    monkeypatch.setattr(runner, "_run", adb)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    runner.collect(PACKAGE, tmp_path / "traces", 1, section={}, name="startup",
                   log=lambda m: None)
    start = next(i for i, c in enumerate(adb.calls) if "perfetto" in " ".join(c) and "-c" in c)
    check("--background-wait, and no track events", "--background-wait" in adb.calls[start]
          and "track_event" not in (adb.stdin[start] or ""), adb.calls[start])


# --- what the report and compare make of it -------------------------------------

def test_the_report_merges_and_says_names() -> None:
    merged = report._merge_compose_names([{"asked": True, "calls": 920},
                                          {"asked": True, "calls": 922}, None])
    check("median calls of the runs that had any, and how many runs",
          merged == {"asked": True, "calls": 921, "runs": "2/3"}, merged)
    lines = report._compose_names_lines(merged)
    check("named, with the repeats that lacked them, and the cost said",
          "Composable names: 921 calls, in 2 of 3 repeats." in lines[0]
          and "compare these numbers only with another round recorded with them" in lines[0], lines)
    asked = report._compose_names_lines({"asked": True, "calls": 0, "runs": "0/2"})
    check("asked for and none arrived is a warning",
          asked[0].startswith("> ⚠️ The recording asked for composable names and none arrived"),
          asked)
    check("never asked, nothing said", report._compose_names_lines(None) == [])


def test_compare_warns_when_one_round_had_names() -> None:
    def rep(block):
        return {"environment": {"compose_names": block}}
    on, off = {"asked": True, "calls": 921, "runs": "5/5"}, None
    warned = compare._compose_names_moved(rep(off), rep(on))
    check("off before, on after, said", [w["id"] for w in warned] == ["compose_names"]
          and "were off before and on after" in warned[0]["text"], warned)
    check("alike is quiet", compare._compose_names_moved(rep(on), rep(on)) == []
          and compare._compose_names_moved(rep(off), rep(off)) == [])
    check("a report from before the field is unknown, and quiet",
          compare._compose_names_moved({"environment": {}}, rep(on)) == [])
