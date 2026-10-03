# Contributing

[README](README.md) · [Docs index](docs/README.md)

## Before writing code

Every improvement and every bug is a GitHub issue, and every open issue is a
card on the [Echolot Roadmap](https://github.com/users/grishan0v/projects/1).
[docs/planning.md](docs/planning.md) says how an issue is filed, how the board
sorts it, and which decisions an idea is checked against first.

## Working on the tool

```bash
pip install -e '.[dev]'
pytest                       # every check, including the ones doctor runs
pytest -k uninstrumented     # one detector's claims, by name
ruff check echolot tests     # the linter, which CI runs beside pytest
```

`doctor` stays dependency-free: it walks the same list itself, because it runs
on a user's laptop where pytest is not installed.

A new detector takes more than a new `.sql` file; the checklist is in
[docs/detectors.md](docs/detectors.md#adding-a-detector). The README's
pictures are drawn by a script, and [docs/assets/README.md](docs/assets/README.md)
says how.

## Pull requests

A change reaches `main` through a pull request whose base is `main`. The
pull request template lists what it is checked against.

The title opens with the kind of change: `feat:`, `fix:`, `docs:`, `build:`,
`ci:`, `test:`, `refactor:` or `chore:`, with a scope if it helps,
`fix(report): …`. A workflow labels the pull request from it, and the release
notes are grouped by that label; [docs/publishing.md](docs/publishing.md#how-the-notes-are-grouped)
has the sections.
