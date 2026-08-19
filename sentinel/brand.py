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

# The infinity mark, from the owner's 2026-08-19 design pass
# (design/portal/): stroked, no fill, coloured by the host through
# `currentColor` (the portal gives `.brand__mark` the brand gold). Replaces
# the earlier gradient-filled mark.
MARK_SVG = (
    '<svg class="brand__mark" viewBox="0 0 48 24" fill="none" aria-hidden="true">'
    '<path d="M8 12 C8 5 15 5 24 12 C33 19 40 19 40 12 C40 5 33 5 24 12 C15 19 8 19 8 12 Z" '
    'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)
