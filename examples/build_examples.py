#!/usr/bin/env python
"""Regenerate the images used in README.md.

    python examples/build_examples.py

Doubles as a short worked example of using both modules as libraries.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import bongle_model as bm      # noqa: E402
import bongle_skin as bs       # noqa: E402

BG = (44, 48, 64, 255)
GAP = 22


def row(tiles: list[Image.Image], gap: int = GAP, align_bottom=True) -> Image.Image:
    width = sum(t.width for t in tiles) + gap * (len(tiles) - 1)
    height = max(t.height for t in tiles)
    sheet = Image.new("RGBA", (width, height), BG)
    x = 0
    for t in tiles:
        sheet.alpha_composite(t, (x, height - t.height if align_bottom else 0))
        x += t.width + gap
    return sheet


def lineup() -> Image.Image:
    """All three characters at one scale, on a shared ground line."""
    models = {name: build() for name, build in bm.CHARACTERS.items()}
    order = sorted(models, key=lambda n: -models[n].height)
    return row([models[n].elevation("front", scale=9) for n in order])


def otis_sheet() -> Image.Image:
    """Otis's atlas beside his elevations."""
    model = bm.build_otis()
    atlas = model.atlas.to_image(scale=5)
    views = [model.elevation(v, scale=5, bg="#2c3040")
             for v in ("front", "right", "back")]
    return row([atlas, row(views, gap=8)], gap=28, align_bottom=False)


def skin_grid() -> Image.Image:
    """Minecraft-format character skins, as front/side/back elevations."""
    import random
    tiles = []
    for seed in (3, 11, 27, 42):
        traits = bs.Traits.random(random.Random(seed))
        tiles.append(bs.paint_character(traits).preview(scale=5, bg="#2c3040"))
    return row(tiles, gap=12, align_bottom=False)


def uvkey() -> Image.Image:
    """The orientation-key placeholder atlas, and it applied to the model."""
    skin = bs.paint_uvkey(bs.Skin())
    return row([skin.to_image(scale=5), skin.preview(scale=5, bg="#2c3040")],
               gap=28, align_bottom=False)


if __name__ == "__main__":
    for name, build in (("lineup", lineup), ("otis", otis_sheet),
                        ("skins", skin_grid), ("uvkey", uvkey)):
        path = HERE / f"{name}.png"
        build().save(path)
        print(f"wrote {path.relative_to(HERE.parent)}")
