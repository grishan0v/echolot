#!/usr/bin/env python3
"""These pages, as the documentation site's generator needs them.

    python docs/site.py [DIR]    write them to build/site-docs/, or DIR
    zensical build --strict      then build the site into build/site/

The Markdown here is written for GitHub and stays the source: it reads the
same there whether or not the site exists. Three things in it belong to
GitHub, and this rewrites them on the way to the site:

- the navigation line under a page's title, `[← Docs index](README.md) · …`.
  The site's sidebar does that job, so the line is dropped;
- a link that leaves docs/, to the README or into the package. The generator
  sees only these pages, so the link gets the file's address on GitHub;
- README.md as a folder's first page, which the site calls index.md.

One page stays off the site: assets/README.md, how the README's pictures are
drawn, is for whoever works in the repository and is read there. A link to
it goes to GitHub, like a link out of docs/.

Everything else reaches the site as written. GitHub's callouts, `> [!NOTE]`,
are rendered by an extension named in mkdocs.yml at the root, whose nav holds
the groups docs/README.md lists; tests/test_docs_site.py keeps the two in
step, and .github/workflows/docs.yml builds the site on every pull request
that touches it.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
OUT = ROOT / "build" / "site-docs"
GITHUB = "https://github.com/grishan0v/echolot/blob/main/"
# What a page shows besides its text. render.py, which draws the pictures,
# and this file stay behind.
STATIC = (".svg", ".png")
# Pages written for the repository, under docs/ and off the site.
UNPUBLISHED = {"assets/README.md"}

_LINK = re.compile(r"(\]\()([^)\s]+)(\))")
_NAV = re.compile(r"\[←[^\]]*\]\([^)]*\)(?: · \[[^\]]*\]\([^)]*\))*")


def site_path(rel: Path) -> Path:
    """Where a page lands on the site: README.md becomes its folder's index.md."""
    return rel.with_name("index.md") if rel.name == "README.md" else rel


def target(link: str, page: Path) -> str:
    """A link written on `page` (a path under docs/), as the site needs it."""
    if link.startswith(("http://", "https://", "mailto:", "#")):
        return link
    path, hash_, anchor = link.partition("#")
    resolved = (page.parent / path).resolve()
    if (not resolved.is_relative_to(DOCS)
            or resolved.relative_to(DOCS).as_posix() in UNPUBLISHED):
        return GITHUB + resolved.relative_to(ROOT).as_posix() + hash_ + anchor
    if resolved.name == "README.md":
        return path[: -len("README.md")] + "index.md" + hash_ + anchor
    return link


def prepare(text: str, page: Path) -> str:
    """One page's Markdown for the site: no navigation line, links rewritten,
    and nothing inside a code block touched."""
    out: list[str] = []
    fenced = dropped = False
    for line in text.split("\n"):
        if dropped and not line.strip():
            # The blank line under a dropped navigation line goes too, or
            # the title would stand over two.
            dropped = False
            continue
        dropped = False
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and _NAV.fullmatch(line.strip()):
            dropped = True
            continue
        elif not fenced:
            line = _LINK.sub(lambda m: m.group(1) + target(m.group(2), page) + m.group(3),
                             line)
        out.append(line)
    return "\n".join(out)


def main(argv: list[str]) -> int:
    out = Path(argv[0]) if argv else OUT
    if out.exists():
        shutil.rmtree(out)
    pages = 0
    for src in sorted(DOCS.rglob("*")):
        rel = src.relative_to(DOCS)
        if rel.as_posix() in UNPUBLISHED:
            continue
        if src.suffix == ".md":
            dst = out / site_path(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(prepare(src.read_text(encoding="utf-8"), src), encoding="utf-8")
            pages += 1
        elif src.suffix in STATIC:
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out / rel)
    print(f"→ {out}: {pages} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
