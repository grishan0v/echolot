#!/usr/bin/env python3
"""The README's pictures, held to what draws them.

The README's tables are pictures drawn by docs/assets/render.py: the
detectors' table from each detector's `@title`, the questions from the
script's own list. These checks draw every picture again and compare, so a
committed file cannot drift from its source unnoticed, and they hold the
README to the way it shows them: a light picture PyPI can show, a dark one
and a narrow one for GitHub, and the words of each table in its alt text.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

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
    render = _render()
    on_disk = {p.name for p in ASSETS.glob("*.svg")}
    kept = set(render.pictures()) | set(render.RETIRED)
    assert on_disk <= kept, f"no longer drawn by render.py: {sorted(on_disk - kept)}"


def test_the_pictures_released_readmes_show_stay_in_the_tree():
    """The releases already on PyPI show their README's pictures from `main`.
    A picture the README stops showing stays, or those pages show empty
    boxes for as long as the releases are up."""
    render = _render()
    gone = sorted(name for name in render.RETIRED if not (ASSETS / name).is_file())
    assert not gone, f"pictures released READMEs show are gone from docs/assets: {gone}"
    shown = set(re.findall(re.escape(RAW) + r"docs/assets/([^\"\s)]+)", _readme()))
    assert not shown & set(render.RETIRED), (
        f"the README shows retired pictures: {sorted(shown & set(render.RETIRED))}")


def test_every_picture_the_readme_names_is_in_the_tree():
    """The link check leaves these addresses out, since a new picture is not
    on main until its pull request merges. This is the check instead, and it
    runs against the tree the pull request brings."""
    named = re.findall(re.escape(RAW) + r'([^"\s)]+)', _readme())
    assert named, "the README names none of this repository's own pictures"
    missing = sorted(p for p in named if not (ROOT / p).is_file())
    assert not missing, f"the README names files that are not in the tree: {missing}"


def test_every_table_is_shown_in_its_three_versions():
    """Light as the <img>, which is what PyPI keeps; dark and narrow as
    <source> for GitHub. A file drawn and never shown is a table the README
    lost."""
    readme = _readme()
    for name in _render().tables():
        for version in ("narrow", "dark", "light"):
            assert f"{RAW}docs/assets/{name}-{version}.svg" in readme, f"{name}-{version}.svg"


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


def test_each_table_says_its_words_in_its_alt_text():
    """A screen reader, a search and a page with its images off get the
    table in words. render.py writes them; this fails when the README's
    copy is not what it would write."""
    render = _render()
    readme = _readme()
    assert render.with_alts(readme) == readme, (
        "the tables' alt text is out of date — run `python docs/assets/render.py`")
    for words in ("/echolot why is cold start slow",
                  "main_thread_block: Where the main thread spent its time."):
        assert words in readme, words


def test_the_detectors_table_is_every_detector_by_its_title():
    """The table's words are the detectors' own: the title its section has in
    the report, for every file in echolot/sql/detectors/."""
    from echolot.main import DETECTOR_DIR
    from echolot.tp import load_detectors

    drawn = _render().pictures()["detectors-light.svg"]
    for detector in load_detectors(DETECTOR_DIR):
        assert f">{detector.id}<" in drawn, detector.id
        assert detector.title.replace("'", "&#x27;") in drawn, detector.title


def test_a_detector_the_table_does_not_place_stops_the_script(monkeypatch):
    """A new detector has to be given a place in the table's order. Placed at
    the end by default, it would land after the blind spots, wherever it
    belongs."""
    render = _render()
    monkeypatch.setattr(render, "DETECTOR_ORDER", render.DETECTOR_ORDER[1:])
    with pytest.raises(SystemExit, match="not placed: \\['main_thread_block'\\]"):
        render.pictures()


def test_the_narrow_tables_follow_the_system_theme():
    """GitHub picks a narrow picture by the width of the screen alone, so a
    narrow table is one file for both themes, and repaints itself when the
    system asks for dark."""
    render = _render()
    for name in render.tables():
        drawn = render.pictures()[f"{name}-narrow.svg"]
        assert "@media (prefers-color-scheme:dark)" in drawn, name
        assert f'[fill="{render.THEMES["light"]["ink"]}"]' in drawn, name


def test_the_card_for_links_is_what_github_takes():
    """GitHub shows social-preview.png where a link to the repository is
    shared, and takes it only by hand, in the repository's settings: 1280 by
    640 is the size it asks for, and it refuses a file over 1 MB."""
    card = ASSETS / "social-preview.png"
    head = card.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "social-preview.png is not a PNG"
    size = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    assert size == (1280, 640), size
    assert card.stat().st_size < 1024 * 1024, card.stat().st_size


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("phone", [False, True])
def test_the_preview_is_the_readme(theme, phone):
    """`--preview` renders the README itself, so it cannot drift from it, and
    chooses the <source> a reader on that screen and theme would get."""
    page = _render().preview_page(theme, phone)
    assert "Turns a huge Android trace into 20 rows of facts" in page
    assert RAW not in page, "a picture still read from GitHub"
    assert 'src="assets/ask-light.svg"' in page
    narrow = re.search(r'<source media="([^"]+)" srcset="assets/ask-narrow\.svg"', page)
    dark = re.search(r'<source media="([^"]+)" srcset="assets/ask-dark\.svg"', page)
    assert narrow.group(1) == ("all" if phone else "not all")
    assert dark.group(1) == ("all" if theme == "dark" else "not all")


@pytest.mark.parametrize("argv", [["--preview"], ["--preveiw", "out"],
                                  ["--preview", "out", "extra"]])
def test_an_argument_the_script_cannot_use_is_refused(argv, capsys):
    """Every argv but `--preview DIR` used to fall through: the pictures were
    drawn again, no preview was written, and nothing said why (#249)."""
    with pytest.raises(SystemExit) as stopped:
        _render().main(argv)
    assert stopped.value.code == 2
    assert "usage: render.py" in capsys.readouterr().err
