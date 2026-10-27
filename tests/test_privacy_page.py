"""The privacy policy at /privacy (decision 27, drafted 2026-10-26).

Google Play wants a privacy policy's address in the store listing and in
the app; the owner asked for it on the site, for the owner and the lawyer
to approve before it ships. What is held here: the page is public and the
same with a session; every surface links to it; its numbers come from the
code and the configuration, its address from the setting; it makes no
prevention claim; and it cannot be marked approved while a fact is still
marked "[To confirm".
"""
from __future__ import annotations

import html

import pytest

from sentinel import portal
from sentinel.portal import (
    PRIVACY_CONTACT,
    SESSION_COOKIE,
    SESSION_IDLE_SECONDS,
    SESSION_TTL_SECONDS,
    _render_privacy,
    privacy_retention,
    privacy_sections,
    retention_summary,
    serve_portal,
    set_member_password,
)
from tests.helpers.landing_redirect import exchange
from tests.test_no_prevention_claims import claims_in

ALICE = "alice@example.com"
PASSWORD = "pw-alice-long-enough"


@pytest.fixture
def portal_at(db):
    srv = serve_portal(db, 0, host="127.0.0.1")
    yield "127.0.0.1", srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _member(db):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES ('alice-1', 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', '2026-01-01', "
            "'2026-01-01')", (ALICE,))
    set_member_password(db, ALICE, PASSWORD)


def _sign_in(host_port):
    status, message, _ = exchange(host_port, "POST", "/login", body={"email": ALICE, "password": PASSWORD})
    assert status == 303
    return (message.get("Set-Cookie") or "").split("minas_portal=", 1)[1].split(";", 1)[0]


def _text(page: str) -> str:
    return html.unescape(page)


# --- public, and the same page for a member ---------------------------------------

def test_the_policy_is_public(portal_at):
    status, _message, body = exchange(portal_at, "GET", "/privacy")
    assert status == 200, "a visitor is not sent to the landing page"
    assert '<h1 id="privacy-title">Privacy policy</h1>' in body
    assert PRIVACY_CONTACT in body
    assert '<div class="topbar__meta"><a href="/login">Sign in</a></div>' in body


def test_a_member_reads_the_same_policy_inside_the_portal(portal_at, db):
    _member(db)
    token = _sign_in(portal_at)
    status, _message, body = exchange(portal_at, "GET", "/privacy", cookie=token)
    assert status == 200
    assert '<h1 id="privacy-title">Privacy policy</h1>' in body
    assert 'class="subnav"' in body and "Signed in as" in body


def test_every_web_page_links_to_it(portal_at, db):
    """The landing page and every framed page carry it in the footer; the
    sign-in page has no footer and carries it itself."""
    _member(db)
    for path in ("/", "/login"):
        _status, _message, body = exchange(portal_at, "GET", path)
        assert '<a href="/privacy">Privacy policy</a>' in body, path
    token = _sign_in(portal_at)
    for path in ("/", "/security", "/limits"):
        _status, _message, body = exchange(portal_at, "GET", path, cookie=token)
        assert '<a href="/privacy">Privacy policy</a>' in body, path


# --- what it says comes from the code -------------------------------------------------

def test_the_numbers_come_from_the_code_and_the_configuration():
    page = _text(_render_privacy(None))
    assert f"{SESSION_COOKIE}, when you sign in" in page
    assert f"after {SESSION_IDLE_SECONDS // 3600} hours without activity" in page
    assert f"after {SESSION_TTL_SECONDS // 3600} hours in all" in page
    from sentinel.api import REFRESH_TTL_SECONDS

    assert f"ends after {REFRESH_TTL_SECONDS // 86400} days unopened" in page
    for what, how_long in retention_summary():
        assert what in page and how_long in page, what


