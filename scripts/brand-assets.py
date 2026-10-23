#!/usr/bin/env python3
"""Every logo file the product ships, from one master (decision 21g).

    python3 scripts/brand-assets.py clean <supplied.png>   # -> design/brand/minas-logo.png
    python3 scripts/brand-assets.py derive                 # master -> every size below

Needs Pillow, which is NOT a dependency of the service: the outputs are
committed, so neither the server nor CI runs this. Run `clean` when the
owner supplies a new logo, then `derive`, and commit what they write.

`clean` exists because the logo supplied on 2026-10-22 carries a square
behind the mark that is nearly but not fully transparent - alpha 5, with
noise up to ~12 - which shows as a smudge on the portal's dark surface and a
grey box on white. Alpha is remapped so that floor becomes fully transparent
while the anti-aliased edges (16..255) keep their shape, then the mark is
trimmed and centred on a square canvas.

What `derive` writes, and why each size (sizes checked against Expo's and
Android's own pages, 2026-10-22):

    sentinel/static/minas-mark.png        128 px, transparent - the portal's
                                          lockup, shown at 28-36 CSS px
    sentinel/static/favicon.png            64 px, transparent - browser tab
    sentinel/static/apple-touch-icon.png  180 px, opaque - iOS home screen
    design/brand/app-icon.png            1024 px, opaque - Expo `icon`; iOS
                                          wants the square filled, no alpha
    design/brand/android-foreground.png  1024 px, transparent - the mark kept
                                          inside the inner 66 of 108 dp, the
                                          part no OEM mask clips
    design/brand/android-monochrome.png  1024 px, white silhouette, same
                                          geometry - Android themed icons
    design/brand/splash.png              1024 px, transparent - Expo splash

The app in mobile/ gets byte-identical copies of the four store files under
the names its app.json uses (mobile/assets/images/): Metro serves only what
is inside the app's folder. tests/test_mobile_app.py fails if a copy drifts.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "design" / "brand" / "minas-logo.png"
# The portal's --bg. Behind the mark wherever a platform needs an opaque
# square (app icon, home-screen icon, adaptive background).
BACKGROUND = (13, 15, 19)
ALPHA_FLOOR = 12  # the supplied backdrop's alpha, noise included
# design/brand file -> the name mobile/app.json refers to it by.
MOBILE_COPIES = {
    "app-icon.png": "icon.png",
    "android-foreground.png": "android-icon-foreground.png",
    "android-monochrome.png": "android-icon-monochrome.png",
    "splash.png": "splash-icon.png",
}


def _resize(im: Image.Image, size: int) -> Image.Image:
    # Premultiplied, so transparent pixels' colour cannot bleed into edges.
    return im.convert("RGBa").resize((size, size), Image.LANCZOS).convert("RGBA")


def _square(im: Image.Image, margin: float) -> Image.Image:
    """Trim to the visible mark and centre it on a square with `margin` (a
    fraction of the side) clear on every edge."""
    box = im.getchannel("A").getbbox()
    mark = im.crop(box)
    side = round(max(mark.size) / (1 - 2 * margin))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(mark, ((side - mark.width) // 2, (side - mark.height) // 2))
    return canvas


def _placed(master: Image.Image, canvas: int, mark_fraction: float, background=None) -> Image.Image:
    """The mark scaled to `mark_fraction` of a `canvas` square, centred."""
    mark = _resize(_square(master, 0.0), round(canvas * mark_fraction))
    out = Image.new("RGBA", (canvas, canvas), (*background, 255) if background else (0, 0, 0, 0))
    offset = (canvas - mark.width) // 2
    out.alpha_composite(mark, (offset, offset))
    return out


def clean(source: Path) -> None:
    im = Image.open(source).convert("RGBA")
    alpha = im.getchannel("A").point(
        lambda a: 0 if a <= ALPHA_FLOOR else round((a - ALPHA_FLOOR) * 255 / (255 - ALPHA_FLOOR)))
    im.putalpha(alpha)
    master = _resize(_square(im, 0.04), 2048)
    MASTER.parent.mkdir(parents=True, exist_ok=True)
    master.save(MASTER, optimize=True)
    print(f"wrote {MASTER.relative_to(ROOT)} {master.size}")


def derive() -> None:
    master = Image.open(MASTER).convert("RGBA")
    static = ROOT / "sentinel" / "static"
    brand = ROOT / "design" / "brand"
    static.mkdir(parents=True, exist_ok=True)
    # Android: layers are 108 dp; the inner 66 dp is never clipped. 0.58 of
    # the side keeps the mark's square comfortably inside 66/108 (0.611).
    adaptive = _placed(master, 1024, 0.58)
    white = Image.new("RGBA", adaptive.size, (255, 255, 255, 255))
    white.putalpha(adaptive.getchannel("A"))
    outputs = {
        static / "minas-mark.png": _placed(master, 128, 1.0),
        static / "favicon.png": _placed(master, 64, 1.0),
        static / "apple-touch-icon.png": _placed(master, 180, 0.72, BACKGROUND).convert("RGB"),
        brand / "app-icon.png": _placed(master, 1024, 0.72, BACKGROUND).convert("RGB"),
        brand / "android-foreground.png": adaptive,
        brand / "android-monochrome.png": white,
        brand / "splash.png": _placed(master, 1024, 0.9),
    }
    for path, image in outputs.items():
        image.save(path, optimize=True)
        print(f"wrote {path.relative_to(ROOT)} {image.size} {image.mode} {path.stat().st_size} bytes")
    app = ROOT / "mobile" / "assets" / "images"
    app.mkdir(parents=True, exist_ok=True)
    for source, name in MOBILE_COPIES.items():
        (app / name).write_bytes((brand / source).read_bytes())
        print(f"copied design/brand/{source} -> mobile/assets/images/{name}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "clean":
        clean(Path(sys.argv[2]))
    elif len(sys.argv) == 2 and sys.argv[1] == "derive":
        derive()
    else:
        sys.exit(__doc__)
