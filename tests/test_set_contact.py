from __future__ import annotations

import argparse

import pytest

from sentinel.cli import cmd_set_contact
from sentinel.portal import (
    check_member_login,
    grant_account,
    is_scoped,
    member_accounts,
    member_exists,
    set_member_password,
)
from tests.conftest import make_account


def _args(**kw):
    ns = argparse.Namespace(label=None, alert_email=None, alert_phone=None, clear=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_set_contact_updates_existing_account(db, cipher, monkeypatch, tmp_path):
    account_id = make_account(db, "acct-c")
    # cmd_set_contact opens its own Database from env; point it at the same file.
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    rc = cmd_set_contact(_args(label="acct-c", alert_email="x@y.com", alert_phone="+15550001111"))
    assert rc == 0
    with db.cursor() as cur:
        cur.execute("SELECT alert_email, alert_phone FROM accounts WHERE id = ?", (account_id,))
        row = cur.fetchone()
    assert row["alert_email"] == "x@y.com"
    assert row["alert_phone"] == "+15550001111"


def test_set_contact_clear_refuses_to_remove_the_primary_email(db, cipher, monkeypatch, capsys):
    """Decision 4 (#24): email is the alert channel that cannot be switched
    off, so it cannot be cleared either. --clear is refused and nothing
    changes; the phone alone can be cleared by restating the email."""
    make_account(db, "acct-d")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-d", alert_email="a@b.com", alert_phone="+15550001111"))
    assert cmd_set_contact(_args(label="acct-d", clear=True)) == 2
    assert "cannot be removed" in capsys.readouterr().err
    with db.cursor() as cur:
        cur.execute("SELECT alert_email, alert_phone FROM accounts WHERE label = 'acct-d'")
        row = cur.fetchone()
    assert row["alert_email"] == "a@b.com" and row["alert_phone"] == "+15550001111"
    assert cmd_set_contact(_args(label="acct-d", alert_email="a@b.com")) == 0
    with db.cursor() as cur:
        cur.execute("SELECT alert_email, alert_phone FROM accounts WHERE label = 'acct-d'")
        row = cur.fetchone()
    assert row["alert_email"] == "a@b.com" and row["alert_phone"] is None


def test_set_contact_unknown_account_returns_error(db, monkeypatch):
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    assert cmd_set_contact(_args(label="nope", alert_email="x@y.com")) == 1


# --- the audit trail (G-32, #36) ----------------------------------------------
#
# Where the alerts about an account go is the setting an attacker changes
# FIRST: redirect the alerts, then drain the account. Until #36 this command
# rewrote the destination and left no trace at all. The tests below pin that a
# change leaves an event row - and that a non-change doesn't, because a
# forensic log that records re-runs as changes is a log nobody can read.

def _events(db, account_id):
    import json

    with db.cursor() as cur:
        cur.execute(
            "SELECT event_type, payload_json, actor FROM events "
            "WHERE account_id = ? ORDER BY id",
            (account_id,),
        )
        return [(r["event_type"], json.loads(r["payload_json"]), r["actor"])
                for r in cur.fetchall()]


def test_contact_change_is_recorded_with_old_and_new(db, monkeypatch):
    account_id = make_account(db, "acct-e")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-e", alert_email="old@y.com"))
    cmd_set_contact(_args(label="acct-e", alert_email="new@y.com", alert_phone="+15550001111"))
    events = _events(db, account_id)
    assert [e[0] for e in events] == ["alert_contact_updated", "alert_contact_updated"]
    assert events[1][1] == {
        "email_from": "old@y.com", "email_to": "new@y.com",
        "phone_from": None, "phone_to": "+15550001111",
    }
    # The CLI carries no operator identity; the row says so rather than guessing.
    assert events[1][2] is None


def test_rerunning_the_same_values_records_nothing(db, monkeypatch):
    account_id = make_account(db, "acct-f")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-f", alert_email="same@y.com"))
    cmd_set_contact(_args(label="acct-f", alert_email="same@y.com"))
    assert len(_events(db, account_id)) == 1


def test_clearing_the_phone_is_recorded(db, monkeypatch):
    account_id = make_account(db, "acct-g")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-g", alert_email="a@b.com", alert_phone="+15550001111"))
    cmd_set_contact(_args(label="acct-g", alert_email="a@b.com"))
    events = _events(db, account_id)
    assert events[-1][0] == "alert_contact_updated"
    assert events[-1][1]["phone_from"] == "+15550001111"
    assert events[-1][1]["phone_to"] is None
    assert events[-1][1]["email_to"] == "a@b.com"


# --- #91: the emergency contact has a supported write path -------------------
#
# Until #91 the secondary was written once at onboarding and never again; a
# real person's removal request was honoured by SQL against production,
# unattributed by necessity. These pin the supported path: set, clear,
# diff-recorded, no-op-safe - and that touching the secondary can never
# disturb where the alerts themselves go.

def _seed_secondary(db, label, email="sec@y.com", phone=None):
    with db.cursor() as cur:
        cur.execute(
            "UPDATE accounts SET alert_email_secondary = ?, alert_phone_secondary = ? "
            "WHERE label = ?", (email, phone, label))


def _contacts(db, label):
    with db.cursor() as cur:
        cur.execute(
            "SELECT alert_email, alert_phone, alert_email_secondary, alert_phone_secondary "
            "FROM accounts WHERE label = ?", (label,))
        return dict(cur.fetchone())


def test_secondary_contact_can_be_set_and_is_recorded(db, monkeypatch):
    account_id = make_account(db, "acct-s1")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    rc = cmd_set_contact(_args(label="acct-s1", alert_email_secondary="kin@y.com",
                               alert_phone_secondary="+15550002222"))
    assert rc == 0
    row = _contacts(db, "acct-s1")
    assert row["alert_email_secondary"] == "kin@y.com"
    assert row["alert_phone_secondary"] == "+15550002222"
    events = _events(db, account_id)
    assert events[-1][0] == "secondary_contact_updated"
    assert events[-1][1] == {"email_from": None, "email_to": "kin@y.com",
                             "phone_from": None, "phone_to": "+15550002222"}


def test_a_secondary_change_cannot_disturb_the_primary(db, monkeypatch):
    """The criterion. Removing an emergency contact must not move where the
    alerts themselves go - the two destinations change only on their own
    flags."""
    make_account(db, "acct-s2")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-s2", alert_email="owner@y.com",
                          alert_phone="+15550003333"))
    _seed_secondary(db, "acct-s2")
    cmd_set_contact(_args(label="acct-s2", clear_secondary=True))
    row = _contacts(db, "acct-s2")
    assert row["alert_email"] == "owner@y.com", "clearing the secondary moved the primary"
    assert row["alert_phone"] == "+15550003333"
    assert row["alert_email_secondary"] is None
    assert row["alert_phone_secondary"] is None


