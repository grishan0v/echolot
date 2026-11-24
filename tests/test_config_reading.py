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
from echolot.analyze import DETECTOR_DIR, plan_detectors  # noqa: E402
from echolot.main import _note_detectors, main  # noqa: E402
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
def test_a_misspelled_key_under_instrumentation_is_refused(tmp_path: Path) -> None:
    """`alowed` was dropped, and no list allows every place (#735)."""
    raw = {**BASE, "instrumentation": {"alowed": ["feature/only"]}}
    said = _refused(_config(tmp_path, raw))
    check("by name, with the keys it has",
          "no key 'alowed' under instrumentation" in said and "allowed" in said
          and "temp_prefix" in said, said)


@pytest.mark.parametrize("value", [5, {"app": "src"}, ["app/src/main", 7]])
def test_allowed_that_is_no_folder_is_refused(tmp_path: Path, value: object) -> None:
    said = _refused(_config(tmp_path, {**BASE, "instrumentation": {"allowed": value}}))
    check("what it got", "instrumentation.allowed must be a folder or a list of folders"
          in said and repr(value) in said, said)


def test_allowed_written_as_text_is_a_list_of_one(tmp_path: Path) -> None:
    for value, want in (("app/src/main", ["app/src/main"]), (["a", "b/*"], ["a", "b/*"]),
                        (None, [])):
        cfg = Config.load(_config(tmp_path, {**BASE, "instrumentation": {"allowed": value}}))
        check(f"{value!r}", cfg.allowed == want, cfg.allowed)


@pytest.mark.parametrize("section, block, key, lists", [
    ("runner", {"mode": "launch", "iteratons": 3}, "iteratons", "iterations"),
    ("runner", {"duraton_ms": 30000}, "duraton_ms", "duration_ms"),
    ("toolchain", {"tp_bniary": "x"}, "tp_bniary", "tp_binary"),
    ("project", {**BASE["project"], "maping": "m.txt"}, "maping", "mapping"),
    ("project", {"package": "com.example.app", "proces": "x"}, "proces", "process"),
    ("loop", {"max_round": 3}, "max_round", "max_rounds"),
])
def test_a_misspelled_key_in_a_section_is_refused(tmp_path: Path, section: str, block: dict,
                                                  key: str, lists: str) -> None:
    """Below the top level, a key nobody asked for was never looked at:
    `iteratons: 3` recorded five repeats, `tp_bniary` the bundled shell (#737)."""
    said = _refused(_config(tmp_path, {**BASE, section: block}))
    check("by name, with the keys the section has",
          f"no key {key!r} under {section}" in said and lists in said, said)


def test_a_misspelled_runner_key_in_a_named_scenario_is_refused(tmp_path: Path) -> None:
    raw = {"project": BASE["project"], "scenarios": {"cold": {
        "start": {"name": "AppStart"}, "end": {"name": "Screen.firstFrame"},
        "runner": {"mode": "command", "comand": "adb shell input tap 1 1"}}}}
    said = _refused(_config(tmp_path, raw))
    check("where it is", "no key 'comand' under scenarios.cold.runner" in said
          and "command" in said, said)


def test_a_misspelled_key_in_local_yml_names_local_yml(tmp_path: Path) -> None:
    path = _config(tmp_path, BASE)
    (tmp_path / "local.yml").write_text("runner:\n  devise: R58\n", encoding="utf-8")
    said = _refused(path)
    check("local.yml named", "local.yml: no key 'devise' under runner" in said, said)


def test_every_runner_key_and_setup_s_notes_still_load(tmp_path: Path) -> None:
    from echolot.config import runner_keys
    block = {k: None for k in runner_keys()} | {"_evidence": "x", "_source": "scan"}
    cfg = Config.load(_config(tmp_path, {**BASE, "runner": block,
                                         "project": {**BASE["project"], "_source": "derived"}}))
    check("loads", set(cfg.runner) == set(block), cfg.runner)


