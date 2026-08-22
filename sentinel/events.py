from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sentinel.db import Database

logger = logging.getLogger("sentinel.events")


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def append_event(
    db: Database, account_id: int, event_type: str, severity: str, payload: dict,
    actor: str | None = None,
) -> int:
    """Writes one row to the append-only event log. The application must
    never UPDATE or DELETE the events table - it is the forensic record that
    baselines and alert evidence are derived from.

    `actor` is the operator username where a person did this, and None where
    nothing did - a detector firing has nobody behind it (G-50). It stays
    nullable for exactly that reason: filling it in with a guess would make the
    log say a person acted when none did, which is worse than saying nothing."""
    now = datetime.now(timezone.utc).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO events (account_id, event_type, severity, payload_json, created_at, actor) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, event_type, severity, json.dumps(payload, cls=_DecimalEncoder), now, actor),
        )
        return cur.lastrowid


# Event types that are a change to how an account is watched or reached, as
# opposed to something a detector saw. `/activity` in the portal shows exactly
# these (G-32, #36): a customer asking "who has touched my account" is asking
# about settings and administration, and mixing detections in would bury the
# one page where an unauthorised change is supposed to stand out.
#
# A allowlist rather than a denylist of detector rules. Detector rules are
# added often and this list is not, so a denylist would silently start showing
# every new detection here - and a page that fills with noise is a page nobody
# reads, which is how an unauthorised change goes unnoticed.
ADMIN_EVENT_TYPES = (
    "customer_added",
    "alert_contact_updated",
    "secondary_contact_updated",
    "sms_alerts_changed",
    "member_portal_password_generated",
    "member_password_changed",
    "member_two_factor_enabled",
    "member_two_factor_disabled",
    "member_telegram_connected",
    "member_telegram_disconnected",
    "digest_frequency_changed",
    "monitoring_key_replaced",
)

# How each reads to a customer. The dashboard shows event_type; this page is
# read by somebody deciding whether to phone their exchange, so it says what
# happened in words.
ADMIN_EVENT_LABELS = {
    "customer_added": "Account added to monitoring",
    "alert_contact_updated": "Alert destination changed",
    "secondary_contact_updated": "Emergency contact changed",
    "sms_alerts_changed": "SMS alert preference changed",
    "member_portal_password_generated": "Portal password issued by Minas",
    "member_password_changed": "Portal password changed",
    "member_two_factor_enabled": "Two-factor turned on",
    "member_two_factor_disabled": "Two-factor turned off",
    "member_telegram_connected": "Telegram alerts connected",
    "member_telegram_disconnected": "Telegram alerts disconnected",
    "digest_frequency_changed": "Digest schedule changed",
    "monitoring_key_replaced": "Monitoring key replaced",
}


def notify_alert_destination_change(db: Database, account_label: str,
                                    old_email: str | None, new_email: str | None) -> None:
    """Tell the OLD address that the alerts have been redirected (G-32, #36).

    This is the one settings change the activity page cannot show the person
    who most needs it. Portal scope is the contact email, so the moment the
    destination changes, the previous contact stops seeing that account at all
    - including the record of the change that removed them. An attacker who
    redirects the alerts silently removes the evidence from the only page
    built to reveal it.

    So the notice goes to the address being removed, the way the
    password-change tripwire does. Not a receipt: it is the last message that
    address will ever get about this account, and it is sent precisely because
    somebody may not have authorised it.

    Guarded like every other notice on a change path: a mail failure is logged,
    never raised. The change has already happened, and an exception here would
    make a dead SMTP server look like a failed settings update."""
    if not old_email or old_email == new_email:
        return
    from sentinel.alerts.base import AlertMessage
    from sentinel.alerts.contact import ContactRouter
    from sentinel.portal import _PlainNotice

    destination = new_email or "no email address"
    message = AlertMessage(
        account_label=account_label,
        rule="alert_destination_changed",
        severity="LOW",
        confidence="n/a",
        message=(
            f"The alert destination for '{account_label}' was changed from {old_email} to "
            f"{destination}. Alerts about this account will no longer be sent to this address, "
            f"and this address can no longer see the account in the Minas portal.\n\n"
            f"If you did not ask for this, contact Minas immediately - redirecting the alerts "
            f"is what somebody does before moving the funds."
        ),
        evidence={},
    )
    try:
        ContactRouter(db=db).send(
            _PlainNotice(message), account_label, [old_email], [],
        )
    except Exception as exc:  # noqa: BLE001 - the change has already happened
        logger.error("could not send the destination-change notice to %s: %s", old_email, exc)


