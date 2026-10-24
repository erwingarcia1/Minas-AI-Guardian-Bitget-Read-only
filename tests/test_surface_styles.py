"""#82 and decision 24: the portal and the ops dashboard must not share a
stylesheet, and both carry the Minas identity.

They did share one — dashboard.py owned one `_STYLE` and the portal imported
it wholesale, so the two trust-opposite surfaces looked identical. #82 split
them and, for a time, kept the dashboard plain (decision 2 on the issue);
decision 24 (2026-10-24) brought the identity back to the dashboard, on the
portal's dark ground. The looks still must not quietly converge: the sheets
stay separate, both draw the brand from sentinel/brand.py, and what tells an
operator screen from a customer page — the operations marking — is pinned
here where the absence of the brand used to be, so an ops screenshot still
cannot pass for a customer page.

These are the guards for that, in the house pattern: ownership is asserted
in both directions, and each stylesheet gets its execution pair — every class
a surface emits must be styled by its own sheet, because markup whose classes
nothing styles is dead coverage that rots invisibly.
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
    for the ops pages) are legitimate; the stylesheet and the pill are not.
    Both surfaces draw the brand from sentinel/brand.py, never from each
    other."""
    assert "_PORTAL_STYLE" not in DASHBOARD_SRC
    for line in DASHBOARD_SRC.splitlines():
        if "from sentinel.portal import" in line:
            assert "_STYLE" not in line and "pill" not in line, line


def test_the_brand_lives_in_brand_py_and_the_portal_draws_from_it():
    """One home for the identity, so it cannot drift into two spellings of
    gold: the portal spells the hex in its own :root and this pins it equal
    to brand.py's. The dashboard does the same, below (decision 24)."""
    assert brand.MARK_HTML.startswith('<img class="brand__mark"')
    assert "from sentinel.brand import ICON_LINKS, MARK_HTML, WEB_ASSETS" in PORTAL_SRC
    assert brand.GOLD in portal._PORTAL_STYLE


def test_the_surfaces_keep_their_distinguishing_marks():
    """The point of the split is that a screenshot of one cannot pass for
    the other. The portal's shapes (the subnav with its gold hairline, the
    auth card) and its brand tag must not appear in the dashboard's sheet
    or source; the dashboard's KPI grid - its most recognisable structure -
    must not appear in the portal's; and each lockup's qualifier is its
    own."""
    assert ".subnav" not in dashboard._STYLE
    assert ".auth-card" not in dashboard._STYLE
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
    # ... and the dashboard's own qualifier, which the portal never wears.
    assert '<span class="ops__tag">Operations</span>' in DASHBOARD_SRC
    assert "ops__tag" not in PORTAL_SRC and "ops__tag" not in portal._PORTAL_STYLE


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


# --- the dashboard half (decision 24, 2026-10-24): the same identity, marked as Operations ----
#
# Decision 2 on #82 kept the dashboard plain so that an ops screenshot could
# not pass for a customer page. Decision 24 reverses that half: the dashboard
# wears the Minas colours and the mark on the portal's dark ground. What the
# old rule protected is carried by an explicit operations marking on every
# page - the "Operations" lockup, the operator's name, the h1 with "internal
# · Minas staff only", the /admin path - and these guards pin the marking
# where the old ones pinned the absence of the brand.

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


def _dashboard_sheet() -> str:
    """The dashboard's sheet with its comments stripped (comments style
    nothing - the sheet's own header names what it forbids), lower-cased."""
    return re.sub(r"/\*.*?\*/", "", dashboard._STYLE, flags=re.S).lower()


def _dashboard_token(name: str) -> str:
    """A colour from the dashboard's :root, as #rrggbb."""
    root = dashboard._STYLE[dashboard._STYLE.index(":root"):]
    return re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", root[:root.index("}")]).group(1)


