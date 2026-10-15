"""The build on the phone held against the markers in the code (#393).

In launch and command modes `collect` records the build installed on the
phone. A hunt ran `mark --apply` and recorded five starts with no build in
between (#390), and `analyze` says so since #391, after the round. Now
`collect` reads the build before it records: every name with the prefix in
the app's code, read on the phone, against the names on the code's tagged
lines. A build that lacks any of them stops the set before anything is
recorded, or, in command mode, whose scenario may install the app itself,
after the first repeat. A phone that cannot say records as before.

And `analyze` says it the other way round: names the traces hold that no
string in the code makes any more, from a build made before `mark --remove`.

The phone's half was checked on an A51, Android 13 (toybox 0.8.6, ziptool's
unzip): a test app with two markers in it, and a large Kotlin app's benchmark
build, 30 MB of code in three files read in about 2 s.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import mark, recorder, runner  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import _markers_against_code, main  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_build_before_record import BENCHMARK, PAIR, _app, _report  # noqa: E402
from tests.test_collect_fake_adb import ACTIVITY, PACKAGE, SERIAL, FakeAdb, old_set  # noqa: E402
from tests.test_mark_edits import write  # noqa: E402

INSTALL = "build and install the app with `./gradlew :app:installBenchmark`"
SOURCE = "app/src/main/kotlin/App.kt"


def answer(*names: str, prefix: str = "AGENTTMP_", apks: int = 1, files: int = 3,
           tested: bool = True, status: str | None = None) -> str:
    """What the phone prints for the build read: grep's own test, a line per APK, the names, grep's status."""
    lines = [f"echolot-selftest {prefix}echolot"] if tested else []
    lines += [f"echolot-apk  29675248                     {files} files"] * apks
    lines += list(names)
    lines.append(f"echolot-grep {status if status is not None else ('0' if names else '1')}")
    return "\n".join(lines) + "\n"


def planted(*names: str) -> runner.Planted:
    return runner.Planted("AGENTTMP_", {n: [SOURCE] for n in names}, INSTALL)


@pytest.fixture
def adb(monkeypatch) -> FakeAdb:
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    return fake


def _collect(tmp_path: Path, markers: runner.Planted, section: dict | None = None,
             n: int = 3) -> list[str]:
    said: list[str] = []
    runner.collect(PACKAGE, tmp_path / "traces", n, section=section or {"activity": ACTIVITY},
                   name="startup", log=said.append, planted=markers)
    return said


# --- what the phone says -----------------------------------------------------------

def test_the_phone_s_answer_is_the_names_or_why_not() -> None:
    read = runner._build_names_read
    check("the names, every other line aside",
          read(answer("AGENTTMP_a", "AGENTTMP_b"), "AGENTTMP_")
          == ({"AGENTTMP_a", "AGENTTMP_b"}, ""), read(answer("AGENTTMP_a"), "AGENTTMP_"))
    check("none, from code that has none", read(answer(), "AGENTTMP_") == (set(), ""))
    check("lines ending in CR LF, as adb's older shell sends them",
          read(answer("AGENTTMP_a").replace("\n", "\r\n"), "AGENTTMP_") == ({"AGENTTMP_a"}, ""))
    for out, why in ((answer(apks=0), "`pm path` lists no APK"),
                     (answer(files=0), "`unzip` on the phone lists no code"),
                     (answer(tested=False), "`grep` on the phone does not read"),
                     (answer("AGENTTMP_a", status="2"), "`grep` on the phone does not read"),
                     (answer().replace("AGENTTMP_echolot", "\x11AGENTTMP_echolot"),
                      "`grep` on the phone does not read")):
        names, said = read(out, "AGENTTMP_")
        check(f"cannot tell: {why}", names is None and said.startswith(why), (names, said))


def test_the_script_is_given_the_package_and_the_prefix_quoted() -> None:
    asked: list[list[str]] = []

    def phone(args, stdin=None, timeout=120):
        asked.append(args)
        return answer("AGENT.TMP_x", prefix="AGENT.TMP_")

    names, _ = runner.build_names(SERIAL, "com.example.app:remote", "AGENT.TMP_", run=phone)
    script = asked[0][-1]
    check("asked on the device", asked[0][:4] == ["adb", "-s", SERIAL, "shell"], asked[0])
    check("the package without the process's suffix", "pm path com.example.app 2>" in script,
          script)
    check("the prefix as itself in the pattern, and in the test",
          "' AGENT.TMP_ | grep -a -z -o -E 'AGENT\\.TMP_.*' |" in script, script)
    check("and the answer read with it", names == {"AGENT.TMP_x"}, names)

    def silent(args, stdin=None, timeout=120):
        raise runner.RunnerError("no answer within 180s from: adb shell")

    check("a phone that does not answer cannot tell",
          runner.build_names(SERIAL, PACKAGE, "AGENTTMP_", run=silent)
          == (None, "the phone did not answer: no answer within 180s from: adb shell"))

    def garbled(args, stdin=None, timeout=120):
        raise UnicodeDecodeError("utf-8", b"\xed", 0, 1, "invalid continuation byte")

    check("nor one whose answer is not text",
          runner.build_names(SERIAL, PACKAGE, "AGENTTMP_", run=garbled)[0] is None)


# --- launch mode: before anything is recorded ----------------------------------------

def test_a_build_without_the_markers_stops_launch_mode_before_anything_is_recorded(
        tmp_path: Path, adb: FakeAdb) -> None:
    """#390: five cold starts of a build made before `mark --apply`."""
    out = tmp_path / "traces"
    before = old_set(out)
    adb.script["build"] = [answer()]
    with pytest.raises(runner.RunnerError) as e:
        _collect(tmp_path, planted("AGENTTMP_a", "AGENTTMP_b", "AGENTTMP_c"))
    said = str(e.value)
    check("said with the device, the count, and where each one is", said.startswith(
        f"the build of {PACKAGE} on {SERIAL} has none of the 3 temporary markers in the code "
        f"(AGENTTMP_a in {SOURCE}; AGENTTMP_b in {SOURCE}; AGENTTMP_c in {SOURCE}), so no "
        f"trace of it would hold them. Build and install the app with `./gradlew "
        f":app:installBenchmark`, then collect again."), said)
    check("and what a fresh build without one means",
          "If a build made from this code still lacks one, the file it is in is not part of "
          "that build" in said and "`echolot mark --remove` takes out every marker" in said, said)
    kinds = adb.kinds()
    check("read after the app is found and before the screen; nothing recorded",
          kinds == ["devices", "installed", "build"], kinds)
    check("the set from before is where it was", sorted(p.name for p in out.iterdir()) == before)


