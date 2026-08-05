---
name: bongle-fix-import
description: Diagnose a model that bongle.io rejects, renders wrongly, or renders with parts missing or scattered. Use when the user says a model "won't import", "has missing textures", "is exploded", "parts are floating", "the head isn't rendering", or reports errors from bongle's editor — and when deciding whether a fault is in the file or in the viewer.
---

# Diagnosing a bongle import

Work through this in order. The first two steps are cheap and catch most faults.

## 1. The rig is the usual culprit

bongle matches bones **by name**. A missing bone produces a distinctive symptom:
**the editor renders the geometry fine while the game viewer scatters the
parts**, because there is nothing to attach them to.

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

Check locally rather than guessing:

```bash
python -c "import bongle_model as bm; m = bm.build_otis(); print(bm.json.dumps(m.bone_issues(), indent=2))"
```

`bone_issues()` reproduces bongle's own validation. `export()` calls it and
refuses to write a rejected model, so any file produced by a current
`bongle_model.py` already passes. A file that fails was likely produced by hand,
by `make_bbmodel.py` (boxes but **no bones**), or by an older version.

## 2. Read bongle's own error list

The editor has a **View Model Issues** dialog behind the warning/error badges in
the status bar, bottom right next to the FPS counter. It names each missing or
misparented bone precisely. Always prefer these messages to inference — ask the
user to open it and paste the contents if you can't see it yourself.

A clean model shows **no badges at all** beside the FPS counter.

## 3. Texture not showing

If the model renders untextured or partly untextured, check the texture block in
the `.bbmodel`:

- **`mode: "bitmap"`** must be present. Blockbench finds the embedded base64
  `source` without it, but other importers key off `mode` and never look — the
  model then renders untextured. This was a real bug in this repo.
- `path` / `relative_path` should name the sibling PNG as a fallback for
  path-based loaders, and the PNG should sit next to the `.bbmodel`.
- Float noise like `-3.5999999999999996` in coordinates can trip stricter
  parsers than Blockbench's. Values are rounded to 4dp on export.

A duplicate texture entry in the Textures panel is usually transient — check on a
clean reload before chasing it.

## 4. Distinguish file faults from viewer faults

Both `bongle_model.py` and `make_bbmodel.py` can emit a file named for the same
character. Two different models with the same filename in one downloads folder
are indistinguishable once open, because the project title comes from the file.

Tell them apart in the **Outliner**, not by filename:

- `bongle_model.py` output — bone folders (`waist`, `body`, `head`, …) with
  cubes as leaves, cube names like `head_mesh`
- `make_bbmodel.py` output — 12 cubes, names like `head`, `head_layer`, `body`

If the user reports no change after re-importing, suspect they opened the older
duplicate. Confirm via the Outliner contents and box count.

Also check the change actually reached the server: bongle's editor has a
**Save avatar to bongle** button. Loading `/editor/a/<name>` fetches the stored
version, so if the editor shows the fix, the save went through.

## Verifying claims honestly

Never assert a fix works in a viewer you haven't seen. Specifically:

- Local checks (`bone_issues`, `overlaps`, `unpainted`, UV rects, embedded
  texture round-trip) prove the **file** is correct. Say that, and say it's what
  you verified.
- Whether the **game viewer** renders it is a separate claim. Ask the user, or
  look — and if looking, note that bongle's WebGL previews may not render at all
  in an automated browser context (blank thumbnails, single-digit FPS), which
  makes visual diagnosis unreliable rather than negative.

If a fault persists after the rig is valid, suspect changes you introduced on
your own initiative — the `_mesh` suffix on cube names is a local convention, not
a bongle requirement, and is a candidate whenever exactly one body part
misbehaves.