def test_clearing_the_secondary_records_who_was_removed(db, monkeypatch):
    """The azdatoo case: the removal is exactly the change a customer reads
    /activity to find, so the event carries the removed address."""
    account_id = make_account(db, "acct-s3")
    _seed_secondary(db, "acct-s3", email="azdatoo@example.invalid")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-s3", clear_secondary=True))
    events = _events(db, account_id)
    assert events[-1][0] == "secondary_contact_updated"
    assert events[-1][1]["email_from"] == "azdatoo@example.invalid"
    assert events[-1][1]["email_to"] is None


def test_rerunning_the_same_secondary_records_nothing(db, monkeypatch):
    account_id = make_account(db, "acct-s4")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-s4", alert_email_secondary="kin@y.com"))
    cmd_set_contact(_args(label="acct-s4", alert_email_secondary="kin@y.com"))
    assert len(_events(db, account_id)) == 1


def test_an_invocation_with_no_flags_changes_nothing(db, monkeypatch):
    """Bare `set-contact --label x` used to CLEAR the primary - a footgun,
    removed with #91. Nothing requested, nothing changed, nothing recorded."""
    account_id = make_account(db, "acct-s5")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-s5", alert_email="keep@y.com"))
    rc = cmd_set_contact(_args(label="acct-s5"))
    assert rc == 0
    assert _contacts(db, "acct-s5")["alert_email"] == "keep@y.com"
    assert len(_events(db, account_id)) == 1


