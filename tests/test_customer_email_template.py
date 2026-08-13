from __future__ import annotations

import pytest

from sentinel.alerts.base import AlertMessage, runbook

PORTAL = "https://portal.example.test"


def _alert(severity="CRITICAL", rule="withdrawal_to_unseen_address", **kw):
    """A detection carrying the things that must not reach a customer: an
    internal rule name, a confidence token, and evidence holding an address."""
    fields = dict(
        account_label="Bitget Main",
        rule=rule,
        severity=severity,
        confidence="high",
        message="Withdrawal to address never seen before in this account's history: 0xDEADBEEF",
        evidence={"address": "0xDEADBEEF", "amount": "4.2 ETH", "balance_after": "0.1 ETH"},
    )
    fields.update(kw)
    return AlertMessage(**fields)


# --- the runbook is gated on severity ---------------------------------------


@pytest.mark.parametrize("severity", ["CRITICAL", "HIGH"])
def test_runbook_appears_where_the_advice_is_proportionate(severity):
    assert runbook() in _alert(severity=severity).render_customer()


@pytest.mark.parametrize("severity", ["INFO", "LOW", "MEDIUM", "UNKNOWN", ""])
def test_runbook_is_absent_everywhere_else(severity):
    """The INFO case is the one that shipped: a message saying 'no action
    needed' that then told the reader to freeze their account."""
    body = _alert(severity=severity).render_customer()
    assert runbook() not in body
    assert "Freeze the account" not in body
    assert "Revoke" not in body


def test_a_notice_never_carries_the_runbook_whatever_its_severity():
    """Severity on a notice is a routing artefact, not a claim about danger."""
    notice = _alert(severity="CRITICAL", rule="password_changed",
                    message="Your password was changed.", notice=True)
    assert runbook() not in notice.render_customer()


# --- no internal identifiers ------------------------------------------------


@pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "INFO", "LOW"])
def test_customer_copy_carries_no_internal_identifiers(monkeypatch, severity):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", PORTAL)
    alert = _alert(severity=severity)
    text = alert.customer_subject() + "\n" + alert.render_customer()
    assert "withdrawal_to_unseen_address" not in text
    assert "Confidence" not in text
    assert "confidence" not in text
    # Evidence is where balances and addresses live; none of it may appear.
    assert "0xDEADBEEF" not in text
    assert "4.2 ETH" not in text
    assert "balance_after" not in text
    # The free-text detail embeds the address, so it is excluded wholesale.
    assert alert.message not in text


def test_an_unmapped_rule_falls_back_rather_than_de_slugging():
    """A new detector must not be able to leak its identifier by omission."""
    alert = _alert(rule="some_brand_new_detector_v2")
    text = alert.customer_subject() + alert.render_customer()
    assert "some_brand_new_detector_v2" not in text
    assert "brand new detector" not in text.lower()
    assert "Unusual activity" in text


def test_subject_states_the_situation():
    subject = _alert().customer_subject()
    assert subject.startswith("Urgent: ")
    assert "withdrawal went to an address" in subject
    assert "Bitget Main" in subject


# --- the first line carries the message -------------------------------------


def test_first_line_carries_situation_account_and_the_action(monkeypatch):
    """Assume nothing below line one is read on a phone."""
    monkeypatch.setenv("SENTINEL_PORTAL_URL", PORTAL)
    first = _alert().render_customer().splitlines()[0]
    assert "withdrawal went to an address" in first
    assert "Bitget Main" in first
    assert PORTAL in first


# --- the portal link --------------------------------------------------------


def test_portal_link_is_included_when_the_url_is_set(monkeypatch):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", PORTAL)
    assert f"Sign in to see the details: {PORTAL}" in _alert().render_customer()


def test_trailing_slash_does_not_double_up(monkeypatch):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", PORTAL + "/")
    assert f"{PORTAL}/" not in _alert().render_customer()


def test_without_a_url_the_alert_still_says_where_to_go(monkeypatch):
    monkeypatch.delenv("SENTINEL_PORTAL_URL", raising=False)
    body = _alert().render_customer()
    assert "Sign in to Minas AI Guardian" in body
    assert "http" not in body


@pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "INFO", "LOW"])
def test_every_severity_gets_a_way_in(monkeypatch, severity):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", PORTAL)
    assert PORTAL in _alert(severity=severity).render_customer()


# --- notices keep their text ------------------------------------------------


def test_a_reset_notice_renders_its_link_verbatim():
    """The reset token exists only in this sentence - dropping the free text
    for notices, as the detection path does, silently breaks the flow."""
    link = "https://portal.example.test/reset?token=abc123"
    notice = _alert(severity="LOW", rule="password_reset_requested", notice=True,
                    message=f"Open this link to choose a new one: {link}",
                    account_label="your Minas AI Guardian login")
    body = notice.render_customer()
    assert link in body
    assert notice.customer_subject() == "Reset your Minas AI Guardian password"


