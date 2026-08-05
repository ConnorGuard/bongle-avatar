---
name: bongle-skin
description: Generate or hand-author 64×64 Minecraft-format skin atlases with bongle_skin.py — randomised characters, named presets, or the UV orientation key. Use when the user wants a skin for the standard humanoid rather than custom geometry, mentions "Minecraft skin", "64x64 skin", "player.png", slim vs classic arms, skin overlay/hat layer, or asks to check whether a skin's UVs map correctly.
---

# Minecraft-format skins

`bongle_skin.py` writes standard 64×64 skin atlases: the six unwrapped cube
faces of the vanilla humanoid, base plus overlay layer, classic (4px) or slim
(3px) arms. Use this when the target is the **stock humanoid**. For custom
proportions, rotated ears or extra boxes, use `bongle_model.py` and the
`bongle-character` skill instead.

## Which mode

```bash
python bongle_skin.py character --count 8 --preview   # randomised from traits
python bongle_skin.py character --name connor         # deterministic from a string
python bongle_skin.py preset finn --preview           # hand-authored designs
python bongle_skin.py uvkey --preview                 # orientation key
python bongle_skin.py verify path/to/skin.png         # diff against a reference
```

- `character` — randomised from an 11-field `Traits` record. `--name` hashes the
  string with SHA-256, so a name always yields the same skin on any machine.
  `--traits '{"hair_style":"mohawk"}'` pins individual fields.
- `preset` — specific designs as painter functions in `PRESETS`
  (`paint_finn`, `paint_jake` are the examples).
- `uvkey` — one hue per facing plus eye marks, for checking UV mapping.
- `verify` — diffs a uvkey render against a reference atlas per arm width, which
  also identifies whether an unknown skin is classic or slim.

**Keep `--scale 1`** for anything that will be loaded as a skin. Upscaling
produces a 128×128+ image that is no longer a valid atlas; `--scale` is for
inspection only.

## The atlas layout

| part | base origin | overlay origin | box (w×h×d) |
| --- | --- | --- | --- |
| head | (0,0) | (32,0) | 8×8×8 |
| torso | (16,16) | (16,32) | 8×12×4 |
| right arm | (40,16) | (40,32) | 4×12×4 |
| left arm | (32,48) | (48,48) | 4×12×4 |
| right leg | (0,16) | (0,32) | 4×12×4 |
| left leg | (16,48) | (0,48) | 4×12×4 |

Within an island: `top`, `bottom` in a strip of depth `d`, then `right`,
`front`, `left`, `back` below. `--slim` narrows the arm islands.

## Painting

Never compute atlas offsets. `skin.face(part, face, layer)` returns a
face-local view:

```python
skin = bs.Skin(slim=False)
skin.face("head", "front").fill("#f1c39a")
skin.face("torso", "front").rows(0, 3, "#e63946")   # row 0 is the TOP row
skin.face("head", "front", "overlay").border("#fff")
assert not skin.occupancy_check()   # nothing outside a legal UV island
```

`occupancy_check()` runs automatically before every save.

## The overlay layer

The second layer is a slightly inflated shell — 0.5 for the hat, 0.25 elsewhere.
Use it for anything that should stand off the body: hats, hair volume, jackets,
animal ears.

Two things to get right:

- **Keep the base layer complete underneath.** `paint_finn` paints a full face on
  the base head and puts the bear hat on the overlay with a hole cut in its
  front, so the skin still reads if a renderer drops the overlay.
- **A skin cannot add geometry.** Overlay "ears" are a shell around the head
  cube. They read correctly in a 3D renderer but a flat orthographic preview
  understates them. Genuinely protruding ears need `bongle_model.py`.

## Adding a preset

Write a painter taking a `Skin` and returning it, register it in `PRESETS`, keep
its palette in a module-scope dict:

```python
def paint_someone(skin: Skin) -> Skin:
    for face in FACES:
        skin.face("head", face).fill(PALETTE["skin"])
    ...
    return skin
```

## Verify before reporting

Render `--preview` (front/right/back/left elevations assembled from the painted
faces) and **look at it**. If reproducing an existing skin, use
`verify` and quote the result rather than asserting a match.

If a skin must load in bongle's game viewer rather than just render, note that
bongle requires a named bone rig — see the `bongle-fix-import` skill. A bare
skin PNG has no rig; `make_bbmodel.py` output has boxes but no bones.
