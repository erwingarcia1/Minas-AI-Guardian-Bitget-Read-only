"""The other side of sixteen conditionals (#106).

mutmut 3.8.0 mutates a conditional expression `a if c else b` by forcing
its condition - `(c) or True`, `(c) and False` - which 3.7.0 never did. On
main@58975e2 that showed 25 conditionals no test took both ways. Nine are
equivalent or unreachable and carry ledger rows with their reasons
(scripts/mutation-known-survivors.txt). These are the sixteen that are real:
a branch the suite never took, where taking the other one changes what a
person reads, what gets stored, or whether the code runs at all.

Each test takes the branch the mutant removes and asserts the difference.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from sentinel.alerts.base import CAUSE_KEY, AlertMessage, runbook
from sentinel.alerts.contact import ContactRouter
from sentinel.alerts.delivery import last_accepted, record_delivery
from sentinel.alerts.email import EmailChannel
from sentinel.alerts.escalation import Escalator
from sentinel.alerts.operator import OperatorAlerter
from sentinel.alerts.preferences import default_grid, member_prefs, set_member_prefs
from sentinel.alerts.telegram_link import process_update
from sentinel.config import RulesConfig
from sentinel.portal import set_member_password


def _alert(**kw) -> AlertMessage:
    fields = dict(account_label="Bitget Main", rule="password_changed", severity="LOW",
                  confidence="high", message="The password was changed.", evidence={"login": "a@x"})
    fields.update(kw)
    return AlertMessage(**fields)


# --- alerts/base.py: what a person reads --------------------------------------------

def test_a_runbook_with_no_closing_note_ends_at_its_last_step():
    """account_unreachable carries steps but no monitoring note: nothing may
    follow the last step, not even the blank paragraph an empty note made."""
    text = runbook("bitget", rule="account_unreachable")
    assert text, "the rule's steps are the point of this test"
    assert not text.endswith("\n"), repr(text[-40:])


def test_the_operator_copy_of_a_rule_with_no_advice_ends_at_its_evidence():
    assert _alert().render().endswith("  login: a@x")


def test_a_customer_message_with_no_incident_number_names_none():
    """Decision 16's reference is a number the customer can quote. With no
    number there is nothing to quote - never "incident #None"."""
    assert "incident #" not in _alert(incident_id=None).render_customer()
    assert "This is incident #42." in _alert(incident_id=42).render_customer()


# --- alerts/contact.py ------------------------------------------------------------

def test_sms_is_on_for_an_account_the_router_cannot_find(db):
    """The default is the customer being reached, not the router crashing."""
    assert ContactRouter(db=db)._sms_pref_enabled(999_999) is True


# --- alerts/delivery.py: what gets stored -------------------------------------------

def test_the_last_delivery_to_one_recipient_is_that_recipients(db):
    """The drill tells each member when it last reached THEM. Without the
    recipient filter it would report somebody else's newer delivery."""
    early = datetime(2026, 9, 1, tzinfo=timezone.utc)
    record_delivery(db, channel="email", recipient="Alice@Example.com", status="accepted", when=early)
    record_delivery(db, channel="email", recipient="bob@example.com", status="accepted",
                    when=early + timedelta(days=3))
    assert last_accepted(db, recipient=" alice@example.com ") == early.isoformat()


def test_a_leg_with_no_recipient_is_stored_as_absent_never_empty(db):
    """Only email addresses are normalised. An SMS or Telegram leg keeps what
    it was given - and no recipient stays None, not '' (CONTRIBUTING: falsy
    sentinels)."""
    record_delivery(db, channel="sms", recipient=None, status="skipped")
    record_delivery(db, channel="telegram", recipient=" ChatID-77 ", status="accepted")
    with db.cursor() as cur:
        cur.execute("SELECT channel, recipient FROM deliveries ORDER BY id")
        rows = [tuple(r) for r in cur.fetchall()]
    assert rows == [("sms", None), ("telegram", " ChatID-77 ")]


# --- alerts/email.py ---------------------------------------------------------------

class _SMTP:
    sent: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        _SMTP.sent.append(msg)


@pytest.mark.parametrize("audience", ["operator", "customer"])
def test_each_audience_gets_its_own_subject_line(monkeypatch, audience):
    _SMTP.sent = []
    monkeypatch.setattr("sentinel.alerts.email.net.smtp", _SMTP)
    alert = _alert(rule="withdrawal_initiated", severity="CRITICAL")
    EmailChannel("h", 25, "u", "p", "s@x", ["r@x"], audience=audience).send(alert)
    expected = alert.operator_subject() if audience == "operator" else alert.customer_subject()
    assert alert.operator_subject() != alert.customer_subject()
    assert _SMTP.sent[0]["Subject"] == expected


