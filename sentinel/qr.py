"""The two-factor secret as a QR code, for the authenticator app to scan.

Enrolment shows an `otpauth://` URI (`provisioning_uri` in sentinel/totp.py)
in three places: the ops dashboard's first-sign-in page, the member
portal's "Set up two-factor" page and the terminal walk-through of
`sentinel operator add`. Authenticator apps scan that URI as a QR code.
Typing its 32-character secret by hand was the only route, and it is the
error-prone one: the owner asked for the picture on 2026-10-24 after
failing to enrol from a phone.

**Not a new disclosure.** The QR code is the URI that is already on the
page, drawn as modules instead of letters: the same secret, on the same
page, shown to the same person at the same moment, and nowhere else. The
secret is written nowhere new - not logged, not stored, not sent to a
service - and the markup carries it only as the symbol's modules, never as
text (no `<title>`, no `<desc>`). What was true of the text stays true of
the picture: whoever can read the page can enrol, and nobody else.

**Inline SVG, on purpose.** The portal and the dashboard make no external
request and run no JavaScript (docs/portal-design.md decision 1,
design/dashboard/DESIGN-SYSTEM.md); a QR fetched from a service or carried
in a `data:` image would break one of those. `svg()` returns markup that is
pasted straight into the page: no XML declaration, no namespace (the HTML
parser supplies it), no external reference, no script, no `data:` URI, no
class. Neither server sends a Content-Security-Policy beyond
`frame-ancestors 'none'` (sentinel/edge.py, pinned in
tests/test_csrf_origin.py), and that directive does not restrict inline
markup, so the picture renders under the policy the servers already send.
The symbol sits on its own solid light background, so it scans against the
portal's dark ground as well as the dashboard's light one.

The terminal rendering is segno's compact block form: half-block
characters, two module rows per line. segno draws the light modules with
the terminal's foreground colour and the dark ones as its background, so
on a light-themed terminal the symbol would print inverted; the CLI forces
the polarity - every line bright white on black, reset at its end -
instead of hoping the scanner copes, and the escapes live there, not here:
`terminal()` returns plain text. The block characters are not ASCII, so
the caller checks that its stdout can carry them and prints the text alone
when it cannot (sentinel/cli.py).

segno: pure Python, no dependencies of its own, no network, no files.
"""
from __future__ import annotations

import html
import io

import segno

# Error-correction level M (about 15% of the symbol recoverable): enough to
# survive a smudged screen, small enough that a URI of 120-180 bytes stays at
# a version whose modules are still large at the size below.
ERROR_LEVEL = "m"

# Light modules of margin around the symbol: the specification's quiet zone,
# which a scanner needs to find the edges.
QUIET_ZONE = 4

# The rendered size on a page, in CSS pixels, whatever version the URI needs:
# fixed, so the picture fits a 375px phone beside the panel's padding and the
# layout does not move with the length of a member's email address.
SIZE_PX = 224

# The terminal's margin, in modules: two is enough where every character
# costs a column.
TERMINAL_QUIET_ZONE = 2


def _symbol(uri: str):
    # Never a Micro QR, whatever the length: authenticator apps do not read them.
    return segno.make(uri, error=ERROR_LEVEL, micro=False)


def svg(uri: str, *, label: str) -> str:
    """`uri` as an inline `<svg>` element: `role="img"` with `label` as its
    accessible name, SIZE_PX square, a solid light background under dark
    modules, and nothing else.

    The markup is exactly what segno draws for the URI, with those four
    attributes added to the root element (tests/test_qr.py compares against
    segno directly). Deterministic: the same URI draws the same picture, so
    the page re-rendered after a wrong code shows the same code.

    A URI no QR code can hold returns "" and the page shows the text alone,
    as it did before the picture. Measured 2026-10-24: the bound is a URI
    over 2,331 bytes at error level M (version 40), which is an account name
    of about 2,189 plain characters - no identity in this product approaches
    it."""
    try:
        symbol = _symbol(uri)
    except segno.DataOverflowError:
        return ""
    markup = symbol.svg_inline(
        scale=1, border=QUIET_ZONE, dark="#000", light="#fff",
        omitsize=True, svgclass=None, lineclass=None,
    )
    attributes = (f'role="img" aria-label="{html.escape(label, quote=True)}" '
                  f'width="{SIZE_PX}" height="{SIZE_PX}"')
    return markup.replace("<svg ", f"<svg {attributes} ", 1)


def terminal(uri: str) -> str:
    """`uri` as segno's compact block rendering for a terminal, one line per
    two module rows. Not ASCII (U+2580, U+2584, U+2588): the caller decides
    whether its stdout can carry it."""
    out = io.StringIO()
    _symbol(uri).terminal(out=out, compact=True, border=TERMINAL_QUIET_ZONE)
    return out.getvalue().rstrip("\n")
