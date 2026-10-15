"""A Compose app's composable names, switched on for a set of cold starts.

`androidx.compose.runtime:runtime-tracing` names every composable call in a
trace, and the `composables` detector reads those names. They reach a trace
only through the app's Perfetto SDK, which is off until something switches
it on. A macrobenchmark does it with `perfettoSdkTracing.enable=true`;
`runner.compose_names` does it over adb, the way #363's spike found works on
an SM-A515F (Android 13):

1. ask the app which version of `tracing-perfetto` it carries — the enable
   broadcast without a library answers `result=11` with `requiredVersion`;
2. take that version's `tracing-perfetto-binary` for the device's ABI, from
   Gradle's cache or Google's Maven, and push it into the app's own media
   directory, which the app may read and adb may write;
3. switch cold-start tracing on with the library's path, `persistent` so that
   it holds across the `force-stop` before every repeat.

After the set it is switched off again and the library removed, as
`runner.cooldown`'s keys put the device back. It costs the start: on that
device the app's `StartupTracingInitializer` took 54–87 ms to load the
library, and the start 67 ms more on the median. So a round with names
compares only with another round with names, and the report says which it
was (`environment.compose_names`).

Every adb call goes through the `run` the runner hands in.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .runner import RunnerError, installed

RECEIVER = "androidx.tracing.perfetto.TracingReceiver"
ENABLE = "androidx.tracing.perfetto.action.ENABLE_TRACING_COLD_START"
DISABLE = "androidx.tracing.perfetto.action.DISABLE_TRACING_COLD_START"
LIBRARY = "libtracing_perfetto.so"
# What the app reads at its start to switch its SDK on, beside the library.
CONFIG = "libtracing_perfetto_startup.properties"
MAVEN = ("https://dl.google.com/android/maven2/androidx/tracing/tracing-perfetto-binary/"
         "{v}/tracing-perfetto-binary-{v}.aar")

# `am broadcast` prints the receiver's answer as one line; the json inside
# `data="…"` keeps its own escaped quotes, and `extras` may follow it.
_ANSWER = re.compile(r'^Broadcast completed: result=(?P<code>-?\d+)'
                     r'(?:, data="(?P<data>.*?)")?(?:, extras: .*)?\s*$', re.MULTILINE)


def wanted(section: dict[str, Any]) -> bool:
    """`runner.compose_names`, refused in a sentence when it cannot be meant."""
    value = section.get("compose_names")
    if value is None or value is False:
        return False
    if value is not True:
        raise RunnerError(f"runner.compose_names: {value!r} is not true or false")
    return True


def answer(text: str) -> tuple[int, dict[str, Any]]:
    """The receiver's answer: its result code, and the json it sent with it."""
    m = _ANSWER.search(text)
    if not m:
        raise RunnerError(f"the app's tracing receiver gave no answer: {text.strip()[:200]}")
    data: dict[str, Any] = {}
    if m.group("data"):
        try:
            data = json.loads(m.group("data"))
        except json.JSONDecodeError:
            data = {"message": m.group("data")}
    return int(m.group("code")), data


def _gradle_cache(version: str) -> Path | None:
    """The AAR from Gradle's own cache, where a project that built once has it."""
    home = Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")
    base = home / "caches/modules-2/files-2.1/androidx.tracing/tracing-perfetto-binary" / version
    found = sorted(base.glob(f"*/tracing-perfetto-binary-{version}.aar"))
    return found[0] if found else None


def _cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "echolot"


def _download(version: str, dest: Path) -> None:
    """The AAR from Google's Maven, checked against the SHA-1 published beside it."""
    url = MAVEN.format(v=version)
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            body = resp.read()
        with urllib.request.urlopen(url + ".sha1", timeout=60) as resp:
            want = resp.read().decode().split()[0].strip().lower()
    except (urllib.error.URLError, OSError) as e:
        raise RunnerError(
            f"runner.compose_names: tracing-perfetto-binary {version} could not be "
            f"fetched from {url} ({getattr(e, 'reason', e)}). A project that built "
            f"once has it in Gradle's cache; or put the AAR at {dest}") from None
    got = hashlib.sha1(body).hexdigest()
    if got != want:
        raise RunnerError(
            f"runner.compose_names: {url} arrived with SHA-1 {got}, and Maven says {want}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.replace(dest)


def library(version: str, abi: str, log: Callable[[str], None] = print) -> Path:
    """The app's version of the library, for the device's ABI, as a local file.

    The app checks the library against SHA-256 sums of its own version, so
    nothing but that version's binary will do, and the app says which version
    that is. Gradle's cache first, then echolot's own, then Google's Maven.
    """
    aar = _gradle_cache(version)
    if aar is None:
        aar = _cache_dir() / f"tracing-perfetto-binary-{version}.aar"
        if not aar.exists():
            log(f"  composable names: fetching tracing-perfetto-binary {version}")
            _download(version, aar)
    member = f"jni/{abi}/{LIBRARY}"
    out = _cache_dir() / f"tracing-perfetto-{version}-{abi}" / LIBRARY
    if not out.exists():
        try:
            with zipfile.ZipFile(aar) as z:
                data = z.read(member)
        except KeyError:
            raise RunnerError(
                f"runner.compose_names: tracing-perfetto-binary {version} has no "
                f"library for {abi}") from None
        except zipfile.BadZipFile:
            raise RunnerError(f"runner.compose_names: {aar} is not an AAR") from None
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=out.parent, delete=False) as f:
            f.write(data)
        Path(f.name).replace(out)
    return out


