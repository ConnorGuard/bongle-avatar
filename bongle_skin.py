#!/usr/bin/env python
"""bongle-skin - generator for 64x64 pixel-art avatar model skins.

Writes Minecraft-format skin atlases: a 64x64 RGBA PNG whose islands are the
unwrapped cube faces of a humanoid model (head, torso, two arms, two legs),
each as base layer plus optional overlay layer. Supports classic (4px) and
slim (3px) arms.

Two paint modes:

  uvkey      Orientation-key placeholder - one hue per facing with a 1px
             darker border, eye marks on the head front. Byte-identical to
             the reference player.png; see the `verify` command.
  character  Actual avatars - skin tone, hair, face, outfit painted onto the
             correct faces, randomised from a seed or a name.

    python bongle_skin.py uvkey --out out
    python bongle_skin.py character --count 8 --preview
    python bongle_skin.py character --name connor --preview --scale 8
    python bongle_skin.py verify path/to/player.png
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

ATLAS = 64
FACES = ("top", "bottom", "right", "front", "left", "back")


# --------------------------------------------------------------------------
# model geometry
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Part:
    """A box of the model and where its two layers live in the atlas."""
    base: tuple[int, int]
    overlay: tuple[int, int]
    size: tuple[int, int, int]  # width, height, depth in model pixels


def model(slim: bool = False) -> dict[str, Part]:
    """The 64x64 skin layout. Arm width is 3 for slim, 4 for classic."""
    aw = 3 if slim else 4
    return {
        "head":      Part(base=(0, 0),   overlay=(32, 0),  size=(8, 8, 8)),
        "torso":     Part(base=(16, 16), overlay=(16, 32), size=(8, 12, 4)),
        "right_arm": Part(base=(40, 16), overlay=(40, 32), size=(aw, 12, 4)),
        "left_arm":  Part(base=(32, 48), overlay=(48, 48), size=(aw, 12, 4)),
        "right_leg": Part(base=(0, 16),  overlay=(0, 32),  size=(4, 12, 4)),
        "left_leg":  Part(base=(16, 48), overlay=(0, 48),  size=(4, 12, 4)),
    }


def face_uv(origin: tuple[int, int], size: tuple[int, int, int], face: str):
    """Box unwrap: top/bottom sit in a strip above the four side faces."""
    ox, oy = origin
    w, h, d = size
    return {
        "top":    (ox + d,         oy,     w, d),
        "bottom": (ox + d + w,     oy,     w, d),
        "right":  (ox,             oy + d, d, h),
        "front":  (ox + d,         oy + d, w, h),
        "left":   (ox + d + w,     oy + d, d, h),
        "back":   (ox + d + w + d, oy + d, w, h),
    }[face]


# --------------------------------------------------------------------------
# colour
# --------------------------------------------------------------------------

def rgba(spec, alpha=255):
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
    return (int(r + (255 - r) * amount), int(g + (255 - g) * amount),
            int(b + (255 - b) * amount), a)


# --------------------------------------------------------------------------
# atlas + face views
# --------------------------------------------------------------------------

class FaceView:
    """Draw on one cube face in face-local coordinates."""

    def __init__(self, skin: "Skin", x: int, y: int, w: int, h: int):
        self.skin, self.x, self.y, self.w, self.h = skin, x, y, w, h

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.skin.set(self.x + x, self.y + y, color)

    def get(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.skin.get(self.x + x, self.y + y)
        return None

    def fill(self, color):
        self.rect(0, 0, self.w - 1, self.h - 1, color)

    def rect(self, x0, y0, x1, y1, color):
        for y in range(max(0, min(y0, y1)), min(self.h - 1, max(y0, y1)) + 1):
            for x in range(max(0, min(x0, x1)), min(self.w - 1, max(x0, x1)) + 1):
                self.set(x, y, color)

    def rows(self, y0, y1, color):
        self.rect(0, y0, self.w - 1, y1, color)

    def border(self, color):
        self.rect(0, 0, self.w - 1, 0, color)
        self.rect(0, self.h - 1, self.w - 1, self.h - 1, color)
        self.rect(0, 0, 0, self.h - 1, color)
        self.rect(self.w - 1, 0, self.w - 1, self.h - 1, color)

    def shade_edges(self, amount=0.12):
        """Darken the 1px rim of whatever is already painted."""
        for x in range(self.w):
            for y in range(self.h):
                on_rim = x in (0, self.w - 1) or y in (0, self.h - 1)
                base = self.get(x, y)
                if on_rim and base is not None:
                    self.set(x, y, darken(base, amount))


class Skin:
    def __init__(self, slim: bool = False):
        self.slim = slim
        self.parts = model(slim)
        self.px = [[None] * ATLAS for _ in range(ATLAS)]

    def set(self, x, y, color):
        if color is not None and 0 <= x < ATLAS and 0 <= y < ATLAS:
            self.px[y][x] = rgba(color)

    def get(self, x, y):
        if 0 <= x < ATLAS and 0 <= y < ATLAS:
            return self.px[y][x]
        return None

    def face(self, part: str, face: str, layer: str = "base") -> FaceView:
        if part not in self.parts:
            raise ValueError(f"unknown part {part!r}; have {sorted(self.parts)}")
        if face not in FACES:
            raise ValueError(f"unknown face {face!r}; have {list(FACES)}")
        p = self.parts[part]
        origin = p.base if layer == "base" else p.overlay
        return FaceView(self, *face_uv(origin, p.size, face))

    def to_image(self, scale: int = 1) -> Image.Image:
        img = Image.new("RGBA", (ATLAS, ATLAS), (0, 0, 0, 0))
        px = img.load()
        for y in range(ATLAS):
            for x in range(ATLAS):
                c = self.px[y][x]
                if c is not None:
                    px[x, y] = c
        if scale > 1:
            img = img.resize((ATLAS * scale, ATLAS * scale), Image.NEAREST)
        return img

    def preview(self, scale: int = 1, bg=None, views=("front", "right", "back", "left")):
        """Orthographic elevations assembled from the painted faces.

        Each view is 16x32 model pixels: head over torso, arms flanking,
        legs below - the standard proportions of the model these UVs wrap.
        """
        vw, vh = 16, 32
        gap = 1
        canvas = Image.new("RGBA", ((vw + gap) * len(views) - gap, vh), (0, 0, 0, 0))
        for i, view in enumerate(views):
            tile = self._elevation(view)
            canvas.alpha_composite(tile, ((vw + gap) * i, 0))
        if bg is not None:
            back = Image.new("RGBA", canvas.size, rgba(bg))
            canvas = Image.alpha_composite(back, canvas)
        if scale > 1:
            canvas = canvas.resize((canvas.width * scale, canvas.height * scale),
                                   Image.NEAREST)
        return canvas

    def _elevation(self, view: str) -> Image.Image:
        """One 16x32 elevation. Left/right views show the depth faces."""
        tile = Image.new("RGBA", (16, 32), (0, 0, 0, 0))
        px = tile.load()

        def blit(part, face, dx, dy, mirror=False):
            """Base layer then overlay, so hats and ears show in the preview."""
            p = self.parts[part]
            for origin in (p.base, p.overlay):
                fx, fy, fw, fh = face_uv(origin, p.size, face)
                for y in range(fh):
                    for x in range(fw):
                        src = self.get(fx + (fw - 1 - x if mirror else x), fy + y)
                        if src is not None and 0 <= dx + x < 16 and 0 <= dy + y < 32:
                            px[dx + x, dy + y] = src

        aw = self.parts["right_arm"].size[0]
        if view in ("front", "back"):
            mirror = view == "back"
            # In a back view the model's left appears on the viewer's left.
            near, far = ("right_arm", "left_arm") if not mirror else ("left_arm", "right_arm")
            near_leg, far_leg = (("right_leg", "left_leg") if not mirror
                                 else ("left_leg", "right_leg"))
            blit("head", view, 4, 0, mirror)
            blit("torso", view, 4, 8, mirror)
            blit(near, view, 4 - aw, 8, mirror)
            blit(far, view, 12, 8, mirror)
            blit(near_leg, view, 4, 20, mirror)
            blit(far_leg, view, 8, 20, mirror)
        else:
            # Side elevation: depth faces are 8 deep on the head, 4 elsewhere.
            arm = "left_arm" if view == "left" else "right_arm"
            leg = "left_leg" if view == "left" else "right_leg"
            blit("head", view, 4, 0)
            blit("torso", view, 6, 8)
            blit(arm, view, 6, 8)
            blit(leg, view, 6, 20)
        return tile

    def occupancy_check(self) -> list[str]:
        """Report painted pixels that fall outside any legal UV island."""
        legal = set()
        for part, p in self.parts.items():
            for layer_origin in (p.base, p.overlay):
                for face in FACES:
                    fx, fy, fw, fh = face_uv(layer_origin, p.size, face)
                    for y in range(fh):
                        for x in range(fw):
                            legal.add((fx + x, fy + y))
        stray = [(x, y) for y in range(ATLAS) for x in range(ATLAS)
                 if self.px[y][x] is not None and (x, y) not in legal]
        return [f"stray pixel at ({x},{y})" for x, y in stray]


# --------------------------------------------------------------------------
# paint mode: uvkey (reproduces the reference placeholder)
# --------------------------------------------------------------------------

UV_KEY = {
    "top":    ("#ecf8fd", "#b4d4e1"),
    "bottom": ("#6e788c", "#536174"),
    "right":  ("#7bffa3", "#43e88d"),
    "front":  ("#7bd4ff", "#5bbcf4"),
    "left":   ("#ffa7a4", "#f48686"),
    "back":   ("#fff899", "#f8dd72"),
}
EYE_MARK = "#cdefff"


def paint_uvkey(skin: Skin) -> Skin:
    for part in skin.parts:
        for face in FACES:
            fill, border = UV_KEY[face]
            fv = skin.face(part, face)
            fv.fill(fill)
            fv.border(border)
    # Eye marks make the head's front unmistakable on the model.
    head = skin.face("head", "front")
    head.rect(1, 3, 2, 4, EYE_MARK)
    head.rect(5, 3, 6, 4, EYE_MARK)
    return skin


# --------------------------------------------------------------------------
# paint mode: character
# --------------------------------------------------------------------------

SKIN_TONES = {
    "porcelain": "#f7d9c4", "peach": "#f1c39a", "tan": "#d9a06a",
    "olive": "#b9814f", "umber": "#8d5524", "espresso": "#5c3317",
    "mint": "#a8e0b5", "lilac": "#cdb4f0",
}
HAIR_COLORS = {
    "black": "#22222c", "brown": "#5a3620", "chestnut": "#8b5a2b",
    "blonde": "#e8c86a", "ginger": "#c85a20", "silver": "#cfd4dc",
    "teal": "#2ec4b6", "pink": "#ff6fae", "violet": "#8b5cf6", "lime": "#a3e635",
}
CLOTH = {
    "cyan": "#2ec4b6", "teal": "#177e89", "azure": "#3b82f6", "navy": "#1f2a44",
    "rose": "#ff6fae", "crimson": "#e63946", "amber": "#f4a53c", "lemon": "#ffd166",
    "lime": "#8ac926", "forest": "#2f7d32", "violet": "#8b5cf6", "plum": "#5b2a63",
    "slate": "#5c677d", "cream": "#f2e8cf", "charcoal": "#2b2d34",
}
EYE_COLORS = {
    "ink": "#26262f", "brown": "#5a3620", "blue": "#3b82f6",
    "green": "#2f7d32", "amber": "#f4a53c", "violet": "#8b5cf6",
}
INK = "#26262f"
WHITE = "#f6f7fb"

# fringe/side/back = how many rows of the head each face keeps as hair
HAIR_STYLES = {
    "bald":     dict(top=False, fringe=0, side=0, back=0),
    "buzz":     dict(top=True, fringe=1, side=1, back=2),
    "short":    dict(top=True, fringe=2, side=2, back=3),
    "bowl":     dict(top=True, fringe=3, side=4, back=5),
    "long":     dict(top=True, fringe=2, side=8, back=8),
    "mohawk":   dict(top="crest", fringe=1, side=0, back=1),
    "ponytail": dict(top=True, fringe=2, side=2, back=4, tail=True),
}
OUTFITS = ("tee", "tank", "hoodie", "overalls", "stripes", "collar")
SLEEVES = {"tee": 4, "tank": 0, "hoodie": 9, "overalls": 3, "stripes": 4, "collar": 10}


@dataclass(frozen=True)
class Traits:
    skin: str = "peach"
    hair: str = "brown"
    hair_style: str = "short"
    eye_color: str = "ink"
    outfit: str = "tee"
    shirt: str = "cyan"
    pants: str = "navy"
    shoes: str = "charcoal"
    slim: bool = False

    @staticmethod
    def random(rng: random.Random) -> "Traits":
        return Traits(
            skin=rng.choice(list(SKIN_TONES)),
            hair=rng.choice(list(HAIR_COLORS)),
            hair_style=rng.choice(list(HAIR_STYLES)),
            eye_color=rng.choice(list(EYE_COLORS)),
            outfit=rng.choice(OUTFITS),
            shirt=rng.choice(list(CLOTH)),
            pants=rng.choice(list(CLOTH)),
            shoes=rng.choice(list(CLOTH)),
            slim=rng.random() < 0.5,
        )

    def validate(self) -> None:
        tables = {
            "skin": SKIN_TONES, "hair": HAIR_COLORS, "hair_style": HAIR_STYLES,
            "eye_color": EYE_COLORS, "outfit": OUTFITS, "shirt": CLOTH,
            "pants": CLOTH, "shoes": CLOTH,
        }
        for field, table in tables.items():
            value = getattr(self, field)
            if value not in table:
                raise ValueError(
                    f"unknown {field}={value!r}; choose from {sorted(table)}")
        if not isinstance(self.slim, bool):
            raise ValueError(f"slim must be true/false, got {self.slim!r}")


def _paint_head(skin: Skin, t: Traits):
    tone = rgba(SKIN_TONES[t.skin])
    hair = rgba(HAIR_COLORS[t.hair])
    style = HAIR_STYLES[t.hair_style]

    for face in FACES:
        skin.face("head", face).fill(tone)
    skin.face("head", "bottom").fill(darken(tone, 0.3))   # under the chin

    top = skin.face("head", "top")
    if style["top"] == "crest":
        top.rect(3, 0, 4, top.h - 1, hair)
    elif style["top"]:
        top.fill(hair)

    if style["fringe"]:
        skin.face("head", "front").rows(0, style["fringe"] - 1, hair)
    for side in ("left", "right"):
        if style["side"]:
            skin.face("head", side).rows(0, style["side"] - 1, hair)
    if style["back"]:
        skin.face("head", "back").rows(0, style["back"] - 1, hair)
    if style.get("tail"):
        back = skin.face("head", "back")
        back.rect(3, style["back"], 4, back.h - 1, hair)

    front = skin.face("head", "front")
    iris = rgba(EYE_COLORS[t.eye_color])
    front.rect(1, 3, 2, 4, rgba(WHITE))
    front.rect(5, 3, 6, 4, rgba(WHITE))
    front.set(2, 4, iris)
    front.set(5, 4, iris)
    front.set(1, 3, darken(rgba(WHITE), 0.25))
    front.set(6, 3, darken(rgba(WHITE), 0.25))
    front.rect(3, 6, 4, 6, rgba(INK))                     # mouth
    front.set(3, 5, darken(tone, 0.14))                   # nose
    front.set(4, 5, darken(tone, 0.14))


def _paint_torso(skin: Skin, t: Traits):
    shirt = rgba(CLOTH[t.shirt])
    pants = rgba(CLOTH[t.pants])
    tone = rgba(SKIN_TONES[t.skin])
    cream = rgba(CLOTH["cream"])

    for face in FACES:
        skin.face("torso", face).fill(shirt)
    skin.face("torso", "top").fill(darken(tone, 0.12))    # shoulders and neck
    skin.face("torso", "bottom").fill(darken(pants, 0.2))

    front, back = skin.face("torso", "front"), skin.face("torso", "back")
    if t.outfit == "tank":
        for fv in (front, back):
            fv.rows(0, 1, tone)
            fv.rect(0, 0, 1, 3, tone)
            fv.rect(fv.w - 2, 0, fv.w - 1, 3, tone)
        for side in ("left", "right"):
            skin.face("torso", side).rows(0, 2, tone)
    elif t.outfit == "hoodie":
        for fv in (front, back):
            fv.rows(0, 0, lighten(shirt, 0.25))
        front.rect(2, 8, 5, 10, darken(shirt, 0.2))       # pocket
        front.set(3, 1, cream)                            # drawstrings
        front.set(4, 1, cream)
    elif t.outfit == "overalls":
        for fv in (front, back):
            fv.rows(3, fv.h - 1, pants)
            fv.rect(1, 0, 2, 2, pants)
            fv.rect(fv.w - 3, 0, fv.w - 2, 2, pants)
        for side in ("left", "right"):
            skin.face("torso", side).rows(3, 11, pants)
        front.set(1, 3, rgba(CLOTH["lemon"]))
        front.set(6, 3, rgba(CLOTH["lemon"]))
    elif t.outfit == "stripes":
        for fv in (front, back, skin.face("torso", "left"), skin.face("torso", "right")):
            for y in range(fv.h):
                if y % 2:
                    fv.rows(y, y, lighten(shirt, 0.35))
    elif t.outfit == "collar":
        for fv in (front, back):
            fv.rows(0, 0, cream)
        front.set(3, 1, cream)
        front.set(4, 1, cream)
        front.rect(3, 2, 3, front.h - 1, darken(shirt, 0.25))

    for face in ("left", "right", "back"):
        skin.face("torso", face).shade_edges(0.1)


def _paint_arms(skin: Skin, t: Traits):
    shirt = rgba(CLOTH[t.shirt])
    tone = rgba(SKIN_TONES[t.skin])
    sleeve = SLEEVES[t.outfit]
    for part in ("left_arm", "right_arm"):
        for face in FACES:
            fv = skin.face(part, face)
            fv.fill(tone)
            if face in ("front", "back", "left", "right") and sleeve:
                fv.rows(0, min(sleeve, fv.h) - 1, shirt)
            elif face == "top" and sleeve:
                fv.fill(shirt)
        if t.outfit == "stripes" and sleeve:
            for face in ("front", "back", "left", "right"):
                fv = skin.face(part, face)
                for y in range(min(sleeve, fv.h)):
                    if y % 2:
                        fv.rows(y, y, lighten(shirt, 0.35))
        skin.face(part, "bottom").fill(darken(tone, 0.18))   # palm


def _paint_legs(skin: Skin, t: Traits):
    pants = rgba(CLOTH[t.pants])
    shoes = rgba(CLOTH[t.shoes])
    for part in ("left_leg", "right_leg"):
        for face in FACES:
            fv = skin.face(part, face)
            fv.fill(pants)
            if face in ("front", "back", "left", "right"):
                fv.rows(fv.h - 3, fv.h - 1, shoes)
        skin.face(part, "top").fill(darken(pants, 0.2))
        skin.face(part, "bottom").fill(darken(shoes, 0.35))  # sole
        for face in ("left", "right", "back"):
            skin.face(part, face).shade_edges(0.1)


def paint_character(traits: Traits) -> Skin:
    traits.validate()
    skin = Skin(slim=traits.slim)
    _paint_legs(skin, traits)
    _paint_torso(skin, traits)
    _paint_arms(skin, traits)
    _paint_head(skin, traits)
    return skin


def seed_from_name(name: str) -> int:
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big")


# --------------------------------------------------------------------------
# paint mode: hand-authored character presets
# --------------------------------------------------------------------------

FINN = {
    "skin": "#f5cba7", "skin_shade": "#d9a97f",
    "hat": "#f4f2e8", "hat_shade": "#d6d3c4",
    "shirt": "#59b3e3", "shirt_shade": "#3d8ec0",
    "shorts": "#2f4f8f", "shorts_shade": "#233c6e",
    "sock": "#f4f2e8", "shoe": "#2c2c33",
    "pack": "#4e8b3f", "pack_shade": "#3a6a2e",
    "ink": "#1e1e26",
}

JAKE = {
    "fur": "#f2c14e", "fur_shade": "#cf9b34",
    "belly": "#fadfa4", "belly_shade": "#e6c483",
    "ear": "#dba63c",
    "white": "#f8f7f0", "ink": "#26262c",
}


def paint_finn(skin: Skin) -> Skin:
    """Finn the Human - bear hat on the overlay layer, face on the base."""
    c = FINN

    # Head base stays a complete face, so the skin still reads if a renderer
    # drops the overlay layer.
    for face in FACES:
        skin.face("head", face).fill(c["skin"])
    skin.face("head", "bottom").fill(c["skin_shade"])
    front = skin.face("head", "front")
    front.set(2, 3, c["ink"])
    front.set(5, 3, c["ink"])
    front.rect(3, 5, 4, 5, c["ink"])
    front.set(2, 5, c["skin_shade"])
    front.set(5, 5, c["skin_shade"])

    # Bear hat: full shell everywhere except a face hole in the front.
    for face in ("top", "back", "left", "right"):
        skin.face("head", face, "overlay").fill(c["hat"])
    hat = skin.face("head", "front", "overlay")
    hat.rows(0, 1, c["hat"])                     # brim, just above the eyes
    hat.rect(0, 0, 0, 7, c["hat"])               # cheek frames
    hat.rect(7, 0, 7, 7, c["hat"])
    hat.rows(1, 1, c["hat_shade"])               # shadow line under the brim
    crown = skin.face("head", "top", "overlay")
    crown.rect(1, 1, 2, 2, c["hat_shade"])       # ears, seen from above
    crown.rect(5, 1, 6, 2, c["hat_shade"])
    for face in ("left", "right"):
        skin.face("head", face, "overlay").rows(0, 0, c["hat_shade"])

    # Torso: blue tee over dark shorts, green pack on the back.
    for face in FACES:
        skin.face("torso", face).fill(c["shirt"])
    for face in ("front", "back", "left", "right"):
        skin.face("torso", face).rows(9, 11, c["shorts"])
    skin.face("torso", "bottom").fill(c["shorts_shade"])
    back = skin.face("torso", "back")
    back.rect(1, 1, 6, 8, c["pack"])
    back.rect(1, 8, 6, 8, c["pack_shade"])
    chest = skin.face("torso", "front")
    chest.rect(1, 0, 1, 4, c["pack"])            # shoulder straps
    chest.rect(6, 0, 6, 4, c["pack"])
    for face in ("left", "right"):
        skin.face("torso", face).shade_edges(0.08)

    # Arms: short sleeves, bare forearms.
    for part in ("left_arm", "right_arm"):
        for face in FACES:
            skin.face(part, face).fill(c["skin"])
        for face in ("front", "back", "left", "right"):
            skin.face(part, face).rows(0, 3, c["shirt"])
        skin.face(part, "top").fill(c["shirt"])
        skin.face(part, "bottom").fill(c["skin_shade"])

    # Legs: shorts, bare shin, white socks, black shoes.
    for part in ("left_leg", "right_leg"):
        for face in FACES:
            skin.face(part, face).fill(c["skin"])
        for face in ("front", "back", "left", "right"):
            fv = skin.face(part, face)
            fv.rows(0, 2, c["shorts"])
            fv.rows(7, 9, c["sock"])
            fv.rows(10, 11, c["shoe"])
        skin.face(part, "top").fill(c["shorts"])
        skin.face(part, "bottom").fill(c["shoe"])

    return skin


def paint_jake(skin: Skin) -> Skin:
    """Jake the Dog - all fur, cream muzzle and belly, ears on the overlay."""
    c = JAKE

    for part in skin.parts:
        for face in FACES:
            skin.face(part, face).fill(c["fur"])

    # Head: eyes, nose and a cream muzzle across the lower front.
    front = skin.face("head", "front")
    front.rows(5, 6, c["belly"])
    front.set(1, 5, c["fur"])
    front.set(6, 5, c["fur"])
    front.rect(1, 2, 2, 3, c["white"])
    front.rect(5, 2, 6, 3, c["white"])
    front.set(2, 3, c["ink"])
    front.set(5, 3, c["ink"])
    front.rect(3, 4, 4, 4, c["ink"])             # nose
    front.rect(2, 6, 5, 6, c["ink"])             # mouth
    skin.face("head", "bottom").fill(c["belly_shade"])
    skin.face("head", "back").shade_edges(0.08)

    # Floppy ears on the overlay layer so they stand off the head. The side
    # faces carry the bulk; the front and back corners make them read head-on,
    # since a skin can't add geometry beyond the head box.
    for face in ("left", "right"):
        ear = skin.face("head", face, "overlay")
        ear.rect(2, 0, 5, 4, c["ear"])
        ear.rect(2, 4, 5, 4, darken(rgba(c["ear"]), 0.2))
    for face in ("front", "back"):
        ear = skin.face("head", face, "overlay")
        ear.rect(0, 0, 1, 2, c["ear"])
        ear.rect(6, 0, 7, 2, c["ear"])
        ear.rect(0, 2, 1, 2, darken(rgba(c["ear"]), 0.2))
        ear.rect(6, 2, 7, 2, darken(rgba(c["ear"]), 0.2))
    crown = skin.face("head", "top", "overlay")
    crown.rect(0, 2, 1, 5, c["ear"])
    crown.rect(6, 2, 7, 5, c["ear"])

    # Torso: cream belly panel.
    chest = skin.face("torso", "front")
    chest.rect(2, 3, 5, 10, c["belly"])
    chest.rect(2, 10, 5, 10, c["belly_shade"])
    skin.face("torso", "bottom").fill(c["belly_shade"])
    for face in ("left", "right", "back"):
        skin.face("torso", face).shade_edges(0.08)

    # Paws at the ends of every limb.
    for part in ("left_arm", "right_arm", "left_leg", "right_leg"):
        for face in ("front", "back", "left", "right"):
            skin.face(part, face).rows(10, 11, c["belly"])
        skin.face(part, "bottom").fill(c["belly_shade"])
        skin.face(part, "top").fill(c["fur_shade"])

    return skin


PRESETS = {"finn": paint_finn, "jake": paint_jake}


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _save(skin: Skin, args, stem: str, extra: dict | None = None):
    args.out.mkdir(parents=True, exist_ok=True)
    strays = skin.occupancy_check()
    if strays:
        raise ValueError(f"{stem}: painted outside the UV layout: {strays[:5]}")
    skin.to_image(scale=args.scale).save(args.out / f"{stem}.png")
    if args.preview:
        skin.preview(scale=max(args.scale, 4), bg=args.bg).save(
            args.out / f"{stem}_preview.png")
    if extra is not None:
        (args.out / f"{stem}.json").write_text(json.dumps(extra, indent=2))
    return args.out / f"{stem}.png"


def cmd_uvkey(args):
    skin = paint_uvkey(Skin(slim=args.slim))
    path = _save(skin, args, args.name)
    print(f"wrote {path}  (64x64 uv-key placeholder, "
          f"{'slim' if args.slim else 'classic'} arms)")


def cmd_character(args):
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    if args.name:
        seeds = [seed_from_name(args.name)]
        stems = [args.name]
    else:
        seeds = [rng.getrandbits(48) for _ in range(args.count)]
        stems = [f"skin_{i:04d}" for i in range(args.count)]

    manifest = []
    for stem, seed in zip(stems, seeds):
        traits = Traits.random(random.Random(seed))
        if args.traits:
            overrides = json.loads(args.traits)
            unknown = set(overrides) - set(asdict(traits))
            if unknown:
                raise ValueError(f"unknown trait keys: {sorted(unknown)}")
            traits = replace(traits, **overrides)
        if args.slim:
            traits = replace(traits, slim=True)
        _save(paint_character(traits), args, stem)
        manifest.append({"file": f"{stem}.png", "seed": seed,
                         "traits": asdict(traits)})

    # A single named skin gets its own sidecar so it can't clobber a batch run.
    index = f"{args.name}.json" if args.name else "manifest.json"
    payload = manifest[0] if args.name else manifest
    (args.out / index).write_text(json.dumps(payload, indent=2))
    print(f"wrote {len(manifest)} skin(s) + {index} to {args.out}")
    if len(manifest) == 1:
        print(json.dumps(manifest[0]["traits"], indent=2))


def cmd_preset(args):
    names = list(PRESETS) if args.which == "all" else [args.which]
    for name in names:
        if name not in PRESETS:
            raise ValueError(f"unknown preset {name!r}; have {sorted(PRESETS)} or 'all'")
        path = _save(PRESETS[name](Skin(slim=args.slim)), args, name)
        print(f"wrote {path}")


def cmd_verify(args):
    """Confirm the generator's uvkey output matches a reference atlas exactly."""
    ref = Image.open(args.reference).convert("RGBA")
    if ref.size != (ATLAS, ATLAS):
        raise ValueError(f"{args.reference} is {ref.size[0]}x{ref.size[1]}, "
                         f"expected {ATLAS}x{ATLAS}")
    rp = ref.load()
    results = {}
    for slim in (False, True):
        mine = paint_uvkey(Skin(slim=slim)).to_image().load()
        diff = [(x, y) for y in range(ATLAS) for x in range(ATLAS)
                if rp[x, y] != mine[x, y]]
        results["slim" if slim else "classic"] = diff

    for label, diff in results.items():
        verdict = "IDENTICAL" if not diff else f"{len(diff)} px differ"
        print(f"{label:8} arms: {verdict}")
        for x, y in diff[:8]:
            print(f"    ({x:2},{y:2}) reference={rp[x, y]} generated="
                  f"{paint_uvkey(Skin(slim=(label == 'slim'))).get(x, y)}")
    if not any(results.values()):
        print("\nreference reproduced exactly by both arm widths")
    elif not results["classic"] or not results["slim"]:
        match = "classic" if not results["classic"] else "slim"
        print(f"\nreference reproduced exactly with {match} arms")
    else:
        raise ValueError("generator does not reproduce the reference")


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", type=Path, default=Path("skins"))
    common.add_argument("--scale", type=int, default=1,
                        help="upscale the 64x64 atlas (default 1, i.e. game-ready)")
    common.add_argument("--preview", action="store_true",
                        help="also write front/right/back/left elevations")
    common.add_argument("--bg", default=None, help="preview background colour")
    common.add_argument("--slim", action="store_true",
                        help="3px arms (Alex proportions) instead of 4px")

    p = argparse.ArgumentParser(
        prog="bongle_skin",
        description="Generate 64x64 pixel-art avatar model skins.")
    sub = p.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("uvkey", parents=[common],
                       help="orientation-key placeholder skin")
    u.add_argument("--name", default="uvkey")
    u.set_defaults(func=cmd_uvkey)

    c = sub.add_parser("character", parents=[common],
                       help="randomised character skins")
    c.add_argument("--count", type=int, default=8)
    c.add_argument("--seed", type=int, default=None)
    c.add_argument("--name", default=None,
                   help="deterministic skin for a string")
    c.add_argument("--traits", default=None,
                   help='JSON overrides, e.g. \'{"hair_style":"mohawk"}\'')
    c.set_defaults(func=cmd_character)

    pr = sub.add_parser("preset", parents=[common],
                        help="hand-authored character skins")
    pr.add_argument("which", help=f"one of {sorted(PRESETS)}, or 'all'")
    pr.set_defaults(func=cmd_preset)

    v = sub.add_parser("verify", help="diff uvkey output against a reference atlas")
    v.add_argument("reference", type=Path)
    v.set_defaults(func=cmd_verify)

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
