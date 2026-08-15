#!/usr/bin/env python3
"""A thin wrapper over the CLI self-check — for CI and for muscle memory.

The checks themselves live in echolot/selftest.py and the fixture in
echolot/fixture.py: they are not test scaffolding but a self-verification
asset, and `echolot doctor` stands on them. This file is only an entry point,
so that running from tests/ and from CI looks familiar.

    python tests/check_fixture.py        # equivalent to: echolot doctor
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["doctor"]))
