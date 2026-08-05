#!/usr/bin/env python
"""Measure an existing pixel-art sprite sheet so a generator can match it.

Reports image size, the palette, the likely cell grid (found by looking for
uniform separator rows/columns), and the tight bounding box of each cell's
content - which is what tells you the real sprite footprint inside its cell.

    python inspect_sheet.py path/to/sheet.png
    python inspect_sheet.py path/to/sheet.png --cell 16x24
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from PIL import Image


def load(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def uniform_lines(img: Image.Image, axis: str) -> list[int]:
    """Indices of rows (or columns) that are a single flat colour."""
    px = img.load()
    w, h = img.size
    out = []
    if axis == "row":
        for y in range(h):
            if len({px[x, y] for x in range(w)}) == 1:
                out.append(y)
    else:
        for x in range(w):
            if len({px[x, y] for y in range(h)}) == 1:
                out.append(x)
    return out


def runs(indices: list[int]) -> list[tuple[int, int]]:
    """Collapse a sorted index list into (start, end) inclusive runs."""
    out = []
    for i in indices:
        if out and i == out[-1][1] + 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def guess_scale(img: Image.Image) -> int:
    """Largest N where the image is a clean NxN block upscale of itself."""
    w, h = img.size
    px = img.load()
    for n in range(min(w, h, 32), 1, -1):
        if w % n or h % n:
            continue
        ok = True
        for by in range(0, h, n):
            for bx in range(0, w, n):
                first = px[bx, by]
                if any(px[bx + dx, by + dy] != first
                       for dy in range(n) for dx in range(n)):
                    ok = False
                    break
            if not ok:
                break
        if ok:
            return n
    return 1


def content_boxes(img: Image.Image, cw: int, ch: int, pad: int = 0):
    """Per-cell tight bounding box of non-background pixels."""
    px = img.load()
    w, h = img.size
    bg = Counter(px[x, y] for x in range(w) for y in range(h)).most_common(1)[0][0]
    step_x, step_y = cw + pad, ch + pad
    boxes = []
    for row, oy in enumerate(range(0, h - ch + 1, step_y)):
        for col, ox in enumerate(range(0, w - cw + 1, step_x)):
            xs, ys = [], []
            for y in range(ch):
                for x in range(cw):
                    p = px[ox + x, oy + y]
                    if p != bg and p[3] != 0:
                        xs.append(x)
                        ys.append(y)
            if xs:
                boxes.append((row, col, min(xs), min(ys), max(xs), max(ys)))
    return bg, boxes


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path)
    ap.add_argument("--cell", help="known cell size as WxH, e.g. 16x24")
    ap.add_argument("--pad", type=int, default=0, help="gap between cells")
    ap.add_argument("--top", type=int, default=24, help="palette entries to list")
    args = ap.parse_args()

    img = load(args.path)
    w, h = img.size
    print(f"file    : {args.path}")
    print(f"size    : {w} x {h} px")

    scale = guess_scale(img)
    print(f"scale   : {scale}x block upscale "
          f"(native art is {w // scale} x {h // scale})")

    px = img.load()
    colors = Counter(px[x, y] for y in range(h) for x in range(w))
    opaque = {c: n for c, n in colors.items() if c[3] != 0}
    print(f"palette : {len(opaque)} opaque colours, "
          f"{colors.get((0, 0, 0, 0), 0)} transparent px")
    for color, count in Counter(opaque).most_common(args.top):
        r, g, b, a = color
        print(f"          #{r:02x}{g:02x}{b:02x} a={a:<3} x{count}")

    print("separators (flat rows)   :", runs(uniform_lines(img, "row")) or "none")
    print("separators (flat columns):", runs(uniform_lines(img, "col")) or "none")

    if args.cell:
        cw, ch = (int(v) for v in args.cell.lower().split("x"))
        bg, boxes = content_boxes(img, cw, ch, args.pad)
        print(f"\nbackground: {bg}")
        print(f"cells {cw}x{ch} (pad {args.pad}) -> "
              f"{len({b[0] for b in boxes})} rows x {len({b[1] for b in boxes})} cols")
        print("row col  x0  y0  x1  y1   w   h")
        for row, col, x0, y0, x1, y1 in boxes:
            print(f"{row:3} {col:3} {x0:3} {y0:3} {x1:3} {y1:3} "
                  f"{x1 - x0 + 1:3} {y1 - y0 + 1:3}")


if __name__ == "__main__":
    main()
