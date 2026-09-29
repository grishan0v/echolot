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

- the size of a trace and its report, the sample's window, tally and lock wait
  are read out of the README's own text; a README edit that moves one fails
  the test until the pictures are drawn again;
- the cost of a hunt is worked out from the recorded runs listed in the
  script, one number per run. The medians, the ratio and the count of models
  are computed from those runs and never typed.

## The rules

- **Two themes.** A themed picture comes as `<name>-light.svg` and
  `<name>-dark.svg`, and the README shows them through `<picture>` with the
  light one as the `<img>`. PyPI removes the `<source>` and always shows that
  `<img>`, on white. The terminal replay, `session.svg`, keeps its own colours
  and needs one file.
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
