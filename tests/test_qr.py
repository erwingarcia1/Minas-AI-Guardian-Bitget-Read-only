"""The enrolment QR code (sentinel/qr.py): the otpauth URI the page already
shows, drawn as modules - inline, self-contained, and exactly segno's."""
from __future__ import annotations

import re

import segno

from sentinel import qr
from sentinel.totp import provisioning_uri

SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
URI = provisioning_uri(SECRET, "erwin")
OTHER = provisioning_uri("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "erwin")
LABEL = "QR code of the secret below, for an authenticator app to scan"


def _segno_svg(uri: str) -> str:
    """What segno itself draws for the URI, with the module's own settings."""
    return segno.make(uri, error="m", micro=False).svg_inline(
        scale=1, border=4, dark="#000", light="#fff", omitsize=True, svgclass=None, lineclass=None)


def test_the_svg_is_an_inline_image_with_an_accessible_name():
    markup = qr.svg(URI, label=LABEL)
    assert markup.startswith("<svg ") and markup.endswith("</svg>")
    assert 'role="img"' in markup and f'aria-label="{LABEL}"' in markup
    assert 'width="224" height="224"' in markup


def test_the_svg_references_nothing_outside_itself():
    """No request, no script, no data: URI - the page's no-external-request,
    no-JavaScript posture holds with the picture on it."""
    lowered = qr.svg(URI, label=LABEL).lower()
    for forbidden in ("http", "<script", "data:", "xmlns", "xlink", "href", "<title", "<desc", "class="):
        assert forbidden not in lowered, forbidden


def test_the_svg_carries_the_secret_only_as_modules_never_as_text():
    markup = qr.svg(URI, label=LABEL)
    assert SECRET not in markup and "otpauth" not in markup


def test_the_svg_is_deterministic_for_a_uri_and_differs_for_another():
    assert qr.svg(URI, label=LABEL) == qr.svg(URI, label=LABEL)
    assert qr.svg(URI, label=LABEL) != qr.svg(OTHER, label=LABEL)


def test_the_svg_is_the_one_segno_draws_for_the_uri():
    """Compared against segno directly: the module adds four attributes to
    the root element and changes nothing else. A pin on the parameters -
    error level M, the quiet zone, the colours - each of which a different
    value would show as different markup."""
    markup = qr.svg(URI, label=LABEL)
    added = f' role="img" aria-label="{LABEL}" width="{qr.SIZE_PX}" height="{qr.SIZE_PX}"'
    assert added in markup
    assert markup.replace(added, "", 1) == _segno_svg(URI)
    assert segno.make(URI, error=qr.ERROR_LEVEL, micro=False).error == "M"


def test_the_svg_has_a_solid_light_background_and_a_quiet_zone():
    """A dark page (the portal) must not become the symbol's light modules:
    the first path fills the whole viewBox light, under the dark modules,
    and the viewBox is the symbol plus four modules of margin each side."""
    markup = qr.svg(URI, label=LABEL)
    modules = segno.make(URI, error="m", micro=False).symbol_size(scale=1, border=0)[0]
    span = modules + 2 * qr.QUIET_ZONE
    assert 2 <= qr.QUIET_ZONE <= 4
    assert f'viewBox="0 0 {span} {span}"' in markup
    paths = re.findall(r"<path [^>]*>", markup)
    assert len(paths) == 2
    assert paths[0] == f'<path fill="#fff" d="M0 0h{span}v{span}h-{span}z"/>'
    assert paths[1].startswith('<path stroke="#000" d="')


def test_a_uri_no_qr_code_can_hold_draws_nothing():
    """Over 2,331 bytes (error level M, version 40) there is no symbol to
    draw: svg() returns "" instead of raising, and the page shows the text
    alone. No identity in this product comes near the bound; a
    3,000-character account name is well past it."""
    assert qr.svg(provisioning_uri(SECRET, "a" * 3000), label=LABEL) == ""


def test_the_label_is_escaped_into_the_attribute():
    markup = qr.svg(URI, label='say "hi" <now> & go')
    assert 'aria-label="say &quot;hi&quot; &lt;now&gt; &amp; go"' in markup


def test_the_terminal_rendering_is_a_multi_line_block():
    """Block characters and spaces only - no escape codes - and the same
    symbol every time for the same URI."""
    block = qr.terminal(URI)
    lines = block.split("\n")
    assert len(lines) > 10 and all(lines)
    assert set(block) <= {" ", "▀", "▄", "█", "\n"}
    assert qr.terminal(URI) == block and qr.terminal(OTHER) != block
