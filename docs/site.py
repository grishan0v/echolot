#!/usr/bin/env python3
"""These pages, as the documentation site's generator needs them.

    python docs/site.py [DIR]        write the default language to build/site-docs/ (or DIR),
                                     plus build/site-docs-<lang>/ and build/mkdocs-<lang>.yml
                                     for every translation when no DIR is given
    python docs/site.py --lang ru    write only that language
    zensical build --strict          then build the default site into build/site/
    zensical build -f build/mkdocs-ru.yml --strict   build a translation into build/site/ru/

The Markdown here is written for GitHub and stays the source: it reads the
same there whether or not the site exists. Three things in it belong to
GitHub, and this rewrites them on the way to the site:

- the navigation line under a page's title, `[← Docs index](README.md) · …`.
  The site's sidebar and header language selector do those jobs, so the line
  is dropped;
- a link that leaves the language's docs folder, to the README or into the
  package. The generator sees only one language's pages, so the link gets
  the file's address on GitHub;
- README.md as a folder's first page, which the site calls index.md.

One page stays off the site: assets/README.md, how the README's pictures are
drawn, is for whoever works in the repository and is read there. A link to
it goes to GitHub, like a link out of docs/.

Everything else reaches the site as written. GitHub's callouts, `> [!NOTE]`,
are rendered by an extension named in mkdocs.yml at the root, whose nav holds
the groups docs/README.md lists; tests/test_docs_site.py keeps the two in
step, and .github/workflows/docs.yml builds the site for every language in
docs/languages.yml on every pull request that touches it.
"""

from __future__ import annotations

import argparse
import copy
import os
import re
import shutil
import sys
from pathlib import Path

import yaml

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
OUT = ROOT / "build" / "site-docs"
LANGUAGES_FILE = DOCS / "languages.yml"
GITHUB = "https://github.com/grishan0v/echolot/blob/main/"
# What a page shows besides its text. render.py, which draws the pictures,
# and this file stay behind.
STATIC = (".svg", ".png")
# Pages written for the repository, under docs/ and off the site.
UNPUBLISHED = {"assets/README.md"}

_LINK = re.compile(r"(\]\()([^)\s]+)(\))")
_NAV = re.compile(
    r"\[←[^\]]*\]\([^)]*\)(?: · (?:\[[^\]]*\]\([^)]*\)|\*\*[^*]+\*\*))*"
)


def load_languages() -> tuple[str, dict[str, dict]]:
    """Return (default_lang, languages_dict) from docs/languages.yml."""
    data = yaml.safe_load(LANGUAGES_FILE.read_text(encoding="utf-8"))
    return data["default"], data["languages"]


def _translation_dirs() -> set[str]:
    default, langs = load_languages()
    return {
        Path(info["docs_dir"]).parts[0]
        for code, info in langs.items()
        if code != default and info.get("docs_dir") not in (None, ".", "")
    }


def site_path(rel: Path) -> Path:
    """Where a page lands on the site: README.md becomes its folder's index.md."""
    return rel.with_name("index.md") if rel.name == "README.md" else rel


def target(link: str, page: Path, *, lang_dir: Path = DOCS) -> str:
    """A link written on `page` (under `lang_dir`), as the site needs it."""
    if link.startswith(("http://", "https://", "mailto:", "#")):
        return link
    path, hash_, anchor = link.partition("#")
    resolved = (page.parent / path).resolve()
    in_docs = resolved.is_relative_to(DOCS)
    docs_rel = resolved.relative_to(DOCS).as_posix() if in_docs else ""
    leaves_lang = not resolved.is_relative_to(lang_dir)
    if lang_dir == DOCS and in_docs:
        first = resolved.relative_to(DOCS).parts[0]
        if first in _translation_dirs():
            leaves_lang = True
    if leaves_lang or docs_rel in UNPUBLISHED:
        return GITHUB + resolved.relative_to(ROOT).as_posix() + hash_ + anchor
    if resolved.name == "README.md":
        return path[: -len("README.md")] + "index.md" + hash_ + anchor
    return link


def prepare(text: str, page: Path, *, lang_dir: Path = DOCS) -> str:
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
            line = _LINK.sub(
                lambda m: m.group(1) + target(m.group(2), page, lang_dir=lang_dir) + m.group(3),
                line,
            )
        out.append(line)
    return "\n".join(out)


def index_groups(index_path: Path) -> list[tuple[str, list[tuple[str, str]]]]:
    """Groups from a docs README: each `##` heading, and the pages its table links, in order."""
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"## (.+)", line)
        if heading:
            groups.append((heading.group(1), []))
        elif groups and line.startswith("|"):
            groups[-1][1].extend(re.findall(r"\*\*\[([^\]]+)\]\(([^)]+)\)\*\*", line))
    return [g for g in groups if g[1]]


