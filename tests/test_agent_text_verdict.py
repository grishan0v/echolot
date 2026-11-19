"""The agent's text and the verdict the check accepts.

`echolot hunt --done --verdict` is only as good as the form an agent is shown,
so the example in the subagent's guide is held to the check itself, and every
text that closes a hunt gives the same way to do it. The flags those texts
name are held to the parser by `test_agent_text_commands.py`.

    python -m pytest tests/test_agent_text_verdict.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from echolot import layer
from echolot import verdict as verdict_mod
from tests.support import check
from tests.test_hunt_verdict import report

# --- the agent's text --------------------------------------------------------

AGENT = layer.CLAUDE_DIR / "agents" / "perf-hunter.md"


def _example(text: str) -> dict[str, Any]:
    m = re.search(r"```json\n(\{.*?\})\n```", text, re.S)
    assert m, "the verdict example is not in the text"
    return json.loads(m.group(1))


def test_the_verdict_the_hunter_is_shown_is_one_echolot_accepts() -> None:
    """The example is the form an agent copies. Held against reports that hold
    the rows it cites, it must pass the check, and its fields are the ones
    the check knows."""
    example = _example(AGENT.read_text(encoding="utf-8"))
    rows = report(uninstrumented_cpu=[e["location"] for c in example["causes"]
                                      for e in c["evidence"] if "detector" in e],
                  markers_=[e["location"] for c in example["causes"]
                            for e in c["evidence"] if "detector" not in e]
                  + [example["inside"]["location"]])
    check("sound against reports that hold its rows",
          verdict_mod.validate(example, {n: rows for n in range(1, 10)}) == [],
          verdict_mod.validate(example, {n: rows for n in range(1, 10)}))
    check("it shows an estimate and a confidence, which are optional",
          example["causes"][0].get("expected_ms") and example["causes"][0].get("confidence"))


def test_every_text_that_closes_a_hunt_gives_the_verdict_form() -> None:
    """The guide, the command and the plugin's skill say the same close."""
    plugin = Path(__file__).resolve().parent.parent / "plugins" / "echolot" / "skills" \
        / "echolot-hunt" / "SKILL.md"
    for where, path in (("guide", layer.GUIDE_DIR / "hunt.md"),
                        ("command", layer.CLAUDE_DIR / "commands" / "echolot-hunt.md"),
                        ("plugin skill", plugin)):
        flat = " ".join(path.read_text(encoding="utf-8").split())
        check(f"the {where} closes with a verdict", "echolot hunt --done --verdict" in flat, flat)
        check(f"the {where} still has the text form", 'echolot hunt --done "' in flat, flat)
        check(f"the {where} says what to do when it is refused",
              "refused" in flat and "nothing" in flat, flat)
