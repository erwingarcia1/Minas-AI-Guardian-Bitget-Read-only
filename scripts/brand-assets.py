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

    python3 scripts/brand-assets.py store                  # the Google Play graphics

writes the two images Play Console asks for (sizes from Play's own help,
2026-10-26), uploaded by the owner and committed as the record:

    mobile/store/play-icon.png           512 px, 32-bit PNG - the listing's
                                          icon, the launcher icon's
                                          composition; Play rounds the corners
    mobile/store/play-feature-graphic.png  1024 x 500, 24-bit PNG - the mark,
                                          "Minas AI Guardian" and the landing
                                          page's eyebrow line, on the portal's
                                          ground

`store` is separate from `derive` because the feature graphic sets type,
and type needs a font file: Inter where it is installed, DejaVu Sans
otherwise (the portal itself uses the reader's system font). The font it
used is printed; rerunning it elsewhere may change the text's shapes.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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


# The portal's tokens (mobile/src/theme/tokens.json) the feature graphic uses.
TEXT = (244, 246, 250)
TEXT_MUTED = (170, 178, 193)
GOLD = (240, 180, 41)
VIOLET_LOGO = (88, 56, 120)  # sentinel/brand.py: the mark's dominant violet
# The landing page's eyebrow (sentinel/portal.py `_render_landing`).
EYEBROW = "Read-only monitoring for crypto exchange accounts"
FONTS = {
    "bold": ("/usr/share/fonts/opentype/inter/Inter-Bold.otf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    "medium": ("/usr/share/fonts/opentype/inter/Inter-Medium.otf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
}


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONTS[weight]:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    sys.exit(f"no {weight} font found; looked for {', '.join(FONTS[weight])}")


def _feature_graphic(master: Image.Image) -> Image.Image:
    """1024 x 500: the mark beside the name ("Guardian" in gold, as the
    app's welcome screen sets it) over the eyebrow line, centred, with a
    soft violet glow behind the mark. Everything sits well inside the
    edges, which Play crops on some screens."""
    width, height = 1024, 500
    out = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
    title, eyebrow = _font("bold", 66), _font("medium", 25)
    measure = ImageDraw.Draw(out)
    name_w = measure.textlength("Minas AI ", font=title) + measure.textlength("Guardian", font=title)
    eyebrow_w = measure.textlength(EYEBROW, font=eyebrow)
    mark_side, gap = 200, 44
    text_w = max(name_w, eyebrow_w)
    left = round((width - (mark_side + gap + text_w)) / 2)
    mark_top = (height - mark_side) // 2
    # The glow: a radial falloff of the mark's violet, centred on the mark.
    # Pillow's gradient reads 179 at the middle of each edge (0 at the
    # centre, distance x 1.4), so the fade ends there and the square's edge
    # never shows.
    glow_d = 600
    falloff = Image.radial_gradient("L").resize((glow_d, glow_d), Image.LANCZOS)
    alpha = falloff.point(lambda v: round(max(0.0, 1 - v / 179) ** 2 * 0.55 * 255))
    glow = Image.new("RGBA", (glow_d, glow_d), (*VIOLET_LOGO, 255))
    glow.putalpha(alpha)
    out.alpha_composite(glow, (left + mark_side // 2 - glow_d // 2, height // 2 - glow_d // 2))
    mark = _resize(_square(master, 0.0), mark_side)
    out.alpha_composite(mark, (left, mark_top))
    draw = ImageDraw.Draw(out)
    text_x = left + mark_side + gap
    ascent, descent = title.getmetrics()
    block = ascent + descent + 18 + sum(eyebrow.getmetrics())
    top = (height - block) // 2
    draw.text((text_x, top), "Minas AI ", font=title, fill=TEXT)
    draw.text((text_x + measure.textlength("Minas AI ", font=title), top), "Guardian", font=title, fill=GOLD)
    draw.text((text_x, top + ascent + descent + 18), EYEBROW, font=eyebrow, fill=TEXT_MUTED)
    return out.convert("RGB")


def store() -> None:
    master = Image.open(MASTER).convert("RGBA")
    folder = ROOT / "mobile" / "store"
    folder.mkdir(parents=True, exist_ok=True)
    outputs = {
        folder / "play-icon.png": _placed(master, 512, 0.72, BACKGROUND),
        folder / "play-feature-graphic.png": _feature_graphic(master),
    }
    for path, image in outputs.items():
        image.save(path, optimize=True)
        print(f"wrote {path.relative_to(ROOT)} {image.size} {image.mode} {path.stat().st_size} bytes")
    print(f"fonts: {_font('bold', 10).path}, {_font('medium', 10).path}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "clean":
        clean(Path(sys.argv[2]))
    elif len(sys.argv) == 2 and sys.argv[1] == "derive":
        derive()
    elif len(sys.argv) == 2 and sys.argv[1] == "store":
        store()
    else:
        sys.exit(__doc__)