def test_the_ones_a_build_lacks_are_named(tmp_path: Path, adb: FakeAdb) -> None:
    """New markers in a later round, and a build that has only the older ones."""
    adb.script["build"] = [answer("AGENTTMP_a", "AGENTTMP_old")]
    with pytest.raises(runner.RunnerError) as e:
        _collect(tmp_path, planted("AGENTTMP_a", "AGENTTMP_b", "AGENTTMP_c"))
    check("two of three", f"lacks 2 of the 3 temporary markers in the code (AGENTTMP_b in "
          f"{SOURCE}; AGENTTMP_c in {SOURCE}), so no trace of it would hold them" in str(e.value),
          str(e.value))
    adb.script["build"] = [answer("AGENTTMP_a")]
    with pytest.raises(runner.RunnerError) as e:
        _collect(tmp_path, planted("AGENTTMP_b"))
    check("the only one", f"lacks the temporary marker in the code, AGENTTMP_b in {SOURCE}, so "
          f"no trace of it would hold it." in str(e.value) and "still lacks it," in str(e.value),
          str(e.value))


def test_a_build_with_every_marker_is_recorded_and_said(tmp_path: Path, adb: FakeAdb) -> None:
    adb.script["build"] = [answer("AGENTTMP_a", "AGENTTMP_b", "AGENTTMP_other")]
    said = _collect(tmp_path, planted("AGENTTMP_a", "AGENTTMP_b"))
    kinds = adb.kinds()
    check("read once, before the screen and the set",
          kinds[:4] == ["devices", "installed", "build", "screen"]
          and kinds.count("build") == 1 and kinds.count("pull") == 3, kinds)
    check("and said", "  the build on the phone has all 2 temporary markers in the code" in said,
          said)