def test_the_dashboard_wears_the_brand_from_brand_py():
    """Decision 24: the identity hues are in the dashboard's sheet, spelled
    as brand.py spells them (one home - the portal's own rule), and the mark
    and the icons come from brand.py's markup: no logo path is typed here.
    What the brand itself forbids stays out - no gradient, no glow, no
    motion, no external request; box-shadow only as an inset focus ring."""
    sheet = _dashboard_sheet()
    for hue in (brand.GOLD, brand.VIOLET, brand.VIOLET_DEEP, brand.VIOLET_INK, brand.VIOLET_LOGO):
        assert hue.lower() in sheet, f"identity hue {hue} is missing from the dashboard sheet"
    assert _dashboard_token("gold") == brand.GOLD and _dashboard_token("violet") == brand.VIOLET and _dashboard_token("brand-2") == brand.VIOLET_LOGO
    for forbidden in ("gradient", "glow", "@keyframes", "@import"):
        assert forbidden not in sheet, forbidden
    assert not re.search(r"url\(\s*[\"']?\s*(?:https?:)?//", sheet), "external request in the dashboard sheet"
    for m in re.finditer(r"box-shadow:\s*([^;]*)", sheet):
        assert m.group(1).strip().startswith("inset"), f"box-shadow beyond a focus ring: {m.group(0)}"
    assert "MARK_SVG" not in DASHBOARD_SRC
    assert "from sentinel.brand import" in DASHBOARD_SRC
    assert "MARK_HTML" in DASHBOARD_SRC and "ICON_LINKS" in DASHBOARD_SRC
    assert "/brand/" not in DASHBOARD_SRC, "the logo paths come from brand.py, never typed here"
    assert '<h1><span class="minas">' not in DASHBOARD_SRC
    assert "Ops dashboard" in DASHBOARD_SRC and "Minas staff only" in DASHBOARD_SRC


def test_the_dashboard_says_what_it_is_on_every_page():
    """The shell names the surface - the tab title, the lockup's qualifier,
    the header - and names the operator when one is signed in, so a
    mispasted internal URL or a screenshot reads as exactly what it is."""
    from sentinel.dashboard import _page

    page = _page("", "<p>x</p>")
    assert "<title>Ops dashboard — internal</title>" in page
    assert "Minas staff only" in page
    assert "Member portal" not in page
    assert "/limits" not in page
    assert "Operations" in page and "Signed in as" not in page
    signed_in = _page("", "<p>x</p>", operator="erwin")
    for part in ("Signed in as", "erwin", "Sign out", "Operations"):
        assert part in signed_in, part
    assert "Member portal" not in signed_in and "/limits" not in signed_in


def test_the_shell_refreshes_at_the_interval_it_is_given():
    """30 seconds by default (the fleet and triage pages), 15 for the
    metrics page, none when a page asks for none (the forms)."""
    from sentinel.dashboard import _page

    assert '<meta http-equiv="refresh" content="30">' in _page("", "")
    assert '<meta http-equiv="refresh" content="15">' in _page("", "", refresh_seconds=15)
    assert "http-equiv" not in _page("", "", False)


def test_the_surfaces_share_the_ground_and_are_told_apart_by_the_marking():
    """One identity on one dark ground, on purpose (decision 24). What tells
    the two apart is said in words on every dashboard page: the lockup's
    qualifier is Operations where the portal's is Member portal, the
    operator's name is in the band, the h1 and "internal · Minas staff only"
    stay, and the title says internal. The portal never emits the ops
    qualifier; the dashboard's shell never wears the portal's."""
    from sentinel.dashboard import _page

    assert "--bg:#0d0f13" in dashboard._STYLE
    assert "--bg:#0d0f13" in portal._PORTAL_STYLE
    shell = _page("", "<p>x</p>", operator="erwin")
    assert '<span class="ops__tag">Operations</span>' in shell
    assert "Signed in as <b>erwin</b>" in shell and "Sign out" in shell
    assert "<h1>Ops dashboard</h1>" in shell and "internal · Minas staff only" in shell
    assert "<title>Ops dashboard — internal</title>" in shell
    assert "ops__tag" not in PORTAL_SRC and "ops__tag" not in portal._PORTAL_STYLE
    assert "brand__tag" not in shell and "Member portal" not in shell


def test_every_class_the_dashboard_emits_is_styled_by_its_own_sheet():
    """The execution pair, for this sheet too. The mark's class arrives
    inside brand.MARK_HTML, which the literal scan cannot see, so it is
    asserted by name: an unstyled mark renders at the image's own size."""
    missing = {
        token for token in _dashboard_emitted_classes()
        if not re.search(rf"\.{re.escape(token)}(?![\w-])", dashboard._STYLE)
    }
    assert not missing, f"dashboard emits classes its stylesheet never styles: {sorted(missing)}"
    assert ".brand__mark" in dashboard._STYLE


