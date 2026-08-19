"""The Minas identity, in one place (#82).

The brand belongs to the CUSTOMER surface: the portal carries it, and the ops
dashboard is deliberately plain, dense and unbranded (its stripping happens
in its own design pass — decision 4 on #82). The reasoning is recorded on
#82 and matters enough to restate: an ops screenshot must not be usable to
impersonate a customer page, and a mispasted internal URL should look
visibly wrong to whoever receives it. Distinct looks are part of the
anti-phishing story, not aesthetics.

Nothing here is a stylesheet. Surfaces own their stylesheets separately —
tests/test_surface_styles.py refuses a shared one — and draw identity
constants from here so the brand cannot drift into two spellings of gold.
"""
from __future__ import annotations

# The identity hues. Interface palettes (backgrounds, borders, text ramps)
# belong to each surface's stylesheet; these are the two colours that MEAN
# Minas, referenced by the portal's tokens and baked into the mark below.
GOLD = "#f0b429"
VIOLET = "#8b5cf6"
VIOLET_DEEP = "#6d28d9"

# The infinity mark. Renders inside a `.mark` container the host stylesheet
# sizes and frames.
MARK_SVG = (
    '<div class="mark"><svg viewBox="0 0 24 24" fill="none"><defs><linearGradient id="inf" x1="3" y1="20" '
    'x2="21" y2="4" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#f0b429"/><stop offset=".48" '
    'stop-color="#f0b429"/><stop offset=".58" stop-color="#8b5cf6"/><stop offset="1" stop-color="#6d28d9"/>'
    '</linearGradient></defs><path fill="url(#inf)" d="M18.6 6.62c-1.44 0-2.8.56-3.77 1.53L12 10.66 10.48 12h.01'
    'L7.8 14.39c-.64.64-1.49.99-2.4.99-1.87 0-3.39-1.51-3.39-3.38S3.53 8.62 5.4 8.62c.91 0 1.76.35 2.44 1.03l1.13 1 '
    '1.51-1.34L9.22 8.2C8.2 7.18 6.84 6.62 5.4 6.62 2.42 6.62 0 9.04 0 12s2.42 5.38 5.4 5.38c1.44 0 2.8-.56 3.77-1.53'
    'l2.83-2.5.01.01L13.52 12h-.01l2.69-2.39c.64-.64 1.49-.99 2.4-.99 1.87 0 3.39 1.51 3.39 3.38s-1.52 3.38-3.39 3.38'
    'c-.9 0-1.76-.35-2.44-1.03l-1.14-1.01-1.51 1.34 1.27 1.12c1.02 1.01 2.37 1.57 3.82 1.57 2.98 0 5.4-2.41 5.4-5.38'
    's-2.42-5.38-5.4-5.38z"/></svg></div>'
)
