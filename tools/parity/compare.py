#!/usr/bin/env python3
"""Perceptual comparison of the Mac's frames with legion's Windows references.

    .venv/bin/python tools/parity/compare.py                    # every pair below whose files exist
    .venv/bin/python tools/parity/compare.py main-menu video    # some of them, by name
    .venv/bin/python tools/parity/compare.py --pair MAC.png REF.png [--name N]
                                             [--cursor X,Y ...] [--mask X,Y,W,H ...]

The Mac's frames are the dumps of the menu test (build/menus/<scenario>/,
tests/menus/); the references are legion's (analysis/parity/menus-ref/ and
ingame-ref/, fetched by scp, never committed). For each pair:

  1. The Mac's frame takes the path legion's took: GOG's ddraw wrapper scales
     the 4:3 game frame to the 2133x1600 crop of the 2560x1600 panel, and
     refgrab.py area-averages the crop to the reference's size. `--wrapper`
     names the wrapper's filter (bilinear by default; `none` resizes directly).
  2. The two are aligned by the integer shift (up to 3 px) that matches best.
  3. Measured, with the cursors (`--cursor`, both machines') and `--mask`
     rectangles left out of every number:
       - SSIM on luminance, 7x7 uniform windows: the whole frame, and again at
         half size ("coarse"), which forgives filtering and one-pixel text;
       - the mean absolute difference per channel, and a per-channel fit
         ref = a*mac + b over pixels whose structure matches, plus a gamma
         estimate: a colour or gamma difference shows there, not in SSIM;
       - the regions that differ: 16-px blocks with SSIM below --threshold,
         grouped into boxes, largest first.

Writes analysis/parity/out/NAME-side.png (Mac | Windows | SSIM map with the
boxes), NAME-diff.png (absolute difference x4 with the boxes) and report.json,
and prints a table. docs/reference/parity.md has the results and what each
difference is.
"""
import argparse
import json
import sys
from collections import deque
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    sys.exit("compare.py needs numpy and pillow in the venv (.venv/bin/pip install numpy pillow)")

ROOT = Path(__file__).resolve().parents[2]
MAC = ROOT / "build/menus"
REF = ROOT / "analysis/parity"
OUT = ROOT / "analysis/parity/out"
NATIVE = (2133, 1600)  # legion's 4:3 crop of its 2560x1600 panel (menus-ref/screens.md)

# Where each cursor rests, in the reference's pixels. Legion parks its cursor
# at about (42,403) in the menus and (690,662) in the editor (screens.md); the
# Mac's sits where the scenario's last click left it (tests/menus/*.script).
LEGION_MENU = (42, 403)
LEGION_EDITOR = (690, 662)

# name: (Mac dump under --mac, reference under --ref, cursors in reference
# pixels[, rectangles X,Y,W,H left out])
# The editor's map view and minimap: legion's view had scrolled to the map's edge.
EDITOR_MAP = [(0, 0, 1024, 556), (846, 576, 178, 178)]
PAIRS = {
    "main-menu": ("settings/main-menu.png", "menus-ref/main-menu.png", [LEGION_MENU, (513, 385)]),
    "video": ("settings/video.png", "menus-ref/options-video.png", [LEGION_MENU, (511, 577)]),
    "audio": ("settings/audio.png", "menus-ref/options-audio.png", [LEGION_MENU, (430, 33)]),
    "interface": ("settings/interface.png", "menus-ref/options-interface.png",
                  [LEGION_MENU, (618, 33)]),
    "single-player": ("single-player/single-player.png", "menus-ref/single-player.png",
                      [LEGION_MENU, (511, 268)]),
    "random-map-setup": ("single-player/game-setup.png", "menus-ref/random-map-setup.png",
                         [LEGION_MENU, (748, 145)]),
    "game-tools": ("game-tools/game-tools.png", "menus-ref/game-tools.png",
                   [LEGION_MENU, (511, 474)]),
    "civ-builder": ("game-tools/civ-builder.png", "menus-ref/civ-builder.png",
                    [LEGION_MENU, (511, 268)]),
    "scenario-editor": ("game-tools/scenario-editor.png", "menus-ref/scenario-editor.png",
                        [LEGION_EDITOR, (512, 384)]),
    # The radio group (legion clicked at the radios' centres; its map view had
    # scrolled, so only the panel is compared).
    "editor-random": ("game-tools/editor-random.png", "ingame-ref/editor-radio-1.png",
                      [LEGION_EDITOR, (44, 611)], EDITOR_MAP),
    "editor-seed": ("game-tools/editor-seed.png", "ingame-ref/editor-radio-2.png",
                    [LEGION_EDITOR, (44, 636)], EDITOR_MAP),
    "editor-blank": ("game-tools/editor-blank.png", "ingame-ref/editor-radio-3.png",
                     [LEGION_EDITOR, (44, 585)], EDITOR_MAP),
    # The in-game run (tests/menus/random-map.script: seed 4242, 800x600x32,
    # the Capitol clicked at (400,225) after the 60 s frame).
    "game-setup": ("random-map/game-setup.png", "ingame-ref/game-setup.png",
                   [LEGION_MENU, (748, 145)]),
    "loading": ("random-map/loading.png", "ingame-ref/loading.png", []),
    "in-game-05s": ("random-map/in-game-05s.png", "ingame-ref/in-game-05s.png", []),
    "in-game-30s": ("random-map/in-game-30s.png", "ingame-ref/in-game-30s.png", []),
    "in-game-60s": ("random-map/in-game-60s.png", "ingame-ref/in-game-60s.png", []),
    "town-center-selected": ("random-map/town-center-selected.png",
                             "ingame-ref/town-center-selected.png", [(400, 225), (745, 188)]),
    "in-game-90s": ("random-map/in-game-90s.png", "ingame-ref/in-game-90s.png",
                    [(400, 225), (745, 188)]),
}

