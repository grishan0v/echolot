#!/usr/bin/env python3
"""The README's pictures, held to what draws them.

A number inside a picture is a claim about the tool exactly like a number in a
sentence, and it goes stale the same way: nobody re-reads a picture when the
sentence it was drawn from changes. docs/assets/render.py reads its numbers out
of the README and the recorded runs; these checks draw every picture again and
compare, so the committed files cannot drift from their sources unnoticed.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
RAW = "https://raw.githubusercontent.com/grishan0v/echolot/main/"


def _render():
    spec = importlib.util.spec_from_file_location("readme_pictures", ASSETS / "render.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_committed_pictures_are_what_the_script_draws():
    drawn = _render().pictures()
    stale = sorted(name for name, body in drawn.items()
                   if not (ASSETS / name).is_file()
                   or (ASSETS / name).read_text(encoding="utf-8") != body)
    assert not stale, (
        f"out of date: {stale} — run `python docs/assets/render.py` and commit "
        f"what it writes")


def test_nothing_in_the_folder_is_left_over_from_an_older_script():
    drawn = set(_render().pictures())
    on_disk = {p.name for p in ASSETS.glob("*.svg")}
    assert on_disk <= drawn, f"no longer drawn by render.py: {sorted(on_disk - drawn)}"


def test_every_picture_the_readme_names_is_in_the_tree():
    """The link check leaves these addresses out, since a new picture is not
    on main until its pull request merges. This is the check instead, and it
    runs against the tree the pull request brings."""
    named = re.findall(re.escape(RAW) + r'([^"\s)]+)', _readme())
    assert named, "the README names none of this repository's own pictures"
    missing = sorted(p for p in named if not (ROOT / p).is_file())
    assert not missing, f"the README names files that are not in the tree: {missing}"


def test_a_themed_picture_falls_back_to_its_light_version():
    """PyPI's sanitiser removes <source>, so the <img> inside <picture> is
    what PyPI shows, on a white page: it has to be the light version."""
    pictures = re.findall(r"<picture>(.*?)</picture>", _readme(), re.S)
    assert pictures, "the README has no <picture>"
    for block in pictures:
        dark = re.search(r'<source media="\(prefers-color-scheme: dark\)" '
                         r'srcset="([^"]+)-dark\.(svg|png)"', block)
        light = re.search(r'<img [^>]*src="([^"]+)-light\.(svg|png)"', block)
        assert dark and light and dark.groups() == light.groups(), (
            f"a <picture> without a dark <source> and a light <img> of the "
            f"same name:\n{block.strip()}")


def test_a_narrow_picture_comes_before_the_theme():
    """A browser takes the first <source> that matches. A narrow version
    placed after the dark one would never show on a phone with a dark
    theme, and one that also names a theme is not safe on GitHub, which
    rewrites that half of the condition — so it asks for the width alone."""
    for block in re.findall(r"<picture>(.*?)</picture>", _readme(), re.S):
        media = re.findall(r'<source media="([^"]+)"', block)
        narrow = [i for i, m in enumerate(media) if "max-width" in m]
        for i in narrow:
            assert "prefers-color-scheme" not in media[i], (
                f"a narrow <source> that also names a theme: {media[i]}")
            assert all(i < j for j, m in enumerate(media) if "prefers-color-scheme" in m), (
                f"a narrow <source> after a theme one:\n{block.strip()}")


def test_the_numbers_come_from_the_readme():
    """The script reads the README rather than keeping copies of its figures,
    so a figure changed there changes the picture. Swap one and look."""
    render = _render()
    moved = _readme().replace("An 81 MB trace with 475k slices",
                              "An 93 MB trace with 475k slices")
    assert moved != _readme(), "the sentence this check edits has moved"
    hero = render.pictures(moved)["hero-light.svg"]
    assert "93 MB" in hero and "81 MB" not in hero