def test_secondary_events_reach_the_portal_activity_page():
    """Registration half: the event type must be in the /activity allowlist
    with a customer-readable label, or the removal is invisible on the one
    page built to reveal it."""
    from sentinel.events import ADMIN_EVENT_LABELS, ADMIN_EVENT_TYPES
    assert "secondary_contact_updated" in ADMIN_EVENT_TYPES
    assert ADMIN_EVENT_LABELS["secondary_contact_updated"] == "Emergency contact changed"


# --- the --as gate (#80's pattern) -------------------------------------------

def test_once_operators_exist_the_change_requires_a_name(db, monkeypatch):
    from sentinel.operators import create_operator
    account_id = make_account(db, "acct-s6")
    _seed_secondary(db, "acct-s6")
    create_operator(db, "erwin", "a-long-enough-password-1")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    rc = cmd_set_contact(_args(label="acct-s6", clear_secondary=True))
    assert rc == 2, "operators exist - an unattributed change must be refused"
    assert _contacts(db, "acct-s6")["alert_email_secondary"] == "sec@y.com", (
        "the refusal must leave the row untouched")
    assert _events(db, account_id) == []

    rc = cmd_set_contact(_args(label="acct-s6", clear_secondary=True, as_operator="erwin"))
    assert rc == 0
    events = _events(db, account_id)
    assert events[-1][0] == "secondary_contact_updated"
    assert events[-1][2] == "erwin", "the event must carry the operator's name"


def test_sms_toggle_is_recorded_once_per_actual_change(db, monkeypatch):
    account_id = make_account(db, "acct-h")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-h", sms_alerts="off"))
    cmd_set_contact(_args(label="acct-h", sms_alerts="off"))  # not a change
    cmd_set_contact(_args(label="acct-h", sms_alerts="on"))
    events = [e for e in _events(db, account_id) if e[0] == "sms_alerts_changed"]
    assert [e[1] for e in events] == [
        {"from": True, "to": False},
        {"from": False, "to": True},
    ]


# --- the portal login follows the move (#61, decision 10) ---------------------
#
# set-contact is the only way to move an account's contacts, and it used to
# update `accounts` alone. Portal logins are keyed by email, and since #61 a
# login issued from an account page is SCOPED: it opens exactly its grants.
# So a move left the new address receiving the alerts with no login and
# nobody told, and a scoped login for the OLD address kept its grant - it
# could still open the account and acknowledge its alerts, which stops the
# escalation ladder - while the notice told that very address it could no
# longer see the account. A legacy login lost the account through its
# contact rows, which is the asymmetry these pin. Revoked rather than
# reworded (decision 26): a mailbox somebody moved away from, perhaps
# because it was compromised, must not keep access.

OLD, NEW, PW = "old@y.com", "new@y.com", "a-long-portal-password"


def _seed_primary(db, label, email):
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = ? WHERE label = ?", (email, label))


def _scoped_login(db, email, *account_ids):
    """A login issued from an account page, as the dashboard issues it."""
    set_member_password(db, email, PW, grant_account_id=account_ids[0], actor="erwin")
    for account_id in account_ids[1:]:
        grant_account(db, email, account_id, actor="erwin")


def _opens(db, email):
    return sorted(r["label"] for r in member_accounts(db, email))


def _revocations(db, account_id):
    return [(payload, actor) for kind, payload, actor in _events(db, account_id)
            if kind == "member_account_revoked"]


def _portal_lines(capsys):
    return [line for line in capsys.readouterr().out.splitlines() if line.startswith("portal:")]


