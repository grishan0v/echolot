"""How echolot.yml is read, in the shapes it read wrong: a `tp_binary` with `~`
or a relative path, `name: null` in an anchor, a misspelled detector, a file
that is not UTF-8 or not a file, and an entry with only comments under it.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, recorder  # noqa: E402
from echolot.config import NO_ANCHOR, Config, ConfigError  # noqa: E402
from echolot.main import DETECTOR_DIR, _note_detectors, main, plan_detectors  # noqa: E402
from echolot.tp import load_detectors  # noqa: E402
from tests.support import check  # noqa: E402

BASE = {"project": {"package": "com.example.app", "process": "com.example.app"},
        "scenario": {"name": "fixture", "start": {"name": "AppStart"},
                     "end": {"name": "Screen.firstFrame"}}}


def _config(root: Path, raw: dict, name: str = "echolot.yml") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def test_tp_binary_is_read_like_the_mapping(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    (project / "tools").mkdir(parents=True)
    (project / "tools/trace_processor_shell").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cfg = Config.load(_config(project, {**BASE, "toolchain": {
        "tp_binary": "tools/trace_processor_shell"}}))
    check("a relative path from the config's directory",
          cfg.tp_binary == str(project / "tools/trace_processor_shell"), cfg.tp_binary)
    cfg = Config.load(_config(project, {**BASE, "toolchain": {"tp_binary": "~/bin/tp"}}))
    check("~ expanded", cfg.tp_binary == str(tmp_path / "home/bin/tp"), cfg.tp_binary)
    cfg = Config.load(_config(project, {**BASE, "toolchain": {
        "tp_binary": "trace_processor_shell"}}))
    check("and a bare name left for PATH", cfg.tp_binary == "trace_processor_shell", cfg.tp_binary)


def test_name_null_is_no_anchor(tmp_path: Path) -> None:
    raw = {**BASE, "scenario": {**BASE["scenario"], "end": {
        "name": None, "_source": "derived", "_evidence": "nothing found in probe"}}}
    check("no anchor", Config(raw).scenario_end == NO_ANCHOR)
    raw["scenario"]["end"] = {"_source": "derived"}
    with pytest.raises(ConfigError, match="has no `name`"):
        Config(raw).scenario_end  # noqa: B018
    trace = tmp_path / "t.perfetto-trace"
    trace.write_bytes(fixture.build())
    code, said = _run("calibrate", str(trace), "-c", str(_config(tmp_path, raw)))
    check("and calibrate says so, no traceback",
          code == 2 and "has no `name`" in said and "Traceback" not in said, said[-600:])


def test_a_misspelled_detector_is_refused(tmp_path: Path) -> None:
    raw = {**BASE, "detectors": {"frame_jnk": False, "main_thread_blok": {"min_slice_ms": 40}}}
    with pytest.raises(ConfigError, match="no detector 'frame_jnk', 'main_thread_blok'"):
        plan_detectors(Config(raw))
    code, said = _run("calibrate", str(tmp_path / "t.perfetto-trace"),
                      "-c", str(_config(tmp_path, raw)))
    check("by calibrate too", code == 2 and "no detector 'frame_jnk'" in said, said[-400:])
    raw["detectors"]["main_thread_block"] = {"min_slice_ms": 40}
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        _note_detectors(Config(raw))
    shipped = len(load_detectors(DETECTOR_DIR))
    check("and the note counts shipped ones only",
          f"tunes 1 of {shipped} detectors; the other {shipped - 1}" in err.getvalue(),
          err.getvalue())


def test_a_config_that_cannot_be_read_is_a_sentence(tmp_path: Path) -> None:
    bad = tmp_path / "echolot.yml"
    bad.write_bytes("# price in \u20ac, see \u21165\nproject: {package: a}\n".encode("cp1251"))
    with pytest.raises(ConfigError, match="is not UTF-8"):
        Config.load(bad)
    with pytest.raises(ConfigError, match="is not a file"):
        Config.load(tmp_path)
    good = _config(tmp_path / "p", BASE)
    (good.parent / "local.yml").mkdir()
    with pytest.raises(ConfigError, match="is not a file"):
        Config.load(good)


def test_an_entry_with_only_comments_tunes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "echolot.yml"
    path.write_text(yaml.safe_dump(BASE) + "detectors:\n  main_thread_block:\n"
                    "    # min_slice_ms: kept the default, nothing to derive it from\n"
                    "  gc_pressure:\n    # min_count: kept the default\n", encoding="utf-8")
    cfg = Config.load(path)
    check("no overrides", cfg.detector_overrides == {}, cfg.detector_overrides)
    plan = {d.id: source for d, _, source in plan_detectors(cfg)}
    check("and the plan runs both on defaults",
          plan["main_thread_block"] == "default" and plan["gc_pressure"] == "default", plan)


# --- keys the config does not know (#582) ---------------------------------------

def _refused(path: Path) -> str:
    with pytest.raises(ConfigError) as e:
        Config.load(path)
    return str(e.value)


def test_scenario_written_for_scenarios_is_refused(tmp_path: Path) -> None:
    """Two named scenarios under `scenario:` were one scenario called `run`."""
    raw = {"project": BASE["project"], "scenario": {
        "cold": {"start": {"name": "bindApplication"}, "end": {"name": "home_shown"}},
        "scroll": {"start": {"name": "Feed.scroll"}, "end": {"name": "Feed.idle"}}}}
    said = _refused(_config(tmp_path, raw))
    check("the two names, and where they go",
          "'cold', 'scroll' reads as a scenario's name" in said and "`scenarios:`" in said, said)


def test_an_unknown_key_in_one_scenario_is_refused(tmp_path: Path) -> None:
    raw = {**BASE, "scenario": {**BASE["scenario"], "budgetms": 800}}
    said = _refused(_config(tmp_path, raw))
    check("by name, with what it takes",
          "scenario: no key 'budgetms'" in said and "budget_ms" in said, said)


@pytest.mark.parametrize("value, says", [
    ("coldStart", "`name: coldStart`"),
    ("launch", "`window: launch` under it"),
    (["coldStart", "scroll"], "`scenarios:`"),
])
def test_a_scenario_that_is_not_a_block_is_refused(tmp_path: Path, value: object,
                                                   says: str) -> None:
    """`scenario: coldStart` measured the whole recording as `run` (#736)."""
    said = _refused(_config(tmp_path, {"project": BASE["project"], "scenario": value}))
    check("what it got, and how a block is written",
          said.startswith("scenario must be a block") and repr(value) in said
          and "`name`, `start` and `end` under it" in said and says in said, said)


def test_a_scenario_with_nothing_after_it_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "echolot.yml"
    path.write_text(yaml.safe_dump({"project": BASE["project"]}) + "scenario:\n",
                    encoding="utf-8")
    cfg = Config.load(path)
    check("the whole recording, as before", cfg.scenario_name == "run"
          and cfg.scenario_start == NO_ANCHOR, (cfg.scenario_name, cfg.scenario_start))


def test_an_unknown_section_is_refused_by_name(tmp_path: Path) -> None:
    raw = {**BASE, "detectros": {"main_thread_block": {"min_slice_ms": 50}}}
    said = _refused(_config(tmp_path, raw))
    check("the section, and the known ones",
          "no section 'detectros'" in said and "detectors" in said, said)


def test_an_unknown_section_in_local_yml_names_local_yml(tmp_path: Path) -> None:
    path = _config(tmp_path, BASE)
    (tmp_path / "local.yml").write_text("runer:\n  device: R58\n", encoding="utf-8")
    said = _refused(path)
    check("local.yml named", "local.yml: no section 'runer'" in said, said)


def test_setup_s_own_notes_still_load(tmp_path: Path) -> None:
    cfg = Config.load(_config(tmp_path, {**BASE, "_source": "scan", "_evidence": "x",
                                          "threads": {"own": ["DefaultDispatch*"]},
                                          "scenario": {**BASE["scenario"], "_source": "x"}}))
    check("loads", cfg.scenario_name == "fixture", cfg.scenario_name)


@pytest.mark.parametrize("key, value", [
    ("package", ["com.example.app", "com.example.other"]),
    ("process", 12345),
])
def test_package_and_process_are_text(tmp_path: Path, key: str, value: object) -> None:
    raw = {**BASE, "project": {**BASE["project"], key: value}}
    said = _refused(_config(tmp_path, raw))
    check("the key and the value", f"project.{key} must be one name" in said
          and repr(value) in said, said)


def test_a_section_written_twice_is_refused_with_both_lines(tmp_path: Path) -> None:
    path = tmp_path / "echolot.yml"
    path.write_text("project:\n  package: com.example.app\n"
                    "scenario:\n  name: fixture\n"
                    "project:\n  package: com.example.other\n", encoding="utf-8")
    said = _refused(path)
    check("the key and both lines", "'project' is written twice, on lines 1 and 5" in said, said)
