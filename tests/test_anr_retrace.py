#!/usr/bin/env python3
"""An ANR record of a minified build, named back with the build's mapping.

The device keeps a minified build's frames as R8 wrote them:
`a.b.run(SourceFile:3)` is in no package the checkout declares and in no file
of it, so without the mapping the report says every frame belongs to the
platform or a library. The record and the mappings here came off a phone and
out of R8 9.0 for a test app (lockapp.py).
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anr  # noqa: E402
from echolot.main import main as cli  # noqa: E402
from tests import lockapp  # noqa: E402
from tests.support import check  # noqa: E402

MAIN = ["com.example.locks.Store.read(Store.java:17)",
        "com.example.locks.MainActivity.freeze(MainActivity.java:21)"]
HOLDER = ["com.example.locks.Store.hold(Store.java:10)",
          "com.example.locks.Holder.run(Holder.java:23)"]
MAPPINGS = {"lines": lockapp.MAPPING, "default": lockapp.MAPPING, "offsets": lockapp.OFFSETS}


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli(list(argv))
    return code, out.getvalue(), err.getvalue()


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def retraced(tmp_path: Path, build: str = "lines", text: str | None = None,
             mapping_text: str | None = None) -> anr.Report:
    report = anr.parse(text if text is not None else lockapp.record(build))
    anr.retrace(report, write(tmp_path / "mapping.txt", mapping_text or MAPPINGS[build]))
    return report


@pytest.mark.parametrize("build", ["lines", "default", "offsets"])
def test_every_way_a_minified_build_writes_a_frame_comes_back_by_name(build, tmp_path):
    report = retraced(tmp_path, build)
    main = report.main
    check("the main thread inside Store.read, then the method it was inlined into",
          main is not None and main.frames[:2] == MAIN, main and main.frames)
    check("the platform's frames under them as they were",
          main.frames[2] == "android.os.Handler.handleCallback(Handler.java:942)", main.frames)
    chain = anr.chains(report)[0]
    check("the lock's holder stands where it spun",
          chain.root is not None and chain.root.frames[:2] == HOLDER, chain)
    check("two frames named back, none left without a place",
          report.retraced == {"frames": 2, "outside": 0}, report.retraced)


def test_named_back_they_are_the_projects_and_are_placed(tmp_path):
    report = retraced(tmp_path)
    root = lockapp.checkout(tmp_path / "app")
    placed, missing = anr.locate(report, root)
    check("all four in the checkout, at the lines R8 recorded",
          [(f.file.rsplit("/", 1)[-1], f.line) for f in placed]
          == [("Store.java", 17), ("MainActivity.java", 21), ("Store.java", 10),
              ("Holder.java", 23)] and not missing, (placed, missing))
    text = anr.render(report, (placed, missing))
    check("the project is in the report", "belongs to the platform or a library" not in text
          and "The build's mapping named back the 2 frames R8 wrote." in text, text)


def test_without_the_mapping_the_report_says_which_frames_it_could_not_read(tmp_path):
    report = anr.parse(lockapp.record())
    root = lockapp.checkout(tmp_path / "app")
    code = anr.locate(report, root)
    text = anr.render(report, code)
    check("the frames read as somebody else's, as they always did",
          "belongs to the platform or a library" in text, text)
    check("and the gaps say what would name them",
          "What 2 frames are: they read as R8 named them, first `a.b.run(SourceFile:3)`"
          in text and "`--mapping`" in text, text)
    found = anr.summary(report, code)
    check("so does the json", found["retrace"] is None and found["minified"] == 2, found)


def test_a_monitor_r8_renamed_comes_back_by_its_class(tmp_path):
    # `synchronized (this)` in a class R8 renamed: the note names `a.c`.
    report = retraced(tmp_path, text=lockapp.record(monitor="a.c"), mapping_text=lockapp.MAPPING)
    chain = anr.chains(report)[0]
    check("the class the source has", chain.monitor == "com.example.locks.Store", chain)


def test_frames_a_console_already_named_stay_as_they_were(tmp_path):
    # A console that had the mapping retraced these: one of a class R8
    # renamed, one of a class it kept, at a line outside R8's ranges.
    text = lockapp._RECORD.format(
        main="com.example.locks.Store.read(Store.java:17)",
        holder="com.example.locks.MainActivity.onCreate(MainActivity.java:16)",
        monitor="java.lang.Object")
    report = retraced(tmp_path, text=text, mapping_text=lockapp.MAPPING)
    check("untouched", report.main.frames[0] == "com.example.locks.Store.read(Store.java:17)"
          and anr.chains(report)[0].root.frames[0]
          == "com.example.locks.MainActivity.onCreate(MainActivity.java:16)", report)
    check("and said so", report.retraced == {"frames": 0, "outside": 0}
          and "no frame here was R8's" in anr.render(report), report.retraced)


def test_another_builds_mapping_is_called_out(tmp_path):
    # Both classes are there, numbered by another build: line 3 and line 6
    # are in no range of their `run`.
    other = ("com.example.locks.Holder -> a.a:\n"
             "    1:2:void run():19:20 -> run\n"
             "com.example.locks.MainActivity$$ExternalSyntheticLambda0 -> a.b:\n"
             "    1:1:void com.example.locks.MainActivity.freeze():21:21 -> run\n")
    report = retraced(tmp_path, mapping_text=other)
    check("both counted", report.retraced == {"frames": 2, "outside": 2}, report.retraced)
    check("named by class, with R8's method and no line",
          report.main.frames[0]
          == "com.example.locks.MainActivity$$ExternalSyntheticLambda0.run(MainActivity.java)",
          report.main.frames)
    text = anr.render(report)
    check("and the report warns", "> ⚠️ 2 of the 2 frames R8 wrote have no place" in text
          and "another build" in text, text)


# --- the command ------------------------------------------------------------

def test_the_command_names_the_frames_with_the_mapping_it_is_given(tmp_path):
    record = write(tmp_path / "anr.txt", lockapp.record("offsets"))
    root = lockapp.checkout(tmp_path / "app")
    mapping = write(tmp_path / "out/mapping.txt", lockapp.OFFSETS)
    code, out, _ = run("anr", str(record), "--root", str(root), "--mapping", str(mapping))
    check("exit 0", code == 0, code)
    check("the frames by name, and placed", MAIN[0] in out
          and "app/src/main/java/com/example/locks/Store.java:17" in out, out)
    found = json.loads(run("anr", str(record), "--root", str(root), "--mapping", str(mapping),
                           "--json")[1])
    check("the json counts them", found["retrace"] == {"frames": 2, "outside": 0}
          and found["minified"] == 0, found)


def test_the_command_takes_the_mapping_the_checkouts_config_names(tmp_path):
    record = write(tmp_path / "anr.txt", lockapp.record())
    root = lockapp.checkout(tmp_path / "app")
    write(root / "app/build/outputs/mapping/release/mapping.txt", lockapp.MAPPING)
    write(root / "echolot.yml", "project:\n"
                                "  package: com.example.locks\n"
                                "  mapping: app/build/outputs/mapping/release/mapping.txt\n")
    code, out, err = run("anr", str(record), "--root", str(root))
    check("exit 0, quietly", code == 0 and not err, (code, err))
    check("named back", HOLDER[0] in out, out)


def test_a_mapping_the_config_cannot_give_is_said_and_passed_over(tmp_path):
    record = write(tmp_path / "anr.txt", lockapp.record())
    root = lockapp.checkout(tmp_path / "app")
    write(root / "echolot.yml", "project:\n  package: com.example.locks\n"
                                "  mapping: nowhere/mapping.txt\n")
    code, out, err = run("anr", str(record), "--root", str(root))
    check("the report is still read", code == 0 and "# ANR Report" in out, code)
    check("and stderr says why the frames stay as they are",
          "no such file" in err and "--mapping" in err, err)
    check("which the report says too", "they read as R8 named them" in out, out)


def test_a_mapping_flag_that_names_nothing_stops_the_command(tmp_path):
    record = write(tmp_path / "anr.txt", lockapp.record())
    code, out, err = run("anr", str(record), "--mapping", str(tmp_path / "nope.txt"))
    check("exit 2 before anything is read", code == 2 and not out, (code, out))
    check("and says so", "no such file" in err, err)


def test_mark_from_anr_plans_from_the_frames_by_their_names(tmp_path):
    record = write(tmp_path / "anr.txt", lockapp.record())
    root = lockapp.checkout(tmp_path / "app")
    write(root / "mapping.txt", lockapp.MAPPING)
    write(root / "echolot.yml", "project:\n  package: com.example.locks\n"
                                "  mapping: mapping.txt\n")
    code, out, _ = run("mark", "--root", str(root), "--from-anr", str(record), "--json")
    check("exit 0", code == 0, code)
    plan = json.loads(out)
    files = {p["file"].rsplit("/", 1)[-1] for p in plan["proposals"]}
    check("the proposals are in the files the frames were retraced to",
          {"Store.java", "MainActivity.java"} <= files, plan["proposals"])
