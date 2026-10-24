"""The portal's front door is a landing page (decision 25, 2026-10-24).

The owner's words: "when a user visits guardian.minastrade.com, it is the
login page, instead of like a landing page that should contain link to
login or something. the landing page ought to be what the system is about.
[...] when a user login session expires, it can take them to like that
page."

So `/` without a session is the landing page: what the Guardian is, how it
works, what it cannot do, the one address, and the way to sign in. The
sign-in form lives at /login. A member path without a session is sent to
the landing page (303), not answered with the form in place. A session
cookie that was presented and rejected - expired, retired by a password
change, forged, its member gone - lands there too, and the page says "You
were signed out" and clears the cookie. What stays decided and is held
here: no self-serve signup (decision 17), no prevention claim (#48), the
numbers from the configuration, the address from the setting (#88), one
gold primary per screen, every emitted class styled by the portal's own
sheet.
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sentinel import portal
from sentinel.portal import (
    LANDING_ENDED_TITLE,
    LANDING_LEAD,
    LANDING_SETUP,
    LANDING_STATEMENT,
    LANDING_STEPS,
    LIMITS_CANNOT,
    LIMITS_HEADLINE,
    SESSION_IDLE_SECONDS,
    SESSION_TTL_SECONDS,
    _session_secret,
    make_session_token,
    polling_cadence,
    serve_portal,
    set_member_password,
)
from sentinel.snooze import active_snooze
from tests.helpers.landing_redirect import assert_sent_to_the_landing_page, exchange
from tests.test_no_prevention_claims import claims_in

ROOT = Path(__file__).resolve().parent.parent
ALICE = "alice@example.com"
PASSWORD = "pw-alice-long-enough"
# Not the real address, on purpose: the page must learn it from the setting.
HOST = "guardian.example.test"
OLD = "old-address.example.net"
SHOWN = f'<strong style="color:var(--text)">{HOST}</strong>'


@pytest.fixture
def portal_at(db):
    srv = serve_portal(db, 0, host="127.0.0.1")
    yield "127.0.0.1", srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _account(db, email=ALICE, label="alice-1"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', '2026-01-01', '2026-01-01')",
            (label, email),
        )
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        return account_id


def _alert(db, account_id):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, 'withdrawal_initiated', 'CRITICAL', 'k', "
            "'0.5 BTC left the account', '{}', 'high', ?)",
            (account_id, datetime.now(timezone.utc).isoformat()),
        )
        return cur.lastrowid


def _acknowledged_at(db, alert_id):
    with db.cursor() as cur:
        cur.execute("SELECT acknowledged_at FROM alerts WHERE id = ?", (alert_id,))
        return cur.fetchone()["acknowledged_at"]


def _sign_in(host_port, email=ALICE, password=PASSWORD):
    status, message, _ = exchange(host_port, "POST", "/login", body={"email": email, "password": password})
    assert status == 303, "the sign-in redirect"
    set_cookie = message.get("Set-Cookie") or ""
    assert "minas_portal=" in set_cookie
    return set_cookie.split("minas_portal=", 1)[1].split(";", 1)[0]


def _stale_token(db):
    """A real token for a real member, expired: presented, and rejected."""
    return make_session_token(_session_secret(db), ALICE, now=time.time() - 10 * SESSION_TTL_SECONDS)


def _markup(page):
    """The page after its stylesheet - the sheet names every class, so a
    count over the whole page proves nothing."""
    return page[page.index("</style>"):]


# --- the landing page ---------------------------------------------------------

def test_the_root_without_a_cookie_is_the_landing_page(portal_at, db):
    status, message, body = exchange(portal_at, "GET", "/")
    assert status == 200
    assert 'id="landing-title"' in body and 'id="signin-title"' not in body
    assert f'<h1 id="landing-title">{LANDING_STATEMENT}</h1>' in body
    assert LANDING_LEAD in body and LANDING_SETUP in body
    for title, text in LANDING_STEPS:
        assert title in body and text in body, title
    assert '<a class="btn btn--primary" href="/login">Sign in</a>' in body
    assert '<div class="topbar__meta"><a href="/login">Sign in</a></div>' in body
    assert LIMITS_HEADLINE[0] in body and LIMITS_HEADLINE[1] in body
    assert 'id="limits"' in body
    assert "<title>Minas AI Guardian — Member portal</title>" in body
    assert message.get("Set-Cookie") is None, "no cookie was presented, so none is cleared"
    assert LANDING_ENDED_TITLE not in body
    # No sign-up (decision 17), and nothing only a signed-in page shows.
    assert "sign up" not in body.lower().replace("nothing to sign up for", "")
    for only_signed_in in ('class="subnav"', "Signed in as", "Sign out", "My accounts", "alice"):
        assert only_signed_in not in body, only_signed_in


def test_the_polling_numbers_come_from_the_running_configuration(portal_at, db):
    """Step 2 carries the cadence the poller keeps - read from the
    configuration at render time, never typed into the copy."""
    fast, slow = polling_cadence()
    _, _, body = exchange(portal_at, "GET", "/")
    assert f"Balances and movements about every {fast} seconds; permissions and allowlists about every {slow // 60} minutes." in body

    class _Rules:
        def get_int(self, section, key):
            return {"fast_loop_seconds": 30, "slow_loop_seconds": 1200}[key]

    srv = serve_portal(db, 0, host="127.0.0.1", rules=_Rules())
    try:
        _, _, body = exchange(("127.0.0.1", srv.server_address[1]), "GET", "/")
    finally:
        srv.shutdown()
        srv.server_close()
    assert "about every 30 seconds; permissions and allowlists about every 20 minutes." in body
    assert f"about every {fast} seconds" not in body


def test_a_rejected_cookie_lands_signed_out_and_is_cleared(portal_at, db):
    """Expired, forged, for a member who is gone, or retired by a password
    change: every rejected cookie is "You were signed out", said by the
    landing page, which clears the cookie as it says so. The sentence's
    numbers are the session constants, not typed in."""
    set_member_password(db, ALICE, PASSWORD)
    secret = _session_secret(db)
    retired = _sign_in(portal_at)
    expired = make_session_token(secret, ALICE, now=time.time() - 10 * SESSION_TTL_SECONDS)
    forged = retired[:-4] + ("aaaa" if retired[-4:] != "aaaa" else "bbbb")
    ghost = make_session_token(secret, "ghost@example.com", epoch=1)
    set_member_password(db, ALICE, "a-new-password-1", invalidate_sessions=True)  # retires `retired`
    sentence = (f"A session ends after {SESSION_IDLE_SECONDS // 3600} hours without activity, after "
                f"{SESSION_TTL_SECONDS // 3600} hours in all, and when the password is changed. "
                "Sign in again to see your accounts.")
    for name, token in (("expired", expired), ("forged", forged), ("ghost", ghost), ("retired", retired)):
        status, message, body = exchange(portal_at, "GET", "/", cookie=token)
        assert status == 200 and 'id="landing-title"' in body, name
        assert '<div class="notice notice--info" role="status">' in body, name
        assert f'<p class="notice__title">{LANDING_ENDED_TITLE}</p>' in body, name
        assert sentence in body, name
        assert body.index(LANDING_ENDED_TITLE) < body.index('id="landing-title"'), "the notice comes first"
        cleared = message.get("Set-Cookie") or ""
        assert cleared.startswith("minas_portal=;") and "Max-Age=0" in cleared, (name, cleared)
        assert "HttpOnly" in cleared and "Secure" in cleared and "SameSite=Lax" in cleared, (name, cleared)
        assert "Signed in as" not in body and "alice" not in body, name


def test_signing_out_lands_on_the_plain_landing_page(portal_at, db):
    """A member who chose to leave is not told they were signed out."""
    set_member_password(db, ALICE, PASSWORD)
    token = _sign_in(portal_at)
    status, message, _ = exchange(portal_at, "POST", "/logout", body={}, cookie=token)
    assert status == 303 and message.get("Location") == "/"
    assert "Max-Age=0" in (message.get("Set-Cookie") or "")
    status, message, body = exchange(portal_at, "GET", "/")  # the browser has dropped the cookie
    assert status == 200 and 'id="landing-title"' in body
    assert LANDING_ENDED_TITLE not in body and message.get("Set-Cookie") is None


# --- every other path -----------------------------------------------------------

@pytest.mark.parametrize("path", ["/alerts", "/account?id=1", "/healthz", "/limits", "/status",
                                  "/security", "/activity", "/alerts.csv", "/export.json", "/no-such-page"])
def test_a_path_without_a_session_is_sent_to_the_landing_page(portal_at, db, path):
    """Member pages and paths that do not exist answer the same way, so a
    visitor still cannot tell which exist. With a stale cookie the redirect
    leaves the cookie alone: the landing page needs to see it."""
    assert_sent_to_the_landing_page(portal_at, "GET", path)
    assert_sent_to_the_landing_page(portal_at, "GET", path, cookie=_stale_token(db))


def test_the_sign_in_form_is_at_login(portal_at, db):
    status, _, body = exchange(portal_at, "GET", "/login")
    assert status == 200 and 'id="signin-title"' in body and 'name="password"' in body
    assert 'id="landing-title"' not in body
    set_member_password(db, ALICE, PASSWORD)
    token = _sign_in(portal_at)
    status, message, body = exchange(portal_at, "GET", "/login", cookie=token)
    assert status == 303 and message.get("Location") == "/" and body == "", "signed in: home, not a second form"


def test_a_post_without_a_session_is_sent_to_the_landing_page_and_changes_nothing(portal_at, db):
    account_id = _account(db)
    alert_id = _alert(db, account_id)
    assert_sent_to_the_landing_page(portal_at, "POST", "/acknowledge", body={"alert_id": str(alert_id)})
    assert _acknowledged_at(db, alert_id) is None
    assert_sent_to_the_landing_page(portal_at, "POST", "/snooze",
                                    body={"alert_id": str(alert_id), "hours": "4"}, cookie=_stale_token(db))
    assert active_snooze(db, account_id, "k") is None


# --- the design guards, on the rendered page ---------------------------------------

def test_the_landing_page_has_one_gold_primary_and_only_styled_classes(portal_at, db):
    """One primary per screen (the hero's "Sign in"; the topbar's is a
    link), and every class the page emits has a rule in the portal's own
    sheet - the literal scan in tests/test_surface_styles.py cannot see the
    classes that arrive through MARK_HTML and the icons, so the rendered
    page is checked here."""
    _, _, body = exchange(portal_at, "GET", "/")
    markup = _markup(body)
    assert markup.count("btn--primary") == 1
    assert '<a class="btn btn--primary" href="/login">Sign in</a>' in markup
    tokens = {t for attr in re.findall(r'class="([^"]+)"', markup) for t in attr.split()}
    assert tokens, "the page emitted no class at all - the scan proves nothing"
    missing = {t for t in tokens if not re.search(rf"\.{re.escape(t)}(?![\w-])", portal._PORTAL_STYLE)}
    assert not missing, f"the landing page emits classes the sheet never styles: {sorted(missing)}"
    assert "<table" not in markup and "<script" not in markup.replace(portal._LOCAL_TIME_SCRIPT, "")


def test_the_landing_page_makes_no_prevention_claim(portal_at, db):
    """tests/test_no_prevention_claims.py reads the literals; this reads the
    page as a customer does, rendered, with the numbers in."""
    _, _, body = exchange(portal_at, "GET", "/", cookie=_stale_token(db))
    text = html.unescape(re.sub(r"<[^>]+>", " ", _markup(body)))
    assert "cannot stop an attack" in text, "the scan is not reading the page"
    assert claims_in(text) == []


def test_the_limits_section_is_the_limits_pages_own_sentences(portal_at, db):
    """The same LIMITS_* rows, rendered through the same function, so the
    landing page cannot promise what the limits page denies. The one
    difference is deliberate: "see Am I being watched?" is a link for a
    signed-in reader, and the landing page drops it."""
    set_member_password(db, ALICE, PASSWORD)
    token = _sign_in(portal_at)
    _, _, limits = exchange(portal_at, "GET", "/limits", cookie=token)
    _, _, landing = exchange(portal_at, "GET", "/")
    does = re.search(r'<ul class="feature-list">(.*?)</ul>', limits).group(1)
    assert does in landing
    for title, _text, _to_status in LIMITS_CANNOT:
        assert title in landing, title
    assert 'href="/status">Am I being watched?' in limits
    assert 'href="/status"' not in landing


def test_the_footers_limits_link_never_bounces_an_anonymous_reader(portal_at, db):
    """Without a session /limits is sent to the landing page, so the footer
    of every page an anonymous visitor can read - the landing page, the
    not-found page a POST to no route gets - points at the landing page's
    limits section. A member's pages keep /limits, the not-found page
    included: it is rendered in the member's frame, and for a member
    /#limits would be the home page, which has no such anchor."""
    _, _, landing = exchange(portal_at, "GET", "/")
    assert 'href="/#limits">What this product can and cannot do</a>' in landing
    status, _, missing = exchange(portal_at, "POST", "/no-such-route", body={})
    assert status == 404 and 'href="/#limits">What this product can and cannot do</a>' in missing
    assert "Signed in as" not in missing
    set_member_password(db, ALICE, PASSWORD)
    token = _sign_in(portal_at)
    _, _, home = exchange(portal_at, "GET", "/", cookie=token)
    assert 'href="/limits">What this product can and cannot do</a>' in home
    status, _, missing = exchange(portal_at, "GET", "/no-such-page", cookie=token)
    assert status == 404 and 'href="/limits">What this product can and cannot do</a>' in missing
    assert 'href="/#limits"' not in missing and "Signed in as" in missing