def test_moving_the_primary_revokes_the_old_addresses_scoped_grant(db, monkeypatch, capsys):
    from sentinel.operators import create_operator

    account_id = make_account(db, "acct-p1")
    _seed_primary(db, "acct-p1", OLD)
    _scoped_login(db, OLD, account_id)
    create_operator(db, "erwin", "a-long-enough-password-1")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    assert _opens(db, OLD) == ["acct-p1"]  # what the move has to end

    assert cmd_set_contact(_args(label="acct-p1", alert_email=NEW, as_operator="erwin")) == 0

    assert _opens(db, OLD) == [], "the old address's scoped login still opens the account"
    assert _revocations(db, account_id) == [({"login": OLD}, "erwin")], (
        "the revocation must be recorded against the operator who made the move")
    assert member_exists(db, OLD), "the login itself is not this account's to delete"
    assert f"portal: {OLD} no longer opens 'acct-p1'" in capsys.readouterr().out


@pytest.mark.parametrize("move", [{"clear_secondary": True},
                                  {"alert_email_secondary": "kin2@y.com"}],
                         ids=["cleared", "moved"])
def test_moving_the_emergency_contact_revokes_its_scoped_grant(db, monkeypatch, move):
    from sentinel.operators import create_operator

    account_id = make_account(db, "acct-p2")
    _seed_primary(db, "acct-p2", "owner@y.com")
    _seed_secondary(db, "acct-p2", email=OLD)
    _scoped_login(db, OLD, account_id)
    create_operator(db, "erwin", "a-long-enough-password-1")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    assert _opens(db, OLD) == ["acct-p2"]

    assert cmd_set_contact(_args(label="acct-p2", as_operator="erwin", **move)) == 0

    assert _opens(db, OLD) == [], "the removed emergency contact can still open the account"
    assert _revocations(db, account_id) == [({"login": OLD}, "erwin")]


@pytest.mark.parametrize("login, says", [
    ("scoped", f"portal: {OLD} no longer opens 'acct-pa' - its access to this account was removed"),
    ("legacy", f"portal: {OLD} no longer opens 'acct-pa' - it is no longer a contact on this account"),
    (None, None),
], ids=["scoped", "legacy", "no-login"])
def test_the_output_says_when_the_old_address_lost_the_account(db, monkeypatch, capsys, login, says):
    """One line per old address whose access ended, whichever way it held
    it - and none for an address that never had a login to lose."""
    account_id = make_account(db, "acct-pa")
    _seed_primary(db, "acct-pa", OLD)
    if login == "scoped":
        _scoped_login(db, OLD, account_id)
    elif login == "legacy":
        set_member_password(db, OLD, PW)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    assert cmd_set_contact(_args(label="acct-pa", alert_email=NEW)) == 0

    lost = [line for line in _portal_lines(capsys) if line.startswith(f"portal: {OLD} ")]
    assert lost == ([says] if says else [])


@pytest.mark.parametrize("login, says", [
    (None, 'has no portal login yet - issue one from the account page ("Generate portal password")'),
    ("elsewhere", "has a portal login that does not open 'acct-p3' - grant it from the account page "
                  '("Grant this account")'),
    ("here", "has a portal login that already opens 'acct-p3'"),
    # A legacy login opens every account its address is a contact on, so the
    # move itself let it in: reported as open, because it is.
    ("legacy", "has a portal login that already opens 'acct-p3'"),
], ids=["no-login", "scoped-elsewhere", "scoped-here", "legacy"])
def test_the_output_names_the_new_addresses_login_state(db, monkeypatch, capsys, login, says):
    """The new address receives the alerts the moment the move lands; whether
    it can open the account in the portal is a separate, operator-issued act
    (decision 10). So the command says which state it is in and where to
    fix it - and grants nothing itself."""
    account_id = make_account(db, "acct-p3")
    other = make_account(db, "acct-p3-other")
    _seed_primary(db, "acct-p3", OLD)
    _seed_primary(db, "acct-p3-other", NEW)
    if login == "elsewhere":
        _scoped_login(db, NEW, other)
    elif login == "here":
        _scoped_login(db, NEW, account_id)
    elif login == "legacy":
        set_member_password(db, NEW, PW)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    assert cmd_set_contact(_args(label="acct-p3", alert_email=NEW)) == 0

    out = capsys.readouterr().out
    assert f"portal: the new primary contact {NEW} {says}\n" in out
    # Reported, never issued or granted: the scope is what an operator made it.
    if login is None:
        assert not member_exists(db, NEW)
    elif login == "elsewhere":
        assert _opens(db, NEW) == ["acct-p3-other"]
    # The summary line is unchanged and still last.
    assert out.splitlines()[-1] == f"contact for 'acct-p3': email={NEW} phone=-"


