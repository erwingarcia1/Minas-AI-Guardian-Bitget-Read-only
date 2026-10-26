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

**Nothing that names them survives anywhere else (decision 6b, 2026-10-26).**
The person's address, phone numbers, Telegram identity, phone names and
account names also sit in rows that belong to other people's accounts: the
"emergency contact changed" entries, their own password changes and
sign-ins (recorded on every account their login opens), deliveries to
them, the snoozes and marks they made. Those rows are the other customer's
history and stay - what changed, and when - with "[erased]" in place of
the details, and one entry on each such account says so. The activity
page names this as the one exception to "never edited".
"""
from __future__ import annotations

import json
import logging
import re
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
                 ("telegram_links", "member_email"), ("password_resets", "email"),
                 ("api_devices", "email"))

# What a redacted detail reads as, and who an erasure the person asked for
# themselves is recorded against: their address is exactly what went.
ERASED = "[erased]"


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
        # Which phones held this login, and when (decision 21c). Never the
        # token hashes: they are credentials, like the API key above.
        cur.execute("SELECT name, created_at, last_refreshed_at, revoked_at, revoked_reason "
                    "FROM api_devices WHERE email = ? ORDER BY id", (normalized,))
        out["app_devices"] = [dict(r) for r in cur.fetchall()]
    return out


class _Redactor:
    """Finds one person's details in a stored value and replaces them with
    ERASED, and nothing else (decision 6b).

    Precision is the point: these rows are somebody else's history. An
    address matches only as a whole address (al@x.com is not inside
    sal@x.com), a phone number or chat id only where no digit adjoins it (a
    chat id inside a longer amount is an amount), and a phone's name only
    beside the person's own address in the same entry - "iPhone" alone
    names nobody. An account's name matches only in rows that belong to no
    account: it is a word the customer chose, and on a live account the same
    word means something else."""

    def __init__(self, email: str, phones, handles, chats, devices, labels):
        self.email = re.compile(r"(?<![\w.%+-])" + re.escape(email) + r"(?![\w-]|\.\w)", re.IGNORECASE)
        # Six digits at least: a real phone number or Telegram chat id has
        # more, and a stray "+1" in a contact column must not blank every
        # "+1" in somebody else's history.
        numbers = [p for p in {*phones, *chats} if p and sum(ch.isdigit() for ch in p) >= 6]
        self.numbers = [re.compile(r"(?<!\d)" + re.escape(n) + r"(?!\d)") for n in numbers]
        self.number_values = set(numbers)
        names = {h.lstrip("@") for h in handles if h and len(h.lstrip("@")) >= 3}
        self.handles = [re.compile(r"(?<![\w@])@?" + re.escape(n) + r"(?!\w)", re.IGNORECASE) for n in names]
        self.labels = [re.compile(r"(?<![\w-])" + re.escape(lb) + r"(?![\w-])", re.IGNORECASE)
                       for lb in labels if lb and len(lb) >= 3]
        self.devices = {d for d in devices if d}
        # Lower-case substrings that every candidate row must contain one
        # of, so SQLite does the scanning and Python sees only real hits.
        self.needles = sorted({email.lower(), *(n.lower() for n in numbers), *(n.lower() for n in names),
                               *(lb.lower() for lb in labels if lb and len(lb) >= 3)})

    def text(self, value: str, *, labels: bool = True) -> str:
        out = self.email.sub(ERASED, value)
        for pattern in self.numbers + self.handles + (self.labels if labels else []):
            out = pattern.sub(ERASED, out)
        return out

    def value(self, value, *, labels: bool):
        if isinstance(value, str):
            return self.text(value, labels=labels)
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return ERASED if str(value) in self.number_values else value
        if isinstance(value, list):
            return [self.value(v, labels=labels) for v in value]
        if isinstance(value, dict):
            beside = any(isinstance(v, str) and self.email.search(v) for v in value.values())
            return {(self.text(k, labels=labels) if isinstance(k, str) else k):
                    (ERASED if beside and isinstance(v, str) and v in self.devices
                     else self.value(v, labels=labels))
                    for k, v in value.items()}
        return value

    def stored(self, column: str, value: str, *, labels: bool) -> str:
        if column.endswith("_json"):
            try:
                parsed = json.loads(value)
            except ValueError:
                return self.text(value, labels=labels)
            redacted = self.value(parsed, labels=labels)
            return value if redacted == parsed else json.dumps(redacted)
        return self.text(value, labels=labels)


def _text_columns(cur, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [r["name"] for r in cur.fetchall()
            if (r["type"] or "TEXT").upper() in ("TEXT", "")
            and not r["name"].startswith("encrypted_") and not r["name"].endswith("_hash")]


def _redact_everywhere(cur, redactor: _Redactor, erased_ids: set[int]) -> tuple[dict, dict]:
    """Replace the person's details in every table but `accounts`, whose
    contact columns the caller has already blanked. Every table, so one
    added later is covered without anybody remembering to list it.

    Returns (rows per table, {account id: rows per table}) - the second for
    the live accounts touched, which each get an entry saying so. A row a
    UNIQUE constraint will not take back with ERASED in it (two erased
    people's marks on one alert) is deleted instead: a duplicate of a row
    that already says ERASED says nothing more."""
    totals: dict[str, int] = {}
    per_account: dict[int, dict[str, int]] = {}
    cur.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "AND name != 'accounts' ORDER BY name")
    for table in [r["name"] for r in cur.fetchall()]:
        columns = _text_columns(cur, table)
        if not columns:
            continue
        cur.execute(f"PRAGMA table_info({table})")
        owned = any(r["name"] == "account_id" for r in cur.fetchall())
        match = " OR ".join(f"instr(lower({c}), ?) > 0" for c in columns for _ in redactor.needles)
        params = [n for _ in columns for n in redactor.needles]
        cur.execute(f"SELECT rowid AS _rowid, {'account_id, ' if owned else ''}{', '.join(columns)} "
                    f"FROM {table} WHERE {match}", params)
        for row in cur.fetchall():
            account_id = row["account_id"] if owned else None
            changes = {}
            for c in columns:
                if row[c] is None:
                    continue
                new = redactor.stored(c, row[c], labels=account_id is None)
                if new != row[c]:
                    changes[c] = new
            if not changes:
                continue
            try:
                cur.execute(f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in changes)} WHERE rowid = ?",
                            [*changes.values(), row["_rowid"]])
            except Exception as exc:  # noqa: BLE001 - sqlite3.IntegrityError, without importing the driver
                if "UNIQUE" not in str(exc):
                    raise
                cur.execute(f"DELETE FROM {table} WHERE rowid = ?", (row["_rowid"],))
            totals[table] = totals.get(table, 0) + 1
            if account_id is not None and account_id not in erased_ids:
                mine = per_account.setdefault(int(account_id), {})
                mine[table] = mine.get(table, 0) + 1
    return totals, per_account


def _redactor_for(cur, normalized: str, scope: dict) -> _Redactor:
    """Everything that names this person, read before anything is deleted.
    The phone and handle on an account they are the primary of are theirs;
    on one they are the second contact of, the secondary columns are."""
    phones, handles = set(), set()
    for acct in scope["primary"]:
        cur.execute("SELECT alert_phone, telegram_handle FROM accounts WHERE id = ?", (int(acct["id"]),))
        row = cur.fetchone()
        phones.add(row["alert_phone"])
        handles.add(row["telegram_handle"])
    for acct in scope["secondary"]:
        cur.execute("SELECT alert_phone_secondary, telegram_handle_secondary FROM accounts WHERE id = ?",
                    (int(acct["id"]),))
        row = cur.fetchone()
        phones.add(row["alert_phone_secondary"])
        handles.add(row["telegram_handle_secondary"])
    cur.execute("SELECT chat_id FROM telegram_links WHERE member_email = ?", (normalized,))
    chats = {r["chat_id"] for r in cur.fetchall()}
    cur.execute("SELECT name FROM api_devices WHERE email = ?", (normalized,))
    devices = {r["name"] for r in cur.fetchall()}
    return _Redactor(normalized, phones, handles, chats, devices, [a["label"] for a in scope["primary"]])


def erase_member(db: Database, email: str, *, actor: str | None, notify=None) -> dict:
    """The interim erasure. Returns the gap record that was written.

    Order: capture what will be removed and what names the person ->
    delete -> redact their details from everybody else's rows -> tombstone
    gap and the entries that say so -> confirmation (to the address
    captured first, so the person is told even though we no longer hold
    their address anywhere but in this call's stack) -> redact once more,
    because sending the confirmation records a delivery to that address."""
    normalized = _norm(email)
    if not normalized:
        raise ValueError("erasure needs an address")
    scope = erasure_scope(db, normalized)
    if not scope["primary"] and not scope["secondary"] and not scope["login"]:
        raise ValueError("nothing is held for this address")
    stamp = _now()
    removed: dict[str, int] = {}
    erased_accounts = []
    # A self-service erasure is recorded against ERASED, not the address:
    # the address is what was erased. An operator's is recorded against the
    # operator, as everything else they do is.
    recorded_actor = ERASED if actor == normalized else actor

    def count(table, n):
        removed[table] = removed.get(table, 0) + int(n)

    with db.cursor() as cur:
        redactor = _redactor_for(cur, normalized, scope)
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
        redacted, touched = _redact_everywhere(cur, redactor, {a["id"] for a in erased_accounts})

    from sentinel.events import append_event

    # The stored gap carries account ids and counts, never the accounts'
    # names: a name is usually the customer's own. The confirmation, which
    # goes to the person, still names them.
    gap = {
        "gap": True,
        "erased_at": stamp,
        "requested_by": "the account's primary contact" if actor == normalized else (actor or "operator"),
        "accounts": [{"id": a["id"]} for a in erased_accounts],
        "rows_removed": {k: v for k, v in removed.items() if v},
        "rows_redacted_elsewhere": redacted,
        "note": ("Records were deleted at the customer's request under the interim erasure decision "
                 "(decision 6, 2026-08-27). What this account did before this moment cannot be "
                 "reconstructed from Minas's records. This entry is the record that a deletion happened."),
    }
    for acct in erased_accounts:
        append_event(db, acct["id"], "data_erased", "LOW", gap, actor=recorded_actor)
    for acct in scope["secondary"]:
        append_event(db, int(acct["id"]), "alert_contact_updated", "LOW",
                     {"secondary_removed": True, "reason": "erasure request by the secondary contact"},
                     actor=recorded_actor)
    for account_id, rows in sorted(touched.items()):
        append_event(db, account_id, "contact_details_erased", "LOW", {
            "rows_redacted": rows,
            "note": ("A person named in this account's records asked Minas to delete their data "
                     "(decisions 6 and 6b). Their email address, phone number, Telegram identity and "
                     "phone names were replaced with [erased] in these records; the records are "
                     "otherwise unchanged."),
        }, actor=recorded_actor)
    logger.warning("erasure: %d account(s) erased (ids %s), %d secondary contact(s) removed, "
                   "%d record(s) on other accounts redacted, rows=%s",
                   len(erased_accounts), [a["id"] for a in erased_accounts], len(scope["secondary"]),
                   sum(redacted.values()), gap["rows_removed"])
    if notify is not None:
        try:
            notify(normalized, {**gap, "accounts": erased_accounts})
        except Exception as exc:  # noqa: BLE001 - the erasure has happened; the notice must not undo it
            logger.error("erasure confirmation failed: %s", redactor.text(str(exc)))
        finally:
            with db.cursor() as cur:
                _redact_everywhere(cur, redactor, set())
    return gap


def confirmation_text(gap: dict) -> str:
    labels = ", ".join(a["label"] for a in gap["accounts"]) or "no monitored account"
    total = sum(gap["rows_removed"].values())
    elsewhere = sum((gap.get("rows_redacted_elsewhere") or {}).values())
    return (
        "Your Minas AI Guardian data has been deleted, as you asked.\n\n"
        f"Deleted now: the monitoring credentials and every record for {labels} "
        f"({total} stored records), your portal login and its settings, and your contact details.\n\n"
        + (f"Where you were a contact on somebody else's account, or could open one, that account's "
           f"history keeps its entries, with [erased] in place of your email address, phone number "
           f"and Telegram identity ({elsewhere} records).\n\n" if elsewhere else "")
        + "What remains: one entry in our records stating that a deletion happened at your request, "
        "with the date and the number of records removed - nothing that identifies you or "
        "describes what your accounts did. Nothing before that moment can be reconstructed by us.\n\n"
        "Backups made before today and our server logs are not edited, and still hold a copy.\n\n"
        "Monitoring of those accounts has stopped. Contact Minas if you did not ask for this."
    )
