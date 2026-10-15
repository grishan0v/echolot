"""Draw the plugin's icon for Claude's plugin directory.

    uv run --no-project --with pillow python docs/assets/icon.py

writes plugins/echolot/.claude-plugin/icon.png, the path the directory reads
when plugin.json names no icon. The picture: a circle's outline and the same
circle shifted right four times, each copy drawn only inside the first, its
left arc ending on the outline. Wave fronts of a sonar ping, in the logo's
colours: the slate plate, and the sphere's mint, teal and blue from the top of
the circle down.

The directory takes the icon once, when a plugin is first submitted, so this
script is kept for the next picture rather than to redraw this one. Pillow is
in neither extra: nothing else here needs it.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent / "plugins" / "echolot" / ".claude-plugin" / "icon.png"

SIZE = 1024
SCALE = 4  # drawn four times larger and scaled down, for smooth edges
RADIUS = 0.36  # of the side
STROKE = 0.024  # of the side
ARCS = 5  # the outline and four shifted copies
REACH = 0.82  # how far right the last copy goes, as a share of the diameter


def _palette() -> tuple[str, tuple[str, ...]]:
    spec = importlib.util.spec_from_file_location("readme_pictures", HERE / "render.py")
    render = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render)
    return render.SLATE, render.SPHERE


def _rgb(hex_colour: str) -> tuple[int, ...]:
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def _gradient(side: int, top: float, bottom: float, stops) -> Image.Image:
    """The stops evenly from `top` to `bottom`, flat above and below."""
    img = Image.new("RGB", (side, side))
    draw = ImageDraw.Draw(img)
    for y in range(side):
        t = min(1.0, max(0.0, (y - top) / (bottom - top))) * (len(stops) - 1)
        i = min(int(t), len(stops) - 2)
        f = t - i
        colour = tuple(round(a + (b - a) * f) for a, b in zip(stops[i], stops[i + 1], strict=True))
        draw.line([(0, y), (side, y)], fill=colour)
    return img


def draw() -> Image.Image:
    slate, sphere = _palette()
    side = SIZE * SCALE
    r = side * RADIUS
    c = side / 2
    w = int(side * STROKE)
    mask = Image.new("L", (side, side), 0)
    pen = ImageDraw.Draw(mask)
    pen.ellipse([c - r, c - r, c + r, c + r], outline=255, width=w)
    mid = r - w / 2  # where the copies meet the outline: the strokes' centre lines
    step = 2 * mid * REACH / ARCS
    for i in range(1, ARCS):
        s = i * step
        h = math.sqrt(mid * mid - s * s / 4)
        below = math.degrees(math.atan2(h, -s / 2))  # PIL's angles run clockwise
        x = c + s
        pen.arc([x - r, c - r, x + r, c + r], below, 360 - below, fill=255, width=w)
    img = Image.new("RGB", (side, side), _rgb(slate))
    img.paste(_gradient(side, c - r, c + r, [_rgb(h) for h in sphere]), (0, 0), mask)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


if __name__ == "__main__":
    draw().save(OUT, optimize=True)
    print(f"{OUT.relative_to(HERE.parent.parent)}: {OUT.stat().st_size} bytes")
