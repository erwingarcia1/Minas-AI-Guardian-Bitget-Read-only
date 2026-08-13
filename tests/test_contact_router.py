from __future__ import annotations

from sentinel.detectors.base import Detection


def _detection():
    return Detection(rule="withdrawal_initiated", severity="CRITICAL", confidence="high",
                     message="m", evidence={"order_id": "wd-1"})


class _RecEmail:
    instances = []

    def __init__(self, host, port, user, pw, sender, recipients, *, audience="operator"):
        self.recipients = recipients
        self.audience = audience
        _RecEmail.instances.append(self)

    def send(self, alert):
        self.sent = alert


class _RecSms:
    instances = []

    def __init__(self, recipients):
        self.recipients = recipients
        _RecSms.instances.append(self)

    def send(self, alert):
        self.sent = alert


def _router(monkeypatch, smtp=True, sms=True):
    _RecEmail.instances = []
    _RecSms.instances = []
    monkeypatch.setattr("sentinel.alerts.contact.EmailChannel", _RecEmail)
    # SMS goes through build_sms_channel now (ClickSend-preferred, Twilio fallback).
    monkeypatch.setattr(
        "sentinel.alerts.contact.build_sms_channel",
        (lambda recipients: _RecSms(recipients) if (sms and recipients) else None),
    )
    if smtp:
        monkeypatch.setenv("SMTP_HOST", "smtp.test")
        monkeypatch.setenv("SMTP_SENDER", "alerts@test")
    else:
        monkeypatch.delenv("SMTP_HOST", raising=False)
    from sentinel.alerts.contact import ContactRouter
    return ContactRouter()


def test_sends_to_primary_and_secondary_email(monkeypatch):
    r = _router(monkeypatch)
    r.send(_detection(), "acct", ["a@x.com", "b@x.com"], [])
    assert len(_RecEmail.instances) == 1
    assert _RecEmail.instances[0].recipients == ["a@x.com", "b@x.com"]


def test_per_account_email_uses_the_customer_template(monkeypatch):
    """The audience is the whole guard against posting rule names, confidence
    and raw evidence to a customer. Asserted here rather than only on the
    rendering, because this is the wiring that decides which one is used."""
    r = _router(monkeypatch)
    r.send(_detection(), "acct", ["a@x.com"], [])
    assert _RecEmail.instances[0].audience == "customer"


def test_sends_sms_to_all_phones_in_one_channel(monkeypatch):
    r = _router(monkeypatch)
    r.send(_detection(), "acct", [], ["+15551112222", "+15553334444"])
    assert len(_RecSms.instances) == 1
    assert _RecSms.instances[0].recipients == ["+15551112222", "+15553334444"]


def test_dedupes_and_drops_blanks(monkeypatch):
    r = _router(monkeypatch)
    r.send(_detection(), "acct", ["a@x.com", "a@x.com", None, ""], [])
    assert _RecEmail.instances[0].recipients == ["a@x.com"]


def test_no_contacts_is_noop(monkeypatch):
    r = _router(monkeypatch)
    r.send(_detection(), "acct", [], [])
    assert not _RecEmail.instances and not _RecSms.instances


def test_unconfigured_transport_is_noop_not_error(monkeypatch):
    r = _router(monkeypatch, smtp=False, sms=False)
    r.send(_detection(), "acct", ["a@x.com"], ["+15551112222"])  # must not raise
    assert not _RecEmail.instances and not _RecSms.instances


def test_team_telegram_channel_wired_when_env_set(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM_TEAM_CHAT_ID", "-100999")
    from sentinel.cli import _build_named_channels
    named = _build_named_channels()
    assert "telegram" in named and "telegram_team" in named


def test_team_telegram_absent_when_env_unset(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.delenv("TELEGRAM_TEAM_CHAT_ID", raising=False)
    from sentinel.cli import _build_named_channels
    named = _build_named_channels()
    assert "telegram" in named and "telegram_team" not in named
