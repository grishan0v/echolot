#!/usr/bin/env python3
"""Gradle mode — the one collect mode that had never been run.

It shipped in 0.4 and stayed unexercised: the other two modes drive a device
and are checked against one, while this one hands the driving to a
macrobenchmark and only gathers what it wrote. Running it for the first time
found the gap below within a minute, and nothing about it needs a phone to
pin — the mode is a command, a directory, and the files that appeared in it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import runner  # noqa: E402


def test_the_gradle_task_runs_in_the_project_it_names(tmp_path):
    """`runner.project_root` is where the wrapper is, not just where traces land.

    `./gradlew` is a relative path, and it used to resolve against whatever
    directory `echolot` was started from — the one holding `echolot.yml`,
    which for anyone keeping the config beside the tool is not the one holding
    the app. The failure was quiet in the worst way: the wrapper is missing,
    the shell says so, and `collect` reports a scenario command that returned
    127 without ever mentioning the directory it looked in.
    """
    project = tmp_path / "app"
    project.mkdir()
    (project / "marker").write_text("this is the project", encoding="utf-8")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    seen = project / "where-it-ran.txt"
    runner.run_command(f"pwd -P > {seen}", timeout=30, cwd=project)

    where = Path(seen.read_text(encoding="utf-8").strip()).resolve()
    assert where == project.resolve(), (
        f"the command ran in {where}, not in the project root it was given")


def test_a_project_root_that_is_not_there_is_named(tmp_path):
    """The directory is in the message, because that is the thing to fix."""
    with pytest.raises(runner.RunnerError) as e:
        runner.run_command("true", timeout=30, cwd=tmp_path / "not-here")
    assert "not-here" in str(e.value), str(e.value)
    assert "project_root" in str(e.value), str(e.value)


def test_without_a_cwd_the_command_stays_where_it_was_started(tmp_path):
    """Command mode is unchanged: its commands drive a device, not a build."""
    seen = tmp_path / "where.txt"
    runner.run_command(f"pwd -P > {seen}", timeout=30)
    where = Path(seen.read_text(encoding="utf-8").strip()).resolve()
    assert where == Path.cwd().resolve(), where


def test_harvest_takes_what_appeared_and_leaves_what_was_there(tmp_path):
    """The rule the mode stands on, and the one that makes a rerun honest.

    A benchmark module keeps every trace it ever wrote — the directory used
    for this had fifteen from a run twelve days earlier. Gathering by name
    would have merged two sittings into one report and taken medians across
    them; gathering by "newer than the moment we started" is what keeps a
    second run about the second run.
    """
    import time

    root = tmp_path / "project"
    old_dir = root / "benchmark/build/outputs/connected/SM-A515F - 13"
    old_dir.mkdir(parents=True)
    stale = old_dir / "Startup_iter000_2026-08-28.perfetto-trace"
    stale.write_bytes(b"from a previous sitting")

    since = time.time()
    time.sleep(0.01)
    fresh = old_dir / "Startup_iter000_2026-09-09.perfetto-trace"
    fresh.write_bytes(b"from this one")

    out = tmp_path / "out"
    got = runner.harvest(root, since, out, "startup")

    assert len(got) == 1, [str(g["path"]) for g in got]
    assert got[0]["path"].read_bytes() == b"from this one"


def test_a_gradle_run_says_which_knobs_it_ignored(tmp_path, monkeypatch):
    """The macrobenchmark chose what to record, so half the runner config is moot.

    Found on the first real run of the mode: `environment: true` sat in the
    config while the report came back with the thermal counters missing, and
    the config had nothing to do with either half of that. These are the keys
    most likely to be copied in from a launch-mode config and believed.
    """
    project = tmp_path / "app"
    project.mkdir()
    said: list[str] = []

    monkeypatch.setattr(runner, "run_command", lambda *a, **kw: 1.0)
    monkeypatch.setattr(runner, "harvest",
                        lambda *a, **kw: [{"path": tmp_path / "t", "size": 1}])

    runner.collect(
        package="com.example.app", out_dir=tmp_path / "out", iterations=1,
        name="startup", log=said.append,
        section={"mode": "gradle", "gradle_task": ":benchmark:connected",
                 "project_root": str(project), "environment": True},
    )

    warned = [line for line in said if "does not apply" in line]
    assert warned, said
    assert "runner.environment" in warned[0], warned[0]