def test_every_class_the_metrics_body_emits_is_styled_by_the_dashboard_sheet():
    """The metrics page renders through the dashboard's shell (decision 24),
    so its markup lives or dies by the dashboard's sheet - checked on the
    rendered body, since it is built from the live registry."""
    from sentinel.metrics import render_metrics_body

    body = render_metrics_body()
    tokens = {t for attr in re.findall(r"""class=["']([^"']+)["']""", body) for t in attr.split()}
    assert tokens, "the metrics body emitted no class at all - the scan proves nothing"
    missing = {t for t in tokens if not re.search(rf"\.{re.escape(t)}(?![\w-])", dashboard._STYLE)}
    assert not missing, f"the metrics body emits classes the dashboard sheet never styles: {sorted(missing)}"


def test_the_dashboard_design_note_exists_and_records_the_decision():
    """design/dashboard/DESIGN-SYSTEM.md is the dashboard's contract: it
    names the decision in force and the issue it reverses, and agrees with
    the sheet on the accent and the brand ground - parsed from the sheet, so
    the note and the code cannot drift apart silently."""
    note = (ROOT / "design" / "dashboard" / "DESIGN-SYSTEM.md").read_text()
    assert "decision 24" in note and "#82" in note
    accent, ground = _dashboard_token("accent"), _dashboard_token("brand")
    assert accent == brand.VIOLET_DEEP and ground == brand.VIOLET_INK
    assert accent in note and ground in note


def test_gold_fill_is_reserved_for_the_one_primary_on_the_dashboard():
    """The portal's rule, applied here: gold as a FILL marks the one primary
    per screen ("+ Add customer") and text selection, nothing else. Gold as
    text on the dark ground - the Operations tag, the critical KPI numeral,
    the sign-in card's count - is allowed; it clears 10:1 there."""
    allowed = {"a.live.primary", "::selection"}
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", _dashboard_sheet()):
        if "background:var(--gold)" in m.group(2) or "background: var(--gold)" in m.group(2):
            selector = m.group(1).strip().split(",")[0].strip()
            assert selector in allowed, f"gold fill leaked to {selector!r}"


def _token(name: str) -> str:
    """A colour from the portal's :root, as #rrggbb."""
    root = portal._PORTAL_STYLE[portal._PORTAL_STYLE.index(":root"):]
    return re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", root[:root.index("}")]).group(1)


def _contrast(fg: str, bg: str) -> float:
    """WCAG 2 contrast ratio of two #rrggbb colours."""
    def luminance(hex_colour: str) -> float:
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    light, dark = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_a_cards_fact_labels_clear_aa_on_the_card():
    """`.kv dt` is 12px text on a card's surface, where WCAG AA wants 4.5:1.
    The subtle tier is 4.4:1 there, so labels are muted - as in the app."""
    rule = re.search(r"\.kv dt \{([^}]*)\}", portal._PORTAL_STYLE).group(1)
    colour = re.search(r"color:var\(--([a-z-]+)\)", rule).group(1)
    assert _contrast(_token(colour), _token("surface")) >= 4.5, colour


# Every token the dashboard's sheet uses as TEXT on its cards and its page.
DASHBOARD_TEXT_TOKENS = ("txt", "muted", "faint", "violet-text", "gold", "ok", "crit", "bad", "high", "med", "low")


def test_every_dashboard_text_token_clears_aa_on_its_surfaces():
    """13px cells and 11px labels on the dark ground: every colour the
    dashboard's sheet uses as text must clear WCAG AA (4.5:1) on the cards
    and on the page, by the same formula as the portal's check above. The
    band and the fills have their own pairs: text and the gold tag on the
    brand ground, ink on gold, text on the accent, the card-head colour on
    the card-head ground."""
    for name in DASHBOARD_TEXT_TOKENS:
        for ground in ("panel", "panel-2", "bg"):
            ratio = _contrast(_dashboard_token(name), _dashboard_token(ground))
            assert ratio >= 4.5, f"--{name} on --{ground} is {ratio:.2f}:1"
    for fg, bg in (("txt", "brand"), ("gold", "brand"), ("gold-ink", "gold"), ("txt", "accent"),
                   ("violet-text", "panel-2")):
        ratio = _contrast(_dashboard_token(fg), _dashboard_token(bg))
        assert ratio >= 4.5, f"--{fg} on --{bg} is {ratio:.2f}:1"