def notify_secondary_contact_added(db: Database, account_label: str,
                                   email: str | None, rules=None) -> None:
    """The one-time "you are listed as an emergency contact" note (#89).

    The complaint that produced decision 1 (#27) came from a secondary
    contact who did not know what her role meant - nobody should depend on
    the person who added them remembering to say so. Fired from EVERY writer
    of `alert_email_secondary` (onboarding and `set-contact`), exactly once
    per newly-added address: the callers gate on an actual change, so a
    re-run with the same values sends nothing.

    Content rules are decision 1's, same as every secondary-facing message:
    it says why she is receiving it, states the windows in the terms the
    Escalator actually uses (read from rules when the caller has them, the
    schema defaults otherwise - production runs the defaults), and carries
    no balances, no amounts, no addresses, and no link to click.

    Guarded like every notice on a change path: a send failure is logged,
    never raised - the edit has already happened, and a dead SMTP server
    must not look like a failed onboarding or settings change."""
    if not email:
        return
    from sentinel.alerts.base import AlertMessage
    from sentinel.alerts.contact import ContactRouter
    from sentinel.portal import _PlainNotice
    from sentinel.rules_schema import default_for

    critical_min = default_for("alerting", "escalation_unacknowledged_minutes")
    high_min = default_for("alerting", "escalation_unacknowledged_minutes_high")
    if rules is not None:
        critical_min = rules.get("alerting", "escalation_unacknowledged_minutes",
                                 default=critical_min)
        high_min = rules.get("alerting", "escalation_unacknowledged_minutes_high",
                             default=high_min)
    critical_min, high_min = int(critical_min), int(high_min)

    message = AlertMessage(
        account_label=account_label,
        rule="secondary_contact_added",
        severity="LOW",
        confidence="n/a",
        message=(
            f"You have been listed as the emergency contact for '{account_label}', an "
            f"account monitored by Minas AI Guardian. You are receiving this single note "
            f"so the role never surprises you.\n\n"
            f"What it means: normally you will hear nothing at all. We contact you only "
            f"when the account's primary contact has not responded - after "
            f"{high_min} minutes for a high-severity alert, and after {critical_min} "
            f"minutes for a critical one. When a critical alert you were told about is "
            f"acknowledged, you will get one closing line so you are not left "
            f"wondering.\n\n"
            f"Messages in this role never contain balances, amounts, or account "
            f"addresses, and none of them will ever ask you to click a link or act on "
            f"the account. If you did not expect to be listed for this account, "
            f"contact Minas."
        ),
        evidence={},
    )
    try:
        ContactRouter(db=db).send(
            _PlainNotice(message), account_label, [email], [],
        )
    except Exception as exc:  # noqa: BLE001 - the edit has already happened
        logger.error("could not send the emergency-contact note to %s: %s", email, exc)


def record_member_security_event(db: Database, email: str, event_type: str,
                                 payload: dict | None = None) -> int:
    """Record a change to a member's LOGIN against every account that login
    opens. Returns the number of rows written (G-32, #36).

    A password change or a second factor being removed is not a change to an
    account, and it is the single most useful line on "who has touched my
    account" - it is what an attacker does after getting in and before doing
    anything else. `events.account_id` is NOT NULL because the log is
    per-account, so this fans out rather than relaxing the schema: one row per
    account the login reaches, which is also the honest scope, since that is
    exactly what the change affects.

    Nothing is written when the address opens no accounts. A login with no
    accounts has nowhere to record this, and inventing a row would be
    inventing an account."""
    from sentinel.portal import member_accounts

    written = 0
    for account in member_accounts(db, email):
        append_event(db, account["id"], event_type, "LOW",
                     {**(payload or {}), "login": (email or "").strip().lower()},
                     actor=(email or "").strip().lower())
        written += 1
    return written


def record_config_changes(db: Database, changes, actor: str | None = None) -> int:
    """One row per changed threshold (G-12, #16). Returns the number written.

    `actor` is the operator who made the change where the change came through a
    path that carries an identity, and None where it did not. A file edited on
    a server carries no identity, and `None` is the truthful answer - see the
    note on `config_changes` in schema.sql for why it is not filled in."""
    now = datetime.now(timezone.utc).isoformat()
    rows = [(setting, before, after, actor, now) for setting, before, after in changes]
    if not rows:
        return 0
    with db.cursor() as cur:
        cur.executemany(
            "INSERT INTO config_changes (setting, old_value, new_value, actor, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    for setting, before, after in changes:
        logger.info(
            "config change: %s %s -> %s (%s)",
            setting, before, after, actor or "unattributed - edited on disk",
        )
    return len(rows)
