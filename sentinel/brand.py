"""The Minas identity, in one place (#82; decision 24).

Both surfaces carry it, on the same dark ground: the member portal, and -
since decision 24 (2026-10-24, which reverses the dashboard half of
decision 2 on #82) - the ops dashboard, which marks itself as Operations.
What used to rest on the dashboard looking plain now rests on its marking
and its path: the lockup's qualifier reads "Operations" where the portal's
reads "Member portal", the signed-in operator's name sits in the header,
every page says "Ops dashboard · internal · Minas staff only", and all of it
lives under /admin. An ops screenshot still cannot pass for a customer page;
it says what it is instead of looking different.

Nothing here is a stylesheet. Surfaces own their stylesheets separately —
tests/test_surface_styles.py refuses a shared one — and draw identity
constants from here so the brand cannot drift into two spellings of gold.
The logo lives here too, as files and the markup that shows them: the
portal serves the files, and the dashboard's pages point at the same paths
on the shared hostname.
"""
from __future__ import annotations

from pathlib import Path

# The identity hues. Interface palettes (backgrounds, borders, text ramps)
# belong to each surface's stylesheet; these are the colours that MEAN
# Minas, referenced by both surfaces' tokens.
GOLD = "#f0b429"
VIOLET = "#8b5cf6"
VIOLET_DEEP = "#6d28d9"
# The deck system's purpleDeep: the dashboard's header band and its sign-in card head (decision 24).
VIOLET_INK = "#2e1a47"
# The dominant violet sampled from the 2026-10-22 mark: the band's chip hover ground.
VIOLET_LOGO = "#583878"

# The Minas logo (supplied by the owner 2026-10-22; replaces the stroked
# infinity mark of the 2026-08-19 design pass). Raster, not inline SVG: the
# logo is a gradient artwork, and every size is derived from one master by
# scripts/brand-assets.py - design/brand/ holds the master and the app
# icons, sentinel/static/ the files this package serves.
STATIC = Path(__file__).resolve().parent / "static"

# Path served by the portal -> file under STATIC. Public and pre-sign-in:
# the sign-in page wears the logo too. Our own origin, so the portal still
# makes no external request.
WEB_ASSETS = {
    "/brand/mark.png": "minas-mark.png",
    "/brand/icon.png": "favicon.png",
    "/brand/apple-touch-icon.png": "apple-touch-icon.png",
    # Browsers ask for this path whatever the page says; it gets the icon.
    "/favicon.ico": "favicon.png",
}

# Read once, at import. A file missing from an installed package fails the
# import - loud on start, and in CI's image build - rather than a broken
# image on every page.
_ASSET_BYTES = {path: (STATIC / name).read_bytes() for path, name in WEB_ASSETS.items()}


def asset(path: str) -> bytes | None:
    """The bytes the portal serves at `path`, or None if it is not a brand
    asset."""
    return _ASSET_BYTES.get(path)


# The lockup's mark. Decorative: the link around it is labelled and the
# wordmark beside it is text.
MARK_HTML = '<img class="brand__mark" src="/brand/mark.png" alt="" width="28" height="28">'

# In every portal page's <head>.
ICON_LINKS = (
    '<link rel="icon" type="image/png" href="/brand/icon.png">'
    '<link rel="apple-touch-icon" href="/brand/apple-touch-icon.png">'
)
