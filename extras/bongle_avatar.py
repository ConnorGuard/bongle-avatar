#!/usr/bin/env python
"""bongle-avatar - procedural pixel-art avatar generator.

Avatars are drawn as layered 16x24 pixel sprites (legs, torso, head, hair,
eyes, mouth, accessory) and written out as PNGs at any integer scale with
nearest-neighbour upscaling, so they stay crisp.

Every avatar is fully described by a Traits record, which means output is
reproducible from a seed or a name, and any single trait can be pinned by hand.

    python bongle_avatar.py avatars --count 12 --scale 8
    python bongle_avatar.py sheet --count 32 --cols 8 --scale 4
    python bongle_avatar.py parts --scale 6
    python bongle_avatar.py one --name connor --scale 16
    python bongle_avatar.py one --seed 7 --traits '{"hair_style":"mohawk"}'
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from PIL import Image

# --------------------------------------------------------------------------
# palettes
# --------------------------------------------------------------------------

SKIN = {
    "porcelain": "#f7d9c4",
    "peach": "#f1c39a",
    "tan": "#d9a06a",
    "olive": "#b9814f",
    "umber": "#8d5524",
    "espresso": "#5c3317",
    "mint": "#a8e0b5",
    "lilac": "#cdb4f0",
}

HAIR = {
    "black": "#1b1b23",
    "brown": "#5a3620",
    "chestnut": "#8b5a2b",
    "blonde": "#e8c86a",
    "ginger": "#c85a20",
    "silver": "#cfd4dc",
    "teal": "#2ec4b6",
    "pink": "#ff6fae",
    "violet": "#8b5cf6",
    "lime": "#a3e635",
}

CLOTH = {
    "cyan": "#2ec4b6",
    "teal": "#177e89",
    "azure": "#3b82f6",
    "navy": "#1f2a44",
    "rose": "#ff6fae",
    "crimson": "#e63946",
    "amber": "#f4a53c",
    "lemon": "#ffd166",
    "lime": "#8ac926",
    "forest": "#2f7d32",
    "violet": "#8b5cf6",
    "plum": "#5b2a63",
    "slate": "#5c677d",
    "cream": "#f2e8cf",
    "charcoal": "#2b2d34",
}

EYE = {
    "ink": "#1b1b23",
    "brown": "#5a3620",
    "blue": "#3b82f6",
    "green": "#2f7d32",
    "amber": "#f4a53c",
    "violet": "#8b5cf6",
}

INK = "#16161d"
WHITE = "#f6f7fb"

HAIR_STYLES = [
    "bald", "buzz", "short", "bowl", "spiky",
    "mohawk", "long", "ponytail", "afro", "bun", "pigtails",
]
EYE_STYLES = ["dots", "round", "sleepy", "wink", "shades", "visor"]
MOUTHS = ["smile", "flat", "open", "grin", "smirk"]
OUTFITS = ["tee", "tank", "hoodie", "overalls", "stripes", "collar"]
ACCESSORIES = ["none", "none", "glasses", "headphones", "earrings", "cap", "scarf", "antenna"]

# --------------------------------------------------------------------------
# geometry - a 16x24 grid, rows allocated top to bottom
# --------------------------------------------------------------------------

W, H = 16, 24

HEAD_X0, HEAD_X1 = 4, 11
HEAD_Y0, HEAD_Y1 = 3, 10
NECK_Y = 11
TORSO_X0, TORSO_X1 = 4, 11
TORSO_Y0, TORSO_Y1 = 12, 17
ARM_LX = (2, 3)
ARM_RX = (12, 13)
ARM_Y0, ARM_Y1 = 12, 16
HIP_Y = 18
LEG_Y0, LEG_Y1 = 19, 21
SHOE_Y0, SHOE_Y1 = 22, 23
EYE_LX, EYE_RX = 6, 9
EYE_Y = 7
MOUTH_Y = 9

HEAD_CROP_H = NECK_Y + 1  # rows kept by --head-only


# --------------------------------------------------------------------------
# colour helpers
# --------------------------------------------------------------------------

def rgba(spec, alpha=255):
    """Accept '#rrggbb' or an existing RGBA tuple."""
    if spec is None or isinstance(spec, tuple):
        return spec
    s = spec.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), alpha)


def darken(color, amount=0.18):
    r, g, b, a = rgba(color)
    f = 1.0 - amount
    return (int(r * f), int(g * f), int(b * f), a)


def lighten(color, amount=0.18):
    r, g, b, a = rgba(color)
    return (
        int(r + (255 - r) * amount),
        int(g + (255 - g) * amount),
        int(b + (255 - b) * amount),
        a,
    )


# --------------------------------------------------------------------------
# canvas
# --------------------------------------------------------------------------

class Canvas:
    def __init__(self, w=W, h=H):
        self.w, self.h = w, h
        self.px = [[None] * w for _ in range(h)]

    def set(self, x, y, color):
        if color is None:
            return
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y][x] = rgba(color)

    def rect(self, x0, y0, x1, y1, color):
        """Filled rectangle, inclusive of both corners."""
        for y in range(min(y0, y1), max(y0, y1) + 1):
            for x in range(min(x0, x1), max(x0, x1) + 1):
                self.set(x, y, color)

    def hline(self, x0, x1, y, color):
        self.rect(x0, y, x1, y, color)

    def vline(self, x, y0, y1, color):
        self.rect(x, y0, x, y1, color)

    def get(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.px[y][x]
        return None

    def add_outline(self, color=INK):
        """Ring every filled region with a 1px border in the empty pixels."""
        col = rgba(color)
        edges = []
        for y in range(self.h):
            for x in range(self.w):
                if self.px[y][x] is not None:
                    continue
                if any(self.get(x + dx, y + dy) is not None
                       for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    edges.append((x, y))
        for x, y in edges:
            self.px[y][x] = col

    def crop_rows(self, y0, y1):
        out = Canvas(self.w, y1 - y0)
        out.px = [row[:] for row in self.px[y0:y1]]
        return out

    def to_image(self, scale=1, bg=None):
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        pixels = img.load()
        for y in range(self.h):
            for x in range(self.w):
                color = self.px[y][x]
                if color is not None:
                    pixels[x, y] = color
        if bg is not None:
            back = Image.new("RGBA", img.size, rgba(bg))
            img = Image.alpha_composite(back, img)
        if scale > 1:
            img = img.resize((self.w * scale, self.h * scale), Image.NEAREST)
        return img


# --------------------------------------------------------------------------
# traits
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Traits:
    skin: str = "peach"
    hair: str = "brown"
    hair_style: str = "short"
    eyes: str = "dots"
    eye_color: str = "ink"
    mouth: str = "smile"
    outfit: str = "tee"
    shirt: str = "cyan"
    pants: str = "navy"
    shoes: str = "charcoal"
    accessory: str = "none"

    @staticmethod
    def random(rng: random.Random) -> "Traits":
        return Traits(
            skin=rng.choice(list(SKIN)),
            hair=rng.choice(list(HAIR)),
            hair_style=rng.choice(HAIR_STYLES),
            eyes=rng.choice(EYE_STYLES),
            eye_color=rng.choice(list(EYE)),
            mouth=rng.choice(MOUTHS),
            outfit=rng.choice(OUTFITS),
            shirt=rng.choice(list(CLOTH)),
            pants=rng.choice(list(CLOTH)),
            shoes=rng.choice(list(CLOTH)),
            accessory=rng.choice(ACCESSORIES),
        )

    def validate(self) -> None:
        tables = {
            "skin": SKIN, "hair": HAIR, "hair_style": HAIR_STYLES,
            "eyes": EYE_STYLES, "eye_color": EYE, "mouth": MOUTHS,
            "outfit": OUTFITS, "shirt": CLOTH, "pants": CLOTH,
            "shoes": CLOTH, "accessory": ACCESSORIES,
        }
        for field, table in tables.items():
            value = getattr(self, field)
            if value not in table:
                raise ValueError(
                    f"unknown {field}={value!r}; choose from {sorted(set(table))}"
                )


def seed_from_name(name: str) -> int:
    """Stable across runs and machines, unlike hash()."""
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big")


# --------------------------------------------------------------------------
# layers
# --------------------------------------------------------------------------

def draw_legs(c: Canvas, t: Traits, shading: bool):
    pants = rgba(CLOTH[t.pants])
    shoes = rgba(CLOTH[t.shoes])
    c.rect(5, HIP_Y, 10, HIP_Y, pants)
    c.rect(5, LEG_Y0, 6, LEG_Y1, pants)
    c.rect(9, LEG_Y0, 10, LEG_Y1, pants)
    if shading:
        c.vline(6, LEG_Y0, LEG_Y1, darken(pants))
        c.vline(10, LEG_Y0, LEG_Y1, darken(pants))
    c.rect(4, SHOE_Y0, 6, SHOE_Y0, shoes)
    c.rect(9, SHOE_Y0, 11, SHOE_Y0, shoes)
    sole = darken(shoes, 0.35) if shading else shoes
    c.rect(4, SHOE_Y1, 6, SHOE_Y1, sole)
    c.rect(9, SHOE_Y1, 11, SHOE_Y1, sole)


def draw_arms(c: Canvas, t: Traits, sleeve_to: int, shading: bool):
    """Sleeves down to sleeve_to, bare skin below that."""
    shirt = rgba(CLOTH[t.shirt])
    skin = rgba(SKIN[t.skin])
    for x0, x1 in (ARM_LX, ARM_RX):
        for y in range(ARM_Y0, ARM_Y1 + 1):
            color = shirt if y <= sleeve_to else skin
            c.rect(x0, y, x1, y, color)
    if shading:
        c.vline(ARM_LX[0], ARM_Y0, ARM_Y1, darken(c.get(ARM_LX[0], ARM_Y0) or shirt, 0.12))
        for y in range(ARM_Y0, ARM_Y1 + 1):
            base = c.get(ARM_RX[1], y)
            if base:
                c.set(ARM_RX[1], y, darken(base))


def draw_torso(c: Canvas, t: Traits, shading: bool):
    shirt = rgba(CLOTH[t.shirt])
    pants = rgba(CLOTH[t.pants])
    skin = rgba(SKIN[t.skin])
    outfit = t.outfit

    if outfit == "tank":
        c.rect(TORSO_X0, TORSO_Y0, TORSO_X1, TORSO_Y0, skin)
        c.rect(TORSO_X0, TORSO_Y0 + 1, TORSO_X1, TORSO_Y1, shirt)
        c.set(5, TORSO_Y0, shirt)
        c.set(10, TORSO_Y0, shirt)
        draw_arms(c, t, sleeve_to=ARM_Y0 - 1, shading=shading)
    elif outfit == "hoodie":
        c.rect(TORSO_X0, TORSO_Y0, TORSO_X1, TORSO_Y1, shirt)
        c.rect(TORSO_X0, TORSO_Y0, TORSO_X1, TORSO_Y0, lighten(shirt, 0.22))
        c.rect(6, TORSO_Y1 - 1, 9, TORSO_Y1, darken(shirt, 0.22))
        c.set(6, TORSO_Y0 + 1, rgba(CLOTH["cream"]))
        c.set(9, TORSO_Y0 + 1, rgba(CLOTH["cream"]))
        draw_arms(c, t, sleeve_to=ARM_Y1 - 1, shading=shading)
    elif outfit == "overalls":
        c.rect(TORSO_X0, TORSO_Y0, TORSO_X1, TORSO_Y1, shirt)
        c.rect(TORSO_X0, TORSO_Y0 + 2, TORSO_X1, TORSO_Y1, pants)
        c.rect(5, TORSO_Y0, 5, TORSO_Y0 + 1, pants)
        c.rect(10, TORSO_Y0, 10, TORSO_Y0 + 1, pants)
        c.set(5, TORSO_Y0 + 2, rgba(CLOTH["lemon"]))
        c.set(10, TORSO_Y0 + 2, rgba(CLOTH["lemon"]))
        draw_arms(c, t, sleeve_to=ARM_Y0 + 1, shading=shading)
    elif outfit == "stripes":
        for i, y in enumerate(range(TORSO_Y0, TORSO_Y1 + 1)):
            c.rect(TORSO_X0, y, TORSO_X1, y, shirt if i % 2 == 0 else lighten(shirt, 0.35))
        draw_arms(c, t, sleeve_to=ARM_Y0 + 1, shading=shading)
    elif outfit == "collar":
        c.rect(TORSO_X0, TORSO_Y0, TORSO_X1, TORSO_Y1, shirt)
        cream = rgba(CLOTH["cream"])
        c.set(5, TORSO_Y0, cream)
        c.set(10, TORSO_Y0, cream)
        c.set(6, TORSO_Y0 + 1, cream)
        c.set(9, TORSO_Y0 + 1, cream)
        c.vline(7, TORSO_Y0 + 2, TORSO_Y1, darken(shirt, 0.25))
        draw_arms(c, t, sleeve_to=ARM_Y1, shading=shading)
    else:  # tee
        c.rect(TORSO_X0, TORSO_Y0, TORSO_X1, TORSO_Y1, shirt)
        draw_arms(c, t, sleeve_to=ARM_Y0 + 2, shading=shading)

    if shading:
        c.vline(TORSO_X1, TORSO_Y0 + 1, TORSO_Y1, darken(c.get(TORSO_X1, TORSO_Y1) or shirt))


def draw_head(c: Canvas, t: Traits, shading: bool):
    skin = rgba(SKIN[t.skin])
    shade = darken(skin)
    c.rect(6, NECK_Y, 9, NECK_Y, shade if shading else skin)
    c.rect(HEAD_X0, HEAD_Y0, HEAD_X1, HEAD_Y1, skin)
    c.set(HEAD_X0 - 1, HEAD_Y0 + 4, skin)   # ears
    c.set(HEAD_X1 + 1, HEAD_Y0 + 4, skin)
    if shading:
        c.vline(HEAD_X1, HEAD_Y0 + 1, HEAD_Y1, shade)
        c.hline(HEAD_X0 + 1, HEAD_X1, HEAD_Y1, shade)
        c.set(HEAD_X1 + 1, HEAD_Y0 + 4, shade)


def draw_hair(c: Canvas, t: Traits, shading: bool):
    style = t.hair_style
    if style == "bald":
        return
    col = rgba(HAIR[t.hair])
    hi = lighten(col, 0.22)

    if style == "buzz":
        c.rect(4, 3, 11, 3, col)
        c.set(4, 4, col)
        c.set(11, 4, col)
    elif style == "short":
        c.rect(4, 2, 11, 4, col)
        c.vline(4, 5, 6, col)
        c.vline(11, 5, 6, col)
    elif style == "bowl":
        c.rect(4, 2, 11, 5, col)
        c.vline(4, 6, 8, col)
        c.vline(11, 6, 8, col)
        c.set(5, 1, col)
        c.rect(6, 1, 9, 1, col)
        c.set(10, 1, col)
    elif style == "spiky":
        c.rect(4, 3, 11, 4, col)
        for x, top in ((4, 2), (5, 1), (6, 2), (7, 0), (8, 1), (9, 2), (10, 1), (11, 2)):
            c.vline(x, top, 2, col)
    elif style == "mohawk":
        c.rect(4, 3, 11, 3, darken(rgba(SKIN[t.skin]), 0.12))
        c.rect(6, 1, 9, 4, col)
        c.rect(7, 0, 8, 0, col)
        c.set(5, 3, col)
        c.set(10, 3, col)
    elif style == "long":
        c.rect(4, 2, 11, 5, col)
        c.hline(4, 8, 6, col)
        c.vline(3, 4, 13, col)
        c.vline(12, 4, 13, col)
        c.set(2, 6, col)
        c.set(13, 6, col)
    elif style == "ponytail":
        c.rect(4, 2, 11, 4, col)
        c.vline(4, 5, 5, col)
        c.rect(12, 4, 13, 4, col)
        c.rect(12, 5, 12, 9, col)
        c.set(13, 8, col)
    elif style == "afro":
        c.rect(3, 1, 12, 4, col)
        c.vline(2, 2, 5, col)
        c.vline(13, 2, 5, col)
        c.rect(5, 0, 10, 0, col)
        c.hline(5, 8, 5, col)
    elif style == "bun":
        c.rect(4, 3, 11, 4, col)
        c.rect(6, 0, 9, 1, col)
        c.rect(5, 1, 10, 2, col)
    elif style == "pigtails":
        c.rect(4, 2, 11, 4, col)
        c.rect(2, 5, 3, 8, col)
        c.rect(12, 5, 13, 8, col)
        c.set(3, 4, col)
        c.set(12, 4, col)

    if shading:
        for x in range(c.w):
            for y in range(c.h):
                if c.get(x, y) == col and (y == 0 or c.get(x, y - 1) is None):
                    c.set(x, y, hi)
                    break


def draw_eyes(c: Canvas, t: Traits):
    ink = rgba(INK)
    iris = rgba(EYE[t.eye_color])
    white = rgba(WHITE)
    style = t.eyes

    if style == "dots":
        c.set(EYE_LX, EYE_Y, ink)
        c.set(EYE_RX, EYE_Y, ink)
    elif style == "round":
        for x in (EYE_LX - 1, EYE_RX - 1):
            c.rect(x, EYE_Y - 1, x + 1, EYE_Y, white)
        c.set(EYE_LX, EYE_Y, iris)
        c.set(EYE_RX, EYE_Y, iris)
        c.set(EYE_LX - 1, EYE_Y - 1, ink)
        c.set(EYE_RX, EYE_Y - 1, ink)
    elif style == "sleepy":
        c.hline(EYE_LX - 1, EYE_LX, EYE_Y, ink)
        c.hline(EYE_RX, EYE_RX + 1, EYE_Y, ink)
    elif style == "wink":
        c.rect(EYE_LX - 1, EYE_Y - 1, EYE_LX, EYE_Y, white)
        c.set(EYE_LX, EYE_Y, iris)
        c.hline(EYE_RX, EYE_RX + 1, EYE_Y, ink)
    elif style == "shades":
        c.rect(5, EYE_Y - 1, 10, EYE_Y, rgba(CLOTH["charcoal"]))
        c.set(5, EYE_Y - 1, lighten(rgba(CLOTH["charcoal"]), 0.45))
        c.set(4, EYE_Y - 1, ink)
        c.set(11, EYE_Y - 1, ink)
    elif style == "visor":
        c.rect(4, EYE_Y - 1, 11, EYE_Y, iris)
        c.hline(4, 11, EYE_Y - 1, lighten(iris, 0.4))


def draw_mouth(c: Canvas, t: Traits):
    ink = rgba(INK)
    y = MOUTH_Y
    style = t.mouth
    if style == "smile":
        c.set(6, y - 1, ink)
        c.set(9, y - 1, ink)
        c.hline(7, 8, y, ink)
    elif style == "flat":
        c.hline(7, 8, y, ink)
    elif style == "open":
        c.rect(7, y, 8, y + 1, ink)
        c.set(7, y, darken(rgba("#c04a5a"), 0.0))
        c.set(8, y, rgba("#c04a5a"))
    elif style == "grin":
        c.hline(6, 9, y, ink)
        c.set(6, y - 1, ink)
        c.set(9, y - 1, ink)
        c.set(7, y - 1, rgba(WHITE))
        c.set(8, y - 1, rgba(WHITE))
    elif style == "smirk":
        c.hline(7, 8, y, ink)
        c.set(9, y - 1, ink)


def _contrast_cloth(rng: random.Random, *avoid: str):
    """Pick a cloth colour that isn't already worn, so the piece reads clearly."""
    options = [k for k in CLOTH if k not in avoid] or list(CLOTH)
    return rgba(CLOTH[rng.choice(options)])