def test_a_name_is_the_whole_name_or_the_start_of_one_put_together(
        tmp_path: Path, adb: FakeAdb) -> None:
    adb.script["build"] = [answer("AGENTTMP_loadFeed")]
    with pytest.raises(runner.RunnerError):
        _collect(tmp_path, planted("AGENTTMP_load"))
    adb.script["build"] = [answer("AGENTTMP_load_")]
    said = _collect(tmp_path, planted("AGENTTMP_load_$id"), n=1)
    check("a template is in the build by its fixed start",
          "  the build on the phone has the temporary marker in the code" in said, said)


def test_a_phone_that_cannot_say_records_as_before(tmp_path: Path, adb: FakeAdb) -> None:
    adb.script["build"] = [answer("AGENTTMP_a", tested=False)]
    said = _collect(tmp_path, planted("AGENTTMP_a", "AGENTTMP_b"))
    check("all three repeats", adb.kinds().count("pull") == 3, adb.kinds())
    check("with a line that says it could not tell, and who says it later", any(
        line.startswith("  [!] whether the build on the phone has the 2 temporary markers in "
                        "the code could not be read: `grep` on the phone does not read")
        and line.endswith("`analyze` says so if the traces lack all of them")
        for line in said), said)


# --- command mode: the scenario may install the app itself -----------------------------

COMMAND = {"mode": "command", "command": "true", "reset_policy": "none"}


def test_command_mode_reads_the_build_again_after_the_first_repeat(
        tmp_path: Path, adb: FakeAdb) -> None:
    adb.script["build"] = [answer(), answer("AGENTTMP_a")]
    said = _collect(tmp_path, planted("AGENTTMP_a"), section=COMMAND)
    kinds = adb.kinds()
    check("before the set, and once the first repeat has recorded, before its pull",
          kinds[:3] == ["devices", "build", "screen"]
          and kinds[3:9] == ["screen", "rm", "perfetto", "wait", "build", "pull"]
          and kinds.count("build") == 2 and kinds.count("pull") == 3, kinds)
    check("said both times", "  [!] the build on the phone lacks the temporary marker in the "
          "code; the scenario may install another, so it is read again after the first repeat"
          in said and "  the build on the phone has the temporary marker in the code" in said,
          said)


def test_command_mode_stops_at_the_first_repeat_when_the_build_still_lacks_them(
        tmp_path: Path, adb: FakeAdb) -> None:
    out = tmp_path / "traces"
    before = old_set(out)
    adb.script["build"] = [answer("AGENTTMP_a")]
    with pytest.raises(runner.RunnerError) as e:
        _collect(tmp_path, planted("AGENTTMP_a", "AGENTTMP_b"), section=COMMAND)
    check("at the first repeat", str(e.value).startswith(
        f"repeat 1 of 3: the build of {PACKAGE} on {SERIAL} lacks 1 of the 2 temporary markers"),
        str(e.value))
    kinds = adb.kinds()
    check("its recording removed and never pulled", kinds[-2:] == ["build", "rm"]
          and "pull" not in kinds, kinds)
    check("and the set from before is where it was",
          sorted(p.name for p in out.iterdir()) == before, list(out.iterdir()))


def test_command_mode_with_the_markers_in_the_build_reads_it_once(
        tmp_path: Path, adb: FakeAdb) -> None:
    adb.script["build"] = [answer("AGENTTMP_a")]
    _collect(tmp_path, planted("AGENTTMP_a"), section=COMMAND)
    check("once", adb.kinds().count("build") == 1, adb.kinds())


def test_gradle_mode_is_not_asked(tmp_path: Path, monkeypatch) -> None:
    """The macrobenchmark builds the app from the code."""
    def refuse(args, stdin=None, timeout=120):
        raise AssertionError(f"gradle mode called adb: {args}")
    monkeypatch.setattr(runner, "_run", refuse)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    app = tmp_path / "app"
    app.mkdir()
    (app / "gradlew").write_text("#!/bin/sh\nd=build/outputs/x\nmkdir -p $d\n"
                                 "printf t > $d/S_iter000_2026.perfetto-trace\n", encoding="utf-8")
    (app / "gradlew").chmod(0o755)
    got = runner.collect(PACKAGE, tmp_path / "traces", 1, name="startup", log=lambda m: None,
                         section={"mode": "gradle", "gradle_task": "x",
                                  "project_root": str(app)},
                         planted=planted("AGENTTMP_a"))
    check("recorded, and adb never asked", len(got) == 1, got)