# --- the one address (#88, decision 22d) ---------------------------------------------

def test_the_landing_page_names_the_one_address_from_the_setting(portal_at, db, monkeypatch):
    """The sign-in page teaches the same rule in the same words, so the page
    is pinned by its own id first: these sentences on the form would not do."""
    monkeypatch.setenv("SENTINEL_PORTAL_URL", f"https://{HOST}/")
    status, _, body = exchange(portal_at, "GET", "/", host=HOST)
    assert status == 200 and 'id="landing-title"' in body and 'id="signin-title"' not in body
    assert f"Our only address is {SHOWN}." in body
    assert "Minas will never email you a link asking for your API key." in body
    assert f"https://{HOST}" not in body and "minastrade" not in body, "a name to type, from the setting, never a link"
    assert "Our address has changed" not in body, "the one address is not told to move"
    monkeypatch.delenv("SENTINEL_PORTAL_URL")
    status, _, body = exchange(portal_at, "GET", "/", host=HOST)
    assert status == 200 and 'id="landing-title"' in body and 'id="signin-title"' not in body
    assert "Always open this site and sign in" in body and "Our only address" not in body


def test_an_old_address_is_told_the_address_changed_on_the_landing_page_too(portal_at, db, monkeypatch):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", f"https://{HOST}")
    _, _, body = exchange(portal_at, "GET", "/", host=OLD)
    assert 'id="landing-title"' in body
    assert "Our address has changed." in body and f"sign in at {SHOWN}" in body
    assert body.index("Our address has changed.") < body.index('id="landing-title"')
    # Under the signed-out notice when there is one, above everything else.
    _, _, body = exchange(portal_at, "GET", "/", host=OLD, cookie=_stale_token(db))
    assert body.index(LANDING_ENDED_TITLE) < body.index("Our address has changed.") < body.index('id="landing-title"')


def test_the_bare_landing_renderer_has_one_caller():
    """Registration half of the test above: a path that rendered the landing
    page without going through `Handler._landing_page` would not know the
    request's host, and the old address would be told nothing there."""
    src = (ROOT / "sentinel/portal.py").read_text()
    assert src.count("def _render_landing(") == 1
    assert src.count("_render_landing(") == 2, "call it through Handler._landing_page, which passes the request's host"
