"""Trace collection: N repeats of one scenario on a device.

A separate module because this is the only part of echolot that touches the
outside world: adb, the device, launching the app. Everything else is a pure
function of a trace file.

Repeats are not decoration. A single run cannot tell a regression from a random
spike: on a live cold start the spread between iterations reaches tens of
percent. Thresholds are calibrated from repeats and the report is aggregated
over repeats; without them both are guessing.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

# Default atrace categories. sched in ftrace_events is mandatory: without it
# there is no thread_state, and both runnable_starvation and uninstrumented_cpu
# go silent.
DEFAULT_CATEGORIES = [
    "am", "wm", "gfx", "view", "dalvik", "binder_driver", "res", "database",
]

TRACE_CONFIG = """\
buffers: {{ size_kb: {buffer_kb} fill_policy: DISCARD }}
data_sources: {{
  config {{
    name: "linux.ftrace"
    ftrace_config {{
      ftrace_events: "sched/sched_switch"
      ftrace_events: "sched/sched_waking"
      ftrace_events: "sched/sched_process_exit"
      ftrace_events: "sched/sched_process_free"
      ftrace_events: "task/task_newtask"
      ftrace_events: "task/task_rename"
{categories}
      atrace_apps: "{package}"
    }}
  }}
}}
data_sources: {{
  config {{
    name: "linux.process_stats"
    process_stats_config {{ scan_all_processes_on_start: true }}
  }}
}}
duration_ms: {duration_ms}
"""

DEVICE_TRACE = "/data/misc/perfetto-traces/echolot.pftrace"


class RunnerError(Exception):
    pass


def _run(args: list[str], stdin: str | None = None, timeout: int = 120) -> str:
    try:
        done = subprocess.run(args, input=stdin, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        raise RunnerError(
            "adb not found. It ships in the Android SDK platform-tools; "
            "make sure that directory is on PATH."
        ) from None
    except subprocess.TimeoutExpired:
        raise RunnerError(
            f"no answer within {timeout}s from: {' '.join(args)}")
    if done.returncode != 0:
        raise RunnerError(
            f"{' '.join(args)}\n{(done.stderr or done.stdout).strip()}")
    return done.stdout


def devices() -> list[tuple[str, str]]:
    out = _run(["adb", "devices"], timeout=30)
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            found.append((parts[0], parts[1]))
    return found


def pick_device(serial: str | None = None) -> str:
    """Picks a device, and explains what to do when there is nothing to pick."""
    found = devices()
    if serial:
        for dev, state in found:
            if dev == serial:
                if state != "device":
                    raise RunnerError(_state_hint(dev, state))
                return dev
        raise RunnerError(
            f"device {serial} is not among the connected ones: "
            f"{', '.join(d for d, _ in found) or 'none'}")

    ready = [d for d, state in found if state == "device"]
    if len(ready) == 1:
        return ready[0]
    if not ready:
        if not found:
            raise RunnerError(
                "adb sees no devices at all. Plug in a phone or start an "
                "emulator.")
        # The most common case with a real phone — and the fix is not ours.
        raise RunnerError("; ".join(_state_hint(d, s) for d, s in found))
    raise RunnerError(
        f"several devices connected: {', '.join(ready)}. "
        f"Pick one with --device <serial>.")


def _state_hint(serial: str, state: str) -> str:
    if state == "unauthorized":
        return (f"{serial}: debugging not authorised. The device screen should "
                f"be showing an 'Allow USB debugging?' prompt — accept it and "
                f"retry")
    if state == "offline":
        return (f"{serial}: device is offline. Usually cured by replugging the "
                f"cable or running `adb kill-server`")
    return f"{serial}: state is {state}, expected device"


def resolve_activity(device: str, package: str) -> str:
    out = _run(["adb", "-s", device, "shell", "cmd", "package",
                "resolve-activity", "--brief", package], timeout=60)
    activity = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if "/" not in activity:
        raise RunnerError(
            f"could not determine the launcher activity for {package}. "
            f"Is the app installed? Otherwise set runner.activity explicitly.")
    return activity


def trace_config(package: str, duration_ms: int, categories: list[str],
                 buffer_kb: int) -> str:
    lines = "\n".join(f'      atrace_categories: "{c}"' for c in categories)
    return TRACE_CONFIG.format(package=package, duration_ms=duration_ms,
                               categories=lines, buffer_kb=buffer_kb)


def run_command(command: str, timeout: int) -> float:
    """Lets something else drive the scenario while we record the trace.

    The command comes from the project's own config — the same level of trust
    as the gradle task next to it. Anything that can move the app goes here:
    adb input, uiautomator, maestro, your own script.
    """
    import time
    started = time.monotonic()
    done = subprocess.run(command, shell=True, capture_output=True,
                          text=True, timeout=timeout)
    if done.returncode != 0:
        raise RunnerError(
            f"the scenario command returned {done.returncode}:\n"
            f"{(done.stderr or done.stdout).strip()[:800]}")
    return time.monotonic() - started


def harvest(search_root: Path, since: float, out_dir: Path,
            name: str) -> list[dict]:
    """Collects the traces a macrobenchmark wrote by itself.

    It drops them per iteration into an artifact directory whose path depends
    on the build variant and the device model — awkward to find by hand. We
    take everything that appeared after the run started.
    """
    found = []
    for pattern in ("*.perfetto-trace", "*.pftrace"):
        for path in search_root.rglob(pattern):
            try:
                if path.stat().st_mtime >= since:
                    found.append(path)
            except OSError:
                continue
    found.sort(key=lambda p: p.stat().st_mtime)

    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, src in enumerate(found):
        dst = out_dir / f"{name}_iter{i:03d}.perfetto-trace"
        dst.write_bytes(src.read_bytes())
        results.append({"path": dst, "size": dst.stat().st_size,
                        "source": src})
    return results


def collect(package: str, out_dir: Path, iterations: int,
            section: dict | None = None,
            device: str | None = None,
            name: str = "run",
            log: Callable[[str], None] = print) -> list[dict]:
    """N repeats of a scenario. The mode decides who drives it.

    launch  — we do: force-stop and launch the activity. Cold start.
    command — someone else's command; we record the trace around it.
    gradle  — the macrobenchmark writes traces itself, we only collect them.
    """
    import time

    section = section or {}
    mode = str(section.get("mode", "launch"))
    duration_ms = int(section.get("duration_ms", 12000))
    reset = str(section.get("reset_policy", "force-stop"))

    if mode == "gradle":
        task = section.get("gradle_task")
        if not task:
            raise RunnerError("runner.mode: gradle needs runner.gradle_task")
        root = Path(section.get("project_root", "."))
        command = " ".join([str(section.get("gradle", "./gradlew")), str(task),
                            *(section.get("gradle_args") or [])])
        log(f"gradle: {command}")
        since = time.time()
        spent = run_command(command, timeout=int(section.get("timeout_s", 3600)))
        results = harvest(root, since, out_dir, name)
        if not results:
            raise RunnerError(
                "gradle finished but no new traces appeared. Check that the "
                "task really is a macrobenchmark and that it actually ran.")
        log(f"  traces collected: {len(results)} in {spent:.0f}s")
        for r in results:
            log(f"    {r['path'].name}  {r['size'] / 1e6:.1f} MB")
        return results

    dev = pick_device(device)
    config = trace_config(package, duration_ms,
                          section.get("atrace_categories") or DEFAULT_CATEGORIES,
                          int(section.get("buffer_kb", 131072)))

    activity = None
    command = section.get("command")
    if mode == "launch":
        activity = section.get("activity") or resolve_activity(dev, package)
        log(f"device {dev}, activity {activity}")
    elif mode == "command":
        if not command:
            raise RunnerError("runner.mode: command needs runner.command")
        log(f"device {dev}, scenario: {command}")
    else:
        raise RunnerError(
            f"unknown runner.mode: {mode}. Available: launch, command, gradle.")

    results = []
    for i in range(iterations):
        out = out_dir / f"{name}_iter{i:03d}.perfetto-trace"
        if reset == "force-stop":
            _run(["adb", "-s", dev, "shell", "am", "force-stop", package])
        _run(["adb", "-s", dev, "shell", "rm", "-f", DEVICE_TRACE])
        _run(["adb", "-s", dev, "shell", "perfetto", "-c", "-", "--txt",
              "-o", DEVICE_TRACE, "--background-wait"],
             stdin=config, timeout=120)

        info: dict = {}
        if mode == "launch":
            info = _launch(dev, activity)
        else:
            spent = run_command(command, timeout=duration_ms // 1000 + 300)
            if spent * 1000 > duration_ms:
                log(f"  [!] the scenario ran {spent:.0f}s against a "
                    f"{duration_ms / 1000:.0f}s recording window — the trace "
                    f"covers only its beginning")

        _run(["adb", "-s", dev, "shell",
              "while pidof perfetto > /dev/null; do sleep 0.5; done"],
             timeout=duration_ms // 1000 + 300)
        info.update(_pull(dev, out))
        results.append(info)

        extra = ""
        if info.get("total_time_ms") is not None:
            extra = f", am start -W: {info['total_time_ms']} ms"
        if info.get("launch_state"):
            extra += f" ({info['launch_state']})"
        log(f"  [{i + 1}/{iterations}] {out.name}  "
            f"{info['size'] / 1e6:.1f} MB{extra}")
    return results


def _launch(device: str, activity: str) -> dict:
    started = _run(["adb", "-s", device, "shell", "am", "start", "-W",
                    "-n", activity], timeout=120)
    info: dict = {}
    for key, field in (("TotalTime", "total_time_ms"),
                       ("LaunchState", "launch_state")):
        m = re.search(rf"^{key}:\s*(.+)$", started, re.MULTILINE)
        if m:
            value = m.group(1).strip()
            info[field] = int(value) if value.isdigit() else value
    return info


def _pull(device: str, out_path: Path) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run(["adb", "-s", device, "pull", DEVICE_TRACE, str(out_path)],
         timeout=300)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RunnerError(
            f"the trace did not arrive or is empty: {out_path}. "
            f"Check that perfetto is permitted on the device.")
    return {"path": out_path, "size": out_path.stat().st_size}
