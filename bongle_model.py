#!/usr/bin/env python
"""bongle-model - custom-proportioned boxy characters for Blockbench.

Unlike bongle_skin.py, nothing here is tied to the Minecraft player skeleton.
You declare boxes with whatever sizes, positions and rotations the character
needs; the atlas is then packed automatically (each box gets a box-UV island),
painted face by face, and exported as a matching PNG + .bbmodel pair.

    python bongle_model.py finn jake --preview
    python bongle_model.py all --out models

Adding a character means writing one build_* function and registering it in
CHARACTERS - see build_finn / build_jake for worked examples.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from bongle_skin import darken, lighten, rgba

FACES = ("top", "bottom", "right", "front", "left", "back")

# Blockbench face name -> box face. The model faces north (-Z), so the
# character's right side is west (-X).
FACE_NAMES = {
    "north": "front", "south": "back", "west": "right",
    "east": "left", "up": "top", "down": "bottom",
}


# --------------------------------------------------------------------------
# boxes
# --------------------------------------------------------------------------

# A Bongle Character needs this bone hierarchy - bones are matched by NAME, and
# the editor rejects the model outright if any are missing. name -> parent.
BONGLE_BONES = {
    "waist": None,          # root
    "body": "waist",
    "head": "waist",
    "arm_left": "waist",
    "arm_right": "waist",
    "leg_left": None,       # legs sit at the root, not under the waist
    "leg_right": None,
}
BONGLE_OPTIONAL_BONES = {
    "back": "body",
    "hand_left": "arm_left",
    "hand_right": "arm_right",
}


@dataclass
class Bone:
    """A group in the outliner. Bongle rigs the model by matching these names."""
    name: str
    parent: str | None
    origin: tuple[float, float, float]
    boxes: list[str]


@dataclass
class Box:
    name: str
    start: tuple[float, float, float]
    size: tuple[int, int, int]
    origin: tuple[float, float, float] | None = None
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    uv: tuple[int, int] | None = field(default=None, compare=False)

    def __post_init__(self):
        if any(int(v) != v or v <= 0 for v in self.size):
            raise ValueError(
                f"{self.name}: box sizes must be positive integers for box UV, "
                f"got {self.size}")
        self.size = tuple(int(v) for v in self.size)
        if self.origin is None:
            x, y, z = self.start
            w, h, d = self.size
            self.origin = (x + w / 2, y, z + d / 2)

    @property
    def end(self):
        return tuple(s + n for s, n in zip(self.start, self.size))

    @property
    def island(self) -> tuple[int, int]:
        """Footprint of this box's unwrap in the atlas."""
        w, h, d = self.size
        return (2 * (w + d), h + d)

    def face_uv(self, face: str) -> tuple[int, int, int, int]:
        u, v = self.uv
        w, h, d = self.size
        return {
            "top":    (u + d,         v,     w, d),
            "bottom": (u + d + w,     v,     w, d),
            "right":  (u,             v + d, d, h),
            "front":  (u + d,         v + d, w, h),
            "left":   (u + d + w,     v + d, d, h),
            "back":   (u + d + w + d, v + d, w, h),
        }[face]