# --- from the command line -----------------------------------------------------------

def _project(root: Path, *names: str) -> None:
    _app(root, BENCHMARK)
    write(root, "echolot.yml", f"project:\n  package: {PACKAGE}\nscenario:\n  name: startup\n"
                               f"runner:\n  iterations: 1\n  activity: {ACTIVITY}\n")
    write(root, SOURCE, "class App {\n" + "".join(PAIR.format(n) for n in names) + "}\n")


def _cli(root: Path, monkeypatch, fake: FakeAdb) -> tuple[int, str]:
    monkeypatch.setattr(runner, "_run", fake)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    monkeypatch.chdir(root)
    err = io.StringIO()
    with recorder.isolated(), contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        code = main(["collect"])
    return code, err.getvalue()


def test_collect_holds_the_phone_against_the_code_and_names_the_task(
        tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path, "AGENTTMP_a")
    fake = FakeAdb(script={"build": [answer()]})
    code, err = _cli(tmp_path, monkeypatch, fake)
    check("exit 2, with the marker, its file and the task that installs the variant",
          code == 2 and f"collection error: the build of {PACKAGE} on {SERIAL} lacks the "
          f"temporary marker in the code, AGENTTMP_a in {SOURCE}, so no trace of it would hold "
          "it. Build and install the app with `./gradlew :app:installRelease`, then collect "
          "again." in err, err[-1500:])
    check("asked with the prefix the config names", "AGENTTMP_" in fake.calls[2][-1])


def test_collect_without_markers_in_the_code_does_not_read_the_build(
        tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path)
    fake = FakeAdb()
    code, err = _cli(tmp_path, monkeypatch, fake)
    check("recorded, and never read", code == 0 and "build" not in fake.kinds(),
          (fake.kinds(), err[-800:]))


# --- analyze: names the traces hold and the code no longer has ---------------------

def _code(root: Path, text: str, mode: str = "launch") -> Config:
    _app(root, BENCHMARK)
    write(root, "echolot.yml", "project:\n  package: com.example.app.benchmark\n"
                               f"runner:\n  mode: {mode}\n  gradle_task: x\n")
    write(root, SOURCE, text)
    return Config.load(root / "echolot.yml")


def test_names_the_traces_hold_and_no_code_makes_are_put_in_the_report(tmp_path: Path) -> None:
    """A round from the build made before `mark --remove`."""
    cfg = _code(tmp_path, 'class App { val kept = "AGENTTMP_kept" }\n')
    rep = _report(["AGENTTMP_gone", "AGENTTMP_kept"])
    _markers_against_code(rep, cfg)
    check("the one no code makes, a marker without the tag kept",
          rep["markers"].get("removed") == {"names": ["AGENTTMP_gone"], "mode": "launch",
                                            "install": [":app:installBenchmark"]},
          rep["markers"])
    text = report_mod.to_markdown(rep)
    check("said at the top of the report", "> ⚠️ The temporary marker `AGENTTMP_gone` is in "
          "these traces and nowhere in the code: the app on the phone was built before it was "
          "taken out, and has none of the changes made to the code since. Build and install "
          "the app with `./gradlew :app:installBenchmark`, then record again." in text
          and text.index("nowhere in the code") < text.index("Detectors fired"), text)
    check("and nothing about markers the traces lack: the code has none",
          "unbuilt" not in rep["markers"], rep["markers"])


def test_names_the_code_puts_together_are_not_said(tmp_path: Path) -> None:
    traces = ["AGENTTMP_load_42", "AGENTTMP_feed_7", "AGENTTMP_sum_x", "AGENTTMP_odd"]
    cfg = _code(tmp_path, "class App {\n"
                '  fun a(id: Int) = trace("AGENTTMP_load_" + id) { }\n'
                '  fun b(id: Int) = trace("AGENTTMP_feed_$id") { }\n'
                '  fun c(x: String) = String.format("AGENTTMP_sum_%s", x)\n'
                "}\n")
    rep = _report(traces)
    _markers_against_code(rep, cfg)
    check("only the one nothing makes", rep["markers"]["removed"]["names"] == ["AGENTTMP_odd"],
          rep["markers"])
    write(tmp_path, SOURCE, 'class App { fun d(x: String) = "AGENTTMP_" + x }\n')
    rep = _report(traces)
    _markers_against_code(rep, cfg)
    check("and the prefix on its own makes them all", "removed" not in rep["markers"],
          rep["markers"])


