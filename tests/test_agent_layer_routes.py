"""The routes the agent layer sends an agent down, followed to where they lead.

Each case here is one the 2026-09-28 audit walked and found going somewhere
other than where the text said. A route is a claim with two ends — a word
`next` prints, a line `init` prints, a step a command file gives — and it
went wrong where the ends had been written separately and drifted:

- the Claude Code path never opened an investigation, so `analyze` filed
  nothing and `perf-hunter`'s bare `echolot compare` had nothing to compare,
  and never closed one, so a finished hunt came back as `resume-or-new`;
- an unreadable `.claude/settings.json` read as stale, `next` said `init`,
  and `init` cannot fix it — round and round;
- the one-line layer verdict and the full `doctor` section decided the
  advice separately and disagreed about `--all`;
- `init --for cursor` was forgotten by the next plain `init`;
- a project that declined Claude Code was sent to `/echolot` anyway;
- the AGENTS.md snippet to paste by hand stopped before its end marker;
- `.gitignore` lines were skipped in silence outside a git root;
- two help strings listed fewer choices than there are.

The cases drive the CLI the way a person or an agent does, in a temporary
directory, and read what it printed and what it left on disk.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from echolot import hosts, layer
from echolot import hunt as hunt_mod
from echolot.layer import CLAUDE_DIR, guide_topics
from echolot.main import build_parser, main
from echolot.state import next_kind, next_step, project_state
from tests.support import check

CONFIG = ("project:\n  package: com.example.app\n  process: com.example.app\n"
          "scenario:\n  name: coldStart\n")


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
    code, text = run(project, "init", "--no-doctor", "--no-input", *flags)
    check("init exits 0", code == 0, text)
    return text


# --- an unreadable settings.json -------------------------------------------

def test_an_unreadable_settings_json_is_not_sent_back_to_init(tmp_path: Path) -> None:
    """`init` cannot fix it, so `next` must stop naming `init` once it has run."""
    init(tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.write_text("{ hooks: 'not json' }", encoding="utf-8")

    said = init(tmp_path)
    check("init leaves the file as it was",
          settings.read_text(encoding="utf-8") == "{ hooks: 'not json' }")
    check("and says what to merge by hand", '"Bash(echolot:*)"' in said, said)

    st = project_state(tmp_path)
    check("the layer is unreadable, not stale", st["layer_verdict"] == "unreadable",
          st["layer_verdict"])
    check("no config yet: setup, not init", next_kind(st) == "setup", next_kind(st))

    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    st = project_state(tmp_path)
    check("with a config, the route goes to the person who can fix it",
          next_kind(st) == "fix-settings", next_kind(st))
    step = next_step(st)
    check("and the step says so, then where the route goes on",
          "by hand" in step and "hunt" in step and not step.startswith("echolot init"),
          step)

    # Once it parses again, init puts the permission in and the route is clear.
    settings.write_text("{}", encoding="utf-8")
    init(tmp_path)
    check("fixed by hand, the next init finishes the job",
          next_kind(project_state(tmp_path)) == "hunt",
          next_kind(project_state(tmp_path)))


# --- one advice, two renderings ---------------------------------------------

def _commands(text: str) -> set[str]:
    """The `echolot init…` commands a text puts in front of its reader."""
    return set(re.findall(r"`(echolot init(?: --all)?)`", text))


def _layer(project: Path, case: str) -> None:
    """A project whose `.claude/` layer is in the state `case` names."""
    if case == "absent":
        return
    if case == "opted-out":
        init(project, "--for", "cursor")
        return
    init(project)
    root = project / ".claude"
    skill = root / "skills" / "echolot" / "SKILL.md"
    agent = root / "agents" / "perf-hunter.md"
    if case in ("stale", "stale+customised"):
        # Stale: the copy is what init wrote, and the template has moved on —
        # emulated by changing the copy and recording that as what was written.
        agent.write_text(agent.read_text(encoding="utf-8") + "\n# older\n",
                         encoding="utf-8")
        layer.write_manifest(root, {"agents/perf-hunter.md": layer.sha(agent)})
    if case in ("stale+customised", "conflict", "differs"):
        skill.write_text(skill.read_text(encoding="utf-8") + "\n# ours\n",
                         encoding="utf-8")
    if case == "conflict":
        # Edited here, and installed as something the template is no longer.
        layer.write_manifest(root, {"skills/echolot/SKILL.md": "0" * 16})
    if case == "differs":
        (root / layer.LAYER_MANIFEST).unlink()
    if case == "unreadable":
        (root / "settings.json").write_text("{ nope", encoding="utf-8")


ADVICE = {
    "absent": ("absent", {"echolot init"}),
    "opted-out": ("opted-out", set()),
    "current": ("current", set()),
    "stale": ("stale", {"echolot init"}),
    "stale+customised": ("stale", {"echolot init"}),
    "conflict": ("differs", {"echolot init --all"}),
    "differs": ("differs", {"echolot init --all"}),
    "unreadable": ("unreadable", set()),
}


@pytest.mark.parametrize("case", sorted(ADVICE))
def test_the_one_line_and_the_full_section_give_the_same_advice(
        tmp_path: Path, case: str) -> None:
    """One stale file beside one customised read `init` on one and `--all` on the other.

    `--all` there would have overwritten the customised file to bring the
    stale one up to date. Both renderings now read one assessment; this holds
    them to it across every state a layer can be in.
    """
    _layer(tmp_path, case)
    verdict, line = layer.one_line(tmp_path)
    full = io.StringIO()
    with contextlib.redirect_stdout(full):
        said = layer.print_status(tmp_path)
    want_verdict, want_commands = ADVICE[case]
    check(f"{case}: the one line's verdict", verdict == want_verdict, verdict)
    check(f"{case}: the full section's verdict is the same", said == verdict,
          f"{said} against {verdict}")
    check(f"{case}: the one line advises {sorted(want_commands) or 'nothing'}",
          _commands(line) == want_commands, line)
    check(f"{case}: the full section advises the same",
          _commands(full.getvalue()) == want_commands, full.getvalue())


def test_init_lists_the_files_all_would_overwrite_and_not_the_customised_ones(
        tmp_path: Path) -> None:
    """What the skill shows the human before asking about `--all`."""
    _layer(tmp_path, "conflict")
    said = init(tmp_path)
    check("the edited file is listed as kept",
          "≠ .claude/skills/echolot/SKILL.md (already there and conflict" in said, said)
    check("and `--all` is named with the reason to ask", "echolot init --all" in said
          and "ask" in said, said)

    other = tmp_path / "customised-only"
    other.mkdir()
    _layer(other, "stale+customised")
    said = init(other)
    check("a customised file is kept without offering `--all` for it",
          "--all" not in said, said)


# --- declining Claude Code --------------------------------------------------

def test_declining_claude_code_survives_a_plain_init(tmp_path: Path) -> None:
    """`init --for cursor`, then the plain `init` a person runs to update."""
    init(tmp_path, "--for", "cursor")
    check("the layer stayed out", not (tmp_path / ".claude").exists())
    init(tmp_path)
    check("a plain init kept it out", not (tmp_path / ".claude").exists())
    check("and kept the choice", hosts.load_choice(tmp_path) == ["cursor"],
          hosts.load_choice(tmp_path))
    check("the pointer it chose is still there",
          (tmp_path / ".cursor" / "rules" / "echolot.mdc").exists())

    # `--for` still decides when it is given.
    init(tmp_path, "--for", "claude,cursor")
    check("--for overrides the saved choice", (tmp_path / ".claude").is_dir())
    check("and is saved in its place",
          hosts.load_choice(tmp_path) == ["claude", "cursor"],
          hosts.load_choice(tmp_path))


def test_the_picker_starts_from_the_saved_choice(tmp_path: Path) -> None:
    """On a terminal the question is asked again — from the last answer."""
    init(tmp_path, "--for", "cursor")
    (tmp_path / "GEMINI.md").write_text("# ours\n", encoding="utf-8")
    start = hosts.starting_set(tmp_path)
    check("the saved choice, not detection", [h.key for h in start] == ["cursor"],
          [h.key for h in start])
    screen = io.StringIO()
    with mock.patch("builtins.input", return_value=""):
        picked = hosts.pick(start, stream=screen,
                            found={h.key for h in hosts.detect(tmp_path)})
    check("Enter keeps the saved choice", [h.key for h in picked] == ["cursor"],
          [h.key for h in picked])
    gemini = next(ln for ln in screen.getvalue().splitlines() if "Gemini CLI" in ln)
    check("a client found since is shown, unticked", "·" in gemini and "(found)" in gemini,
          gemini)


# --- the door a project chose -----------------------------------------------

def test_a_project_that_declined_claude_code_is_not_sent_to_it(tmp_path: Path) -> None:
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    init(tmp_path, "--for", "cursor")
    traces = tmp_path / ".echolot" / "traces"
    traces.mkdir(parents=True)
    (traces / "coldStart_iter000.perfetto-trace").write_bytes(b"x")

    code, said = run(tmp_path, "hunt", "cold start 3 s → 7 s", "--since", "unknown")
    check("hunt opens", code == 0, said)
    check("and names the door this project has", "/echolot" not in said
          and "echolot guide hunt" in said, said)

    aged = hunt_mod.load(tmp_path)
    aged["touched_at"] = (datetime.now(timezone.utc)
                          - timedelta(days=3)).isoformat(timespec="seconds")
    hunt_mod.save(tmp_path, aged)
    (traces / "coldStart_iter001.perfetto-trace").write_bytes(b"x")
    st = project_state(tmp_path)
    check("an old investigation with history asks", next_kind(st) == "resume-or-new",
          next_kind(st))
    check("through the guide, not a slash command it does not have",
          "/echolot" not in next_step(st) and "echolot guide" in next_step(st),
          next_step(st))


# --- what init prints for a person to finish by hand -----------------------

def test_the_agents_md_section_can_be_pasted_as_printed(tmp_path: Path) -> None:
    """Pasted as shown, the next `init` must find a section it can keep current."""
    own = tmp_path / "AGENTS.md"
    own.write_text("# our rules\n\nRun the linter first.\n", encoding="utf-8")
    said = init(tmp_path, "--for", "agents")
    check("the file was left alone",
          own.read_text(encoding="utf-8") == "# our rules\n\nRun the linter first.\n")
    check("the whole section is printed, both markers included",
          hosts.MARKER in said and hosts.END_MARKER in said, said)
    section = said[said.index(hosts.MARKER):said.index(hosts.END_MARKER)
                   + len(hosts.END_MARKER)]
    check("exactly the section init itself writes",
          section + "\n" == hosts.BODY, section)

    own.write_text(own.read_text(encoding="utf-8") + "\n" + section + "\n",
                   encoding="utf-8")
    what, _ = hosts.write_stub(tmp_path, hosts.BY_KEY["agents"])
    check("the next init keeps it current rather than leaving it as edited",
          what == "current", what)


def test_init_outside_a_git_root_says_what_it_did_not_write(tmp_path: Path) -> None:
    said = init(tmp_path)
    check("no .gitignore appears outside a checkout",
          not (tmp_path / ".gitignore").exists())
    check("and init says so, with both lines to add",
          ".gitignore not written" in said
          and "/.echolot/" in said and "/local.yml" in said, said)

    # Already covered by a .gitignore here: nothing to say.
    quiet = tmp_path / "covered"
    quiet.mkdir()
    (quiet / ".gitignore").write_text(".echolot/\nlocal.yml\n", encoding="utf-8")
    check("nothing said when there is nothing to add",
          ".gitignore" not in init(quiet))

    # At a git root it is written, as before.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    said = init(repo)
    check("a git root gets the lines written",
          "/.echolot/" in (repo / ".gitignore").read_text(encoding="utf-8"), said)


# --- the hunt, opened first and closed last --------------------------------

def _report(fired: bool) -> dict:
    rows = [{"location": "inflate", "runs": "3/3", "count": 1.0,
             "self_ms": 120.0}] if fired else []
    return {"schema": 1, "runs": 3, "window": {"duration_ms": 1000.0},
            "config": {"sha": "aaaa", "defaults": False}, "environment": {},
            "summary": {"detectors_run": 1, "detectors_fired": int(fired),
                        "fired_ids": ["main_thread_block"] if fired else []},
            "detectors": [{"id": "main_thread_block", "title": "", "why": "",
                           "rows": rows, "params": {}, "params_source": "default",
                           "error": None}]}


def test_an_investigation_opened_first_is_what_compare_and_the_next_visit_read(
        tmp_path: Path) -> None:
    """The sequence echolot-hunt.md prescribes, end to end in the CLI.

    Open before anything is recorded, let two rounds of `analyze` file their
    reports, compare the two with no arguments the way the hunter does, close
    with the conclusion — and the next visit, days later, is not asked about
    work that is over.
    """
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    code, said = run(tmp_path, "hunt", "cold start 3 s → 7 s", "--since", "the tab redesign")
    check("the investigation opens", code == 0 and "opened #1" in said, said)

    out = tmp_path / ".echolot" / "out"
    out.mkdir(parents=True)
    for fired in (False, True):         # two rounds of `analyze`
        (out / "report.json").write_text(json.dumps(_report(fired)), encoding="utf-8")
        check("each round's report is filed under it",
              hunt_mod.record_report(tmp_path, out) is not None)

    code, said = run(tmp_path, "compare")
    check("a bare compare has its two rounds", code == 0, said)
    check("and the row that appeared is in it", "inflate" in said, said)

    code, said = run(tmp_path, "hunt", "--done",
                     "inflate on the main thread — confidence medium")
    check("--done closes it", code == 0 and "concluded" in said, said)
    code, said = run(tmp_path, "hunt")
    check("bare hunt does not call a closed one open",
          "No investigation is open" in said and "Open investigation" not in said, said)

    h = hunt_mod.load(tmp_path)
    h["touched_at"] = (datetime.now(timezone.utc)
                       - timedelta(days=9)).isoformat(timespec="seconds")
    hunt_mod.save(tmp_path, h)
    st = project_state(tmp_path)
    st["layer_verdict"] = "current"     # the layer is not what this is about
    check("days later, a closed hunt is not offered to carry on",
          next_kind(st) == "hunt", next_kind(st))


def _read(rel: str) -> str:
    return (CLAUDE_DIR / rel).read_text(encoding="utf-8")


def test_the_hunt_command_opens_before_anything_is_recorded_and_closes_after() -> None:
    """The order in the text is the order the agent follows."""
    text = _read("commands/echolot-hunt.md")
    opens = text.find('echolot hunt "<')
    collects = text.find("echolot collect")
    run_section = text.find("## The run")
    closes = text.find("echolot hunt --done")
    check("the command opens an investigation", opens >= 0)
    check("with the change", "--since" in text[opens:text.find("\n", opens)])
    check("before it records anything", 0 <= opens < collects, (opens, collects))
    check("and before it hands the work over", opens < run_section)
    check("and closes it once the agent is back", closes > run_section, closes)


def test_the_skill_says_what_hunt_with_words_means_once() -> None:
    skill = _read("skills/echolot/SKILL.md")
    defined = [ln for ln in skill.splitlines() if ln.startswith("- `/echolot hunt <words>`")]
    check("`/echolot hunt <words>` has one definition", len(defined) == 1, defined)
    row = next(ln for ln in skill.splitlines() if ln.startswith("| `hunt`"))
    check("the hunt row says the investigation is opened and closed",
          "opens the investigation" in row and "closes" in row, row)


def test_the_hunter_works_inside_the_investigation_and_does_not_open_one() -> None:
    hunter = _read("agents/perf-hunter.md")
    check("perf-hunter never opens an investigation", 'echolot hunt "' not in hunter)
    check("or closes one", "echolot hunt --done" not in hunter)


# --- help that lists every choice -------------------------------------------

def _help(verb: str) -> str:
    import argparse
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return " ".join(action.choices[verb].format_help().split())
    raise AssertionError("no subcommands")


def test_the_help_lists_every_guide_topic_and_every_client() -> None:
    guide = _help("guide")
    missing = [t for t in guide_topics() if t not in guide]
    check("guide --help names every topic", not missing, missing)
    init_help = _help("init")
    missing = [h.key for h in hosts.HOSTS if h.key not in init_help]
    check("init --help names every client --for takes", not missing, missing)