def test_the_ai_summary_says_whether_it_is_on_from_the_configuration():
    class Rules:
        def __init__(self, on):
            self.on = on

        def get(self, *keys, default=None):
            return self.on if keys == ("triage", "enabled") else default

        def get_int(self, section, setting):
            from sentinel.rules_schema import default_for

            return int(default_for(section, setting) or 0)

    assert "it is switched off." in _text(_render_privacy(None))
    assert "it is switched on." in _text(_render_privacy(None, Rules(True)))
    assert "it is switched off." in _text(_render_privacy(None, Rules(False)))


def test_the_apps_server_is_named_from_the_setting(monkeypatch):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", "https://guardian.example.test/")
    assert "it talks only to guardian.example.test." in _text(_render_privacy(None))
    monkeypatch.delenv("SENTINEL_PORTAL_URL", raising=False)
    assert "it talks only to the Minas server." in _text(_render_privacy(None))


def test_it_makes_no_prevention_claim():
    texts = [heading for _a, heading, _p in privacy_sections()]
    texts += [p for _a, _h, paragraphs in privacy_sections() for p in paragraphs]
    texts += [part for row in privacy_retention() for part in row]
    assert [t for t in texts if claims_in(t)] == []


def test_the_anchors_are_unique():
    anchors = [anchor for anchor, _h, _p in privacy_sections()]
    assert len(anchors) == len(set(anchors))


# --- a draft until it is approved ------------------------------------------------------

def test_the_page_says_it_is_a_draft_exactly_while_it_is_one(monkeypatch):
    for approved in (False, True):
        monkeypatch.setattr(portal, "PRIVACY_APPROVED", approved)
        assert ("Draft for review." in _render_privacy(None)) is not approved


def test_approval_needs_every_open_fact_answered():
    """Approval is the owner's and the lawyer's (decision 27), and flipping
    the flag is not enough: the page must have no question left in it.
    While it is a draft, the check must be able to see the marks it would
    refuse."""
    marks = _text(_render_privacy(None)).count("[To confirm")
    if portal.PRIVACY_APPROVED:
        assert marks == 0, "approved with questions still open"
    else:
        assert marks > 0


# --- the owner's answers (2026-10-27) ----------------------------------------------------

def test_it_names_the_company_and_the_developer_name_on_google_play():
    """Google wants the policy to name the developer as the store listing
    does ("Minas AI"); the company behind it is Minas Academy."""
    page = _text(_render_privacy(None))
    assert "run by Minas Academy" in page and "of Toronto, Canada" in page
    assert "publishes it on Google Play as Minas AI" in page


def test_the_owners_answers_are_on_the_page():
    page = _text(_render_privacy(None))
    assert "We answer every request within 30 days." in page
    assert "for people aged 18 and over" in page
    assert "Office of the Privacy Commissioner of Canada" in page
    assert "standard contractual clauses" in page


def test_the_sms_sentence_names_the_provider_the_sender_would_use(monkeypatch):
    for name in ("CLICKSEND_USERNAME", "CLICKSEND_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
                 "TWILIO_FROM_NUMBER"):
        monkeypatch.delenv(name, raising=False)
    assert "SMS alerts are not being sent yet." in _text(_render_privacy(None))
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550100")
    assert "SMS alerts go through Twilio" in _text(_render_privacy(None))
    monkeypatch.setenv("CLICKSEND_USERNAME", "user")
    monkeypatch.setenv("CLICKSEND_API_KEY", "key")
    assert "SMS alerts go through ClickSend" in _text(_render_privacy(None))


def test_the_backup_period_is_the_backup_scripts():
    """The page promises what scripts/host/backup.sh does; the host can
    override the script's default, so the default is what both must say."""
    import re
    from pathlib import Path

    script = (Path(__file__).resolve().parent.parent / "scripts/host/backup.sh").read_text()
    default = int(re.search(r'KEEP_DAYS="\$\{MINAS_BACKUP_KEEP_DAYS:-(\d+)\}"', script).group(1))
    assert portal.BACKUP_KEEP_DAYS == default == 28
    assert f"a deletion reaches the backups within {default} days" in _text(_render_privacy(None))