def draw_accessory(c: Canvas, t: Traits, rng: random.Random):
    kind = t.accessory
    if kind == "none":
        return
    ink = rgba(INK)
    if kind == "glasses":
        frame = ink
        c.rect(5, EYE_Y - 2, 6, EYE_Y - 2, frame)
        c.rect(9, EYE_Y - 2, 10, EYE_Y - 2, frame)
        c.set(4, EYE_Y - 1, frame)
        c.set(11, EYE_Y - 1, frame)
        c.set(7, EYE_Y - 2, frame)
        c.set(8, EYE_Y - 2, frame)
        c.set(5, EYE_Y + 1, frame)
        c.set(6, EYE_Y + 1, frame)
        c.set(9, EYE_Y + 1, frame)
        c.set(10, EYE_Y + 1, frame)
    elif kind == "headphones":
        cup = _contrast_cloth(rng, t.shirt, t.hair)
        band = rgba(CLOTH["charcoal"])
        c.rect(2, 5, 3, 8, cup)          # ear cups, outside the head box
        c.rect(12, 5, 13, 8, cup)
        c.vline(2, 5, 8, darken(cup, 0.3))
        c.vline(13, 5, 8, darken(cup, 0.3))
        c.hline(4, 11, 1, band)          # headband arcing over the hair
        c.set(3, 2, band)
        c.set(12, 2, band)
        c.set(3, 3, band)
        c.set(12, 3, band)
        c.set(3, 4, band)
        c.set(12, 4, band)
    elif kind == "earrings":
        gold = rgba(CLOTH["lemon"])
        c.set(3, 9, gold)
        c.set(12, 9, gold)
    elif kind == "cap":
        col = _contrast_cloth(rng, t.shirt)
        c.rect(4, 2, 11, 3, col)
        c.rect(5, 1, 10, 1, col)
        c.rect(3, 4, 12, 4, darken(col, 0.28))
        c.set(2, 4, darken(col, 0.28))
    elif kind == "scarf":
        col = _contrast_cloth(rng, t.shirt)
        c.rect(4, NECK_Y, 11, NECK_Y, col)
        c.rect(4, NECK_Y + 1, 11, NECK_Y + 1, col)
        c.rect(9, NECK_Y + 2, 10, NECK_Y + 4, col)
        c.hline(4, 11, NECK_Y + 1, darken(col, 0.22))
    elif kind == "antenna":
        c.vline(8, 0, 2, rgba(CLOTH["slate"]))
        c.set(8, 0, rgba(CLOTH["lemon"]))
        c.set(7, 0, rgba(CLOTH["lemon"]))


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------

