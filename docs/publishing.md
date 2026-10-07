# Publishing to PyPI

[← Docs index](README.md) · [README](../README.md)

A release is a git tag. `.github/workflows/publish.yml` builds the sdist and
wheel on the tag, uploads them with PyPI Trusted Publishing — no API token is
stored anywhere in the repository or in GitHub secrets — and then creates the
GitHub Release for that tag: the same two files attached, notes listing the
pull requests merged since the previous tag by kind of change, a link to the
version on PyPI on top. The GitHub Release is created only after PyPI has accepted the upload, so
the two never disagree about which versions exist.

## One-time setup on PyPI

This was completed before the first release. The project is already published
as `echolot`, and the `pypi` environment is configured for Trusted Publishing.
If the publisher ever needs to be recreated, register it with the following
values:

1. Sign in at <https://pypi.org> and open
   <https://pypi.org/manage/project/echolot/settings/publishing/>. The
   publisher of a project that already exists lives on the page of that
   project; the pending publishers in the account settings are for a name
   nobody has uploaded yet.
2. Under **Add a new publisher → GitHub**, fill in:

   | field | value |
   |---|---|
   | Owner | `grishan0v` |
   | Repository name | `echolot` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

   The environment name must match the `environment: name:` in the workflow.
   GitHub creates the environment on the first run, and here it carries no
   protection rules — nothing to configure. A rule added to it later, a
   required reviewer or a wait timer, would hold the upload on every tag.

## Before a release: what CI already checked

`.github/workflows/checks.yml` runs on every pull request into `main`, and on
`main` after a merge: `pytest` with the coverage gate, on 3.10 and 3.14 for a
pull request and on all five versions the classifiers claim for `main`,
written out again by hand; `ruff check echolot tests action` beside pytest on
3.14; and a `package` job that looks at the README three ways, builds the
artefacts and installs them. The `protect-main` ruleset requires two of those
to pass before a merge: `checks`, which passes only when every Python version
that ran passed, the linter with them, and `package`. A break that only 3.11,
3.12 or 3.13 has shows up on `main` after the merge, so look at the run there
before cutting a tag.

The `package` job is the one that matters at release time, and it exists
because a PyPI version number can never be reused. Not even after deletion. A
mistake found on a version tag costs a number; the same mistake found on the
pull request costs a commit.

What it checks, and why each is separate:

| step | what would otherwise reach PyPI |
|---|---|
| the version badge against the classifiers | a front page claiming Python versions nothing runs on |
| `readme_renderer` over README.md | image addresses the sanitiser strips, arriving as empty boxes |
| no relative links in README.md | hrefs that resolve against `pypi.org` and 404 |
| `python -m build` and `twine check` | broken packaging metadata |
| the sdist and the wheel, each installed into a clean environment and run from outside the checkout: the shipped files counted, then `echolot --help` and `doctor -q` | a package without its detectors, its guide or its `.claude/` layer — package-data an editable install never reads, so nothing else would notice it missing |

**`twine check` is not the render.** For a Markdown README it never opens the
file: its `_RENDERERS` table maps `text/markdown` to `None` with the comment
"Rendering cannot fail". What it validates is the packaging metadata, which is
worth validating and is not the same thing. The render is its own step and has
to install `readme_renderer[md]` first, because Markdown support is an extra
that neither twine nor `readme_renderer` itself depends on.

That distinction cost four releases: the licence badge pointed at a relative
`LICENSE` path, which resolves against `pypi.org` and does not exist there,
while every build stayed green.

## Cutting a release

The bump goes through a pull request like any other change, because the
`protect-main` ruleset requires one for every change to `main`. The tag comes
after, on the commit the merge put there.

```bash
# 1. on a branch, bump __version__ in echolot/__init__.py, "version" in
#    plugins/echolot/.claude-plugin/plugin.json and "ref" in
#    .claude-plugin/marketplace.json ("v" + the version), and open a pull
#    request into main (pyproject.toml reads the version from that attribute;
#    tests/test_plugin.py fails until the three agree). Past 0.10.0, the
#    action's `uses: grishan0v/echolot@...` lines in README.md and
#    docs/compare.md name the same tag; tests/test_action.py holds them
# 2. once it is merged, tag the merged commit — the tag must be "v" + that
#    version, the workflow checks
git switch main && git pull
git tag vX.Y.Z
git push origin vX.Y.Z
```