def test_the_example_and_what_scan_drafts_still_load(tmp_path: Path) -> None:
    from echolot import scan
    from tests.test_scan import repo
    from tests.test_scan_benchmark_kinds import LAUNCH, project
    Config.load(Path(__file__).resolve().parent.parent / "echolot.yml.example")
    (tmp_path / "app").mkdir()
    repo(tmp_path / "app")
    for name, root in (("app", tmp_path / "app"),
                       ("benchmark", project(tmp_path / "bench", StartupBenchmark=LAUNCH))):
        draft = tmp_path / f"{name}.yml"
        draft.write_text("\n".join(scan.skeleton(scan.describe(root, devices=False))) + "\n",
                         encoding="utf-8")
        check(f"the {name} draft loads", Config.load(draft).runner.get("mode") is not None)


def test_each_runner_key_read_is_one_the_config_knows() -> None:
    """The lists sit beside the code that reads each key. A key read and not
    listed would be refused before it is ever read."""
    import re as re_
    from echolot.config import runner_keys
    from echolot.runner import RECORDING_KNOBS
    root = Path(__file__).resolve().parent.parent / "echolot"
    read = set()
    pattern = re_.compile(r'(?:section\.get\(|_positive\(section, |_listed\(section, )"(\w+)"')
    for name in ("runner.py", "stabilize.py", "composenames.py", "power.py", "main.py",
                 "check.py", "ready.py"):
        read |= set(pattern.findall((root / name).read_text(encoding="utf-8")))
    read |= set(RECORDING_KNOBS)
    check("every one listed", read <= set(runner_keys()), sorted(read - set(runner_keys())))
    from echolot import stabilize
    # `knobs_of` reads its two switches in a loop over their names.
    check("and each listed is read", set(runner_keys()) <= read | set(stabilize.KEYS),
          sorted(set(runner_keys()) - read))


def test_min_battery_loads_in_echolot_yml_and_local_yml_and_is_read(tmp_path: Path) -> None:
    """`collect` tells a person on a low battery to set `runner.min_battery: 0`
    in local.yml, and 0.18.0 refused that config as an unknown key (#834)."""
    from echolot import power
    cfg = Config.load(_config(tmp_path / "shared", {**BASE, "runner": {"min_battery": 10}}))
    check("echolot.yml", power.floor(cfg.runner) == 10, cfg.runner)
    path = _config(tmp_path / "local", BASE)
    (tmp_path / "local" / "local.yml").write_text("runner:\n  min_battery: 0\n", encoding="utf-8")
    cfg = Config.load(path)
    check("local.yml", power.floor(cfg.runner) == 0, cfg.runner)
    raw = {"project": BASE["project"], "scenarios": {"cold": {
        "start": {"name": "AppStart"}, "end": {"name": "Screen.firstFrame"},
        "runner": {"min_battery": 5}}}}
    check("a scenario's runner", power.floor(Config.load(_config(tmp_path / "s", raw)).runner) == 5)


def _config_examples() -> list[tuple[str, int, str]]:
    """Every YAML example in the docs, the READMEs and the skill that is a
    config or a piece of one: each of its top-level keys is a section."""
    import re as re_
    from echolot.config import _SECTIONS
    root = Path(__file__).resolve().parent.parent
    files = sorted((root / "docs").rglob("*.md")) + sorted(root.glob("README*.md"))
    files += sorted((root / "echolot" / "claude").rglob("*.md"))
    found = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for m in re_.finditer(r"^```ya?ml\n(.*?)^```", text, re_.S | re_.M):
            try:
                data = yaml.safe_load(m.group(1))
            except yaml.YAMLError:
                continue
            if isinstance(data, dict) and data and all(k in _SECTIONS for k in data):
                found.append((str(path.relative_to(root)),
                              text.count("\n", 0, m.start()) + 1, m.group(1)))
    return found


def test_every_config_example_in_the_docs_loads(tmp_path: Path) -> None:
    """The runner examples in both collecting.md set `min_battery`, and
    neither loaded in 0.18.0 (#834)."""
    examples = _config_examples()
    check("some found", len(examples) > 20, len(examples))
    refused = []
    for i, (where, line, text) in enumerate(examples):
        path = tmp_path / str(i) / "echolot.yml"
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")
        try:
            Config.load(path)
        except ConfigError as e:
            refused.append(f"{where}:{line}: {e}")
    check("every one loads", not refused, refused)


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