def render(t: Traits, *, shading=True, outline=False, head_only=False,
           rng: random.Random | None = None) -> Canvas:
    t.validate()
    rng = rng or random.Random(seed_from_name(json.dumps(asdict(t), sort_keys=True)))
    c = Canvas()
    draw_legs(c, t, shading)
    draw_torso(c, t, shading)
    draw_head(c, t, shading)
    draw_hair(c, t, shading)
    draw_eyes(c, t)
    draw_mouth(c, t)
    draw_accessory(c, t, rng)
    if head_only:
        c = c.crop_rows(0, HEAD_CROP_H)
    if outline:
        c.add_outline()  # after cropping, so a bust gets a clean silhouette
    return c


def compose_sheet(canvases, cols, pad=1, scale=1, bg=None):
    if not canvases:
        raise ValueError("nothing to compose")
    cw = max(c.w for c in canvases)
    ch = max(c.h for c in canvases)
    rows = (len(canvases) + cols - 1) // cols
    sheet = Canvas(cols * cw + pad * (cols - 1), rows * ch + pad * (rows - 1))
    for i, c in enumerate(canvases):
        ox = (i % cols) * (cw + pad)
        oy = (i // cols) * (ch + pad)
        for y in range(c.h):
            for x in range(c.w):
                color = c.get(x, y)
                if color is not None:
                    sheet.set(ox + x, oy + y, color)
    return sheet.to_image(scale=scale, bg=bg)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _rng(seed):
    return random.Random(seed) if seed is not None else random.Random()


def _render_opts(args):
    return dict(shading=not args.flat, outline=args.outline,
                head_only=args.head_only)


def cmd_avatars(args):
    rng = _rng(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i in range(args.count):
        sub_seed = rng.getrandbits(48)
        traits = Traits.random(random.Random(sub_seed))
        canvas = render(traits, rng=random.Random(sub_seed), **_render_opts(args))
        name = f"avatar_{i:04d}.png"
        canvas.to_image(scale=args.scale, bg=args.bg).save(args.out / name)
        manifest.append({"file": name, "seed": sub_seed, "traits": asdict(traits)})
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {args.count} avatars + manifest.json to {args.out}")


def cmd_sheet(args):
    rng = _rng(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    canvases, manifest = [], []
    for i in range(args.count):
        sub_seed = rng.getrandbits(48)
        traits = Traits.random(random.Random(sub_seed))
        canvases.append(render(traits, rng=random.Random(sub_seed), **_render_opts(args)))
        manifest.append({"index": i, "seed": sub_seed, "traits": asdict(traits)})
    path = args.out / args.name
    compose_sheet(canvases, cols=args.cols, pad=args.pad,
                  scale=args.scale, bg=args.bg).save(path)
    (args.out / (path.stem + ".json")).write_text(json.dumps(manifest, indent=2))
    print(f"wrote {path} ({args.count} sprites, {args.cols} cols)")


def cmd_one(args):
    if args.name:
        seed = seed_from_name(args.name)
    elif args.seed is not None:
        seed = args.seed
    else:
        seed = random.getrandbits(48)
    traits = Traits.random(random.Random(seed))
    if args.traits:
        overrides = json.loads(args.traits)
        unknown = set(overrides) - set(asdict(traits))
        if unknown:
            raise SystemExit(f"unknown trait keys: {sorted(unknown)}")
        traits = replace(traits, **overrides)
    canvas = render(traits, rng=random.Random(seed), **_render_opts(args))
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.name or f"seed_{seed}"
    path = args.out / f"{stem}.png"
    canvas.to_image(scale=args.scale, bg=args.bg).save(path)
    (args.out / f"{stem}.json").write_text(
        json.dumps({"seed": seed, "traits": asdict(traits)}, indent=2))
    print(f"wrote {path}")
    print(json.dumps(asdict(traits), indent=2))


def cmd_parts(args):
    """Reference sheet: one row per trait category, one column per variant."""
    base = Traits()
    categories = [
        ("skin", list(SKIN)),
        ("hair", list(HAIR)),
        ("hair_style", HAIR_STYLES),
        ("eyes", EYE_STYLES),
        ("mouth", MOUTHS),
        ("outfit", OUTFITS),
        ("shirt", list(CLOTH)),
        ("accessory", [a for a in dict.fromkeys(ACCESSORIES)]),
    ]
    cols = max(len(v) for _, v in categories)
    canvases, manifest = [], []
    for field, variants in categories:
        for i in range(cols):
            if i < len(variants):
                traits = replace(base, **{field: variants[i]})
                canvases.append(render(traits, rng=random.Random(i),
                                       **_render_opts(args)))
                manifest.append({"row": field, "col": i, "value": variants[i]})
            else:
                canvases.append(Canvas(W, HEAD_CROP_H if args.head_only else H))
                manifest.append({"row": field, "col": i, "value": None})
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "parts.png"
    compose_sheet(canvases, cols=cols, pad=args.pad,
                  scale=args.scale, bg=args.bg).save(path)
    (args.out / "parts.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {path} ({len(categories)} rows x {cols} cols)")
    for field, variants in categories:
        print(f"  {field}: {', '.join(variants)}")


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--scale", type=int, default=8,
                        help="integer nearest-neighbour upscale (default 8)")
    common.add_argument("--out", type=Path, default=Path("out"),
                        help="output directory (default ./out)")
    common.add_argument("--bg", default=None,
                        help="background colour like '#1b1b23' (default transparent)")
    common.add_argument("--outline", action="store_true",
                        help="add a 1px dark outline around the sprite")
    common.add_argument("--flat", action="store_true",
                        help="disable shading for a flat look")
    common.add_argument("--head-only", action="store_true",
                        help="crop to the head (16x12) instead of full body")

    p = argparse.ArgumentParser(
        prog="bongle_avatar",
        description="Procedural pixel-art avatar PNG generator.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("avatars", parents=[common],
                       help="write N individual avatar PNGs")
    a.add_argument("--count", type=int, default=8)
    a.add_argument("--seed", type=int, default=None)
    a.set_defaults(func=cmd_avatars)

    s = sub.add_parser("sheet", parents=[common],
                       help="write one sprite sheet of N avatars")
    s.add_argument("--count", type=int, default=32)
    s.add_argument("--cols", type=int, default=8)
    s.add_argument("--pad", type=int, default=1, help="gap in sprite pixels")
    s.add_argument("--seed", type=int, default=None)
    s.add_argument("--name", default="sheet.png")
    s.set_defaults(func=cmd_sheet)

    o = sub.add_parser("one", parents=[common],
                       help="write a single avatar from a name, seed or traits")
    o.add_argument("--name", default=None, help="deterministic avatar for a string")
    o.add_argument("--seed", type=int, default=None)
    o.add_argument("--traits", default=None,
                   help='JSON trait overrides, e.g. \'{"hair_style":"afro"}\'')
    o.set_defaults(func=cmd_one)

    r = sub.add_parser("parts", parents=[common],
                       help="reference sheet of every trait variant")
    r.add_argument("--pad", type=int, default=1)
    r.set_defaults(func=cmd_parts)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
