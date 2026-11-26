"""The values a flag's help states, apart from the modules that use them (#561).

Every command builds the whole parser before it runs, and the parser reads
these: `compare --floor-ms` defaults to the floor, `--noise` says how many
runs it needs, `status --next` lists the words it can print. They used to
live in `compare`, `noise` and `state`, so building the parser imported
those, and with them most of the package — `echolot --version` and
`echolot guide` paid for the delta engine and the trace_processor client
they never touch. Here they cost nothing to read. Each module they belong
to imports them back under the same name: `compare.FLOOR_MS`,
`noise.SPLITS` and `state.NEXT_KINDS` are what they were.

Nothing else belongs here. Nothing here may import from the package.
"""

from __future__ import annotations

# compare: what counts as movement worth a row. Absolute floor first, so a
# 4 ms wobble on a 6 ms slice is not "×1.7 slower"; relative floor second,
# so a 40 ms move on a 900 ms slice is not a finding either.
FLOOR_MS = 5.0
FLOOR_RATIO = 0.10

# compare: how many slice names on one side only each side prints; `--names`
# prints all.
NAMES_SHOWN = 10

# analyze --noise: how many random splits, and the seed they are drawn from.
# Two hundred put the share of splits with a row that holds in steps of half
# a percent and its 95th percentile on ten splits, and take about half a
# minute for fifteen runs of three hundred rows; the seed is the issue's
# number and nothing else.
SPLITS = 200
SEED = 569

# analyze --noise: the fewest runs a set needs: four a side, the fewest at
# which `stats.shift` judges a row at all whichever way the runs fall.
MIN_RUNS = 8

# status --next: the next step as one word — what `/echolot` in Claude Code
# switches on — and as the line a person reads, both from the same decision
# (`state.next_kind`). This is the vocabulary, not the routing order —
# `next_kind` is that. The order here is the one `status --help` lists, and
# it is also what keeps that help the same under Rich and under argparse at
# 80 columns: argparse breaks a line at a hyphen and Rich does not, so a
# hyphenated word that lands at a line's end renders two ways
# (tests/test_cli_help.py). Adding a word, check that test before settling
# where it goes.
NEXT_KINDS = ("upgrade", "init", "init-force", "doctor", "setup", "fix-config",
              "resume-or-new", "fix-settings", "hunt")
