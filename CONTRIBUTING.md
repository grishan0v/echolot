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
ruff check echolot tests action   # the linter, which CI runs beside pytest
```

`doctor` stays dependency-free: it walks the same list itself, because it runs
on a user's laptop where pytest is not installed.

A new detector takes more than a new `.sql` file; the checklist is in
[docs/detectors.md](docs/detectors.md#adding-a-detector). The README's
pictures are drawn by a script, and [docs/assets/README.md](docs/assets/README.md)
says how.

## The documentation site

`docs/` is also published as a site, with search and a sidebar, to GitHub
Pages from `main`: <https://grishan0v.github.io/echolot/>. The pages stay Markdown written for GitHub and read the
same there. On the way to the site, [docs/site.py](docs/site.py) rewrites the
three things in them that are GitHub's: the navigation line under a title,
links that leave `docs/`, and `README.md` as a folder's first page.
[mkdocs.yml](mkdocs.yml) holds the site's configuration and its nav.

```bash
pip install -e '.[docs]'
python docs/site.py && zensical build --strict   # what CI runs on a pull request
python docs/site.py && zensical serve            # to read it; run site.py again after an edit
```

A page uses only what renders in both places. GitHub's callouts
(`> [!NOTE]`) do; admonitions (`!!! note`) and content tabs (`=== "tab"`) show
as raw text on GitHub and are not used. Anchors are made the way GitHub makes
them, so a link to `page.md#a-heading` lands in both places, and the strict
build fails when it does not. A new page goes into a group in
`docs/README.md` and into the nav in `mkdocs.yml`; `tests/test_docs_site.py`
fails until both have it.

## Pull requests

A change reaches `main` through a pull request whose base is `main`. The
pull request template lists what it is checked against.

The title opens with the kind of change: `feat:`, `fix:`, `docs:`, `build:`,
`ci:`, `test:`, `refactor:` or `chore:`, with a scope if it helps,
`fix(report): …`. A workflow labels the pull request from it, and the release
notes are grouped by that label; [docs/publishing.md](docs/publishing.md#how-the-notes-are-grouped)
has the sections.
