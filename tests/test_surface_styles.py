"""#82: the portal and the ops dashboard must not share a stylesheet.

They did — dashboard.py owned one `_STYLE` and the portal imported it
wholesale, so the two trust-opposite surfaces looked identical and the Minas
brand decorated the internal ops page. The owner's decision (recorded on
#82): the portal carries the identity, the dashboard is plain and unbranded,
and the looks must not quietly converge again — an ops screenshot must not
be usable to impersonate a customer page.

These are the guards for that, in the house pattern: ownership is asserted
in both directions, and the stylesheet gets its execution pair — every class
the portal emits must be styled by the portal's own sheet, because markup
whose classes nothing styles is dead coverage that rots invisibly.
"""
from __future__ import annotations

import re
from pathlib import Path

from sentinel import brand, dashboard, portal

ROOT = Path(__file__).parent.parent
PORTAL_SRC = (ROOT / "sentinel" / "portal.py").read_text()
DASHBOARD_SRC = (ROOT / "sentinel" / "dashboard.py").read_text()


def test_the_two_surfaces_own_separate_stylesheets():
    assert portal._PORTAL_STYLE != dashboard._STYLE
    assert portal._PORTAL_STYLE not in dashboard._STYLE
    assert dashboard._STYLE not in portal._PORTAL_STYLE


def test_the_portal_imports_no_style_pill_or_mark_from_the_dashboard():
    """The convergence that existed: `from sentinel.dashboard import _STYLE,
    _MARK_SVG, _status_pill`. Logic helpers may be shared; anything that
    styles or brands may not."""
    dash_imports = re.search(
        r"from sentinel\.dashboard import \(([^)]*)\)", PORTAL_SRC)
    imported = dash_imports.group(1) if dash_imports else ""
    for forbidden in ("_STYLE", "_MARK_SVG", "_status_pill"):
        assert forbidden not in imported, (
            f"the portal re-imports {forbidden} from the dashboard - the "
            f"shared-stylesheet convergence #82 ended is back")


def test_the_dashboard_does_not_import_the_portals_stylesheet():
    """The other direction of the same trap. Logic imports (member lookups
    for the ops pages) are legitimate; the stylesheet and the pill are not."""
    assert "_PORTAL_STYLE" not in DASHBOARD_SRC
    for line in DASHBOARD_SRC.splitlines():
        if "from sentinel.portal import" in line:
            assert "_STYLE" not in line and "pill" not in line, line


def test_the_brand_lives_in_brand_py_and_the_portal_draws_from_it():
    """One home for the identity, so it cannot drift into two spellings of
    gold - and so the dashboard's own design pass can drop it by deleting an
    import rather than hunting hex codes."""
    assert brand.MARK_SVG.startswith('<div class="mark">')
    assert "from sentinel.brand import MARK_SVG" in PORTAL_SRC
    assert brand.GOLD in portal._PORTAL_STYLE


def test_the_surfaces_keep_their_distinguishing_marks():
    """The point of the split is that a screenshot of one cannot pass for
    the other. The portal signature (the brand tag, the gold hairline) must
    not appear in the dashboard's sheet, and the dashboard's KPI grid - its
    most recognisable structure - must not appear in the portal's."""
    assert "portal-tag" not in dashboard._STYLE
    assert ".kpis" not in portal._PORTAL_STYLE
    assert ".portal-tag" in portal._PORTAL_STYLE


def _portal_emitted_classes() -> set[str]:
    """Class tokens the portal's markup emits, from string literals. Tokens
    carrying f-string braces are dynamic; their known value families are
    asserted explicitly in the test below instead."""
    tokens: set[str] = set()
    for attr in re.findall(r"""class=["']([^"']+)["']""", PORTAL_SRC):
        for token in attr.split():
            if "{" not in token and "}" not in token:
                tokens.add(token)
    return tokens


# Deliberately unstyled, with the reason on record - not silently tolerated.
UNSTYLED_ON_PURPOSE = {
    # `.ts` is a data hook for the local-time enhancement (data-utc), not a
    # visual role; styling it would invite treating it as decoration.
    "ts",
}


def test_every_class_the_portal_emits_is_styled_by_its_own_sheet():
    """The execution pair for a stylesheet: markup emitting classes nothing
    styles is dead coverage - it looks styled in the source and rots
    invisibly in the browser. Every literal class the portal emits must
    have a selector in _PORTAL_STYLE (or a recorded reason not to)."""
    missing = {
        token for token in _portal_emitted_classes() - UNSTYLED_ON_PURPOSE
        if not re.search(rf"\.{re.escape(token)}\b", portal._PORTAL_STYLE)
    }
    assert not missing, f"portal emits classes its stylesheet never styles: {sorted(missing)}"


def test_the_dynamic_class_families_are_styled_too():
    """The families built with f-strings, asserted by value: severity pills,
    status pills, and check-icon tones."""
    for cls in ("sev-CRITICAL", "sev-HIGH", "sev-MEDIUM", "sev-LOW",
                "s-active", "s-disabled", "s-tamper"):
        assert re.search(rf"\.{re.escape(cls)}(?![\w-])", portal._PORTAL_STYLE), (
            f"dynamic class {cls} has no style")
    for tone in ("ok", "warn", "crit"):
        assert f".ic.{tone}" in portal._PORTAL_STYLE, f"icon tone {tone} has no style"
