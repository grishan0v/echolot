#!/usr/bin/env python3
"""The plugin: four thin skills that Claude Code, Codex and Cursor load.

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
import textwrap
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


def test_the_plugin_is_the_four_skills_a_manifest_and_a_readme():
    found = sorted(p.parent.name for p in (PLUGIN / "skills").glob("*/SKILL.md"))
    check("the plugin carries the four entry points", found == sorted(SKILLS), found)
    extra = [p.name for p in PLUGIN.iterdir()
             if p.name not in (".claude-plugin", ".cursor-plugin", "skills", "README.md")]
    check("no commands/ or agents/ beside the skills: Codex drops them", not extra, extra)


def test_the_readme_says_what_the_plugin_runs_writes_fetches_and_sends():
    """Claude's plugin directory blocks a plugin folder without a README of 40
    words or more, and its security scan asks that the README say everything
    the plugin runs, sends or fetches (#376)."""
    text = (PLUGIN / "README.md").read_text(encoding="utf-8")
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    check("40 words or more outside code blocks", len(prose.split()) >= 40,
          len(prose.split()))
    for word in ("**Runs**", "**Writes**", "**Fetches**", "**Sends**", "pipx install echolot"):
        check(f"the README says {word}", word in text, word)


def test_the_icon_is_one_the_directory_takes():
    """Claude's plugin directory reads .claude-plugin/icon.png when plugin.json
    names no icon: a square PNG or JPEG, 512 to 2048 px a side, under 2 MB. It
    takes the icon once, at the first submission, so a file it would refuse
    leaves the default icon for good (#380)."""
    icon = PLUGIN / ".claude-plugin" / "icon.png"
    head = icon.read_bytes()[:24]
    check("icon.png is a PNG", head[:8] == b"\x89PNG\r\n\x1a\n", head[:8])
    width, height = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    check("square", width == height, (width, height))
    check("512 to 2048 px a side", 512 <= width <= 2048, width)
    check("under 2 MB", icon.stat().st_size < 2 * 1024 * 1024, icon.stat().st_size)
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    check("plugin.json leaves the icon at its default path", "icon" not in manifest, manifest)


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


def test_the_manifest_gives_the_directory_its_listing_links():
    """Claude's plugin directory builds the listing from plugin.json at the
    tracked tag. Without `displayName` it called the plugin "Echolot", and it
    left the privacy policy and support empty (#384). The homepage is the
    documentation site: the listing names the repository on its own line."""
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    site = re.search(r"^site_url: (\S+)$", (ROOT / "mkdocs.yml").read_text("utf-8"),
                     re.MULTILINE).group(1)
    check("the name as it is written everywhere else",
          manifest.get("displayName") == "echolot", manifest.get("displayName"))
    check("the homepage is the documentation site", manifest.get("homepage") == site,
          (manifest.get("homepage"), site))
    check("support is the issue tracker",
          manifest.get("supportUrl") == "https://github.com/grishan0v/echolot/issues",
          manifest.get("supportUrl"))
    check("the privacy policy is docs/privacy.md, where the site puts it",
          manifest.get("privacyPolicyUrl") == site + "privacy/"
          and (ROOT / "docs" / "privacy.md").is_file(), manifest.get("privacyPolicyUrl"))


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


def _brief(text: str) -> str | None:
    """The brief block a text hands the loop, without its indentation."""
    m = re.search(r"```text\n(.*?)```", text, re.S)
    return textwrap.dedent(m.group(1)).strip() if m else None


def test_the_brief_is_handed_down_whole_and_the_loops_guide_stays_below():
    """Told where the brief was rather than given it, the main thread read
    `echolot guide`, `guide hunt` and `guide loop` before starting the loop in
    two live Codex runs: about 35,000 characters in the window the subagent is
    there to protect (#202). Now the brief is a block to fill in, the same in
    the skill a Claude Code session loads and in `echolot guide hunt`, which is
    what a Codex session reads: Codex does not list the hunt skill to the
    model at all."""
    _, body = _skill("echolot-hunt")
    guide = (layer.GUIDE_DIR / "hunt.md").read_text(encoding="utf-8")
    brief = _brief(body)
    check("the skill carries the brief", brief, body)
    check("the same brief as `echolot guide hunt`", brief == _brief(guide),
          f"{brief}\n---\n{_brief(guide)}")
    check("the brief starts the loop at its own guide",
          brief.startswith("Run `echolot guide loop` first"), brief)
    for where, text in (("skill", body), ("guide", guide)):
        check(f"the {where} tells the main thread to leave the loop's guide alone",
              "Do not run `echolot guide loop` yourself" in " ".join(text.split()), text)
    check("the skill no longer sends the main thread to `guide hunt` first",
          "Run `echolot guide hunt`" not in body, body)
    _, door = _skill("echolot")
    check("the door keeps a hunt away from the overview",
          "A hunt skips it" in " ".join(door.split()), door)


def test_the_hunt_takes_traces_set_aside_only_when_they_were_recorded_for_it():
    """`echolot hunt` moves every loose trace aside: most often the last round
    of an earlier question, or the one trace setup's probe captured. The skill
    took them all as evidence and recorded nothing new (#273). `echolot guide
    hunt` keeps the condition; so does the skill."""
    _, body = _skill("echolot-hunt")
    flat = " ".join(body.split())
    check("traces set aside count only if recorded for this question",
          "evidence only if the human recorded them for it before asking" in flat,
          body)
    check("and otherwise the hunt records its own",
          "No traces recorded for this question: `echolot collect" in flat, body)


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


# --- the marketplace ---------------------------------------------------------------

CURSOR_MANIFEST = PLUGIN / ".cursor-plugin" / "plugin.json"
CURSOR_MARKETPLACE = ROOT / ".cursor-plugin" / "marketplace.json"


def test_cursors_manifest_says_what_claudes_does():
    """Cursor reads `.cursor-plugin/plugin.json`, and Claude Code and Codex
    read `.claude-plugin/` (#204). One plugin, so one name, one version and
    one description: the release that moves Claude's version moves this one
    too, or this fails."""
    claude = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    cursor = json.loads(CURSOR_MANIFEST.read_text("utf-8"))
    for key in ("name", "displayName", "version", "description", "homepage",
                "repository", "license", "keywords"):
        check(f"{key} as in Claude's manifest", cursor.get(key) == claude.get(key),
              (key, cursor.get(key), claude.get(key)))
    check("without the links only Claude's directory reads: Cursor's schema "
          "refuses a key it does not name",
          not {"supportUrl", "privacyPolicyUrl"} & cursor.keys(), cursor)
    check("the author by name: Cursor's schema takes a name and an email",
          cursor.get("author") == {"name": claude["author"]["name"]}, cursor.get("author"))
    check("the logo is the icon Claude's directory reads",
          cursor.get("logo") == ".claude-plugin/icon.png"
          and (PLUGIN / cursor["logo"]).is_file(), cursor.get("logo"))
    check("the skills found by their folders, as the other hosts find them",
          "skills" not in cursor, cursor)


def test_cursors_marketplace_serves_the_plugin_folder():
    """What Cursor's team marketplaces import from the repository (#204). A
    source there is a folder and takes no ref, so it serves the default
    branch; the manifest in that folder wins over the entry."""
    m = json.loads(CURSOR_MARKETPLACE.read_text("utf-8"))
    check("the marketplace is called echolot", m.get("name") == "echolot", m)
    check("it names its owner", (m.get("owner") or {}).get("name"), m)
    entries = m.get("plugins") or []
    check("one plugin, echolot", [e.get("name") for e in entries] == ["echolot"], entries)
    source = entries[0].get("source")
    check("from the folder the plugin is kept in, with Cursor's manifest there",
          source == "plugins/echolot"
          and (ROOT / source / ".cursor-plugin" / "plugin.json").is_file(), source)
    check("the version is the manifest's alone", "version" not in entries[0], entries[0])


MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"


def test_the_marketplace_serves_the_plugin_from_this_versions_release_tag():
    """One file, and both hosts read it: `/plugin marketplace add
    grishan0v/echolot` in Claude Code, `codex plugin marketplace add
    grishan0v/echolot` in Codex (#144).

    The plugin is fetched at the release tag, so what a person installs is
    the plugin the CLI on PyPI was released with. Served from `main`, it
    would name topics and flags that no released echolot has yet. The
    release that moves the version moves the tag here too, or this fails.
    """
    m = json.loads(MARKETPLACE.read_text("utf-8"))
    check("the marketplace names its owner", (m.get("owner") or {}).get("name"), m)
    entries = m.get("plugins") or []
    check("one plugin, echolot", [e.get("name") for e in entries] == ["echolot"], entries)
    source = entries[0].get("source") or {}
    check("fetched from this repository", source.get("source") == "git-subdir"
          and source.get("url") == "https://github.com/grishan0v/echolot.git", source)
    check("from the directory the plugin is kept in",
          (ROOT / source.get("path", "") / ".claude-plugin" / "plugin.json").is_file(),
          source)
    check("at this version's release tag", source.get("ref") == f"v{echolot.__version__}",
          (source.get("ref"), echolot.__version__))
    check("the version is plugin.json's alone: set in both, Claude Code "
          "takes plugin.json's without a word", "version" not in entries[0], entries[0])
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    check("the homepage is plugin.json's", entries[0].get("homepage") == manifest.get("homepage"),
          (entries[0].get("homepage"), manifest.get("homepage")))


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code is not installed")
def test_claude_code_accepts_the_marketplace():
    run = subprocess.run(["claude", "plugin", "validate", str(ROOT)],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr


def test_a_layer_left_from_an_earlier_init_is_said_and_not_asked_for(tmp_path, capsys):
    """The plugin chosen on a project that already has the `.claude/` layer.

    `init` keeps its hands off the layer then, so its stale files used to
    send `next` to `init` for good. It is said instead: Claude Code loads
    that copy beside the plugin's skills, and the files may be what
    teammates without the plugin work from, so removing them is the human's
    call (#144).
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert main(["init", "--into", str(tmp_path), "--for", "claude", "--no-input",
                 "--no-doctor"]) == 0
    agent = tmp_path / ".claude" / "agents" / "perf-hunter.md"
    agent.write_text(agent.read_text(encoding="utf-8") + "\n# older\n", encoding="utf-8")
    layer.write_manifest(tmp_path / ".claude", {"agents/perf-hunter.md": layer.sha(agent)})
    assert main(["init", "--into", str(tmp_path), "--for", "plugin", "--no-input",
                 "--no-doctor"]) == 0
    capsys.readouterr()

    check("the layer's files are still there", agent.exists(), agent)
    st = state.project_state(tmp_path)
    check("read as not kept current", st["layer_verdict"] == "opted-out", st["layer_line"])
    check("and said, with what to do",
          "Claude Code loads this copy beside them" in st["layer_line"], st["layer_line"])
    check("`next` stops asking for init", state.next_kind(st) != "init", state.next_kind(st))