def prepare_language(lang: str, out: Path) -> int:
    """Prepare one language's Markdown pages and static assets into `out`."""
    default, langs = load_languages()
    if lang not in langs:
        raise SystemExit(f"unknown language: {lang!r}")
    info = langs[lang]
    lang_dir = (DOCS / info["docs_dir"]).resolve()
    if out.exists():
        shutil.rmtree(out)

    other_lang_dirs = _translation_dirs() if lang == default else set()
    pages = 0
    for src in sorted(lang_dir.rglob("*")):
        rel = src.relative_to(lang_dir)
        if rel.parts[0] in other_lang_dirs:
            continue
        if (
            lang == default
            and rel.parts[0] == "assets"
            and len(rel.parts) > 2
            and rel.parts[1] in other_lang_dirs
        ):
            continue
        docs_rel = src.resolve().relative_to(DOCS).as_posix()
        if docs_rel in UNPUBLISHED:
            continue
        if src.suffix == ".md":
            dst = out / site_path(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(
                prepare(src.read_text(encoding="utf-8"), src, lang_dir=lang_dir),
                encoding="utf-8",
            )
            pages += 1
        elif lang == default and src.suffix in STATIC:
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out / rel)

    if lang != default:
        assets_dir = DOCS / "assets"
        if assets_dir.is_dir():
            for src in sorted(assets_dir.iterdir()):
                if src.is_file() and src.suffix in STATIC:
                    dst = out / "assets" / src.name
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
    return pages


class _ConfigLoader(yaml.SafeLoader):
    """mkdocs.yml names a Python callable for the slugs; nothing read here needs it."""


_ConfigLoader.add_multi_constructor("tag:yaml.org,2002:python/", lambda *_: None)


def translated_theme(lang: str, cfg_path: Path) -> dict:
    """mkdocs.yml's theme, for a translation whose config is written at `cfg_path`.

    The theme is the same in every language: the features, the logo, the
    favicon and the template that adds the tags for shared links. What
    changes is the language, the words on the light and dark switch, and
    the template folder, which is named relative to the config file.
    """
    info = load_languages()[1][lang]
    base = yaml.load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"), Loader=_ConfigLoader)
    theme = copy.deepcopy(base["theme"])
    theme["language"] = lang
    toggle = {"default": info["toggle_dark"], "slate": info["toggle_light"]}
    for entry in theme.get("palette", []):
        entry["toggle"]["name"] = toggle[entry["scheme"]]
    if theme.get("custom_dir"):
        theme["custom_dir"] = Path(
            os.path.relpath(ROOT / theme["custom_dir"], cfg_path.parent)
        ).as_posix()
    return theme


def write_lang_config(lang: str, cfg_path: Path, docs_dir: Path, site_dir: Path) -> None:
    """Write a Zensical-compatible mkdocs config for a non-default language."""
    _, langs = load_languages()
    info = langs[lang]
    lang_dir = (DOCS / info["docs_dir"]).resolve()
    groups = index_groups(lang_dir / "README.md")

    base_text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    ext_match = re.search(
        r"^markdown_extensions:\n(?:(?:  .*|#.*)?\n)*", base_text, re.M
    )
    assert ext_match, "mkdocs.yml has no markdown_extensions block"
    extensions_block = ext_match.group(0).rstrip("\n")

    prefix = info["site_prefix"].strip("/")
    site_url = f"https://grishan0v.github.io/echolot/{prefix}/" if prefix else "https://grishan0v.github.io/echolot/"

    nav = [{info["nav_home"]: "index.md"}]
    for group_title, entries in groups:
        nav.append({group_title: [{title: page} for title, page in entries]})

    alternates = [
        {
            "name": l_info["name"],
            "link": f"/echolot/{l_info['site_prefix'].strip('/')}/"
            if l_info["site_prefix"].strip("/")
            else "/echolot/",
            "lang": l_code,
        }
        for l_code, l_info in langs.items()
    ]

    header_cfg = {
        "site_name": "echolot",
        "site_description": info["site_description"],
        "site_url": site_url,
        "repo_url": "https://github.com/grishan0v/echolot",
        "repo_name": "grishan0v/echolot",
        "docs_dir": Path(os.path.relpath(docs_dir, cfg_path.parent)).as_posix(),
        "site_dir": Path(os.path.relpath(site_dir, cfg_path.parent)).as_posix(),
        "theme": translated_theme(lang, cfg_path),
    }
    footer_cfg = {
        "nav": nav,
        "extra": {"alternate": alternates},
    }

    content = (
        yaml.safe_dump(header_cfg, allow_unicode=True, sort_keys=False)
        + "\n"
        + extensions_block
        + "\n\n"
        + yaml.safe_dump(footer_cfg, allow_unicode=True, sort_keys=False)
    )
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(content, encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="site.py",
        description="Prepares docs/ pages and localized site configs for zensical.",
    )
    parser.add_argument("dir", nargs="?", type=Path, help="output directory for one language")
    parser.add_argument("--lang", help="prepare only this language code")
    args = parser.parse_args(argv)

    default, langs = load_languages()
    if args.lang or args.dir is not None:
        lang = args.lang or default
        out = args.dir if args.dir is not None else (
            OUT if lang == default else ROOT / "build" / f"site-docs-{lang}"
        )
        pages = prepare_language(lang, out)
        print(f"→ {out}: {pages} pages")
        return 0

    pages = prepare_language(default, OUT)
    print(f"→ {OUT}: {pages} pages")
    for code, info in langs.items():
        if code == default:
            continue
        lang_dir = DOCS / info["docs_dir"]
        if not lang_dir.is_dir():
            continue
        lang_out = ROOT / "build" / f"site-docs-{code}"
        cfg_path = ROOT / "build" / f"mkdocs-{code}.yml"
        site_out = ROOT / "build" / "site" / info["site_prefix"].strip("/")
        lang_pages = prepare_language(code, lang_out)
        write_lang_config(code, cfg_path, lang_out, site_out)
        print(f"→ {lang_out}: {lang_pages} pages ({cfg_path.relative_to(ROOT)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
