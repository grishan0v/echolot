"""Ships the plugin inside the package, for `echolot plugin cursor` (#399).

pyproject.toml says everything else about the build. The one thing it cannot
say is a directory outside the package: the plugin that Claude Code, Codex and
Cursor install sits at `plugins/echolot`, where their marketplaces read it, and
package-data reaches only below `echolot/`. A copy kept inside the package
would be a second plugin to keep in step with the first at every release. So
the build copies the one in the repository into the wheel, as
`echolot/plugin`, and MANIFEST.in carries it in the sdist that a wheel is
built from.

A checkout has no copy: `echolot.plugin.source()` reads `plugins/echolot`
there, in an editable install and under `python -m echolot` alike.
"""

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

PLUGIN = Path(__file__).resolve().parent / "plugins" / "echolot"


class BuildPyWithThePlugin(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / "echolot" / "plugin"
        if target.exists():
            shutil.rmtree(target)
        # A missing source fails the build: a wheel without the plugin would
        # install, and `echolot plugin cursor` would find nothing to copy.
        shutil.copytree(PLUGIN, target,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))


setup(cmdclass={"build_py": BuildPyWithThePlugin})