# --- alerts/escalation.py ------------------------------------------------------------

class _Recording:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def _stale(db, severity="CRITICAL", evidence="{}"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, status, onboarded_at, updated_at) "
            "VALUES ('acct-1', 'bitget', 'independent', 'x', 'y', 'z', 'active', '2026-01-01', '2026-01-01')")
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, "
            "sent_at) VALUES (?, 'withdrawal_initiated', ?, 'k', '0.5 BTC left the account', ?, 'high', ?)",
            (account_id, severity, evidence,
             (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()))
        return cur.lastrowid


def test_a_critical_escalation_calls_itself_a_critical_alert_and_carries_no_cause(db):
    """The coverage wording ("monitoring is blind on ...") is for coverage
    alerts; a CRITICAL says what it is. And an original with no established
    cause hands on no cause key at all - not one holding None."""
    alert_id = _stale(db)
    channel = _Recording()
    Escalator([channel], unacknowledged_minutes=15).check(db)
    [escalated] = channel.sent
    assert escalated.message.startswith(f"CRITICAL alert #{alert_id} has not been acknowledged.")
    assert "coverage alert" not in escalated.message
    assert CAUSE_KEY not in escalated.evidence
    assert escalated.evidence == {"original_alert_id": alert_id}


# --- alerts/operator.py --------------------------------------------------------------

def _rules() -> RulesConfig:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config" / "rules.yaml"
        if candidate.exists():
            return RulesConfig(str(candidate))
    raise FileNotFoundError("no config/rules.yaml above the tests")


class _Refusing:
    def send(self, alert):
        raise RuntimeError("provider said no")


def test_each_operator_page_leg_records_its_own_detail(db):
    """A refused leg keeps the provider's words; an accepted one is labelled
    with the page it belongs to. Either swapped for the other loses the one
    thing that row exists to say."""
    channels = {sev: [_Recording(), _Refusing()] for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
    page_id = OperatorAlerter(db, channels, _rules()).page(rule="liveness_failed", severity="CRITICAL",
                                                           message="m")
    with db.cursor() as cur:
        cur.execute("SELECT status, detail FROM deliveries WHERE kind = 'operator_page' ORDER BY id")
        legs = {r["status"]: r["detail"] for r in cur.fetchall()}
    assert legs == {"accepted": f"operator page #{page_id}", "failed": "provider said no"}


# --- alerts/preferences.py -----------------------------------------------------------

def test_switching_only_quiet_hours_on_is_a_change_and_is_stored(db):
    """The writer updates only when something differs, and judges that in
    its WHERE clause. Turning quiet hours on with nothing else changed must
    count as a change - otherwise the member's switch silently does
    nothing."""
    email = "alice@example.com"
    set_member_password(db, email, "a-long-enough-password-1", notify=None)
    choices = dict(timezone_name="America/Toronto", quiet_start="22:00", quiet_end="07:00",
                   grid=default_grid())
    assert set_member_prefs(db, email, quiet_hours_enabled=False, **choices)
    assert member_prefs(db, email)["quiet_hours_enabled"] is False
    assert set_member_prefs(db, email, quiet_hours_enabled=True, **choices)
    assert member_prefs(db, email)["quiet_hours_enabled"] is True
    assert not set_member_prefs(db, email, quiet_hours_enabled=True, **choices)  # no change, no write


# --- alerts/telegram_link.py ----------------------------------------------------------

def test_a_bare_start_is_refused_not_a_crash(db, monkeypatch):
    """`/start` with no code is what a member sends after tapping the bot's
    own Start button. It gets the refusal, and the worker keeps going."""
    from sentinel.alerts import telegram_link

    sent = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": []}

    monkeypatch.setattr(telegram_link.net, "post", lambda url, **kw: sent.append(kw.get("json")) or _Resp())
    process_update(db, "tok", {"update_id": 1, "message": {"chat": {"id": 777}, "text": "/start"}})
    [payload] = sent
    assert "isn't valid" in payload["text"]


# --- config.py ---------------------------------------------------------------------

def test_config_reads_convert_and_absent_reads_as_zero(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("polling:\n  fast_loop_seconds: \"45\"\n")
    rules = RulesConfig(str(path))
    value = rules.get_decimal("not", "a", "setting", default=0.5)
    assert isinstance(value, Decimal) and value == Decimal("0.5")
    assert rules.get_int("not", "a", "setting", default=None) == 0
    assert rules.get_seconds("not", "a", "setting", default=None) == 0.0
