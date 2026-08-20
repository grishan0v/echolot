# Publishing to PyPI

[← Docs index](README.md) · [README](../README.md)

A release is a git tag. `.github/workflows/publish.yml` builds the sdist and
wheel on the tag, uploads them with PyPI Trusted Publishing — no API token is
stored anywhere in the repository or in GitHub secrets — and then creates the
GitHub Release for that tag: the same two files attached, notes generated from
the commits since the previous tag, a link to the version on PyPI on top. The
GitHub Release is created only after PyPI has accepted the upload, so the two
never disagree about which versions exist.

## One-time setup on PyPI

Done once, before the first tag. The project does not exist on PyPI yet, so
this is registered as a *pending* publisher; the first successful upload
creates the project and the publisher becomes permanent.

1. Sign in at <https://pypi.org> and open
   <https://pypi.org/manage/account/publishing/>.
2. Under **Add a new pending publisher → GitHub**, fill in:

   | field | value |
   |---|---|
   | PyPI project name | `echolot` |
   | Owner | `grishan0v` |
   | Repository name | `echolot` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

   The environment name must match the `environment: name:` in the workflow.
   GitHub creates the environment on the first run; nothing to configure there.

## Before a release: what CI already checked

`.github/workflows/checks.yml` runs on every pull request into `main`, and on
`main` after a merge: `pytest` across every Python version the classifiers
claim, and a `package` job that looks at the README three ways and then builds
the artefacts.

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

```bash
# 1. bump the version in pyproject.toml, commit
# 2. tag it — the tag must be "v" + that version, the workflow checks
git tag v0.1.0
git push origin v0.1.0
```

Watch the run under **Actions**. When it is green the package is at
<https://pypi.org/project/echolot/>, `pipx install echolot` works, and the
release is listed at <https://github.com/grishan0v/echolot/releases>. The
generated notes are a list of commits — edit them in the GitHub UI if a
version deserves a paragraph.

A tag, and therefore a release, is a snapshot: it contains what was committed
before the tag was made and nothing after. To ship a fix, bump the version and
tag again — an uploaded version number can never be reused on PyPI, even after
deletion.

## Checking the artefacts locally

The packaging half of the `package` job, useful before tagging. CI has already
run all four of its checks on the pull request, so this is a second look rather
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
