---
name: bongle-character
description: Add or edit a custom-proportioned character model for bongle.io — box geometry, face painting, the required bone rig, export and verification. Use when the user asks to create a new character or creature, change an existing one's proportions, colours, accessories or rotated parts (ears, tails), or says things like "add a character called X", "make Otis taller", "give Jake a hat", "build me a dragon avatar".
---

# Building a bongle character

Characters live in `bongle_model.py` as `build_*` functions registered in the
`CHARACTERS` dict. `build_finn`, `build_otis` and `build_jake` are the worked
examples — read the closest one before writing a new one.

## Order of operations

This order matters; `pack()` sits in the middle.

1. `m = Model("<name>_custom")` — the `_custom` suffix keeps output filenames
   from colliding with anything `make_bbmodel.py` emits for the same character.
2. `m.add(...)` every box.
3. `m.bone(...)` every bone, assigning boxes.
4. `m.pack()` — assigns UV islands and sizes the atlas. **Painting before this
   raises.**
5. Paint faces.
6. `return m`, and register in `CHARACTERS`.

## Geometry

```python
m.add(name, start=(x, y, z), size=(w, h, d), origin=(px, py, pz),
      rotation=(rx, ry, rz))
```

- **Sizes must be whole numbers** — box UV maps one texel per unit, and the
  constructor raises on a fraction. Positions, pivots and rotations may be
  fractional.
- Units are Blockbench units, 16 per block. Feet at `y=0`. Front faces **−Z**,
  so the character's right is **−X**.
- `origin` is the rotation pivot, defaulting to the box's own bottom-centre.
- Rotation is real geometry, unlike a flat skin. For a taper (a pointy ear),
  use two boxes — a wide base and a narrower tip — sharing **one pivot and one
  angle** so they move as a unit.

### Rotation signs

Derive, don't guess. Rotating about Z by θ moves a point at offset `(0, ±L, 0)`
from the pivot to `x' = −(±L)·sin θ`:

- Hanging **below** the pivot (floppy ear): positive θ swings the tip toward +X,
  so the character's **right** side takes the **negative** angle.
- Standing **above** the pivot (upright ear): the sign flips — the right side
  takes the **positive** angle.

`build_jake` (hanging) and `build_otis` (upright) show both. If ears tuck inward
when opened in Blockbench, flip both signs.

## The rig — mandatory

bongle matches bones **by name** and rejects a model missing any of them.
Symptom of a bad rig: the editor renders geometry fine, but parts scatter in the
game viewer.

```
waist                 (root)
  body
    back              (optional)
  head
  arm_left / arm_right
    hand_left / hand_right   (optional)
leg_left              (root — NOT under waist)
leg_right             (root)
```

```python
m.bone(name, parent, origin, boxes)
```

Every box must belong to **exactly one** bone. Put accessories on the bone they
should move with — a harness on `body`, a hat and ears on `head`, shoes on the
matching leg, a backpack in the optional `back`.

A cube sharing a bone's name is exported with a `_mesh` suffix automatically, so
name-matching stays unambiguous. Don't add the suffix yourself.

## Painting

`m.face(box, face)` gives face-local coordinates — never compute atlas offsets.
Faces: `top`, `bottom`, `left`, `right`, `front`, `back`, using the character's
own left/right.

```python
m.fill("torso", "#cd9457")                      # all six faces
m.fill("torso", "#cd9457", faces=("front",))    # or some
m.face("torso", "front").rows(0, 2, "#59b3e3")  # row 0 is the TOP row
m.face("head", "front").rect(1, 2, 2, 3, "#fff")
m.face("head", "left").border("#000")
m.face("head", "left").shade_edges(0.1)         # darken the 1px rim
```

Keep a palette dict at module scope (see `OTIS`, `JAKE`, `FINN`) rather than
inlining hex codes, and give it `_shade` / `_deep` variants for depth.

Watch for geometry hiding paintwork: a detail placed where another box sits in
front of it is invisible. Otis's chest swirl is deliberately positioned in the
gap between two harness bands for this reason.

## Export and verify

```bash
python bongle_model.py <name> --preview --bg "#2c3040"
```

Three checks run before anything is written — all three must pass, and they
raise rather than warn:

- `bone_issues()` — the rig, including boxes not assigned to any bone
- `overlaps()` — atlas islands colliding
- `unpainted()` — faces left fully transparent

Then **look at the preview** before reporting success. It writes front/right/back
elevations, exact for heights and widths but **rotation is not applied**, so
floppy ears appear straight — verify rotation in Blockbench, not the preview.

Check proportions against the other characters, since relative height is what
reads as correct: Finn 32 units, Otis 24, Jake 17.

## Reporting

State the box count, height in units, and anything the user should check by eye
in Blockbench (rotation directions especially). Don't claim the rotation
direction is confirmed — the preview can't show it.
