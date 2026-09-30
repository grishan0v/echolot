# The README's pictures

[← Docs index](../README.md) · [README](../../README.md)

Every picture in the README is drawn by [render.py](render.py) and committed
next to it. Change the script or the README, then draw them again:

```bash
python docs/assets/render.py                     # writes the .svg files here
python docs/assets/render.py --preview /tmp/pv   # plus two README-like pages, light and dark
```

`tests/test_readme_pictures.py` draws everything once more and fails when a
committed file differs from what the script draws now.

## Where the numbers come from

A number in a picture is a claim like a number in a sentence, so it comes from
the same place:

- the size of a trace and its report, the sample's window, tally and lock wait,
  and the number of repeats in the `collect` example are read out of the
  README's own text; a README edit that moves one fails the test until the
  pictures are drawn again;
- the number of rounds a hunt may take is read out of `perf-hunter.md`, the
  instructions the agent follows;
- the rows of the comparison are read out of the sample table in
  `docs/compare.md`, which `tests/test_docs.py` holds to what `compare` prints;
- the cost of a hunt is worked out from the recorded runs listed in the
  script, one number per run. The medians, the ratio and the count of models
  are computed from those runs and never typed.

## The rules

- **Two themes.** A themed picture comes as `<name>-light.svg` and
  `<name>-dark.svg`, and the README shows them through `<picture>` with the
  light one as the `<img>`. PyPI removes the `<source>` and always shows that
  `<img>`, on white. The terminal replay, `session.svg`, keeps its own colours
  and needs one file.
- **A phone gets its own hero.** Words inside an SVG shrink with it: the
  880-wide hero on a 375-pixel screen had words 5 pixels high.
  `hero-narrow.svg` stacks the same panels at 360 wide, and the hero's
  `<picture>` offers it first, for screens up to 767 pixels. That `<source>`
  asks for the width alone: GitHub rewrites the theme half of a condition that
  names both. So the narrow file is one file, and CSS inside it follows the
  reader's system theme.
- **The first screen owns no margins.** The hero and the session are cropped
  to what they draw, so their left edge is the text's and the space between
  blocks is the README's own paragraph spacing. Their words come in two sizes:
  16 for what a thing is, 13 for the detail under it.
- **The logo is the one picture not drawn here.** `logo-light.png` is the
  designer's lockup; `logo-dark.png` is the same bitmap with the plate and the
  letters swapped. A new lockup replaces both.
- **Flat SVG, system fonts.** GitHub's own font stacks, no web fonts: an SVG
  served from this repository cannot load them. Motion is CSS inside the SVG,
  and it stops for readers who ask for reduced motion.
- **Full addresses.** The README names each picture at
  `https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/…`,
  since PyPI resolves nothing relative. A file keeps its name for good: a
  release on PyPI shows these addresses for as long as it is up. The link
  check leaves these addresses out, because a new picture is not on `main`
  until its pull request merges; the test checks instead that each one names
  a file in the tree.
- **No real project.** The names are the README's placeholders:
  `com.example.app`, `StoreRepository`.
- **Measurements carry their caption:** what was measured, how many runs, the
  echolot version.
- **Colour means one thing.** Deep teal is echolot; the warm end of the logo's
  palette is where the time went. Everything else is grey.