# What a code means, for the sentence `collect` stops with.
_REFUSED = {
    0: "nothing answered: the app has no `androidx.tracing.perfetto` receiver, so it "
       "does not carry `androidx.compose.runtime:runtime-tracing`",
    12: "the library is another version than the app's `tracing-perfetto`",
    13: "the app refused the library's checksum",
}


class Names:
    """Composable names switched on for one set, and off again after it."""

    def __init__(self, package: str, device: str, *, run: Callable[..., str],
                 log: Callable[[str], None] = print) -> None:
        self.package = package
        self.device = device
        self.run = run
        self.log = log
        self.media = f"/sdcard/Android/media/{package}"
        self._enabled = False
        self._pushed = False

    def _adb(self, *args: str) -> str:
        return self.run(["adb", "-s", self.device, *args])

    def _broadcast(self, action: str, *extras: str) -> tuple[int, dict[str, Any]]:
        out = self._adb("shell", "am", "broadcast", "-a", action,
                        "-n", f"{self.package}/{RECEIVER}", *extras)
        return answer(out)

    def _refused(self, code: int, data: dict[str, Any]) -> RunnerError:
        why = _REFUSED.get(code) or data.get("message") or f"result {code}"
        version = f" (the app's tracing-perfetto is {data['requiredVersion']})" \
            if data.get("requiredVersion") else ""
        return RunnerError(f"runner.compose_names: {self.package} did not switch its "
                           f"Perfetto SDK on: {why}{version}")

    def __enter__(self) -> Names:
        try:
            self._switch_on()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _switch_on(self) -> None:
        code, data = self._broadcast(ENABLE, "--es", "persistent", "true")
        if code == 2:
            self.log("  composable names: the app's Perfetto SDK was on already, and is "
                     "left as it is")
            return
        if code == 11:
            version = str(data.get("requiredVersion") or "")
            if not version:
                raise self._refused(code, data)
            abi = self._adb("shell", "getprop", "ro.product.cpu.abi").strip()
            local = library(version, abi, self.log)
            on_device = f"{self.media}/{LIBRARY}"
            self._adb("shell", "mkdir", "-p", self.media)
            self._adb("push", str(local), on_device)
            self._pushed = True
            code, data = self._broadcast(ENABLE, "--es", "path", on_device,
                                         "--es", "persistent", "true")
            if code not in (1, 2):
                raise self._refused(code, data)
            self._enabled = code == 1
            self.log(f"  composable names: tracing-perfetto {version}, its library for "
                     f"{abi} pushed, the Perfetto SDK on for every cold start of the set")
        elif code == 1:
            self._enabled = True
            self.log("  composable names: the Perfetto SDK on for every cold start of the "
                     "set, with the library the app carries")
        else:
            # Nothing answers for an app that is not there either, and the
            # sentence about its missing receiver was said of one (#385).
            if code == 0 and not installed(self.device, self.package, self.run):
                raise RunnerError(
                    f"runner.compose_names: {self.package} is not installed on "
                    f"{self.device}, so nothing answered. Install the build you "
                    f"mean to measure, and collect again.")
            raise self._refused(code, data)
        # The broadcast started the app to deliver it; the first repeat starts
        # it again, cold, with the SDK loading as it starts.
        self._adb("shell", "am", "force-stop", self.package)

    def __exit__(self, *exc) -> bool:
        if self._enabled:
            try:
                code, _ = self._broadcast(DISABLE)
                if code != 1:
                    # Unanswered, as an app asleep on a locked phone left it:
                    # `result=0` and the config still there. The config is
                    # what the app reads at its start, so removing it is the
                    # same as switching the SDK off.
                    self._adb("shell", "rm", "-f", f"{self.media}/{CONFIG}")
                self.log("  put back: composable names")
            except RunnerError as e:
                self.log(f"  [!] could not put back composable names: {e.gist or e}")
            self._enabled = False
        if self._pushed:
            try:
                self._adb("shell", "rm", "-f", f"{self.media}/{LIBRARY}")
            except RunnerError as e:
                self.log(f"  [!] could not remove the pushed library: {e.gist or e}")
            self._pushed = False
        return False
