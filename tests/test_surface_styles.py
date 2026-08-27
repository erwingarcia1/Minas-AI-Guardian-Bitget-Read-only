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
    assert brand.MARK_SVG.startswith('<svg class="brand__mark"')
    assert "from sentinel.brand import MARK_SVG" in PORTAL_SRC
    assert brand.GOLD in portal._PORTAL_STYLE


def test_the_surfaces_keep_their_distinguishing_marks():
    """The point of the split is that a screenshot of one cannot pass for
    the other. The portal signature (the brand tag, the gold hairline) must
    not appear in the dashboard's sheet, and the dashboard's KPI grid - its
    most recognisable structure - must not appear in the portal's."""
    assert ".subnav" not in dashboard._STYLE
    assert ".kpis" not in portal._PORTAL_STYLE
    assert ".auth-card" in portal._PORTAL_STYLE
    assert 'aria-current="page"' in portal._PORTAL_STYLE  # the gold underline
    # The "Member portal" brand tag: the qualifier under the wordmark, in the
    # tab title, and on the printed masthead. The dashboard may TALK about
    # the member portal in ops copy; it must never wear the lockup.
    assert '<span class="brand__tag">Member portal</span>' in PORTAL_SRC
    assert ".brand__tag" in portal._PORTAL_STYLE
    assert "— Member portal</title>" in PORTAL_SRC
    assert "member portal — Monitoring status report" in PORTAL_SRC
    assert "brand__tag" not in DASHBOARD_SRC
    assert "brand__tag" not in dashboard._STYLE


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
# (Empty today: `.ts` moved off this list when the table rules gave it
# white-space:nowrap.)
UNSTYLED_ON_PURPOSE: set[str] = set()


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


def test_the_unstyled_allowlist_carries_no_stale_entries():
    """The other containment direction: an allowlist entry for a class that
    IS styled (or is no longer emitted) is a recorded reason outliving its
    fact."""
    emitted = _portal_emitted_classes()
    for token in UNSTYLED_ON_PURPOSE:
        assert token in emitted, f"allowlisted {token!r} is not even emitted any more"
        assert not re.search(rf"\.{re.escape(token)}\b", portal._PORTAL_STYLE), (
            f"allowlisted {token!r} is styled now - delete its entry")


def test_the_dynamic_class_families_are_styled_too():
    """The families built with f-strings or helpers, asserted by value:
    badge tones (status and severity both resolve to these), state tones,
    notice tones, tick kinds."""
    for cls in ("badge--ok", "badge--warn", "badge--danger", "badge--neutral", "badge--lg",
                "state--ok", "state--warn", "state--danger",
                "notice--danger", "notice--info", "notice--ok",
                "tick--yes", "tick--no",
                "btn--primary", "btn--secondary", "btn--tertiary", "btn--sm", "btn--block"):
        assert re.search(rf"\.{re.escape(cls)}(?![\w-])", portal._PORTAL_STYLE), (
            f"dynamic class {cls} has no style")
    for status in ("active", "disabled", "tamper_suspected"):
        assert status in portal._STATUS_BADGE
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        assert sev in portal._SEV_BADGE


# --- mobile and touch (#82: the owner's design pass is the reference) --------
#
# Phone is the PRIMARY case: alert emails link customers here and they open
# them standing up, frightened, on a 375px screen. These are structural
# assertions - no browser exists where this suite runs, so what they cannot
# verify (actual layout at 375px, thumb reach, contrast as rendered) stays a
# human check against design/portal/'s mockups.


def _rule_bodies(selector_fragment: str) -> list[str]:
    """Bodies of every rule whose selector list mentions the fragment."""
    bodies = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", portal._PORTAL_STYLE):
        if selector_fragment in match.group(1):
            bodies.append(match.group(2))
    return bodies


def test_every_interactive_control_declares_its_touch_minimum():
    """The system's floor is 48px (above the 44 requirement); tertiary and
    link-shaped controls hold 44. One of these acknowledges a security
    alert, pressed on a phone."""
    for selector, minimum in ((".btn ", "min-height:48px"), (".input ", "min-height:48px"),
                              (".subnav a ", "min-height:48px"), (".linkbtn ", "min-height:44px"),
                              (".btn--tertiary ", "min-height:44px")):
        bodies = _rule_bodies(selector.strip())
        assert bodies, f"{selector.strip()} has no rule"
        assert any(minimum in b for b in bodies), f"{selector.strip()} lost {minimum}"


def test_acknowledge_never_uses_the_small_button():
    """DESIGN-SYSTEM.md: btn--sm (40px) is for non-critical card-level
    actions, never alert acknowledgement. The ack forms must use full-size
    buttons."""
    for m in re.finditer(r"action=[\"']/acknowledge[\"'][^>]*>(.*?)</form>", PORTAL_SRC, re.S):
        assert "btn--sm" not in m.group(1), "an acknowledge control shrank below the touch floor"


def test_the_sheet_is_mobile_first():
    """Base rules ARE the narrow design; wider viewports enhance through
    min-width queries (40rem and 48rem). A max-width query means narrow
    became the afterthought again."""
    assert "@media (min-width:" in portal._PORTAL_STYLE
    assert "@media (max-width" not in portal._PORTAL_STYLE


