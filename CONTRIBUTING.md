# Contributing

[README](README.md) · [Docs index](docs/README.md)

## Before writing code

Every improvement and every bug is a GitHub issue, and every open issue is a
card on the [Echolot Roadmap](https://github.com/users/grishan0v/projects/1).
[docs/planning.md](docs/planning.md) says how an issue is filed, how the board
sorts it, and which decisions an idea is checked against first.

Questions, and ideas that are not ready to be an issue yet, go to
[Discussions](https://github.com/grishan0v/echolot/discussions).

Issues, pull requests and Discussions all follow the
[Code of Conduct](CODE_OF_CONDUCT.md). A breach of it goes privately to the
address it gives, the same one [SECURITY.md](SECURITY.md) names.

## Working on the tool

```bash
pip install -e '.[dev]'
pytest                       # every check, including the ones doctor runs
pytest -n auto               # the same on every core, the way CI runs it
pytest -k uninstrumented     # one detector's claims, by name
ruff check echolot tests action   # the linter, which CI runs beside pytest
vulture                      # code nothing calls; CI runs it too
```

`doctor` stays dependency-free: it walks the same list itself, because it runs
on a user's laptop where pytest is not installed.

`vulture` reads its settings from `[tool.vulture]` in `pyproject.toml`. When
it reports a name that is used where it cannot see, such as a field set on a
protobuf message or a method the standard library calls by name, add the name
to `vulture_whitelist.py` with the reason. `vulture --min-confidence 60` also
lists the functions nothing names, which need checking by hand.

A new detector takes more than a new `.sql` file; the checklist is in
[docs/detectors.md](docs/detectors.md#adding-a-detector). The README's
pictures are drawn by a script, and [docs/assets/README.md](docs/assets/README.md)
says how.

### Comments and docstrings

A docstring says what holds: the invariant, in one paragraph. The issue a
change fixes goes in the commit message, where `git log -S` and `git blame`
find it, and stays out of the code. A story of a past bug stays in a comment
only where the code looks wrong without it: the reader would otherwise
"simplify" it back into the bug.

A comment does not count what can be counted. "The detectors whose
`@identity` names a second column" stays true when a detector lands; "seven
of the fifteen" did not, and `tests/test_docs.py` fails on a stated total of
shipped detectors that differs from `echolot/sql/detectors/`.

Files that do not follow this yet get it when a pull request touches them,
not in one sweep over the tree.

## The documentation site

`docs/` is also published as a site, with search, a tab for each group and a sidebar, to GitHub
Pages from `main`: <https://grishan0v.github.io/echolot/>. The pages stay Markdown written for GitHub and read the
same there. On the way to the site, [docs/site.py](docs/site.py) rewrites the
three things in them that are GitHub's: the navigation line under a title,
links that leave `docs/`, and `README.md` as a folder's first page.
[mkdocs.yml](mkdocs.yml) holds the default site's configuration and its nav;
[docs/languages.yml](docs/languages.yml) registers all available translations
(`docs/<lang>/` and `README.<lang>.md`), for which `docs/site.py` generates
`build/site-docs-<lang>/` and `build/mkdocs-<lang>.yml`. Every language gets
the theme from `mkdocs.yml`; only the language and the words on the
light/dark switch change. The logo and the favicon are
`docs/assets/site-icon.png`, drawn by [docs/assets/icon.py](docs/assets/icon.py),
and [docs/overrides/main.html](docs/overrides/main.html) adds the Open Graph
and Twitter tags a shared link unfurls from, with the repository's card,
`docs/assets/social-preview.png`.

```bash
pip install -e '.[docs]'
python docs/site.py && zensical build --strict   # default language (en)
zensical build --config-file build/mkdocs-ru.yml --strict  # translation (ru)
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

## LLM assistance

A change written with an LLM is reviewed here on the same terms as any other.
What it has to carry is a plain account of how it was made, and a person who
answers for it.

**Say what the model did.** The pull request template has a section for it.
It says either that no LLM was used, or which tool ran one, which model, at
what effort (the reasoning level the tool was set to, `default` if none was
set) and which parts the model wrote or changed: the code, the tests, the
docs, the description itself. A model that only finished a line or fixed a
typo still counts; one sentence says so.

**The author is the person, not the model.** Whoever opens the pull request
has read the whole diff and can say why each line is there. They ran `pytest`
and `ruff` themselves instead of taking a model's word that they pass, and
they answer the questions asked in review themselves, rather than handing them
to a model and pasting its answer back.

The reason is where the cost of a change lands. A pull request a model wrote
from the text of an issue looks finished: it has tests, a description and a
confident tone. Whether it is right is found out in review, often by reading
it as closely as it would take to write the change, and it is the reviewer who
notices that a test asserts what the code does rather than what the issue
asked for, or that a detector was changed without a trace ever being opened.
Knowing that a model wrote a part tells the reviewer where to read harder, and
an author who understands the diff is someone the review can talk to.

A pull request with this section left empty, or one made of generated code its
author cannot explain, may be closed without a review. That closes the pull
request, not the idea: the issue stays open, and a change that answers for
itself is welcome.

The same holds for an issue. A model may help with the words, but the command,
the output and the steps in a bug report come from a run on the reporter's own
machine, and a bug that a model found by reading the source, with no run
behind it, is not a bug report. The issue forms ask about LLM assistance too.

The `disclosure` workflow reads the section on every pull request opened from
outside the project and fails when it is missing, when neither answer is
ticked, when a model was used and the tool, the model, the effort or what it
did is left out, or when the last box is not ticked. It can see whether the
section was answered, not whether the answer is true; that stays with the
author. Pull requests from the repository's owner, members and collaborators,
and from apps installed on it such as dependabot, are not checked.
