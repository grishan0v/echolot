#!/usr/bin/env python3
"""Codex in init, doctor and status: the rule, whether Codex reads it, and the
copies /import leaves (#191).

Codex's sandbox has no network, and echolot needs localhost; one rule in
`.codex/rules/echolot.rules` runs the `echolot` command outside it. Before
this, nothing wrote that rule: `init` put a pointer into AGENTS.md and
stopped, and the first `analyze` in Codex failed on a refused port.

What is held here: the file Codex is given is one Codex accepts; `init` writes
it, keeps an edit, and says why it could not write it from inside the sandbox,
which keeps `.codex/` read-only; the command it names keeps the project's
other agents; `doctor` and `status` say whether a rule lets echolot out and
whether Codex trusts the project enough to read it; and the copies /import
made are named, never deleted.
"""

from __future__ import annotations

import contextlib
import errno
import io
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from echolot import codex, hosts, sandbox, selftest
from echolot.main import build_parser, main
from tests.support import check

ROOT = Path(__file__).resolve().parent.parent
CODEX_VARS = ("CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED")


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    """A Codex home of its own, and no sandbox marks from whatever ran this."""
    for name in CODEX_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")
    h = tmp_path / "codex-home"
    h.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(h))
    return h


@pytest.fixture
def checked(monkeypatch):
    """A self-check that passes without starting trace_processor."""
    monkeypatch.setattr(selftest, "run", lambda tp_binary=None: [("a check", None)])