def test_a_new_emergency_contact_is_reported_too(db, monkeypatch, capsys):
    make_account(db, "acct-p9")
    _seed_primary(db, "acct-p9", "owner@y.com")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    assert cmd_set_contact(_args(label="acct-p9", alert_email_secondary="kin@y.com")) == 0
    assert _portal_lines(capsys) == [
        "portal: the new emergency contact kin@y.com has no portal login yet - issue one "
        'from the account page ("Generate portal password")']


# The defences: what the revocation must NOT touch.

def test_a_legacy_login_is_not_materialised_and_loses_the_account_through_its_contact_rows(
        db, monkeypatch):
    """A login nobody has scoped opens every account its address is a contact
    on, so the UPDATE alone already took this account away. revoke_account
    would MATERIALISE its scope first - freezing the contact rows into
    grants - a change to the login nobody asked for."""
    account_id = make_account(db, "acct-p4")
    make_account(db, "acct-p4-other")
    _seed_primary(db, "acct-p4", OLD)
    _seed_primary(db, "acct-p4-other", OLD)
    set_member_password(db, OLD, PW)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    assert _opens(db, OLD) == ["acct-p4", "acct-p4-other"]

    assert cmd_set_contact(_args(label="acct-p4", alert_email=NEW)) == 0

    with db.cursor() as cur:
        cur.execute("SELECT scoped FROM portal_members WHERE email = ?", (OLD,))
        assert cur.fetchone()["scoped"] == 0, "the legacy login was materialised"
        cur.execute("SELECT count(*) AS n FROM member_account_grants WHERE email = ?", (OLD,))
        assert cur.fetchone()["n"] == 0
    assert not is_scoped(db, OLD)
    assert _opens(db, OLD) == ["acct-p4-other"]
    assert _revocations(db, account_id) == []


def test_the_old_login_keeps_its_other_accounts(db, monkeypatch):
    """The move takes THIS account from the old address and nothing else:
    not its other grants, not its login."""
    account_id = make_account(db, "acct-p5")
    other = make_account(db, "acct-p5-other")
    _seed_primary(db, "acct-p5", OLD)
    _scoped_login(db, OLD, account_id, other)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    assert cmd_set_contact(_args(label="acct-p5", alert_email=NEW)) == 0

    assert "acct-p5-other" in _opens(db, OLD)
    assert _revocations(db, other) == []
    assert check_member_login(db, OLD, PW) is True


def test_an_address_that_is_still_a_contact_keeps_its_grant(db, monkeypatch, capsys):
    """Moved from primary to emergency contact in one command: still a
    contact on the account, so still entitled to open it. The revocation is
    for an address the move took OFF the account, not for one that changed
    role. (The notice to it is pinned in tests/test_account_notices.py.)"""
    account_id = make_account(db, "acct-p6")
    _seed_primary(db, "acct-p6", OLD)
    _scoped_login(db, OLD, account_id)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    assert cmd_set_contact(_args(label="acct-p6", alert_email=NEW, alert_email_secondary=OLD)) == 0

    assert _opens(db, OLD) == ["acct-p6"]
    assert _revocations(db, account_id) == []
    assert f"{OLD} no longer opens" not in capsys.readouterr().out


