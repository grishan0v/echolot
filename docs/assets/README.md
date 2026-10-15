# The README's pictures

[← Docs index](../README.md) · [README](../../README.md)

The README shows two kinds of picture: the logo, which a designer drew, and
its tables, which [render.py](render.py) draws and commits next to it. Change
the script, then draw them again:

```bash
python docs/assets/render.py                     # the sample report, the tables' alt text, the .svg files
python docs/assets/render.py --preview /tmp/pv   # plus the README as four pages: light and dark, desktop and phone
```

The script first writes the README's sample report: what `analyze` prints
for the demo app, `echolot/demo.py`. `tests/test_doc_samples.py` fails when
the README's sample differs from the demo's report, and
`tests/test_readme_pictures.py` draws everything once more and fails when a
committed file differs from what the script draws now.

## Why the tables are pictures

GitHub draws a Markdown table with a border round every cell and a stripe on
every other row, and its stylesheet is not the README's to change. A picture
can take the logo's colours and leave room between the rows.

What a picture takes away, the README gives back. The words of each table go
into its alt text, which the script writes, for a screen reader, a search and
a page with its images off. Whatever a reader has to copy stays text: the
install commands are code blocks.

## Where the words come from

- the table of detectors: every file in `echolot/sql/detectors/`, by its
  `@title`, the heading its section has in the report. The order is
  `DETECTOR_ORDER` in the script, and a detector it does not place stops the
  script;
- the table of what to type: `ASK` in the script.

## The rules

- **The logo's colours.** The slate of its plate, `#262C33`, and the three
  colours its sphere runs through from the top down: mint, teal and blue. A
  row's dot takes the sphere's colour for its place in the table, and the
  line under the labels runs through all three. Everything else is a grey
  that sits in GitHub's page, and the slate is the ink on white.
- **Room, and no borders.** Small capitals for the labels, a hairline between
  the rows, no box round the table.
- **Three files a table.** `<name>-light.svg` and `<name>-dark.svg` at the
  README's width, and `<name>-narrow.svg` for a phone, whose screen would
  shrink the wide one's words to five pixels. The README shows them through
  `<picture>` with the light one as the `<img>`: PyPI removes the `<source>`
  and always shows that `<img>`, on white. The narrow `<source>` comes first
  and asks for the width alone, since GitHub rewrites the theme half of a
  condition that names both. So the narrow file is one file: CSS inside it
  follows the reader's system theme, and it sits on a card of its own, which
  keeps it readable when the system and GitHub disagree about the theme.
- **The logo is the one picture not drawn here.** The designer drew it twice,
  on its dark plate and with no background. `echolot-light.png` is the plate
  version, cut around the sphere with rounded corners, for the light theme and
  for PyPI. `echolot-dark.png` is the one with no background, on a canvas of
  the same size with the sphere at the same place and scale, for the dark
  theme. Both are 840 × 660, twice the size the README gives them, reduced to
  256 colours with libimagequant: a quarter of the size, and no difference
  the eye can find.
- **The card for links, not drawn here either.** `social-preview.png` is what
  GitHub shows where a link to the repository is shared: the plate version of
  the logo in the middle of a 1280 × 640 plate of the same slate, the size
  GitHub asks for. The logo takes the middle 550 × 446, so an app that crops
  the card to a square still shows all of it. GitHub takes the card only in
  Settings → General → Social preview and has no API for it, so a new one is
  uploaded there by hand. `tests/test_readme_pictures.py` holds it to that
  size and to GitHub's limit of 1 MB.
- **The plugin's icon, drawn here but not by render.py.**
  `plugins/echolot/.claude-plugin/icon.png` is what Claude's plugin directory
  shows beside the plugin's name: a circle's outline and the same circle
  shifted right four times, each copy's left arc ending on the outline, in
  the slate and the sphere's three colours. [icon.py](icon.py) draws it,
  1024 × 1024, with Pillow, which neither extra carries:
  `uv run --no-project --with pillow python docs/assets/icon.py`. The
  directory takes the icon once, at the first submission, so the script is
  kept for the next picture. `tests/test_plugin.py` holds the file to the
  directory's rules: a square PNG, 512 to 2048 px a side, under 2 MB.
- **Flat SVG, system fonts.** GitHub's own font stacks, no web fonts: an SVG
  served from this repository cannot load them.
- **Full addresses.** The README names each picture at
  `https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/…`,
  since PyPI resolves nothing relative. The link check leaves these addresses
  out, because a new picture is not on `main` until its pull request merges;
  the test checks instead that each one names a file in the tree.
- **A file keeps its name for good.** A release on PyPI shows its README's
  pictures from `main` for as long as it is up. The ones the README showed up
  to 0.12.0 stay here as they are: the old logo, the hero, the animated
  session, the hunt loop, the cost of a hunt and the comparison. `RETIRED` in
  the script lists them, nothing draws them any more, and the test fails when
  one goes. A new picture gets a new name.
- **No real project.** The names are the README's placeholders:
  `com.example.app`, `StoreRepository`.
