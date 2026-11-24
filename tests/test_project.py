"""One project per run, by one rule, for every command that takes `-c` (#830).

The table is the rule: where the command is run from, `-c`, `--root` and
`project.root` in, the `Project` out. It runs for every command that takes
`-c`, read through the parser the way `main()` reads it, so a command whose
flags say something else shows up here and not in a bug report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import project as project_mod  # noqa: E402
from echolot import recorder  # noqa: E402
from echolot.main import build_parser, main  # noqa: E402
from tests.support import check  # noqa: E402

CONFIG = "project:\n  package: com.example.app\n"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Two checkouts, a folder outside both, and a checkout inside a folder
    that holds a config of its own."""
    for folder in ("proj/.git", "proj/app/src", "proj/build/out", "rooted/.git",
                   "rooted/app", "checkout/src", "outside",
                   "above/nested/.git", "above/nested/sub"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "proj/echolot.yml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "proj/other.yml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "rooted/echolot.yml").write_text(
        CONFIG + "  root: ../checkout\n", encoding="utf-8")
    (tmp_path / "above/echolot.yml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


# here, -c, --root, then the project folder, its config, the checkout, and how
# it was found. `{t}` is the tree; "-" is no flag.
ROWS = [
    ("proj", "-", "-", "proj", "proj/echolot.yml", "proj", "here"),
    ("proj/app", "-", "-", "proj", "proj/echolot.yml", "proj", "above"),
    ("proj/app/src", "-", "-", "proj", "proj/echolot.yml", "proj", "above"),
    ("proj/build/out", "-", "-", "proj", "proj/echolot.yml", "proj", "above"),
    ("outside", "-", "-", "outside", "outside/echolot.yml", "outside", "none"),
    ("outside", "../proj/echolot.yml", "-", "proj", "proj/echolot.yml", "proj", "named"),
    ("outside", "{t}/proj/echolot.yml", "-", "proj", "proj/echolot.yml", "proj", "named"),
    ("proj/app", "../echolot.yml", "-", "proj", "proj/echolot.yml", "proj", "named"),
    ("proj", "other.yml", "-", "proj", "proj/other.yml", "proj", "named"),
    ("proj/app", "../other.yml", "-", "proj", "proj/other.yml", "proj", "named"),
    ("proj/app", "missing.yml", "-", "proj/app", "proj/app/missing.yml", "proj/app", "named"),
    ("proj/app", "-", "../../checkout", "proj", "proj/echolot.yml", "checkout", "above"),
    ("rooted", "-", "-", "rooted", "rooted/echolot.yml", "checkout", "here"),
    ("rooted/app", "-", "-", "rooted", "rooted/echolot.yml", "checkout", "above"),
    ("outside", "../rooted/echolot.yml", "-", "rooted", "rooted/echolot.yml", "checkout", "named"),
    ("rooted/app", "-", "../../proj", "rooted", "rooted/echolot.yml", "proj", "above"),
    # The walk stops at the top of the checkout: a config above it is
    # another project's.
    ("above/nested/sub", "-", "-", "above/nested/sub", "above/nested/sub/echolot.yml",
     "above/nested/sub", "none"),
]


def _commands_with_config() -> dict[str, argparse.ArgumentParser]:
    sub = next(a for a in build_parser()._actions
               if isinstance(a, argparse._SubParsersAction))
    return {name: p for name, p in sub.choices.items()
            if any("-c" in a.option_strings for a in p._actions)}


def _argv(name: str, parser: argparse.ArgumentParser, config: str, root: str,
          t: Path) -> list[str] | None:
    """The command with what it needs to parse, and the row's flags."""
    argv = [name]
    for action in parser._actions:
        if not action.option_strings and action.nargs not in ("?", "*"):
            argv.append("x")
    has_root = any("--root" in a.option_strings for a in parser._actions)
    if root != "-":
        if not has_root:
            return None
        argv += ["--root", root]
    if config != "-":
        argv += ["-c", config.format(t=t)]
    return argv


# `--root` that defaults to the working directory, until the command moves
# onto the project (#830's later steps).
ROOT_IS_HERE = {"domains"}


def test_every_command_that_takes_c_resolves_one_project(tree: Path) -> None:
    commands = _commands_with_config()
    check("the commands that take -c are found",
          {"analyze", "hunt", "status", "mark", "collect", "compare", "report",
           "reflect", "ui", "doctor", "probe", "names", "explain", "calibrate",
           "domains"} <= set(commands), sorted(commands))
    parser = build_parser()
    for row in ROWS:
        here, config, root, folder, cfg, checkout, found = row
        for name, sub in commands.items():
            argv = _argv(name, sub, config, root, tree)
            if argv is None:
                continue
            args = parser.parse_args(argv)
            got = project_mod.resolve(args, here=tree / here)
            want_root = tree / (here if name in ROOT_IS_HERE and root == "-" else checkout)
            check(f"{name} from {here} with -c {config} --root {root}",
                  (got.folder, got.config, got.found, got.root)
                  == ((tree / folder).resolve(), (tree / cfg).resolve(), found,
                      want_root.resolve()),
                  (got.folder, got.config, got.found, got.root))
            check(f"{name}: .echolot is the project's", got.echolot == got.folder / ".echolot")


def test_the_bare_command_and_a_refused_one_resolve_from_here(tree: Path) -> None:
    args = build_parser().parse_args([])
    got = project_mod.resolve(args, here=tree / "proj/app")
    check("`echolot` from app/ is the project above", got.folder == (tree / "proj").resolve()
          and got.found == "above", got)
    got = project_mod.resolve(None, here=tree / "proj/app")
    check("and so is a run with no arguments to read", got.folder == (tree / "proj").resolve())


def test_init_into_and_reflect_project_name_the_folder(tree: Path) -> None:
    parser = build_parser()
    got = project_mod.resolve(parser.parse_args(["reflect", "--project", "../rooted"]),
                              here=tree / "proj")
    check("reflect --project is the project", got.folder == (tree / "rooted").resolve()
          and got.config == (tree / "rooted/echolot.yml").resolve()
          and got.root == (tree / "checkout").resolve(), got)
    got = project_mod.resolve(parser.parse_args(["init", "--into", "../outside"]),
                              here=tree / "proj")
    check("init --into is the project", got.folder == (tree / "outside").resolve()
          and not got.set_up, got)


def test_a_config_that_does_not_load_leaves_the_checkout_at_its_folder(tree: Path) -> None:
    (tree / "rooted/echolot.yml").write_text("project: [\n", encoding="utf-8")
    got = project_mod.resolve(None, here=tree / "rooted/app")
    check("the config's folder", got.root == (tree / "rooted").resolve(), got.root)


def test_the_config_is_shown_as_typed_from_here(tree: Path) -> None:
    got = project_mod.resolve(None, here=tree / "proj")
    check("in the project: its name", got.shown() == "echolot.yml", got.shown())
    got = project_mod.resolve(None, here=tree / "proj/app")
    check("below it: the way up", got.shown() == "../echolot.yml", got.shown())


@pytest.fixture
def logged(tree, monkeypatch):
    """The run log on."""
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    return tree


def _lines(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("argv", [["status"], ["hunt"], ["mark", "--json"], []])
def test_a_run_from_a_subfolder_logs_in_the_project(logged: Path, monkeypatch,
                                                    argv: list[str]) -> None:
    """#835: from `app/`, the line goes to the project's log, and no second
    `.echolot/` is left under `app/`."""
    monkeypatch.chdir(logged / "proj/app")
    with recorder.isolated():
        try:
            main(argv)
        except SystemExit:
            pass
    lines = _lines(logged / "proj" / recorder.LOG_FILE)
    check(f"{argv or ['echolot']}: one line in the project's log",
          len(lines) == 1 and lines[0]["cmd"] == (argv[0] if argv else "status"), lines)
    check("and nothing under app/", not (logged / "proj/app/.echolot").exists())


def test_a_refused_call_from_a_subfolder_logs_in_the_project(logged: Path,
                                                             monkeypatch) -> None:
    monkeypatch.chdir(logged / "proj/app")
    with recorder.isolated(), pytest.raises(SystemExit):
        main(["analyze", "--no-such-flag"])
    lines = _lines(logged / "proj" / recorder.LOG_FILE)
    check("the refusal's line is the project's", len(lines) == 1 and lines[0]["exit"] == 2, lines)


def test_reflect_from_a_subfolder_reads_the_project_s_log(logged: Path, monkeypatch,
                                                          capsys) -> None:
    """#835: `reflect` from `app/` reads the log the project's runs wrote,
    and writes nothing under `app/`."""
    monkeypatch.chdir(logged / "proj/app")
    with recorder.isolated():
        main(["status"])
    capsys.readouterr()
    with recorder.isolated():
        code = main(["reflect", "--from-log", "--list"])
    out = capsys.readouterr()
    rows = [line.split() for line in out.out.splitlines()[1:] if line.strip()]
    check("the project's sitting is listed, with the one run in it",
          code == 0 and len(rows) == 1 and rows[0][1] == "recorder" and rows[0][5] == "1", out)
    check("and nothing under app/", not (logged / "proj/app/.echolot").exists())


def test_status_says_which_project_and_config_it_found(tree: Path, monkeypatch,
                                                       capsys) -> None:
    """`echolot` from `app/` names the project and the config above it, so a
    run from a subfolder shows which project it works on."""
    monkeypatch.chdir(tree / "proj/app")
    with recorder.isolated():
        main([])
    out = capsys.readouterr().out
    head, config = out.splitlines()[0], next(
        line for line in out.splitlines() if line.startswith("config"))
    check("the project's folder in the first line", head.endswith(str((tree / "proj").resolve())),
          head)
    check("and its config, as typed from here", config.split()[1] == "../echolot.yml", config)
    monkeypatch.chdir(tree / "above/nested/sub")
    with recorder.isolated():
        main([])
    out = capsys.readouterr().out
    check("a config above the checkout is not this project's",
          "none — no echolot.yml here or in a folder above it" in out, out)