def test_a_notice_survives_the_whole_path_to_the_wire(monkeypatch):
    """Renderer tests prove a notice marked as one renders verbatim.
    tests/test_account_notices.py proves every sender marks one. Neither
    covers the two layers between them - ContactRouter reading the flag off
    the detection, and EmailChannel choosing a template with it - and that is
    exactly where this broke: the flag was set and read by nobody, so a
    locked-out customer was emailed a summary telling them to sign in.

    So this one goes to the wire and reads the message that would have been
    put on it."""
    import sentinel.alerts.email as email_module

    captured = []

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            captured.append(msg)

    monkeypatch.setattr(email_module.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_SENDER", "alerts@test")

    from sentinel.alerts.contact import ContactRouter
    from sentinel.portal import _PlainNotice

    link = "https://portal.example.test/reset?token=SECRET-TOKEN"
    notice = _PlainNotice(AlertMessage(
        account_label="your Minas AI Guardian login",
        rule="password_reset_requested", severity="LOW", confidence="n/a",
        message=f"Open this link to choose a new one: {link}", evidence={},
    ))
    ContactRouter().send(notice, "your Minas AI Guardian login",
                         ["locked-out@example.test"], [])

    assert len(captured) == 1
    body = captured[0].get_content()
    assert "SECRET-TOKEN" in body
    assert captured[0]["Subject"] == "Reset your Minas AI Guardian password"
    assert "Unusual activity" not in body


def test_a_detection_on_the_same_wire_leaves_its_evidence_behind(monkeypatch):
    """The other half of the same seam. A detection must lose its free text on
    the way out, because that text embeds the withdrawal address - so the same
    path that has to preserve a notice's message has to drop a detection's."""
    import sentinel.alerts.email as email_module
    from sentinel.detectors.base import Detection

    captured = []

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            captured.append(msg)

    monkeypatch.setattr(email_module.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_SENDER", "alerts@test")

    from sentinel.alerts.contact import ContactRouter

    detection = Detection(
        rule="withdrawal_to_unseen_address", severity="CRITICAL", confidence="high",
        message="Withdrawal to address never seen before: 0xDEADBEEF",
        evidence={"address": "0xDEADBEEF", "amount": "4.2 ETH"},
    )
    ContactRouter().send(detection, "Bitget Main", ["customer@example.test"], [])

    assert len(captured) == 1
    body = captured[0].get_content()
    assert "0xDEADBEEF" not in body
    assert "4.2 ETH" not in body
    assert "withdrawal_to_unseen_address" not in captured[0]["Subject"]
    assert "A withdrawal went to an address" in body


def test_an_unmapped_notice_still_gets_a_human_subject():
    notice = _alert(rule="some_new_notice", notice=True, message="Something changed.")
    assert "some_new_notice" not in notice.customer_subject()
    assert notice.customer_subject().startswith("A change was made")


# --- the operator format is untouched ---------------------------------------


def test_operator_format_still_carries_everything_it_did():
    """Operators need the rule name, the confidence and the raw evidence, and
    Telegram/SMS read the same rendering - this is deliberately not gated."""
    alert = _alert(severity="INFO")
    body = alert.render()
    assert "withdrawal_to_unseen_address" in body
    assert "Confidence: high" in body
    assert "0xDEADBEEF" in body
    assert runbook() in body
    assert alert.operator_subject() == (
        "[Minas AI Guardian INFO] withdrawal_to_unseen_address - Bitget Main"
    )


# --- the runbook must not blind the monitoring ------------------------------


def test_the_runbook_does_not_tell_a_customer_to_revoke_our_key_blind():
    """The old step 2 said "revoke/rotate the API key(s) on this account".
    Ours is one of them: read-only, unable to move funds, and the only thing
    watching. Revoking it costs an attacker nothing and costs the customer
    their monitoring, at the one moment they need it."""
    text = runbook()
    assert "Revoke/rotate the API key(s)" not in text
    assert "Revoke any API key you do not recognise" in text
    assert "read-only" in text
    # And it must say what revoking everything costs, not merely imply it.
    assert "unwatched" in text


def test_the_runbook_says_where_to_check_the_monitoring_survived(monkeypatch):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", PORTAL)
    assert PORTAL in runbook()


def test_without_a_portal_url_the_runbook_still_names_the_step(monkeypatch):
    monkeypatch.delenv("SENTINEL_PORTAL_URL", raising=False)
    text = runbook()
    assert "http" not in text
    assert "monitoring is still running" in text
