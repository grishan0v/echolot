#!/usr/bin/env python3
"""The plugin: four thin skills that Claude Code and Codex both load.

The `.claude/` layer is written for Claude Code: a skill that hands work on
through the Skill tool, asks with AskUserQuestion, and calls `perf-hunter` by
name. Codex loads a plugin's skills and nothing else, and turns a command into
a skill only below 4,000 bytes, so today's layer packaged as a plugin lost two
of its four entry points and the subagent there (#188). A second set of texts
for Codex would drift from the first within two releases (#5).

So the plugin is thin. Each skill says what to run and takes the rest from
the installed package: `echolot guide <topic>` prints the same files the
layer is made of. What is held here is what keeps it that way: the size, the
words, the routing, and that every topic a skill names exists.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

import echolot
from echolot import hosts, layer, state
from echolot.main import main
from tests.support import check

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "echolot"
SKILLS = ("echolot", "echolot-setup", "echolot-hunt", "echolot-reflect")
# Codex turns a larger command into nothing, and a skill this long has stopped
# being a door: it has become a copy of what the guide prints.
MAX_BYTES = 4000
# Names that exist in Claude Code alone. A skill both hosts load cannot lean
# on them, and OpenAI's review asks for skill texts without them (#188).
CLAUDE_ONLY = ("AskUserQuestion", "Skill tool", "perf-hunter", "run_in_background",
               "Task tool", "subagent_type", "CLAUDE.md")


def _skill(name: str) -> tuple[dict, str]:
    text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{name}: no frontmatter"
    _, head, body = text.split("---", 2)
    return yaml.safe_load(head), body


def test_the_plugin_is_the_four_skills_and_a_manifest():
    found = sorted(p.parent.name for p in (PLUGIN / "skills").glob("*/SKILL.md"))
    check("the plugin carries the four entry points", found == sorted(SKILLS), found)
    extra = [p.name for p in PLUGIN.iterdir() if p.name not in (".claude-plugin", "skills")]
    check("no commands/ or agents/ beside the skills: Codex drops them", not extra, extra)


def test_the_manifest_names_the_plugin_and_the_package_version():
    """One version for the plugin and the CLI it drives.

    A skill names verbs and topics that a given release has; the plugin says
    which release that is. The release that moves the version moves it here
    too, or this fails.
    """
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    check("the plugin is called echolot", manifest.get("name") == "echolot", manifest)
    check("the plugin's version is the package's",
          manifest.get("version") == echolot.__version__,
          (manifest.get("version"), echolot.__version__))
    check("it says the CLI is installed separately",
          "pipx install echolot" in manifest.get("description", ""), manifest)


@pytest.mark.parametrize("name", SKILLS)
def test_each_skill_is_thin_and_says_what_it_is(name):
    meta, body = _skill(name)
    size = len((PLUGIN / "skills" / name / "SKILL.md").read_bytes())
    check(f"{name} is under {MAX_BYTES} bytes", size < MAX_BYTES, size)
    check(f"{name}: its name is its directory", meta.get("name") == name, meta)
    description = meta.get("description") or ""
    check(f"{name}: a description, within OpenAI's 1,024 characters",
          0 < len(description) <= 1024, len(description))
    check(f"{name}: a body", body.strip(), body)


@pytest.mark.parametrize("name", SKILLS)
def test_each_skill_uses_words_either_host_can_follow(name):
    _, body = _skill(name)
    used = [w for w in CLAUDE_ONLY if w in body]
    check(f"{name} names nothing Claude Code alone has", not used, used)


@pytest.mark.parametrize("name", SKILLS)
def test_each_skill_hands_over_to_a_guide_the_package_prints(name):
    """The knowledge is printed by the installed echolot, never copied here."""
    _, body = _skill(name)
    named = set(re.findall(r"echolot guide(?: ([a-z]+))?", body))
    check(f"{name} sends the model to `echolot guide`", named, body[:200])
    topics = layer.guide_topics()
    missing = [t for t in named if t and t not in topics]
    check(f"{name} names guide topics that exist", not missing, missing)


def test_the_door_answers_every_word_next_can_say():
    """A word with no row is a state the door leaves the model to guess at."""
    _, body = _skill("echolot")
    rows = set(re.findall(r"^\| `([a-z-]+)` \|", body, re.M)) - {"next"}   # the header
    check("every `next` word has a row", rows == set(state.NEXT_KINDS),
          (sorted(set(state.NEXT_KINDS) - rows), sorted(rows - set(state.NEXT_KINDS))))
    check("the door installs no .claude/ layer", "echolot init --for plugin" in body, body)


def test_the_hunt_hands_the_loop_to_a_clean_subagent_and_waits():
    """What the Codex spike on #188 found: the subagent is given the whole
    conversation unless told otherwise, and a turn that ends while it runs
    may never be followed by the one that reads its answer."""
    _, body = _skill("echolot-hunt")
    check("the subagent starts with none of this conversation",
          "none of this conversation" in body, body)
    check("the skill says to wait for it", "wait for its conclusion" in body, body)
    check("its instructions are the loop the package prints",
          "echolot guide loop" in body, body)
    check("the investigation is closed every time", "echolot hunt --done" in body, body)


@pytest.mark.parametrize("name", SKILLS)
def test_each_skill_has_the_metadata_codex_reads(name):
    """`agents/openai.yaml`: shown in Codex's skill list, and who may trigger it.

    Only the door is picked by a question's words; the other three are
    reached through it, so a question about slowness always comes in at the
    door. `products` keeps the skills out of ChatGPT's chat, where there is
    no shell to run echolot in.
    """
    data = yaml.safe_load(
        (PLUGIN / "skills" / name / "agents" / "openai.yaml").read_text("utf-8"))
    interface = data.get("interface") or {}
    check(f"{name}: a display name", interface.get("display_name"), data)
    check(f"{name}: a short description",
          interface.get("short_description"), data)
    policy = data.get("policy") or {}
    check(f"{name}: only the policy keys OpenAI's validation accepts",
          set(policy) <= {"allow_implicit_invocation", "products"}, policy)
    check(f"{name}: Codex only", policy.get("products") == ["CODEX"], policy)
    implicit = policy.get("allow_implicit_invocation", True)
    check(f"{name}: {'the door is' if name == 'echolot' else 'reached through the door, not'}"
          f" picked by a question's words", implicit is (name == "echolot"), policy)


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code is not installed")
def test_claude_code_accepts_the_plugin():
    run = subprocess.run(["claude", "plugin", "validate", str(PLUGIN)],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr


# --- the topics the skills name -----------------------------------------------------

@pytest.mark.parametrize("topic,source", [
    ("loop", layer.HUNTER),
    ("setup", layer.COMMANDS_DIR / "echolot-setup.md"),
    ("reflect", layer.COMMANDS_DIR / "echolot-reflect.md"),
    ("report", layer.REFERENCES_DIR / "report.md"),
])
def test_a_topic_prints_the_layer_file_it_is_read_from(topic, source, capsys):
    """The same text a Claude Code session reads, and no frontmatter."""
    assert main(["guide", topic]) == 0
    out = capsys.readouterr().out.strip()
    check(f"`guide {topic}` prints {source.name}",
          out == layer.guide_text(source), out[:200])
    check(f"`guide {topic}` has no frontmatter", not out.startswith("---"), out[:80])


def test_every_topic_ships_in_the_package():
    """Read from inside the package, so the wheel carries every one of them."""
    package = Path(echolot.__file__).resolve().parent
    outside = [t for t, p in layer.guide_topics().items()
               if package not in p.resolve().parents]
    check("every topic's file is inside the package", not outside, outside)


# --- init, for a project whose skills come with the plugin ----------------------------

def test_init_for_the_plugin_writes_the_gitignore_and_no_layer(tmp_path, capsys):
    """What the door runs when `next` says `init`.

    The traces must still stay out of git, and only `init` writes those lines;
    the `.claude/` layer must not go in, or Claude Code loads the skills
    twice. Saved as a choice, so `next` stops asking for `init` (#189).
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert main(["init", "--into", str(tmp_path), "--for", "plugin", "--no-input"]) == 0
    out = capsys.readouterr().out
    check("no .claude/ in the project", not (tmp_path / ".claude").exists(), out)
    ignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    check("the traces are ignored", "/.echolot/" in ignore, ignore)
    check("the choice is saved", hosts.load_choice(tmp_path) == ["plugin"],
          hosts.load_choice(tmp_path))
    check("no pointer file for the plugin", not (tmp_path / "AGENTS.md").exists(), out)

    st = state.project_state(tmp_path)
    check("the layer reads as provided", st["layer_verdict"] == "opted-out", st["layer_line"])
    check("and says by whom", "the skills come with the echolot plugin" in st["layer_line"],
          st["layer_line"])
    check("`next` no longer asks for init", state.next_kind(st) != "init", state.next_kind(st))
    check("the next step names the plugin's door",
          state.next_step(st).startswith("the plugin's echolot skill"), state.next_step(st))


def test_the_plugin_is_never_found_by_looking_at_the_tree(tmp_path):
    """Nothing in a project says a plugin is installed: it is chosen by name."""
    for evidence in (".claude", ".codex", "AGENTS.md"):
        (tmp_path / evidence).mkdir(exist_ok=True)
    found = {h.key for h in hosts.detect(tmp_path)}
    check("detection never picks the plugin", "plugin" not in found, found)