Watch the run under **Actions**. When it is green the package is at
<https://pypi.org/project/echolot/>, `pipx install echolot` works, and the
release is listed at <https://github.com/grishan0v/echolot/releases>. The
generated notes list the pull requests merged since the previous tag, by
title, in the sections [below](#how-the-notes-are-grouped) — edit them in the
GitHub UI if a version deserves a paragraph.

The same tag releases the plugin. The marketplace entry fetches
`plugins/echolot` at that tag, so until it is pushed the entry points at a tag
that does not exist yet, and an install fails; push it right after the merge.

It releases the GitHub Action as well: `uses: grishan0v/echolot@vX.Y.Z`
installs the echolot of that tag from the action's own checkout, so the
action and the tool it runs are always the same version.

A tag, and therefore a release, is a snapshot: it contains what was committed
before the tag was made and nothing after. To ship a fix, bump the version in
another pull request and tag again — an uploaded version number can never be
reused on PyPI, even after deletion.

## How the notes are grouped

GitHub writes the notes, and `.github/release.yml` gives it the sections: a
pull request goes under the first section whose label it carries.

| section | label | set by |
|---|---|---|
| Detectors | `detector` | a change to a file in `echolot/sql/detectors/` |
| Features | `enhancement` | a title opening with `feat:` or `perf:` |
| Fixes | `bug` | `fix:` |
| Documentation | `documentation` | `docs:` |
| Maintenance | `maintenance`, `dependencies`, `github_actions` | `build:`, `chore:`, `ci:`, `refactor:`, `style:`, `test:`; dependabot puts the other two on its own pull requests |
| Other | any other | a title with none of these types |

`.github/workflows/labels.yml` sets the labels when a pull request is opened,
again when its title changes, and once more when it is merged: the files it
ended with are the ones that decide `detector`. The type may carry a scope and
a `!`: `fix(report): …`, `feat!: …`. A title corrected from `feat:` to `fix:`
moves the pull request from Features to Fixes. A title without one of these
types gets no label from the workflow, and a label put on by hand stays.

`detector` goes on and stays on. A fix to a detector lands under Detectors,
because it changes what the report says about the same trace, and that is
what a reader of the notes most wants to know.

Documentation is `docs/` and the README. `echolot/guide/` and
`echolot/claude/` are what an agent reads, and they ship in the package, so a
change there is a feature or a fix.

A pull request in the wrong section is fixed on the pull request, before the
tag: correct the title, or the label. After the tag, edit the release's notes
in the GitHub UI.

The workflow runs on `pull_request_target`, so a pull request from a fork or
from dependabot gets its label too. That trigger's token can write, which is
why the workflow reads the title and the list of files and never checks out
the pull request's code; `tests/test_release_notes.py` holds it to that. The
labels do not decide the version: the bump pull request says why a release is
a minor or a patch.

## Checking the artefacts locally

The packaging half of the `package` job, useful before tagging. CI has already
run all five of its checks on the pull request, so this is a second look rather
than the gate.

```bash
python -m pip install --upgrade build twine
rm -rf dist && python -m build
python -m twine check dist/*
```

That verdict is on the packaging metadata, and for a Markdown README it is the
whole of it — the section above has the detail. `readme_renderer` arrives here
as one of twine's own dependencies, and its presence does not make this the
render either: without the `[md]` extra its Markdown renderer returns `None`
instead of HTML, which is why the workflow installs `readme_renderer[md]`
before rendering anything.

The three README checks are not repeated here. Each is a dozen lines of Python
that already live in `checks.yml`, and a second copy would drift from the first
within two releases. What they cover: the version badge against the
classifiers, the image addresses that have to survive the PyPI sanitiser, and
the absence of relative links — which resolve against `pypi.org`, and are why
README.md uses full GitHub URLs while `docs/*.md` link to each other
relatively.

## Manual upload, if ever needed

Trusted Publishing is the intended path. If a release has to be uploaded by
hand — the workflow is broken, or the account is being tested — create an API
token at <https://pypi.org/manage/account/token/> and:

```bash
python -m twine upload dist/*
# username: __token__
# password: the token
```

Once the project exists, scope the token to it rather than the whole account.
