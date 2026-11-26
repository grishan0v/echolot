#!/usr/bin/env python3
"""The documentation site: docs/site.py, which prepares the pages, and the nav in mkdocs.yml.

The site is built by .github/workflows/docs.yml, with `--strict`, and that is
where a broken link or anchor fails. These are the parts that need no
generator: what reaches the site from a page written for GitHub, and the
nav, which has to hold the groups docs/README.md lists and nothing else.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

_spec = importlib.util.spec_from_file_location("echolot_docs_site", DOCS / "site.py")
site = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = site
_spec.loader.exec_module(site)


class _Loader(yaml.SafeLoader):
    """mkdocs.yml names a Python callable for the slugs; the nav is all this reads."""


_Loader.add_multi_constructor("tag:yaml.org,2002:python/", lambda *_: None)


def _nav() -> list:
    return yaml.load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"), Loader=_Loader)["nav"]


def _index_groups() -> list[tuple[str, list[tuple[str, str]]]]:
    """docs/README.md's groups: each `##` heading, and the pages its table links, in order."""
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    for line in (DOCS / "README.md").read_text(encoding="utf-8").splitlines():
        heading = re.match(r"## (.+)", line)
        if heading:
            groups.append((heading.group(1), []))
        elif groups and line.startswith("|"):
            groups[-1][1].extend(re.findall(r"\*\*\[([^\]]+)\]\(([^)]+)\)\*\*", line))
    return [g for g in groups if g[1]]


def test_the_site_nav_is_the_index_groups_in_their_order() -> None:
    nav = _nav()
    check("the index page first", nav[0] == {"Home": "index.md"}, nav[0])
    groups = [(name, [tuple(*entry.items()) for entry in pages])
              for item in nav[1:] for name, pages in item.items()]
    check("then docs/README.md's groups, each with its pages, under the titles it gives them",
          groups == _index_groups(), (groups, _index_groups()))
    on_site = {page for _, pages in groups for _, page in pages}
    written = {p.name for p in DOCS.glob("*.md") if p.name != "README.md"}
    check("and every page in docs/ is on the site", on_site == written,
          (sorted(written - on_site), sorted(on_site - written)))


def test_mkdocs_alternate_languages_match_registry() -> None:
    cfg = yaml.load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"), Loader=_Loader)
    default, langs = site.load_languages()
    want = [
        {
            "name": info["name"],
            "link": f"/echolot/{info['site_prefix'].strip('/')}/"
            if info["site_prefix"].strip("/")
            else "/echolot/",
            "lang": code,
        }
        for code, info in langs.items()
    ]
    check("mkdocs.yml extra.alternate matches docs/languages.yml",
          cfg.get("extra", {}).get("alternate") == want,
          (cfg.get("extra", {}).get("alternate"), want))
    check("mkdocs.yml theme.language matches default",
          cfg["theme"]["language"] == default, cfg["theme"].get("language"))


def test_translations_prepare_and_generate_site_configs(tmp_path: Path) -> None:
    default, langs = site.load_languages()
    expected_pages = {p.name for p in DOCS.glob("*.md") if p.name != "README.md"}
    for code, info in langs.items():
        if code == default:
            continue
        out = tmp_path / f"site-docs-{code}"
        cfg_path = tmp_path / f"mkdocs-{code}.yml"
        site_dir = tmp_path / "site" / info["site_prefix"].strip("/")
        pages = site.prepare_language(code, out)
        check(f"{code}: prepared all pages", pages == len(expected_pages) + 1, pages)
        check(f"{code}: no navigation line remains",
              not any(site._NAV.search(p.read_text(encoding="utf-8")) for p in out.glob("*.md")))
        site.write_lang_config(code, cfg_path, out, site_dir)
        cfg = yaml.load(cfg_path.read_text(encoding="utf-8"), Loader=_Loader)
        check(f"{code}: theme language", cfg["theme"]["language"] == code, cfg["theme"])
        nav = cfg["nav"]
        check(f"{code}: home first", nav[0] == {info["nav_home"]: "index.md"}, nav[0])
        groups = [(name, [tuple(*entry.items()) for entry in pages])
                  for item in nav[1:] for name, pages in item.items()]
        want_groups = site.index_groups(DOCS / info["docs_dir"] / "README.md")
        check(f"{code}: nav matches translated index", groups == want_groups, (groups, want_groups))
        on_site = {page for _, pages_list in groups for _, page in pages_list}
        check(f"{code}: every page in nav", on_site == expected_pages, on_site ^ expected_pages)
        base = yaml.load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"), Loader=_Loader)["theme"]
        theme = cfg["theme"]
        same = {k: v for k, v in theme.items() if k not in ("language", "palette", "custom_dir")}
        check(f"{code}: the theme is mkdocs.yml's: features, logo, favicon",
              same == {k: v for k, v in base.items()
                       if k not in ("language", "palette", "custom_dir")}, (same, base))
        check(f"{code}: the switch says its words in the language",
              [e["toggle"]["name"] for e in theme["palette"]]
              == [info["toggle_dark"], info["toggle_light"]], theme["palette"])
        check(f"{code}: and the template folder is the same one, named from the config file",
              (cfg_path.parent / theme["custom_dir"]).resolve() == (ROOT / base["custom_dir"]).resolve(),
              theme["custom_dir"])


