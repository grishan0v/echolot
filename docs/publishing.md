# Publishing to PyPI

A release is a git tag. `.github/workflows/publish.yml` builds the sdist and
wheel on the tag and uploads them with PyPI Trusted Publishing — no API token
is stored anywhere in the repository or in GitHub secrets.

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

## Cutting a release

```bash
# 1. bump the version in pyproject.toml, commit
# 2. tag it — the tag must be "v" + that version, the workflow checks
git tag v0.1.0
git push origin v0.1.0
```

Watch the run under **Actions**. When it is green the package is at
<https://pypi.org/project/echolot/> and `pipx install echolot` works.

## Checking the artefacts locally

Same steps the workflow runs; useful before tagging.

```bash
python -m pip install build twine
rm -rf dist && python -m build
python -m twine check dist/*
```

`twine check` verifies that the README renders on PyPI. Relative links do not
resolve there, which is why the README links to `docs/` by full GitHub URL.

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