CURSOR_BOX = (-6, -6, 40, 46)  # a cursor's rectangle around its hot spot (the menu sprite)


def load(path):
    return Image.open(path).convert("RGB")


def as_reference(mac, size, wrapper):
    """The Mac's frame as legion's grab would show it at `size`."""
    if wrapper == "none" or mac.size == NATIVE:
        return mac.resize(size, Image.BOX if mac.width > size[0] else Image.BILINEAR)
    filt = {"bilinear": Image.BILINEAR, "nearest": Image.NEAREST,
            "bicubic": Image.BICUBIC, "lanczos": Image.LANCZOS}[wrapper]
    return mac.resize(NATIVE, filt).resize(size, Image.BOX)


def luminance(a):
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def box_mean(x, r):
    """Mean over (2r+1)^2 windows, edges replicated; same shape as x."""
    p = np.pad(x, r + 1, mode="edge")
    c = p.cumsum(0).cumsum(1)
    n = 2 * r + 1
    s = c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]
    return s[: x.shape[0], : x.shape[1]] / (n * n)


def ssim_map(a, b, r=3):
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    ma, mb = box_mean(a, r), box_mean(b, r)
    va = box_mean(a * a, r) - ma * ma
    vb = box_mean(b * b, r) - mb * mb
    cov = box_mean(a * b, r) - ma * mb
    return ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))


def best_shift(a, b, valid, reach=3):
    """The (dx, dy) that moves `a` onto `b` best (mean |luminance difference|)."""
    la, lb = luminance(a), luminance(b)
    h, w = lb.shape
    best = (float("inf"), 0, 0)
    m = reach + 2
    core = valid[m:h - m, m:w - m]
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            sa = la[m - dy:h - m - dy, m - dx:w - m - dx]
            d = np.abs(sa - lb[m:h - m, m:w - m])[core].mean()
            if d < best[0]:
                best = (d, dx, dy)
    return best[1], best[2]


def shift(a, dx, dy):
    return np.roll(np.roll(a, dy, axis=0), dx, axis=1)


def regions(bad):
    """Boxes of 4-connected True blocks, largest first: [(x0, y0, x1, y1, blocks)]."""
    seen = np.zeros_like(bad)
    out = []
    for y, x in zip(*np.nonzero(bad)):
        if seen[y, x]:
            continue
        q = deque([(y, x)])
        seen[y, x] = True
        y0 = y1 = y
        x0 = x1 = x
        n = 0
        while q:
            cy, cx = q.popleft()
            n += 1
            y0, y1, x0, x1 = min(y0, cy), max(y1, cy), min(x0, cx), max(x1, cx)
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < bad.shape[0] and 0 <= nx < bad.shape[1] and bad[ny, nx] \
                        and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        out.append((int(x0), int(y0), int(x1) + 1, int(y1) + 1, n))
    return sorted(out, key=lambda r: -r[4])


def colour_fit(a, b, sel):
    """Per channel: mean(ref - mac), ref = k*mac + c, and gamma g in ref = mac^g."""
    fits = []
    for ch in range(3):
        x, y = a[..., ch][sel], b[..., ch][sel]
        if x.size < 100:
            fits.append(None)
            continue
        k, c = np.polyfit(x, y, 1)
        mid = (x > 16) & (x < 240) & (y > 16) & (y < 240)
        g = None
        if mid.sum() > 100:
            lx, ly = np.log(x[mid] / 255.0), np.log(y[mid] / 255.0)
            g = float((lx * ly).sum() / (lx * lx).sum())
        fits.append({"mean_delta": round(float((y - x).mean()), 2), "scale": round(float(k), 3),
                     "offset": round(float(c), 1), "gamma": None if g is None else round(g, 3)})
    return fits