def test_a_page_reaches_the_site_without_what_belongs_to_github() -> None:
    page = DOCS / "compare.md"
    text = "\n".join([
        "# Comparing",
        "",
        "[← Docs index](README.md) · [README](../README.md)",
        "",
        "See [the requirements](../README.md#requirements), [the rule]"
        "(../echolot/guide/overview.md#the-one-rule) and [the index](README.md).",
        "[Collecting](collecting.md#modes) stays, and so does [the web](https://example.com).",
        "```markdown",
        "[a link in a sample](../README.md)",
        "```",
    ])
    got = site.prepare(text, page).split("\n")
    check("the navigation line goes, with the blank line under it",
          got[:3] == ["# Comparing", "", "See [the requirements]"
                      "(https://github.com/grishan0v/echolot/blob/main/README.md#requirements), "
                      "[the rule](https://github.com/grishan0v/echolot/blob/main/echolot/guide/"
                      "overview.md#the-one-rule) and [the index](index.md)."], got[:3])
    check("a link inside docs/ and a full address are left as written",
          got[3] == "[Collecting](collecting.md#modes) stays, and so does "
                    "[the web](https://example.com).", got[3])
    check("and nothing inside a code block is touched",
          got[4:] == ["```markdown", "[a link in a sample](../README.md)", "```"], got[4:])


def test_a_page_kept_for_the_repository_is_linked_on_github() -> None:
    link = site.target("assets/README.md#where-the-numbers-come-from", DOCS / "compare.md")
    check("assets/README.md stays off the site, and a link to it goes to GitHub",
          link == "https://github.com/grishan0v/echolot/blob/main/docs/assets/README.md"
                  "#where-the-numbers-come-from", link)


def test_the_prepared_pages_are_every_page_and_picture_and_nothing_else(tmp_path: Path) -> None:
    out = tmp_path / "site-docs"
    check("it runs", site.main([str(out)]) == 0)
    written = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    pages = sorted(["index.md"] + [p.name for p in DOCS.glob("*.md") if p.name != "README.md"])
    pictures = sorted(f"assets/{p.name}" for p in (DOCS / "assets").iterdir()
                      if p.suffix in (".svg", ".png"))
    check("the index under its site name, every page, every picture",
          written == sorted(pages + pictures), written)
    check("and no navigation line is left on any of them",
          not any(site._NAV.search(p.read_text(encoding="utf-8")) for p in out.glob("*.md")))


def test_the_pages_use_nothing_only_the_site_renders() -> None:
    """Admonitions and content tabs show as raw text on GitHub, where the pages are read first."""
    found = [f"{p.relative_to(ROOT)}:{n}" for p in DOCS.rglob("*.md")
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
             if re.match(r"(!!!|\?\?\?\+?|===) ", line)]
    check("no `!!! note`, `??? note` or `=== \"tab\"` in docs/", found == [], found)


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    check(f"{path.name} is a PNG", head[:8] == b"\x89PNG\r\n\x1a\n", head[:8])
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def test_the_site_shows_the_logo_and_tells_a_shared_link_what_it_is() -> None:
    """The header's logo and the tab's favicon are the plugin's sphere of dots
    (#407), and a link to any page unfurls with the repository's card: the
    template in docs/overrides adds Open Graph and Twitter's tags, which the
    theme does not write."""
    theme = yaml.load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"), Loader=_Loader)["theme"]
    for key in ("logo", "favicon"):
        picture = DOCS / theme[key]
        check(f"theme.{key} names a picture under docs/, which reaches the site",
              picture.is_file() and picture.suffix in site.STATIC, theme[key])
        width, height = _png_size(picture)
        check(f"theme.{key} is square", width == height, (width, height))
    check("the groups are tabs", "navigation.tabs" in theme["features"], theme["features"])
    template = (ROOT / theme["custom_dir"] / "main.html").read_text(encoding="utf-8")
    for tag in ("og:title", "og:description", "og:image", "og:url",
                "twitter:card", "twitter:title", "twitter:description", "twitter:image"):
        check(f"the template writes {tag}", f'"{tag}"' in template, tag)
    image = re.search(r'config\.site_url ~ "([^"]+)"', template)
    check("the picture is named by the site's full address", image is not None, template)
    card = DOCS / image.group(1)
    check("and it is a picture under docs/", card.is_file(), image.group(1))
    width, height = _png_size(card)
    declared = tuple(int(re.search(rf'"og:image:{side}" content="(\d+)"', template).group(1))
                     for side in ("width", "height"))
    check("the size the template declares is the picture's", declared == (width, height),
          (declared, (width, height)))
