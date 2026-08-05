#!/usr/bin/env python
"""Build a Blockbench .bbmodel project around a 64x64 skin atlas.

The project contains the humanoid boxes with box-UV mapping, both layers per
part (overlay cubes inflated the way the game inflates them: 0.5 for the hat,
0.25 elsewhere), and the PNG embedded as a base64 data URI - so the file is
self-contained and opens with the texture already applied.

    python make_bbmodel.py skins/finn.png skins/jake.png
    python make_bbmodel.py skins/uvkey.png --out skins
    python make_bbmodel.py skins/some_slim_skin.png --slim
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

ATLAS = 64

# Blockbench face name -> the face of the box it denotes.
# The model faces north (-Z), so the player's right side is west (-X).
FACE_NAMES = {
    "north": "front",
    "south": "back",
    "west": "right",
    "east": "left",
    "up": "top",
    "down": "bottom",
}


def boxes(slim: bool = False):
    """Player model geometry in Blockbench units, feet at y=0."""
    aw = 3 if slim else 4
    return {
        "head": dict(
            origin=(0, 24, 0), start=(-4, 24, -4), size=(8, 8, 8),
            base=(0, 0), layer=(32, 0), inflate=0.5),
        "body": dict(
            origin=(0, 24, 0), start=(-4, 12, -2), size=(8, 12, 4),
            base=(16, 16), layer=(16, 32), inflate=0.25),
        "right_arm": dict(
            origin=(-5, 22, 0), start=(-4 - aw, 12, -2), size=(aw, 12, 4),
            base=(40, 16), layer=(40, 32), inflate=0.25),
        "left_arm": dict(
            origin=(5, 22, 0), start=(4, 12, -2), size=(aw, 12, 4),
            base=(32, 48), layer=(48, 48), inflate=0.25),
        "right_leg": dict(
            origin=(-2, 12, 0), start=(-4, 0, -2), size=(4, 12, 4),
            base=(0, 16), layer=(0, 32), inflate=0.25),
        "left_leg": dict(
            origin=(2, 12, 0), start=(0, 0, -2), size=(4, 12, 4),
            base=(16, 48), layer=(0, 48), inflate=0.25),
    }


def face_uv(origin, size, face):
    """The Minecraft box unwrap: top/bottom strip above the four sides."""
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


def det_uuid(seed: str) -> str:
    """Stable UUIDs so regenerating a project yields an identical file."""
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def cube(name, part, spec, layer: bool, tex_index: int):
    uv_origin = spec["layer"] if layer else spec["base"]
    x, y, z = spec["start"]
    w, h, d = spec["size"]
    faces = {}
    for bb_face, box_face in FACE_NAMES.items():
        fx, fy, fw, fh = face_uv(uv_origin, spec["size"], box_face)
        faces[bb_face] = {"uv": [fx, fy, fx + fw, fy + fh], "texture": tex_index}
    return {
        "name": name,
        "box_uv": True,
        "rescale": False,
        "locked": False,
        "render_order": "default",
        "allow_mirror_modeling": True,
        "from": [x, y, z],
        "to": [x + w, y + h, z + d],
        "inflate": spec["inflate"] if layer else 0,
        "autouv": 0,
        "color": 0,
        "origin": list(spec["origin"]),
        "uv_offset": list(uv_origin),
        "faces": faces,
        "type": "cube",
        "uuid": det_uuid(f"{part}:{'layer' if layer else 'base'}"),
    }


def build(png: Path, slim: bool = False) -> dict:
    data = png.read_bytes()
    tex_uuid = det_uuid(f"texture:{png.name}")
    texture = {
        # mode="bitmap" tells importers the base64 `source` is the texture.
        # Without it, non-Blockbench loaders can render the model untextured.
        "mode": "bitmap",
        "path": png.name,
        "relative_path": f"./{png.name}",
        "name": png.name,
        "folder": "",
        "namespace": "",
        "id": "0",
        "width": ATLAS,
        "height": ATLAS,
        "uv_width": ATLAS,
        "uv_height": ATLAS,
        "particle": False,
        "use_as_default": False,
        "layers_enabled": False,
        "sync_to_project": "",
        "render_mode": "default",
        "render_sides": "auto",
        "frame_time": 1,
        "frame_order_type": "loop",
        "frame_order": "",
        "frame_interpolate": False,
        "visible": True,
        "internal": True,
        "saved": True,
        "uuid": tex_uuid,
        "source": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
    }

    elements, outliner = [], []
    for part, spec in boxes(slim).items():
        base = cube(part, part, spec, layer=False, tex_index=0)
        over = cube(f"{part}_layer", part, spec, layer=True, tex_index=0)
        elements += [base, over]
        outliner.append({
            "name": part,
            "origin": list(spec["origin"]),
            "rotation": [0, 0, 0],
            "uuid": det_uuid(f"group:{part}"),
            "export": True,
            "mirror_uv": False,
            "isOpen": False,
            "locked": False,
            "visibility": True,
            "autouv": False,
            "children": [base["uuid"], over["uuid"]],
        })

    return {
        "meta": {
            "format_version": "4.5",
            "model_format": "free",
            "box_uv": True,
        },
        "name": png.stem,
        "model_identifier": "",
        "visible_box": [2, 2, 0],
        "resolution": {"width": ATLAS, "height": ATLAS},
        "elements": elements,
        "outliner": outliner,
        "textures": [texture],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skins", nargs="+", type=Path, help="64x64 skin PNG(s)")
    ap.add_argument("--out", type=Path, default=None,
                    help="output directory (default: alongside each PNG)")
    ap.add_argument("--slim", action="store_true", help="3px arms")
    args = ap.parse_args(argv)

    for png in args.skins:
        if not png.is_file():
            raise SystemExit(f"error: {png} not found")
        project = build(png, slim=args.slim)
        out_dir = args.out or png.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{png.stem}.bbmodel"
        dest.write_text(json.dumps(project, indent=2))
        kb = dest.stat().st_size / 1024
        print(f"wrote {dest}  ({len(project['elements'])} cubes, "
              f"{'slim' if args.slim else 'classic'} arms, {kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