def test_a_config_kept_away_from_the_code_says_nothing(tmp_path: Path) -> None:
    write(tmp_path, "echolot.yml", "project:\n  package: com.example.app\n")
    rep = _report(["AGENTTMP_a"])
    _markers_against_code(rep, Config.load(tmp_path / "echolot.yml"))
    check("no Kotlin or Java, nothing to hold the traces against",
          "removed" not in rep["markers"], rep["markers"])


def test_in_gradle_mode_the_traces_are_older_than_the_code(tmp_path: Path) -> None:
    cfg = _code(tmp_path, "class App\n", mode="gradle")
    rep = _report(["AGENTTMP_a", "AGENTTMP_b"])
    _markers_against_code(rep, cfg)
    check("no task, and record again", rep["markers"]["removed"]["install"] == []
          and "> ⚠️ 2 temporary markers are in these traces and nowhere in the code "
          "(`AGENTTMP_a`, `AGENTTMP_b`): the traces were recorded before they were taken out. "
          "In gradle mode `echolot collect` builds the app from the code as it is now, so "
          "record again." in report_mod.to_markdown(rep), report_mod.to_markdown(rep))


def test_the_views_say_it_too() -> None:
    rep = _report(["AGENTTMP_" + c for c in "abcdefg"])
    rep["markers"]["removed"] = {"names": rep["markers"]["in_trace"], "mode": "command",
                                 "install": []}
    view = report_mod.markers_view(rep)
    check("`report --markers`, five named and the rest counted",
          "7 temporary markers are in these traces and nowhere in the code (`AGENTTMP_a`, "
          "`AGENTTMP_b`, `AGENTTMP_c`, `AGENTTMP_d`, `AGENTTMP_e` and 2 more)" in view
          and "Build and install the app, then record again." in view, view)
    check("`report`, in its header", "nowhere in the code" in report_mod.overview(rep))
    check("and `report --json`", report_mod.select(rep, [], None, False, False)["markers"][
        "removed"]["names"][:1] == ["AGENTTMP_a"])


def test_a_marker_the_code_puts_together_is_in_the_traces_by_its_start(tmp_path: Path) -> None:
    """The other way round, #390's: a tagged template is not a name a trace holds whole."""
    cfg = _code(tmp_path, "class App {\n"
                '    android.os.Trace.beginSection("AGENTTMP_load_$id") // echolot:mark\n'
                "    android.os.Trace.endSection() // echolot:mark\n}\n")
    rep = _report(["AGENTTMP_load_7"])
    _markers_against_code(rep, cfg)
    check("neither way", "unbuilt" not in rep["markers"] and "removed" not in rep["markers"],
          rep["markers"])


# --- the code's names --------------------------------------------------------------

def test_the_code_s_names_whole_and_put_together(tmp_path: Path) -> None:
    write(tmp_path, SOURCE, "class App {\n"
          + PAIR.format("AGENTTMP_tagged")
          + '  val plain = "AGENTTMP_plain"\n'
          '  val joined = "AGENTTMP_joined_" +\n    id\n'
          '  val template = "AGENTTMP_t_${id}"\n'
          '  val other = "PERF_x"\n'
          "}\n")
    write(tmp_path, "app/src/main/java/B.java", 'class B { String s = "AGENTTMP_" + x; }\n')
    got = mark.named(tmp_path)
    check("the tagged ones, as `planted` has them",
          got.planted == {"AGENTTMP_tagged": [SOURCE]} == mark.planted(tmp_path), got.planted)
    check("every whole one, tagged or not", got.exact == {"AGENTTMP_tagged", "AGENTTMP_plain"},
          got.exact)
    check("the fixed starts of the ones put together",
          got.heads == {"AGENTTMP_joined_", "AGENTTMP_t_", "AGENTTMP_"}, got.heads)
    check("and how many files there were", got.files == 2, got.files)
    check("a head makes what starts with it, a whole name only itself",
          got.makes("AGENTTMP_joined_3") and got.makes("AGENTTMP_plain")
          and not mark.named(tmp_path / "app/src/main/kotlin").makes("AGENTTMP_plainer"))