@pytest.fixture
def project(tmp_path) -> Path:
    p = tmp_path / "app"
    p.mkdir()
    (p / ".git").mkdir()
    (p / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return p


def run(project: Path, *argv: str) -> tuple[int, str]:
    """`echolot …` from inside `project`: exit code, and stdout and stderr."""
    out, err = io.StringIO(), io.StringIO()
    here = Path.cwd()
    os.chdir(project)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
    finally:
        os.chdir(here)
    return code, out.getvalue() + err.getvalue()


def init(project: Path, *flags: str) -> str:
    """`init`'s output with its wrapped lines joined, so a sentence reads whole."""
    code, text = run(project, "init", "--no-doctor", "--no-input", *flags)
    check("init exits 0", code == 0, text)
    return flat(text)


def flat(text: str) -> str:
    return " ".join(text.split())


def trusted(home: Path, project: Path, level: str = "trusted") -> None:
    """What Codex writes into its config when a person trusts a project."""
    (home / "config.toml").write_text(
        f'model = "gpt"\n\n[projects."{project.resolve()}"]\ntrust_level = "{level}"\n',
        encoding="utf-8")


def status_line(project: Path, key: str) -> str | None:
    _, text = run(project, "status")
    for line in text.splitlines():
        if line.split(" ", 1)[0] == key:
            return line.split(" ", 1)[1].strip()
    return None


# --- the file Codex is given --------------------------------------------------

def _examples(kind: str) -> list[str]:
    block = re.search(rf"{kind} = \[(.*?)\]", codex.RULE_FILE, re.S)
    return re.findall(r'"([^"]+)"', block[1])


def test_the_rule_lets_the_echolot_command_out_and_nothing_else():
    check("the rule the spike tried (#188), and the one sandbox.py looks for",
          sandbox.lets_echolot_out(codex.RULE_FILE), codex.RULE_FILE)
    check("allowed without a prompt", 'decision = "allow"' in codex.RULE_FILE)
    # Codex checks the examples when it loads the file, so they have to be
    # commands this echolot takes — a flag that went away would stop Codex
    # loading the file at all.
    parser = build_parser()
    for line in _examples("match"):
        argv = shlex.split(line)
        check(f"{line!r} is an echolot command", argv[0] == "echolot", line)
        parser.parse_args(argv[1:])
    check("python -m is not the command the rule names",
          _examples("not_match") == ["python3 -m echolot doctor"])


@pytest.mark.skipif(shutil.which("codex") is None, reason="the Codex CLI is not installed")
def test_codex_itself_loads_the_rule_and_allows_echolot(tmp_path):
    """The file as Codex reads it: its examples checked, its decision given."""
    rules = tmp_path / "echolot.rules"
    rules.write_text(codex.RULE_FILE, encoding="utf-8")

    def decide(*argv: str) -> str:
        out = subprocess.run(["codex", "execpolicy", "check", "--rules", str(rules),
                              "--", *argv], capture_output=True, text=True, timeout=60)
        check("codex accepts the file", out.returncode == 0, out.stdout + out.stderr)
        return out.stdout

    check("echolot runs outside the sandbox", '"decision":"allow"' in decide(
        "echolot", "analyze", ".echolot/traces/a.perfetto-trace", "-c", "echolot.yml"))
    check("git does not", '"decision"' not in decide("git", "status"))


# --- init --------------------------------------------------------------------

def test_init_for_codex_writes_the_rule_and_says_codex_must_trust_the_project(
        project, home):
    said = init(project, "--for", "codex")
    rule = project / codex.RULE_PATH
    check("the rule is written", rule.read_text(encoding="utf-8") == codex.RULE_FILE)
    check("and said", "+ .codex/rules/echolot.rules" in said, said)
    check("with when Codex reads it", "when a session starts" in said, said)
    check("and that this project is not trusted yet",
          "does not list this project as trusted" in said, said)

    said = init(project)
    check("a second run finds it current", "= .codex/rules/echolot.rules (Codex, current)"
          in said, said)


def test_a_trusted_project_is_told_only_about_the_session(project, home):
    trusted(home, project)
    said = init(project, "--for", "codex")
    check("the session is the one thing left",
          "until it is restarted" in said and "trusted" not in said, said)


def test_an_edited_rule_is_kept_and_all_puts_echolots_back(project, home):
    init(project, "--for", "codex")
    rule = project / codex.RULE_PATH
    mine = codex.RULE_FILE.replace('decision = "allow"', 'decision = "prompt"')
    rule.write_text(mine, encoding="utf-8")

    said = init(project)
    check("the edit is kept", rule.read_text(encoding="utf-8") == mine)
    check("and said, with the way back", "was edited here — left alone" in said, said)

    said = init(project, "--all")
    check("--all puts echolot's back", rule.read_text(encoding="utf-8") == codex.RULE_FILE)
    check("and says what it overwrote", "was edited here — overwritten" in said, said)


def test_a_rule_an_earlier_echolot_wrote_is_brought_up_to_date(project, home, monkeypatch):
    rule = project / codex.RULE_PATH
    rule.parent.mkdir(parents=True)
    older = codex.RULE_FILE.replace("echolot doctor -q", "echolot doctor")
    rule.write_text(older, encoding="utf-8")
    monkeypatch.setattr(codex, "_EARLIER", frozenset({codex._sha(older)}))

    said = init(project, "--for", "codex")
    check("updated, since nobody edited it", "↑ .codex/rules/echolot.rules" in said, said)
    check("to this echolot's text", rule.read_text(encoding="utf-8") == codex.RULE_FILE)


@pytest.fixture
def codex_dir_read_only(monkeypatch):
    """`.codex/` the way Codex's sandbox leaves it: nothing may be made in it."""
    real = Path.mkdir

    def mkdir(self, *args, **kwargs):
        if ".codex" in self.parts:
            raise PermissionError(errno.EPERM, "Operation not permitted", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)


def test_in_codexs_sandbox_init_says_the_rule_has_to_come_from_outside(
        project, home, codex_dir_read_only, monkeypatch):
    """Tried on a live `codex sandbox` (#191): `.codex/`, `.agents/` and
    `.git/` are read-only inside it, so that no command can let itself out."""
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
    said = init(project, "--for", "claude,codex")
    check("nothing half-written", not (project / ".codex").exists())
    check("the sandbox is named as the reason",
          "Codex's sandbox keeps .codex/ read-only" in said, said)
    check("and the way out keeps both agents",
          "Run `echolot init --for claude,codex` outside the sandbox" in said, said)
    check("the rest of init went on", (project / ".claude").is_dir())


def test_outside_a_sandbox_a_refused_write_is_said_as_it_is(
        project, home, codex_dir_read_only):
    said = init(project, "--for", "codex")
    check("the error itself, without a sandbox story",
          "not written: [Errno 1]" in said and "sandbox keeps" not in said, said)


def test_the_command_named_keeps_the_agents_already_chosen():
    check("--for replaces the choice, so the rest is named with codex",
          codex.init_command(["claude", "agents"]) == "echolot init --for claude,agents,codex")
    check("the whole list once codex is chosen, never a plain init",
          codex.init_command(["plugin", "codex"]) == "echolot init --for plugin,codex")


def test_codex_is_found_by_its_folder_and_offered_by_name(project):
    (project / ".codex").mkdir()
    found = [h.key for h in hosts.detect(project)]
    check("a project with .codex/ gets the rule and the AGENTS.md pointer",
          "codex" in found and "agents" in found, found)
    check("--for takes it", [h.key for h in hosts.parse("plugin,codex")] == ["plugin", "codex"])


def test_agents_md_says_why_the_first_analyze_fails_in_codex(project, home):
    init(project, "--for", "agents,gemini")
    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    gemini = (project / "GEMINI.md").read_text(encoding="utf-8")
    check("AGENTS.md, which Codex reads, names the rule",
          "In Codex, the sandbox stops `echolot`" in agents, agents)
    check("inside echolot's own section",
          agents.index("In Codex") < agents.index(hosts.END_MARKER))
    check("GEMINI.md has no word about Codex", "Codex" not in gemini, gemini)


# --- whether Codex reads it ---------------------------------------------------

def test_trust_is_read_the_way_codex_writes_it(project, home):
    check("nothing said is not trust", codex.trust(project) is None)
    trusted(home, project)
    check("trusted", codex.trust(project) == "trusted")
    trusted(home, project, "untrusted")
    check("untrusted", codex.trust(project) == "untrusted")


def test_a_folder_inside_a_trusted_repository_is_trusted(project, home):
    """Codex looks the directory up, then the repository it is in."""
    trusted(home, project)
    inner = project / "android"
    inner.mkdir()
    check("the repository's trust covers it", codex.trust(inner) == "trusted")


def test_a_linked_worktree_takes_the_main_checkouts_trust(tmp_path, project, home):
    tree = tmp_path / "tree"
    tree.mkdir()
    gitdir = project / ".git" / "worktrees" / "tree"
    gitdir.mkdir(parents=True)
    (tree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    trusted(home, project)
    check("the main checkout's trust", codex.trust(tree) == "trusted")


def test_without_tomllib_the_tables_codex_writes_read_the_same():
    """Python 3.10 has no TOML reader; the line reader covers what Codex writes."""
    text = ('model = "gpt"\n'
            '[projects."/work/app"]\ntrust_level = "trusted"\n\n'
            "[projects.'/work/other']\ntrust_level = 'untrusted'\n"
            '[profiles.x]\ntrust_level = "trusted"\n')
    by_line = codex._trust_levels_by_line(text)
    check("both projects, and nothing from another table",
          by_line == {"/work/app": "trusted", "/work/other": "untrusted"}, by_line)
    check("the same as the TOML reader", by_line == codex._trust_levels(text))


# --- doctor and status -----------------------------------------------------------

def test_no_codex_here_no_line(project, home, checked):
    init(project)
    check("status says nothing about Codex", status_line(project, "codex") is None)
    code, said = run(project, "doctor", "-q")
    check("doctor -q neither", code == 0 and "codex:" not in said, said)


def test_doctor_q_gains_a_line_where_codex_is_used(project, home, checked):
    init(project, "--for", "codex")
    code, said = run(project, "doctor", "-q")
    lines = said.splitlines()
    check("the codex line right under the layer line",
          lines[2].startswith("codex: .codex/rules/echolot.rules is not read"), said)


def test_a_codex_project_with_no_rule_is_told_the_command(project, home):
    (project / ".codex").mkdir()
    init(project, "--for", "claude")
    line = status_line(project, "codex")
    check("no rule, said as a problem", line and line.startswith("NO RULE"), line)
    check("and the command keeps Claude Code", "`echolot init --for claude,codex`" in line, line)


def test_an_untrusted_project_is_told_its_rule_is_not_read(project, home):
    init(project, "--for", "codex")
    line = status_line(project, "codex")
    check("not read, and why", "is not read" in line
          and "does not list this project as trusted" in line, line)
    check("with both ways", "trust it when Codex asks" in line
          and "codex-home/rules/" in line, line)


def test_a_trusted_project_with_the_rule_is_fine(project, home):
    init(project, "--for", "codex")
    trusted(home, project)
    line = status_line(project, "codex")
    check("the rule and the trust", line == (".codex/rules/echolot.rules lets echolot "
                                             "out of the sandbox, and Codex trusts this "
                                             "project"), line)


def test_a_rule_for_every_project_is_enough(project, home):
    (project / ".codex").mkdir()
    (home / "rules").mkdir()
    (home / "rules" / "default.rules").write_text(
        'prefix_rule(pattern = ["echolot"], decision = "allow")\n', encoding="utf-8")
    line = status_line(project, "codex")
    check("the home rule is named", line.endswith("lets echolot out of the sandbox, "
                                                  "in every project"), line)


def test_a_rule_edited_away_is_named_with_how_to_get_it_back(project, home):
    init(project, "--for", "codex")
    (project / codex.RULE_PATH).write_text("# nothing here\n", encoding="utf-8")
    line = status_line(project, "codex")
    check("edited, and no longer letting echolot out",
          "was edited and lets echolot out of the sandbox no more" in line, line)
    check("delete it, then init", "delete it, then `echolot init --for codex`" in line,
          line)


def test_in_codexs_sandbox_the_line_is_there_without_a_codex_folder(
        project, home, monkeypatch):
    """The plugin brings no .codex/, and Codex running this is evidence enough."""
    init(project, "--for", "plugin")
    monkeypatch.setenv("CODEX_SANDBOX_NETWORK_DISABLED", "1")
    line = status_line(project, "codex")
    check("the missing rule is said", line and line.startswith("NO RULE"), line)
    check("with the plugin kept", "`echolot init --for plugin,codex`" in line, line)


# --- the copies /import leaves ------------------------------------------------

def _imported(project: Path) -> None:
    for d in ("echolot", "source-command-echolot-hunt", "source-command-echolot-setup"):
        skill = project / ".agents" / "skills" / d
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("the `.Codex/` layer\n", encoding="utf-8")
    agents = project / ".codex" / "agents"
    agents.mkdir(parents=True)
    (agents / "perf-hunter.toml").write_text('name = "perf-hunter"\n', encoding="utf-8")


def test_the_copies_import_made_are_listed_and_left_alone(project, home):
    _imported(project)
    line = status_line(project, "codex")
    check("counted on the status line", "4 copies of echolot's skills that nothing "
                                         "keeps current" in line, line)
    with contextlib.redirect_stdout(io.StringIO()) as out:
        codex.print_status(project, hosts.keys(project))
    said = flat(out.getvalue())
    for rel in (".agents/skills/echolot/", ".agents/skills/source-command-echolot-hunt/",
                ".codex/agents/perf-hunter.toml"):
        check(f"doctor names {rel}", rel in said, said)
    check("and what replaces them", "echolot plugin replaces them" in said, said)
    check("nothing deleted", (project / ".agents" / "skills" / "echolot").is_dir()
          and (project / ".codex" / "agents" / "perf-hunter.toml").is_file())


def test_a_skill_of_another_name_is_not_ours(project, home):
    other = project / ".agents" / "skills" / "lint"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("lint\n", encoding="utf-8")
    check("not counted", codex.imported(project) == [])


# --- the door, in Codex ---------------------------------------------------------

def test_the_plugins_door_writes_the_rule_in_codex():
    door = (ROOT / "plugins" / "echolot" / "skills" / "echolot" / "SKILL.md").read_text(
        encoding="utf-8")
    check("init in Codex adds the rule", "echolot init --for plugin,codex" in door, door)