def test_no_fixed_width_exceeds_a_phone():
    """max-width caps and min-width floors shrink; a fixed `width:` wider
    than 375px cannot."""
    for m in re.finditer(r"(?<![-\w])width:\s*(\d+)px", portal._PORTAL_STYLE):
        assert int(m.group(1)) <= 375, f"fixed width {m.group(0)} exceeds a phone"


def test_the_one_answer_to_tables_is_stacked_kv_and_tables_are_gone():
    """The owner's decision (design/portal/DESIGN-SYSTEM.md): the data is
    key/value facts, rendered as stacked `.kv` rows that become two-column
    at 40rem - and horizontal-scroll tables must NOT be mixed back in. The
    portal emits no <table> at all; a screen issue reintroducing one is the
    third answer this guard refuses."""
    assert "<table" not in PORTAL_SRC
    assert "tablewrap" not in PORTAL_SRC
    kv = _rule_bodies(".kv__row")
    assert kv, ".kv rows lost their style"
    assert ".kv" in portal._PORTAL_STYLE
    assert "grid-template-columns:12rem 1fr" in portal._PORTAL_STYLE, (
        "the 40rem two-column enhancement is gone")


def test_gold_fill_stays_reserved_for_the_primary_action():
    """One primary per screen, visually unmistakable: gold fill marks it.
    The stylesheet may paint gold onto the primary button, the skip link
    (also a primary-of-its-moment), and text selection - nothing else."""
    allowed = {".btn--primary", ".skip-link", "::selection"}
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", portal._PORTAL_STYLE):
        if "background:var(--gold)" in m.group(2) or "background: var(--gold)" in m.group(2):
            selector = m.group(1).strip().split(",")[0].strip()
            assert selector in allowed, f"gold fill leaked to {selector!r}"


def test_focus_pressed_and_disabled_states_exist():
    """Hover is a mouse concept. Keyboard focus, the pressed state, and a
    visible disabled treatment are what a phone and a screen reader use."""
    assert ":focus-visible" in portal._PORTAL_STYLE
    assert ":active" in portal._PORTAL_STYLE
    assert ":disabled" in portal._PORTAL_STYLE
    assert 'aria-disabled="true"' in portal._PORTAL_STYLE


def test_reduced_motion_is_honoured_and_no_motion_exists():
    assert "prefers-reduced-motion" in portal._PORTAL_STYLE
    assert "animation:" not in portal._PORTAL_STYLE.replace("animation: none", "").replace("animation:none", "")
    assert "@keyframes" not in portal._PORTAL_STYLE


# --- the dashboard half (#82, 2026-08-27): plain, dense, unbranded -----------

def _dashboard_emitted_classes() -> set[str]:
    tokens: set[str] = set()
    style_start = DASHBOARD_SRC.index('_STYLE = """')
    style_end = DASHBOARD_SRC.index('"""', style_start + 12)
    markup = DASHBOARD_SRC[:style_start] + DASHBOARD_SRC[style_end:]
    for attr in re.findall(r"""class=\\?["']([^"'\\]+)""", markup):
        for token in attr.split():
            if "{" not in token and "}" not in token:
                tokens.add(token)
    return tokens


def test_the_dashboard_wears_no_brand():
    """Decision 2 on #82: no mark, no wordmark, no identity hue, no
    gradient. A screenshot of this page must not pass for a customer page."""
    # Comments style nothing - the sheet's own header says "no gradients".
    sheet = re.sub(r"/\*.*?\*/", "", dashboard._STYLE, flags=re.S).lower()
    for hue in (brand.GOLD, brand.VIOLET, brand.VIOLET_DEEP):
        assert hue.lower() not in sheet, f"identity hue {hue} in the dashboard sheet"
    assert "gradient" not in sheet and "glow" not in sheet and "box-shadow" not in sheet
    assert "_MARK_SVG" not in DASHBOARD_SRC and "brand.MARK_SVG" not in DASHBOARD_SRC
    assert "<h1><span class=\"minas\">" not in DASHBOARD_SRC
    assert "Ops dashboard" in DASHBOARD_SRC and "Minas staff only" in DASHBOARD_SRC


def test_the_dashboard_says_what_it_is_on_every_page():
    """The shell names the surface - the tab title and the header - so a
    mispasted internal URL reads as exactly that to whoever opens it."""
    from sentinel.dashboard import _page

    page = _page("", "<p>x</p>")
    assert "<title>Ops dashboard — internal</title>" in page
    assert "Minas staff only" in page
    assert "Member portal" not in page


def test_the_two_grounds_differ():
    """Light against dark: the cheapest distinction a glance can make."""
    assert "--bg:#f4f5f7" in dashboard._STYLE
    assert "--bg:#0d0f13" in portal._PORTAL_STYLE


def test_every_class_the_dashboard_emits_is_styled_by_its_own_sheet():
    """The execution pair, for this sheet too."""
    missing = {
        token for token in _dashboard_emitted_classes()
        if not re.search(rf"\.{re.escape(token)}(?![\w-])", dashboard._STYLE)
    }
    assert not missing, f"dashboard emits classes its stylesheet never styles: {sorted(missing)}"


def test_the_dashboard_design_note_exists_and_records_the_decision():
    note = (ROOT / "design" / "dashboard" / "DESIGN-SYSTEM.md").read_text()
    assert "unbranded" in note and "#82" in note
    assert "#1f4e79" in note and "#1f4e79" in dashboard._STYLE