def compare(name, mac_path, ref_path, cursors=(), masks=(), wrapper="bilinear", threshold=0.6,
            block=16, out=OUT):
    mac_img, ref_img = load(mac_path), load(ref_path)
    size = ref_img.size
    sx, sy = size[0] / mac_img.width, size[1] / mac_img.height
    a = np.asarray(as_reference(mac_img, size, wrapper), dtype=np.float64)
    b = np.asarray(ref_img, dtype=np.float64)
    h, w = b.shape[:2]

    valid = np.ones((h, w), bool)
    rects = [(int(x + CURSOR_BOX[0]), int(y + CURSOR_BOX[1]), CURSOR_BOX[2], CURSOR_BOX[3])
             for x, y in cursors] + [tuple(m) for m in masks]
    for x, y, rw, rh in rects:
        valid[max(0, y):max(0, y + rh), max(0, x):max(0, x + rw)] = False

    dx, dy = best_shift(a, b, valid)
    a = shift(a, dx, dy)
    edge = np.zeros((h, w), bool)  # what the shift wrapped around
    edge[:max(dy, 0)] = edge[h + min(dy, 0):] = True
    edge[:, :max(dx, 0)] = edge[:, w + min(dx, 0):] = True
    valid &= ~edge

    la, lb = luminance(a), luminance(b)
    s = ssim_map(la, lb)
    ssim = float(s[valid].mean())
    # Coarse: both at half size, which forgives the filters and sub-pixel text.
    half = lambda x: x[: h // 2 * 2, : w // 2 * 2].reshape(h // 2, 2, w // 2, 2).mean((1, 3))
    vh = half(valid.astype(float)) > 0.99
    coarse = float(ssim_map(half(la), half(lb))[vh].mean())
    diff = np.abs(a - b)
    mad = [round(float(diff[..., c][valid].mean()), 2) for c in range(3)]
    fits = colour_fit(a, b, valid & (s > 0.8))

    # Blocks that differ: low SSIM, or a large colour difference with matching structure.
    bh, bw = h // block, w // block
    crop = lambda x: x[: bh * block, : bw * block].reshape(bh, block, bw, block).mean((1, 3))
    vb = crop(valid.astype(float)) > 0.5
    sb = crop(np.where(valid, s, 1.0))
    db = crop(np.where(valid, luminance(diff), 0.0))
    bad = vb & ((sb < threshold) | (db > 40))
    boxes = []
    for x0, y0, x1, y1, n in regions(bad)[:12]:
        X0, Y0, X1, Y1 = x0 * block, y0 * block, x1 * block, y1 * block
        sel = valid[Y0:Y1, X0:X1]
        boxes.append({"box": [X0, Y0, X1, Y1], "blocks": n,
                      "ssim": round(float(s[Y0:Y1, X0:X1][sel].mean()), 3),
                      "mac_rgb": [int(v) for v in a[Y0:Y1, X0:X1][sel].mean(0)],
                      "ref_rgb": [int(v) for v in b[Y0:Y1, X0:X1][sel].mean(0)]})

    out.mkdir(parents=True, exist_ok=True)
    heat = np.zeros((h, w, 3))
    heat[..., 0] = np.clip((1 - s) * 255 * 1.5, 0, 255)
    heat[..., 1] = heat[..., 2] = lb * 0.25
    heat[~valid] = (0, 0, 96)
    side = Image.new("RGB", (w * 3, h))
    side.paste(Image.fromarray(a.astype(np.uint8)), (0, 0))
    side.paste(ref_img, (w, 0))
    side.paste(Image.fromarray(heat.astype(np.uint8)), (2 * w, 0))
    d = ImageDraw.Draw(side)
    for i, r in enumerate(boxes):
        x0, y0, x1, y1 = r["box"]
        for off in (0, w, 2 * w):
            d.rectangle((x0 + off, y0, x1 + off - 1, y1 - 1), outline=(255, 255, 0))
        d.text((x0 + 2 * w + 2, y0 + 1), str(i + 1), fill=(255, 255, 0))
    for label, off in (("Mac", 0), ("Windows", w), ("1-SSIM", 2 * w)):
        d.text((off + 4, h - 14), label, fill=(255, 0, 255))
    side.save(out / ("%s-side.png" % name))
    dimg = Image.fromarray(np.clip(diff * 4, 0, 255).astype(np.uint8))
    dd = ImageDraw.Draw(dimg)
    for r in boxes:
        dd.rectangle(tuple(v - (1 if i > 1 else 0) for i, v in enumerate(r["box"])),
                     outline=(255, 255, 0))
    dimg.save(out / ("%s-diff.png" % name))

    return {"name": name, "mac": str(mac_path), "ref": str(ref_path),
            "mac_size": list(mac_img.size), "ref_size": list(size), "scale": [round(sx, 4), round(sy, 4)],
            "wrapper": wrapper, "shift": [dx, dy], "ssim": round(ssim, 4),
            "ssim_coarse": round(coarse, 4), "mad_rgb": mad, "colour": fits,
            "masked": rects, "regions": boxes}


def rect(text):
    v = [int(t) for t in text.split(",")]
    if len(v) != 4:
        raise argparse.ArgumentTypeError("X,Y,W,H")
    return v


def point(text):
    v = [int(t) for t in text.split(",")]
    if len(v) != 2:
        raise argparse.ArgumentTypeError("X,Y")
    return v


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("names", nargs="*", help="pairs to compare (default: every one whose files exist)")
    p.add_argument("--mac", type=Path, default=MAC, help="the Mac's dumps (default build/menus)")
    p.add_argument("--ref", type=Path, default=REF, help="legion's references (default analysis/parity)")
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--pair", nargs=2, metavar=("MAC", "REF"), type=Path)
    p.add_argument("--name", default=None, help="--pair's output name")
    p.add_argument("--cursor", type=point, action="append", default=[], help="X,Y (reference pixels)")
    p.add_argument("--mask", type=rect, action="append", default=[], help="X,Y,W,H (reference pixels)")
    p.add_argument("--wrapper", default="bilinear",
                   choices=["bilinear", "nearest", "bicubic", "lanczos", "none"])
    p.add_argument("--threshold", type=float, default=0.6, help="block SSIM below which a block differs")
    args = p.parse_args(argv)

    jobs = []
    if args.pair:
        jobs.append((args.name or args.pair[0].stem, args.pair[0], args.pair[1], args.cursor, []))
    else:
        unknown = set(args.names) - set(PAIRS)
        if unknown:
            p.error("no such pair: %s (known: %s)" % (", ".join(sorted(unknown)), ", ".join(PAIRS)))
        for name, (mac, ref, cursors, *masks) in PAIRS.items():
            if args.names and name not in args.names:
                continue
            m, r = args.mac / mac, args.ref / ref
            if m.is_file() and r.is_file():
                jobs.append((name, m, r, cursors + args.cursor, (masks[0] if masks else [])))
            elif args.names:
                print("%s: missing %s" % (name, m if not m.is_file() else r), file=sys.stderr)
    if not jobs:
        p.error("nothing to compare")

    results = []
    print("%-22s %-9s %-7s %6s %6s  %-17s %-21s %s" % ("pair", "size", "shift", "SSIM", "coarse",
                                                       "mean |d| R,G,B", "gamma R,G,B", "regions"))
    for name, m, r, cursors, masks in jobs:
        res = compare(name, m, r, cursors, list(masks) + args.mask, args.wrapper, args.threshold,
                      out=args.out)
        results.append(res)
        gam = ",".join("-" if f is None or f["gamma"] is None else "%.2f" % f["gamma"]
                       for f in res["colour"])
        print("%-22s %-9s %-7s %6.3f %6.3f  %-17s %-21s %d" % (
            name, "%dx%d" % tuple(res["mac_size"]), "%+d,%+d" % tuple(res["shift"]), res["ssim"],
            res["ssim_coarse"], ",".join("%.1f" % v for v in res["mad_rgb"]), gam, len(res["regions"])))
        for i, b in enumerate(res["regions"][:6]):
            print("    %d. %-20s ssim %.2f  mac rgb %-15s ref rgb %s" % (
                i + 1, "%d,%d-%d,%d" % tuple(b["box"]), b["ssim"], tuple(b["mac_rgb"]), tuple(b["ref_rgb"])))
    args.out.mkdir(parents=True, exist_ok=True)
    report = args.out / "report.json"
    old = {}
    if report.is_file():
        try:
            old = {r["name"]: r for r in json.loads(report.read_text())}
        except (ValueError, KeyError, TypeError):
            old = {}
    old.update({r["name"]: r for r in results})
    report.write_text(json.dumps(list(old.values()), indent=1))
    print("side-by-side and difference images in %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
