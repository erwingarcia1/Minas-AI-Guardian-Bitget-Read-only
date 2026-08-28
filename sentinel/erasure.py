"""Erasure on request (#46), the interim answer under decision 6 (2026-08-27).

The lawyer's question - whether crypto-shredding counts as erasure - stays
open and does not block this. The interim answer is the honest one:

**Hard-delete on request.** Credentials go immediately; everything else the
customer's accounts produced goes with them, now, not "within a period".

**Record the forensic gap explicitly, AS a gap.** The append-only event log
is the product's forensic record, and deleting a customer's rows from it is a
hole. So the account row stays as a tombstone - label replaced, credentials
and contacts blanked, status disabled - and carries one final event,
`data_erased`, that says what was removed, how much, when, and at whose
request, and that nothing before it can be reconstructed from Minas's
records. Anyone reading the log later finds a gap that says it is a gap,
never silence that looks like nothing happened.

**Tell the customer that is what happened.** A written confirmation goes to
the address that asked, sent while we still hold it, saying what was
deleted and what remains (the tombstone and its one event).

**Scope is the person's own accounts.** A member may erase the accounts they
are the PRIMARY contact of, and their own login. On accounts where they are
only the secondary contact, they are removed as a contact - the primary's
data is not theirs to delete.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sentinel.db import Database

logger = logging.getLogger("sentinel.erasure")

# Rows referencing alerts(id) must go before the alerts; rows referencing
# accounts(id) before the account is tombstoned. Order matters for the
# foreign keys and for the honesty of the counts.
ALERT_CHILDREN = ("alert_triage", "expected_activity", "deliveries", "held_alerts",
                  "drills")
ACCOUNT_CHILDREN = ("events", "known_addresses", "seen_withdrawals", "balance_snapshots",
                    "permission_snapshots", "poll_state", "traded_pairs", "seen_fills",
                    "seen_transfers", "transfer_routes", "operator_pages", "fee_baseline",
                    "deliveries", "member_account_grants", "drills", "pause_requests",
                    "account_threshold_overrides", "snoozes", "expected_activity")
MEMBER_TABLES = (("portal_members", "email"), ("member_account_grants", "email"),
                 ("held_alerts", "email"), ("deliveries", "recipient"),
                 ("telegram_links", "member_email"), ("password_resets", "email"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def erasure_scope(db: Database, email: str) -> dict:
    """What an erasure for this address would touch: accounts it is the
    primary contact of (erased), accounts it is only the secondary on
    (contact removed), and whether a portal login exists."""
    normalized = _norm(email)
    if not normalized:
        return {"primary": [], "secondary": [], "login": False}
    with db.cursor() as cur:
        cur.execute("SELECT id, label FROM accounts WHERE lower(alert_email) = ? AND erased_at IS NULL "
                    "ORDER BY id", (normalized,))
        primary = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT id, label FROM accounts WHERE lower(alert_email_secondary) = ? "
                    "AND lower(coalesce(alert_email, '')) != ? AND erased_at IS NULL ORDER BY id",
                    (normalized, normalized))
        secondary = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT 1 FROM portal_members WHERE email = ?", (normalized,))
        login = cur.fetchone() is not None
    return {"primary": primary, "secondary": secondary, "login": login}


def export_member_data(db: Database, email: str) -> dict:
    """Everything we hold about this person's own accounts, as plain data,
    for download BEFORE erasure (criterion 3). Credentials are never
    exported - they are encrypted at rest and belong to the exchange."""
    normalized = _norm(email)
    scope = erasure_scope(db, normalized)
    ids = [a["id"] for a in scope["primary"]]
    out = {"exported_at": _now(), "email": normalized, "accounts": [], "secondary_contact_on": scope["secondary"]}
    with db.cursor() as cur:
        for aid in ids:
            cur.execute("SELECT id, label, exchange, topology, status, onboarded_at, alert_email, alert_phone, "
                        "alert_email_secondary, alert_phone_secondary FROM accounts WHERE id = ?", (aid,))
            acct = dict(cur.fetchone())
            cur.execute("SELECT event_type, severity, payload_json, actor, created_at FROM events "
                        "WHERE account_id = ? ORDER BY id", (aid,))
            acct["events"] = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT id, rule, severity, message, evidence_json, sent_at, delivered_to, "
                        "acknowledged_at, acknowledged_by FROM alerts WHERE account_id = ? ORDER BY id", (aid,))
            acct["alerts"] = [dict(r) for r in cur.fetchall()]
            out["accounts"].append(acct)
        cur.execute("SELECT timezone, quiet_hours_enabled, quiet_start, quiet_end, channel_prefs_json, "
                    "digest_frequency, created_at, last_login_at FROM portal_members WHERE email = ?", (normalized,))
        row = cur.fetchone()
        out["login"] = dict(row) if row else None
    return out


def erase_member(db: Database, email: str, *, actor: str | None, notify=None) -> dict:
    """The interim erasure. Returns the gap record that was written.

    Order: capture what will be removed -> delete -> tombstone + gap event
    -> confirmation (to the address captured first, so the person is told
    even though we no longer hold their address anywhere but in this
    call's stack)."""
    normalized = _norm(email)
    if not normalized:
        raise ValueError("erasure needs an address")
    scope = erasure_scope(db, normalized)
    if not scope["primary"] and not scope["secondary"] and not scope["login"]:
        raise ValueError("nothing is held for this address")
    stamp = _now()
    removed: dict[str, int] = {}
    erased_accounts = []

    def count(table, n):
        removed[table] = removed.get(table, 0) + int(n)

    with db.cursor() as cur:
        for acct in scope["primary"]:
            aid = int(acct["id"])
            cur.execute("SELECT id FROM alerts WHERE account_id = ?", (aid,))
            alert_ids = [int(r["id"]) for r in cur.fetchall()]
            if alert_ids:
                marks = ",".join("?" for _ in alert_ids)
                for table in ALERT_CHILDREN:
                    cur.execute(f"DELETE FROM {table} WHERE alert_id IN ({marks})", alert_ids)
                    count(table, cur.rowcount)
            for table in ACCOUNT_CHILDREN:
                cur.execute(f"DELETE FROM {table} WHERE account_id = ?", (aid,))
                count(table, cur.rowcount)
            cur.execute("DELETE FROM alerts WHERE account_id = ?", (aid,))
            count("alerts", cur.rowcount)
            # The tombstone: the row survives so the gap has somewhere to
            # live; nothing identifying or usable survives on it.
            cur.execute(
                "UPDATE accounts SET label = ?, encrypted_api_key = '', encrypted_secret = '', "
                "encrypted_passphrase = '', alert_email = NULL, alert_phone = NULL, customer_name = NULL, "
                "alert_email_secondary = NULL, alert_phone_secondary = NULL, telegram_handle = NULL, "
                "telegram_handle_secondary = NULL, license = NULL, status = 'disabled', is_drill = 0, "
                "erased_at = ?, updated_at = ? WHERE id = ?",
                (f"erased-{aid}", stamp, stamp, aid))
            erased_accounts.append({"id": aid, "label": acct["label"]})
        for acct in scope["secondary"]:
            cur.execute("UPDATE accounts SET alert_email_secondary = NULL, alert_phone_secondary = NULL, "
                        "telegram_handle_secondary = NULL, updated_at = ? WHERE id = ?", (stamp, int(acct["id"])))
        for table, column in MEMBER_TABLES:
            cur.execute(f"DELETE FROM {table} WHERE {column} = ?", (normalized,))
            count(table, cur.rowcount)
        for table in ("totp_enrolments", "totp_recovery_codes"):
            cur.execute(f"DELETE FROM {table} WHERE subject_kind = 'member' AND subject = ?", (normalized,))
            count(table, cur.rowcount)

    from sentinel.events import append_event

    gap = {
        "gap": True,
        "erased_at": stamp,
        "requested_by": "the account's primary contact" if actor == normalized else (actor or "operator"),
        "accounts": erased_accounts,
        "rows_removed": {k: v for k, v in removed.items() if v},
        "note": ("Records were deleted at the customer's request under the interim erasure decision "
                 "(decision 6, 2026-08-27). What this account did before this moment cannot be "
                 "reconstructed from Minas's records. This entry is the record that a deletion happened."),
    }
    for acct in erased_accounts:
        append_event(db, acct["id"], "data_erased", "LOW", gap, actor=actor)
    for acct in scope["secondary"]:
        append_event(db, int(acct["id"]), "alert_contact_updated", "LOW",
                     {"secondary_removed": True, "reason": "erasure request by the secondary contact"},
                     actor=actor)
    logger.warning("erasure for %s: %d account(s) erased, %d secondary contact(s) removed, rows=%s",
                   normalized, len(erased_accounts), len(scope["secondary"]), gap["rows_removed"])
    if notify is not None:
        try:
            notify(normalized, gap)
        except Exception as exc:  # noqa: BLE001 - the erasure has happened; the notice must not undo it
            logger.error("erasure confirmation to %s failed: %s", normalized, exc)
    return gap


def confirmation_text(gap: dict) -> str:
    labels = ", ".join(a["label"] for a in gap["accounts"]) or "no monitored account"
    total = sum(gap["rows_removed"].values())
    return (
        "Your Minas AI Guardian data has been deleted, as you asked.\n\n"
        f"Deleted now: the monitoring credentials and every record for {labels} "
        f"({total} stored records), your portal login and its settings, and your contact details.\n\n"
        "What remains: one entry in our records stating that a deletion happened at your request, "
        "with the date and the number of records removed - nothing that identifies you or "
        "describes what your accounts did. Nothing before that moment can be reconstructed by us.\n\n"
        "Monitoring of those accounts has stopped. Contact Minas if you did not ask for this."
    )