class FaceView:
    """Paint one face of one box, in face-local coordinates."""

    def __init__(self, atlas: "Atlas", x, y, w, h):
        self.atlas, self.x, self.y, self.w, self.h = atlas, x, y, w, h

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.atlas.set(self.x + x, self.y + y, color)

    def get(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.atlas.get(self.x + x, self.y + y)
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

    def shade_edges(self, amount=0.1):
        for x in range(self.w):
            for y in range(self.h):
                if (x in (0, self.w - 1) or y in (0, self.h - 1)) and self.get(x, y):
                    self.set(x, y, darken(self.get(x, y), amount))


class Atlas:
    def __init__(self, size: int):
        self.size = size
        self.px = [[None] * size for _ in range(size)]

    def set(self, x, y, color):
        if color is not None and 0 <= x < self.size and 0 <= y < self.size:
            self.px[y][x] = rgba(color)

    def get(self, x, y):
        if 0 <= x < self.size and 0 <= y < self.size:
            return self.px[y][x]
        return None

    def to_image(self, scale=1) -> Image.Image:
        img = Image.new("RGBA", (self.size, self.size), (0, 0, 0, 0))
        px = img.load()
        for y in range(self.size):
            for x in range(self.size):
                if self.px[y][x] is not None:
                    px[x, y] = self.px[y][x]
        if scale > 1:
            img = img.resize((self.size * scale,) * 2, Image.NEAREST)
        return img


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------

class Model:
    def __init__(self, name: str):
        self.name = name
        self.boxes: dict[str, Box] = {}
        self.bones: dict[str, Bone] = {}
        self.atlas: Atlas | None = None

    def add(self, name, start, size, origin=None, rotation=(0, 0, 0)) -> Box:
        if name in self.boxes:
            raise ValueError(f"duplicate box {name!r}")
        box = Box(name, tuple(start), tuple(size), origin, tuple(rotation))
        self.boxes[name] = box
        return box

    def bone(self, name, parent, origin, boxes=()) -> Bone:
        """Declare a bone (outliner group) holding zero or more boxes."""
        if name in self.bones:
            raise ValueError(f"duplicate bone {name!r}")
        self.bones[name] = Bone(name, parent, tuple(origin), list(boxes))
        return self.bones[name]

    def bone_issues(self) -> list[str]:
        """Reproduce Bongle's own rig validation, so failures surface here."""
        issues = []
        if not self.bones:
            return ["no bones declared - Bongle needs a named bone hierarchy "
                    f"({', '.join(BONGLE_BONES)})"]
        for name, parent in BONGLE_BONES.items():
            bone = self.bones.get(name)
            if bone is None:
                where = ("must be at the root" if parent is None
                         else f'must be a child of "{parent}"')
                issues.append(f'missing required bone "{name}" ({where})')
            elif bone.parent != parent:
                issues.append(f'bone "{name}" is parented to {bone.parent!r}, '
                              f'expected {parent!r}')
        for name, parent in BONGLE_OPTIONAL_BONES.items():
            bone = self.bones.get(name)
            if bone is not None and bone.parent != parent:
                issues.append(f'optional bone "{name}" must be a child of "{parent}"')
        for bone in self.bones.values():
            if bone.parent is not None and bone.parent not in self.bones:
                issues.append(f'bone "{bone.name}" names a missing parent '
                              f'{bone.parent!r}')

        assigned = [n for b in self.bones.values() for n in b.boxes]
        dupes = sorted({n for n in assigned if assigned.count(n) > 1})
        if dupes:
            issues.append(f"boxes in more than one bone: {dupes}")
        orphans = sorted(set(self.boxes) - set(assigned))
        if orphans:
            issues.append(f"boxes not assigned to any bone: {orphans}")
        unknown = sorted(set(assigned) - set(self.boxes))
        if unknown:
            issues.append(f"bones reference boxes that do not exist: {unknown}")
        return issues

    def pack(self, pad: int = 1) -> "Model":
        """Shelf-pack every island into the smallest square atlas that fits."""
        order = sorted(self.boxes.values(), key=lambda b: (-b.island[1], -b.island[0]))
        for size in (32, 64, 128, 256, 512):
            x = y = shelf = 0
            ok = True
            for box in order:
                iw, ih = box.island
                if iw > size:
                    ok = False
                    break
                if x + iw > size:
                    x, y = 0, y + shelf + pad
                    shelf = 0
                if y + ih > size:
                    ok = False
                    break
                box.uv = (x, y)
                x += iw + pad
                shelf = max(shelf, ih)
            if ok:
                self.atlas = Atlas(size)
                return self
        raise ValueError(f"{self.name}: cannot pack {len(self.boxes)} boxes into 512px")

    def face(self, box: str, face: str) -> FaceView:
        if self.atlas is None:
            raise ValueError("call pack() before painting")
        if box not in self.boxes:
            raise ValueError(f"unknown box {box!r}; have {sorted(self.boxes)}")
        if face not in FACES:
            raise ValueError(f"unknown face {face!r}; have {list(FACES)}")
        return FaceView(self.atlas, *self.boxes[box].face_uv(face))

    def fill(self, box: str, color, faces=FACES):
        for f in faces:
            self.face(box, f).fill(color)

    @property
    def height(self) -> float:
        return max(b.end[1] for b in self.boxes.values())

    def overlaps(self) -> list[str]:
        """Islands that collide in the atlas - a packing bug if non-empty."""
        seen: dict[tuple[int, int], str] = {}
        clashes = []
        for box in self.boxes.values():
            for face in FACES:
                fx, fy, fw, fh = box.face_uv(face)
                for y in range(fy, fy + fh):
                    for x in range(fx, fx + fw):
                        key = (x, y)
                        if key in seen and seen[key] != box.name:
                            clashes.append(
                                f"{box.name}.{face} overlaps {seen[key]} at {key}")
                        seen[key] = box.name
        return clashes[:10]

    def unpainted(self) -> list[str]:
        """Faces left fully transparent - usually an oversight."""
        out = []
        for box in self.boxes.values():
            for face in FACES:
                fx, fy, fw, fh = box.face_uv(face)
                if not any(self.atlas.get(x, y)
                           for y in range(fy, fy + fh) for x in range(fx, fx + fw)):
                    out.append(f"{box.name}.{face}")
        return out

    # ---- export ----------------------------------------------------------

    def _uuid(self, seed: str) -> str:
        h = hashlib.sha256(f"{self.name}:{seed}".encode()).hexdigest()
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def bbmodel(self, png_bytes: bytes) -> dict:
        """Blockbench project JSON, with the bone tree as the outliner."""
        def r(values):
            # Guard against float noise like -3.5999999999999996 reaching a
            # stricter parser than Blockbench's.
            return [round(float(v), 4) for v in values]

        elements = []
        for box in self.boxes.values():
            faces = {}
            for bb_face, box_face in FACE_NAMES.items():
                fx, fy, fw, fh = box.face_uv(box_face)
                faces[bb_face] = {"uv": [fx, fy, fx + fw, fy + fh], "texture": 0}
            uuid = self._uuid(f"cube:{box.name}")
            # A cube sharing a bone's name would make a name-matched rig
            # ambiguous, so suffix those.
            cube_name = (f"{box.name}_mesh" if box.name in self.bones
                         else box.name)
            elements.append({
                "name": cube_name,
                "box_uv": True,
                "rescale": False,
                "locked": False,
                "render_order": "default",
                "allow_mirror_modeling": True,
                "from": r(box.start),
                "to": r(box.end),
                "inflate": 0,
                "rotation": r(box.rotation),
                "autouv": 0,
                "color": 0,
                "origin": r(box.origin),
                "uv_offset": list(box.uv),
                "faces": faces,
                "type": "cube",
                "visibility": True,
                "export": True,
                "uuid": uuid,
            })

        # Outliner is the bone tree, with cube uuids as leaves.
        by_parent: dict[str | None, list[Bone]] = {}
        for bone in self.bones.values():
            by_parent.setdefault(bone.parent, []).append(bone)

        def node(bone: Bone) -> dict:
            children = [self._uuid(f"cube:{n}") for n in bone.boxes]
            children += [node(child) for child in by_parent.get(bone.name, [])]
            return {
                "name": bone.name,
                "origin": r(bone.origin),
                "rotation": [0, 0, 0],
                "uuid": self._uuid(f"bone:{bone.name}"),
                "export": True,
                "mirror_uv": False,
                "isOpen": True,
                "locked": False,
                "visibility": True,
                "autouv": False,
                "children": children,
            }

        outliner = [node(b) for b in by_parent.get(None, [])]

        return {
            "meta": {"format_version": "4.5", "model_format": "free", "box_uv": True},
            "name": self.name,
            "model_identifier": "",
            "visible_box": [3, 3, 0],
            "resolution": {"width": self.atlas.size, "height": self.atlas.size},
            "elements": elements,
            "outliner": outliner,
            "textures": [{
                # mode/relative_path/saved matter to importers that are not
                # Blockbench: without mode="bitmap" some never look at `source`
                # and render the model untextured.
                "mode": "bitmap",
                "path": f"{self.name}.png",
                "relative_path": f"./{self.name}.png",
                "name": f"{self.name}.png", "folder": "",
                "namespace": "", "id": "0",
                "width": self.atlas.size, "height": self.atlas.size,
                "uv_width": self.atlas.size, "uv_height": self.atlas.size,
                "particle": False, "use_as_default": False, "layers_enabled": False,
                "sync_to_project": "", "render_mode": "default",
                "render_sides": "auto", "frame_time": 1,
                "frame_order_type": "loop", "frame_order": "",
                "frame_interpolate": False, "visible": True, "internal": True,
                "saved": True, "uuid": self._uuid("texture"),
                "source": "data:image/png;base64,"
                          + base64.b64encode(png_bytes).decode("ascii"),
            }],
        }

    def elevation(self, view: str = "front", scale: int = 1, bg=None) -> Image.Image:
        """Orthographic elevation for checking proportions.

        Boxes are drawn at their unrotated positions - rotation is not applied,
        so floppy ears appear straight. Heights and widths are exact.
        """
        pad = 2
        w = int(max(b.end[0] for b in self.boxes.values())
                - min(b.start[0] for b in self.boxes.values()))
        d = int(max(b.end[2] for b in self.boxes.values())
                - min(b.start[2] for b in self.boxes.values()))
        x0 = min(b.start[0] for b in self.boxes.values())
        z0 = min(b.start[2] for b in self.boxes.values())
        span = w if view in ("front", "back") else d
        top = int(self.height)
        img = Image.new("RGBA", (span + pad * 2, top + pad * 2), (0, 0, 0, 0))
        px = img.load()

        # Painter's order: far boxes first so nearer ones overwrite them.
        # Front/back look along Z, side views look along X.
        def depth(b):
            return {"front": b.start[2], "back": -b.start[2],
                    "right": b.start[0], "left": -b.start[0]}[view]

        for box in sorted(self.boxes.values(), key=depth, reverse=True):
            face = {"front": "front", "back": "back",
                    "right": "right", "left": "left"}[view]
            fx, fy, fw, fh = box.face_uv(face)
            if view in ("front", "back"):
                ox = int(box.start[0] - x0)
            else:
                ox = int(box.start[2] - z0)
            oy = int(self.height - box.end[1])
            for y in range(fh):
                for x in range(fw):
                    c = self.atlas.get(fx + x, fy + y)
                    if c is None:
                        continue
                    tx, ty = ox + x + pad, oy + y + pad
                    if 0 <= tx < img.width and 0 <= ty < img.height:
                        px[tx, ty] = c
        if bg is not None:
            img = Image.alpha_composite(Image.new("RGBA", img.size, rgba(bg)), img)
        if scale > 1:
            img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
        return img


# --------------------------------------------------------------------------
# Finn the Human
# --------------------------------------------------------------------------

FINN = {
    "skin": "#f5cba7", "skin_shade": "#dcaa84",
    "hat": "#f6f4ea", "hat_shade": "#dcd8c8", "hat_deep": "#c3bfae",
    "shirt": "#59b3e3", "shirt_shade": "#3d8ec0",
    "shorts": "#2f4f8f", "shorts_shade": "#223a6c",
    "sock": "#f6f4ea", "sock_shade": "#d8d5c6",
    "shoe": "#2f2f36", "shoe_sole": "#1d1d22",
    "pack": "#4e8b3f", "pack_shade": "#3a6a2e", "strap": "#3f7233",
    "ink": "#20202a",
}


def build_finn() -> Model:
    """A kid: oversized head under a bear hat, narrow torso, noodly limbs."""
    c = FINN
    # Named distinctly from the skin-based finn.bbmodel: same filename in one
    # folder is indistinguishable once Blockbench has it open.
    m = Model("finn_custom")

    m.add("shoe_right", (-4, 0, -4), (3, 3, 6))
    m.add("shoe_left", (1, 0, -4), (3, 3, 6))
    m.add("leg_right", (-3, 3, -1), (2, 10, 2), origin=(-2, 13, 0))
    m.add("leg_left", (1, 3, -1), (2, 10, 2), origin=(2, 13, 0))
    m.add("torso", (-3, 13, -2), (6, 8, 4), origin=(0, 13, 0))
    m.add("arm_right", (-5, 12, -1), (2, 9, 2), origin=(-4, 20, 0))
    m.add("arm_left", (3, 12, -1), (2, 9, 2), origin=(4, 20, 0))
    m.add("pack", (-2, 14, 2), (4, 6, 2))
    m.add("head", (-4, 21, -4), (8, 8, 8), origin=(0, 21, 0))
    m.add("hat_crown", (-5, 25, -5), (10, 5, 10), origin=(0, 25, 0))
    m.add("hat_flap_right", (-5, 21, -4), (1, 4, 8))
    m.add("hat_flap_left", (4, 21, -4), (1, 4, 8))
    m.add("hat_back", (-4, 21, 4), (8, 4, 1))
    m.add("ear_right", (-4, 30, -2), (3, 2, 4))
    m.add("ear_left", (1, 30, -2), (3, 2, 4))

    # Bongle rig. His pack goes in the optional "back" bone.
    m.bone("waist", None, (0, 13, 0))
    m.bone("body", "waist", (0, 13, 0), ["torso"])
    m.bone("back", "body", (0, 14, 2), ["pack"])
    m.bone("head", "waist", (0, 21, 0),
           ["head", "hat_crown", "hat_flap_right", "hat_flap_left", "hat_back",
            "ear_right", "ear_left"])
    m.bone("arm_left", "waist", (4, 20, 0), ["arm_left"])
    m.bone("arm_right", "waist", (-4, 20, 0), ["arm_right"])
    m.bone("leg_left", None, (2, 13, 0), ["leg_left", "shoe_left"])
    m.bone("leg_right", None, (-2, 13, 0), ["leg_right", "shoe_right"])
    m.pack()

    # --- head: skin, with the face low down since the hat covers the crown
    m.fill("head", c["skin"])
    m.face("head", "bottom").fill(c["skin_shade"])
    face = m.face("head", "front")
    face.rect(1, 4, 2, 5, c["ink"])          # eyes
    face.rect(5, 4, 6, 5, c["ink"])
    face.rect(3, 6, 4, 6, c["ink"])          # mouth
    face.set(2, 6, c["skin_shade"])
    face.set(5, 6, c["skin_shade"])
    for f in ("left", "right", "back"):
        m.face("head", f).shade_edges(0.07)

    # --- hat: off-white shell, darker underside and inner ears
    for part in ("hat_crown", "hat_flap_right", "hat_flap_left", "hat_back"):
        m.fill(part, c["hat"])
        m.face(part, "bottom").fill(c["hat_shade"])
        for f in ("left", "right", "back"):
            m.face(part, f).shade_edges(0.06)
    m.face("hat_crown", "front").rows(4, 4, c["hat_shade"])   # brim shadow
    for ear in ("ear_right", "ear_left"):
        m.fill(ear, c["hat"])
        m.face(ear, "bottom").fill(c["hat_deep"])
        m.face(ear, "top").border(c["hat_shade"])

    # --- torso: blue tee, shorts at the hem, pack straps over the chest
    m.fill("torso", c["shirt"])
    m.face("torso", "top").fill(c["shirt_shade"])
    m.face("torso", "bottom").fill(c["shorts_shade"])
    for f in ("front", "back", "left", "right"):
        m.face("torso", f).rows(6, 7, c["shorts"])
    chest = m.face("torso", "front")
    chest.rect(1, 0, 1, 5, c["strap"])
    chest.rect(4, 0, 4, 5, c["strap"])
    for f in ("left", "right", "back"):
        m.face("torso", f).shade_edges(0.08)

    # --- pack
    m.fill("pack", c["pack"])
    m.face("pack", "back").rows(4, 5, c["pack_shade"])
    m.face("pack", "bottom").fill(c["pack_shade"])
    for f in ("left", "right", "top"):
        m.face("pack", f).shade_edges(0.08)

    # --- arms: short blue sleeve, bare below
    for arm in ("arm_right", "arm_left"):
        m.fill(arm, c["skin"])
        for f in ("front", "back", "left", "right"):
            m.face(arm, f).rows(0, 2, c["shirt"])
        m.face(arm, "top").fill(c["shirt_shade"])
        m.face(arm, "bottom").fill(c["skin_shade"])

    # --- legs: bare shin into a white sock
    for leg in ("leg_right", "leg_left"):
        m.fill(leg, c["skin"])
        for f in ("front", "back", "left", "right"):
            m.face(leg, f).rows(7, 9, c["sock"])
        m.face(leg, "top").fill(c["skin_shade"])
        m.face(leg, "bottom").fill(c["sock_shade"])

    # --- shoes
    for shoe in ("shoe_right", "shoe_left"):
        m.fill(shoe, c["shoe"])
        m.face(shoe, "bottom").fill(c["shoe_sole"])
        m.face(shoe, "top").fill(c["shoe_sole"])
        for f in ("front", "left", "right"):
            m.face(shoe, f).shade_edges(0.12)

    return m


# --------------------------------------------------------------------------
# Jake the Dog
# --------------------------------------------------------------------------

JAKE = {
    "fur": "#f2c14e", "fur_shade": "#d3a038", "fur_deep": "#b5852c",
    "belly": "#fadfa4", "belly_shade": "#e7c886",
    "ear_in": "#c98f36",
    "white": "#f9f8f1", "ink": "#26262c",
}


def build_jake() -> Model:
    """A stubby dog: big head, long floppy ears, short legs, tail."""
    c = JAKE
    m = Model("jake_custom")

    m.add("leg_right", (-3, 0, -1), (2, 3, 3))
    m.add("leg_left", (1, 0, -1), (2, 3, 3))
    # Torso narrower than the head - that stubby head-heavy read is the point.
    m.add("torso", (-3, 3, -2), (6, 7, 5), origin=(0, 3, 0))
    m.add("arm_right", (-5, 4, -1), (2, 5, 2), origin=(-4, 9, 0))
    m.add("arm_left", (3, 4, -1), (2, 5, 2), origin=(4, 9, 0))
    m.add("tail", (-1, 5, 3), (2, 2, 3), origin=(0, 6, 3), rotation=(-15, 0, 0))
    m.add("head", (-4, 10, -4), (8, 7, 8), origin=(0, 10, 0))
    m.add("snout", (-2, 11, -6), (4, 3, 2))
    # Floppy ears: hung from a pivot at the top of the head and swung outward.
    # Positive Z rotation moves a hanging tip toward +X, so the right ear
    # takes the negative angle. Flip both signs to tuck them inward instead.
    m.add("ear_right", (-6, 10, -1), (2, 7, 3),
          origin=(-4, 16, 0), rotation=(0, 0, -22))
    m.add("ear_left", (4, 10, -1), (2, 7, 3),
          origin=(4, 16, 0), rotation=(0, 0, 22))

    m.bone("waist", None, (0, 3, 0))
    m.bone("body", "waist", (0, 3, 0), ["torso", "tail"])
    m.bone("head", "waist", (0, 10, 0),
           ["head", "snout", "ear_right", "ear_left"])
    m.bone("arm_left", "waist", (5, 9, 0), ["arm_left"])
    m.bone("arm_right", "waist", (-5, 9, 0), ["arm_right"])
    m.bone("leg_left", None, (2, 3, 0), ["leg_left"])
    m.bone("leg_right", None, (-2, 3, 0), ["leg_right"])
    m.pack()

    # --- head
    m.fill("head", c["fur"])
    m.face("head", "bottom").fill(c["fur_shade"])
    face = m.face("head", "front")
    face.rect(1, 2, 2, 3, c["white"])        # eye whites
    face.rect(5, 2, 6, 3, c["white"])
    face.set(2, 3, c["ink"])                 # pupils
    face.set(5, 3, c["ink"])
    face.rect(2, 5, 5, 6, c["belly"])        # muzzle meeting the snout
    face.rect(2, 6, 5, 6, c["ink"])          # mouth, below where the snout sits
    for f in ("left", "right", "back"):
        m.face("head", f).shade_edges(0.07)
    m.face("head", "top").shade_edges(0.05)

    # --- snout: cream, black nose on the tip, mouth line underneath
    m.fill("snout", c["belly"])
    m.face("snout", "front").rect(1, 0, 2, 1, c["ink"])
    m.face("snout", "bottom").fill(c["belly_shade"])
    m.face("snout", "bottom").rect(1, 0, 2, 1, c["ink"])
    for f in ("left", "right", "top"):
        m.face("snout", f).shade_edges(0.08)

    # --- ears: fur outside, darker inside face
    for ear, inner in (("ear_right", "left"), ("ear_left", "right")):
        m.fill(ear, c["fur"])
        m.face(ear, inner).fill(c["ear_in"])
        m.face(ear, "bottom").fill(c["fur_deep"])
        m.face(ear, "top").fill(c["fur_shade"])
        for f in ("front", "back"):
            m.face(ear, f).shade_edges(0.1)
            m.face(ear, f).rows(5, 6, c["fur_shade"])

    # --- torso with a cream belly panel
    m.fill("torso", c["fur"])
    m.face("torso", "front").rect(1, 1, 4, 6, c["belly"])
    m.face("torso", "front").rect(1, 6, 4, 6, c["belly_shade"])
    m.face("torso", "bottom").fill(c["belly_shade"])
    m.face("torso", "top").fill(c["fur_shade"])
    for f in ("left", "right", "back"):
        m.face("torso", f).shade_edges(0.08)

    # --- limbs and tail, cream paws
    for limb in ("arm_right", "arm_left", "leg_right", "leg_left"):
        m.fill(limb, c["fur"])
        for f in ("front", "back", "left", "right"):
            m.face(limb, f).rows(m.boxes[limb].size[1] - 2,
                                 m.boxes[limb].size[1] - 1, c["belly"])
        m.face(limb, "bottom").fill(c["belly_shade"])
        m.face(limb, "top").fill(c["fur_shade"])
    m.fill("tail", c["fur"])
    m.face("tail", "back").fill(c["belly"])
    m.face("tail", "bottom").fill(c["fur_shade"])

    return m


# --------------------------------------------------------------------------
# Otis - a staffy, built on Jake's upright frame
# --------------------------------------------------------------------------

OTIS = {
    "coat": "#cd9457", "coat_shade": "#ad7840", "coat_deep": "#8f6234",
    "cream": "#e8d5b8", "cream_shade": "#cbb598",
    "chest": "#f2ece0",
    "mask": "#4a3a2f", "mask_shade": "#3b2f26",
    "nose": "#1e1a18",
    "ear_in": "#d9a08c", "ear_in_shade": "#bd8672",
    "tongue": "#e07a8c", "tongue_shade": "#c4636f",
    "eye": "#241d18", "glint": "#f4f1ea",
    "harness": "#5cb8e8", "harness_shade": "#3f95c4", "harness_deep": "#2d7ba6",
    "metal": "#b9c4cc",
}


def build_otis() -> Model:
    """Otis: staffy build - broad chest, stocky limbs, big upright pointy ears.

    Same upright frame as build_jake, rebalanced wider and heavier, with the
    ears standing up instead of hanging.
    """
    c = OTIS
    m = Model("otis_custom")

    m.add("leg_right", (-4, 0, -1), (3, 4, 3))
    m.add("leg_left", (1, 0, -1), (3, 4, 3))
    # Broad barrel chest - the staffy tell.
    m.add("torso", (-4, 4, -3), (8, 8, 6), origin=(0, 4, 0))
    m.add("arm_right", (-6, 5, -1), (2, 6, 3), origin=(-5, 11, 0))
    m.add("arm_left", (4, 5, -1), (2, 6, 3), origin=(5, 11, 0))
    m.add("tail", (-1, 8, 3), (2, 2, 3), origin=(0, 9, 3), rotation=(-25, 0, 0))
    m.add("head", (-4, 12, -4), (8, 7, 8), origin=(0, 12, 0))
    m.add("snout", (-2, 13, -7), (4, 3, 3))
    m.add("tongue", (-1, 12, -6), (2, 2, 2), origin=(0, 14, -6),
          rotation=(20, 0, 0))

    # Sky blue harness: a band round the barrel, a loop at the shoulders, and
    # a vertical strap linking them down the chest. Boxes are 0.5 larger than
    # the torso in x/z so they sit on its surface instead of inside it.
    m.add("harness_chest", (-4.5, 6, -3.5), (9, 2, 7))
    m.add("harness_neck", (-4.5, 10, -3.5), (9, 2, 7))
    m.add("harness_strap", (-1, 8, -4.1), (2, 2, 1))

    # Pointy ears, base plus a narrower tip sharing one pivot so they rotate as
    # a unit. These sit ABOVE the pivot, which flips the sign versus Jake's
    # hanging ears: positive Z rotation now swings the tip toward -X.
    m.add("ear_base_right", (-4, 19, -2), (2, 3, 3),
          origin=(-3, 19, 0), rotation=(0, 0, 12))
    m.add("ear_tip_right", (-3.5, 22, -1.5), (1, 2, 2),
          origin=(-3, 19, 0), rotation=(0, 0, 12))
    m.add("ear_base_left", (2, 19, -2), (2, 3, 3),
          origin=(3, 19, 0), rotation=(0, 0, -12))
    m.add("ear_tip_left", (2.5, 22, -1.5), (1, 2, 2),
          origin=(3, 19, 0), rotation=(0, 0, -12))

    # Bongle rig. The harness rides the body bone so it moves with the chest.
    m.bone("waist", None, (0, 4, 0))
    m.bone("body", "waist", (0, 4, 0),
           ["torso", "tail", "harness_chest", "harness_neck", "harness_strap"])
    m.bone("head", "waist", (0, 12, 0),
           ["head", "snout", "tongue", "ear_base_right", "ear_tip_right",
            "ear_base_left", "ear_tip_left"])
    m.bone("arm_left", "waist", (5, 11, 0), ["arm_left"])
    m.bone("arm_right", "waist", (-5, 11, 0), ["arm_right"])
    m.bone("leg_left", None, (2, 4, 0), ["leg_left"])
    m.bone("leg_right", None, (-2, 4, 0), ["leg_right"])
    m.pack()

    # --- head: tan skull, dark mask over the lower half
    m.fill("head", c["coat"])
    m.face("head", "bottom").fill(c["mask_shade"])
    face = m.face("head", "front")
    face.rows(4, 6, c["mask"])                   # muzzle mask, tan above it
    face.rect(1, 1, 2, 2, c["eye"])              # wide-set eyes
    face.rect(5, 1, 6, 2, c["eye"])
    face.set(1, 1, c["glint"])
    face.set(5, 1, c["glint"])
    face.rect(0, 3, 0, 4, c["coat_shade"])       # cheeks either side of the mask
    face.rect(7, 3, 7, 4, c["coat_shade"])
    m.face("head", "top").rows(0, 0, c["coat_shade"])
    for f in ("left", "right", "back"):
        m.face("head", f).shade_edges(0.07)
        m.face("head", f).rows(4, 6, c["mask"])

    # --- snout: dark, black nose across the tip
    m.fill("snout", c["mask"])
    m.face("snout", "top").fill(c["mask_shade"])
    m.face("snout", "front").rect(1, 0, 2, 1, c["nose"])
    m.face("snout", "bottom").fill(c["mask_shade"])
    for f in ("left", "right"):
        m.face("snout", f).shade_edges(0.1)

    # --- tongue
    m.fill("tongue", c["tongue"])
    m.face("tongue", "top").fill(c["tongue_shade"])
    m.face("tongue", "bottom").fill(c["tongue_shade"])

    # --- ears: tan outside, pink inside, darker tips
    for side, inner in (("right", "left"), ("left", "right")):
        for part, shade in ((f"ear_base_{side}", c["coat"]),
                            (f"ear_tip_{side}", c["coat_shade"])):
            m.fill(part, shade)
            m.face(part, inner).fill(c["ear_in"] if "base" in part
                                     else c["ear_in_shade"])
            m.face(part, "bottom").fill(c["coat_deep"])
            m.face(part, "top").fill(c["coat_deep"] if "base" in part
                                     else c["coat_shade"])
            for f in ("front", "back"):
                m.face(part, f).shade_edges(0.1)

    # --- torso: tan with the white chest swirl
    m.fill("torso", c["coat"])
    m.face("torso", "top").fill(c["coat_shade"])
    m.face("torso", "bottom").fill(c["cream_shade"])
    chest = m.face("torso", "front")
    # Rows 2-3 fall in the gap between the two harness bands, so the pale
    # patch stays visible instead of hiding entirely under webbing.
    chest.rect(2, 2, 5, 7, c["cream"])
    chest.rect(3, 2, 4, 5, c["chest"])           # the pale swirl on his chest
    for f in ("left", "right", "back"):
        m.face("torso", f).shade_edges(0.08)

    # --- limbs: stocky, cream paws
    for limb in ("arm_right", "arm_left", "leg_right", "leg_left"):
        h = m.boxes[limb].size[1]
        m.fill(limb, c["coat"])
        for f in ("front", "back", "left", "right"):
            m.face(limb, f).rows(h - 2, h - 1, c["cream"])
        m.face(limb, "bottom").fill(c["cream_shade"])
        m.face(limb, "top").fill(c["coat_shade"])

    # --- tail with a pale tip
    m.fill("tail", c["coat"])
    m.face("tail", "back").fill(c["cream"])
    m.face("tail", "bottom").fill(c["coat_shade"])

    # --- harness
    for band in ("harness_chest", "harness_neck", "harness_strap"):
        m.fill(band, c["harness"])
        m.face(band, "bottom").fill(c["harness_deep"])
        m.face(band, "top").fill(c["harness_shade"])
        for f in ("left", "right", "back"):
            m.face(band, f).shade_edges(0.1)
    ring = m.face("harness_neck", "front")
    ring.rect(4, 0, 4, 1, c["metal"])            # hardware off to one side
    m.face("harness_strap", "front").rect(0, 0, 1, 0, c["metal"])

    return m


CHARACTERS = {"finn": build_finn, "jake": build_jake, "otis": build_otis}


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def export(model: Model, out: Path, preview: bool, scale: int, bg):
    out.mkdir(parents=True, exist_ok=True)
    clashes = model.overlaps()
    if clashes:
        raise ValueError(f"{model.name}: atlas islands overlap: {clashes}")
    rig = model.bone_issues()
    if rig:
        raise ValueError(f"{model.name}: Bongle would reject this rig:\n  "
                         + "\n  ".join(rig))
    blank = model.unpainted()

    png = out / f"{model.name}.png"
    model.atlas.to_image().save(png)
    (out / f"{model.name}.bbmodel").write_text(
        json.dumps(model.bbmodel(png.read_bytes()), indent=2))

    if preview:
        views = [model.elevation(v, scale=scale, bg=bg)
                 for v in ("front", "right", "back")]
        gap = 4
        sheet = Image.new(
            "RGBA",
            (sum(v.width for v in views) + gap * (len(views) - 1),
             max(v.height for v in views)),
            rgba(bg) if bg else (0, 0, 0, 0))
        x = 0
        for v in views:
            sheet.alpha_composite(v, (x, sheet.height - v.height))
            x += v.width + gap
        sheet.save(out / f"{model.name}_preview.png")

    print(f"{model.name}: {len(model.boxes)} boxes, {model.atlas.size}x"
          f"{model.atlas.size} atlas, {model.height:g} units tall -> {png.name}"
          f" + {model.name}.bbmodel")
    if blank:
        print(f"  note: unpainted faces: {', '.join(blank)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("which", nargs="+",
                    help=f"{sorted(CHARACTERS)} or 'all'")
    ap.add_argument("--out", type=Path, default=Path("models"))
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--scale", type=int, default=6)
    ap.add_argument("--bg", default=None)
    args = ap.parse_args(argv)

    names = list(CHARACTERS) if "all" in args.which else args.which
    for name in names:
        if name not in CHARACTERS:
            raise SystemExit(f"error: unknown character {name!r}; "
                             f"have {sorted(CHARACTERS)} or 'all'")
        export(CHARACTERS[name](), args.out, args.preview, args.scale, args.bg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
