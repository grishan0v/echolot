"""`python -m echolot` — the same CLI as the `echolot` script.

For wherever the script is not on PATH: a virtualenv that was never
activated, a `pip install --user` whose bin directory the shell does not
know, a CI step that names its interpreter. The package name is the one
thing all of those can still type, and it used to answer "No module named
echolot.__main__".
"""

from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
