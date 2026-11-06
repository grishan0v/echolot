"""Draw the plugin's icon for Claude's plugin directory.

    uv run --no-project --with pillow python docs/assets/icon.py

writes plugins/echolot/.claude-plugin/icon.png, the path the directory reads
when plugin.json names no icon. The picture is the logo's sphere of dots:
points spread evenly over a sphere, tilted so its pole does not face the
viewer, the half in front drawn as dots that grow and brighten toward the
viewer. Its colours are the logo's: the slate plate, and the sphere's mint,
teal and blue from the top of the sphere down.

The directory does not pick a new icon up from a release: it is uploaded
in the listing by hand. So this script is kept for the next picture rather
than to redraw this one. Pillow is in neither extra: nothing else here needs it.
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
POINTS = 420  # over the whole sphere; a little over half are in front
TILT = 0.45  # radians the sphere leans toward the viewer
SPIN = 0.3  # radians it turns about its axis
DOT = (0.006, 0.019)  # a dot's radius at the rim and facing the viewer, of the side


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


def _points() -> list[tuple[float, float, float]]:
    """The sphere's points in front, as (x, y, z) with z toward the viewer, far ones first."""
    golden = math.pi * (3 - math.sqrt(5))
    front = []
    for i in range(POINTS):
        y = 1 - 2 * (i + 0.5) / POINTS
        ring = math.sqrt(1 - y * y)
        x, z = math.cos(golden * i + SPIN) * ring, math.sin(golden * i + SPIN) * ring
        y, z = y * math.cos(TILT) - z * math.sin(TILT), y * math.sin(TILT) + z * math.cos(TILT)
        if z > -0.05:  # just past the rim too, so the outline stays round
            front.append((x, y, z))
    return sorted(front, key=lambda p: p[2])


def draw() -> Image.Image:
    slate, sphere = _palette()
    side = SIZE * SCALE
    r = side * RADIUS
    c = side / 2
    mask = Image.new("L", (side, side), 0)
    pen = ImageDraw.Draw(mask)
    for x, y, z in _points():
        near = max(0.0, z) ** 0.8
        dot = side * (DOT[0] + (DOT[1] - DOT[0]) * near)
        px, py = c + x * r, c - y * r
        pen.ellipse([px - dot, py - dot, px + dot, py + dot], fill=round(120 + 135 * near))
    img = Image.new("RGB", (side, side), _rgb(slate))
    img.paste(_gradient(side, c - r, c + r, [_rgb(h) for h in sphere]), (0, 0), mask)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


if __name__ == "__main__":
    draw().save(OUT, optimize=True)
    print(f"{OUT.relative_to(HERE.parent.parent)}: {OUT.stat().st_size} bytes")