def test_rerunning_the_same_addresses_revokes_nothing_and_reports_nothing(db, monkeypatch, capsys):
    """A phone edit and a re-typed address are recorded or ignored as before,
    but no address moved - so no login line and no revocation."""
    account_id = make_account(db, "acct-p7")
    _seed_primary(db, "acct-p7", OLD)
    _seed_secondary(db, "acct-p7", email="kin@y.com")
    _scoped_login(db, OLD, account_id)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    assert cmd_set_contact(_args(label="acct-p7", alert_email=OLD, alert_phone="+15550001111",
                                 alert_email_secondary="KIN@y.com")) == 0
    assert cmd_set_contact(_args(label="acct-p7", alert_email=OLD, alert_phone="+15550001111")) == 0

    assert _opens(db, OLD) == ["acct-p7"]
    assert _revocations(db, account_id) == []
    assert _portal_lines(capsys) == []


def test_a_refused_change_revokes_nothing(db, monkeypatch):
    """The --as gate stands in front of the revocation exactly as it stands
    in front of the move: refused for want of a name, or for a name that is
    not an active operator, nothing moves and nobody's access is taken."""
    from sentinel.operators import create_operator

    account_id = make_account(db, "acct-p8")
    _seed_primary(db, "acct-p8", OLD)
    _scoped_login(db, OLD, account_id)
    create_operator(db, "erwin", "a-long-enough-password-1")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    assert cmd_set_contact(_args(label="acct-p8", alert_email=NEW)) == 2
    assert cmd_set_contact(_args(label="acct-p8", alert_email=NEW, as_operator="mallory")) == 2

    assert _contacts(db, "acct-p8")["alert_email"] == OLD
    assert _opens(db, OLD) == ["acct-p8"]
    assert _revocations(db, account_id) == []


def test_a_phone_only_change_cannot_clear_the_primary_email(db, monkeypatch, capsys):
    """Decision 4's floor held only against --clear and a blank
    --alert-email: --alert-phone alone set the primary pair to (no email,
    that phone), writing the email NULL. Since the login follows a move,
    that typo would also have taken the customer's own portal access. The
    pair is set together, so the email is restated or nothing moves."""
    account_id = make_account(db, "acct-p10")
    _seed_primary(db, "acct-p10", OLD)
    _scoped_login(db, OLD, account_id)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    assert cmd_set_contact(_args(label="acct-p10", alert_phone="+15550001111")) == 2
    err = capsys.readouterr().err
    assert "cannot be removed" in err and "restate the current email" in err

    assert _contacts(db, "acct-p10")["alert_email"] == OLD
    assert _opens(db, OLD) == ["acct-p10"]
    assert _revocations(db, account_id) == []
    assert [kind for kind, _, _ in _events(db, account_id) if kind == "alert_contact_updated"] == [], (
        "a refused change records nothing")
    # Restated, the phone alone changes and nobody's access moves.
    assert cmd_set_contact(_args(label="acct-p10", alert_email=OLD, alert_phone="+15550001111")) == 0
    assert _contacts(db, "acct-p10")["alert_phone"] == "+15550001111"
    assert _opens(db, OLD) == ["acct-p10"]


def test_one_address_leaving_both_roles_is_reported_once(db, monkeypatch, capsys):
    """One address that was both contacts, moved off both in one command, is
    one fact for the operator - for a legacy login too, which (unlike a
    scoped one, whose second revocation finds nothing) would otherwise
    print its line once per role."""
    make_account(db, "acct-p11")
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = ?, alert_email_secondary = ? WHERE label = ?",
                    (OLD, OLD, "acct-p11"))
    set_member_password(db, OLD, PW)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    assert cmd_set_contact(_args(label="acct-p11", alert_email=NEW, clear_secondary=True)) == 0

    assert [line for line in _portal_lines(capsys) if line.startswith(f"portal: {OLD} ")] == [
        f"portal: {OLD} no longer opens 'acct-p11' - it is no longer a contact on this account"]
