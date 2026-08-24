PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE,
    exchange TEXT NOT NULL DEFAULT 'bitget' CHECK (exchange IN ('bitget', 'bybit', 'okx', 'binance')),
    topology TEXT NOT NULL CHECK (topology IN ('independent', 'sub_account')),
    encrypted_api_key TEXT NOT NULL,
    encrypted_secret TEXT NOT NULL,
    -- Empty (encrypted) for exchanges without an API passphrase, e.g. Bybit.
    encrypted_passphrase TEXT NOT NULL,
    -- Customer / contact details captured at onboarding.
    customer_name TEXT,
    -- Optional per-account alert destination (in addition to severity routing).
    alert_email TEXT,
    alert_phone TEXT,
    alert_email_secondary TEXT,
    alert_phone_secondary TEXT,
    telegram_handle TEXT,
    telegram_handle_secondary TEXT,
    license TEXT,
    -- Member SMS notification preference. 1 = send SMS alerts (default);
    -- 0 = opted out. CRITICAL alerts may still be sent when
    -- alerting.sms_critical_overrides_optout is true (business rule).
    sms_alerts_enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'tamper_suspected', 'disabled')),
    -- The exchange's own id for this account (Bitget userId, OKX uid, ...),
    -- learned from an account-info payload and refreshed by the slow loop.
    -- NULL means "not yet learned", not "none": accounts predating this column
    -- fill it in on their next poll. It exists so a replacement key (#69) can
    -- be proved to be for THIS account - a read-only key for somebody else's
    -- account passes every other check and leaves the real one unwatched.
    exchange_uid TEXT,
    onboarded_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Append-only forensic log. The application never issues UPDATE or DELETE
-- against a row's contents - what was recorded is never edited, which is the
-- property that makes it evidence.
--
-- Retention (#60) does delete whole rows once they pass their configured age.
-- That is a different act from rewriting one: nothing that survives has been
-- altered. The window is deliberately long (two years by default) because #29's
-- incident timeline reads this table, and it is configurable so the legal
-- retention decision sets it without a deploy.
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_account ON events(account_id, created_at);

CREATE TABLE IF NOT EXISTS known_addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    address TEXT NOT NULL,
    coin TEXT,
    source TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    UNIQUE(account_id, address)
);

CREATE TABLE IF NOT EXISTS seen_withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    order_id TEXT NOT NULL,
    status TEXT NOT NULL,
    coin TEXT,
    size TEXT,
    address TEXT,
    raw_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    UNIQUE(account_id, order_id)
);

CREATE TABLE IF NOT EXISTS balance_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    coin TEXT NOT NULL,
    quantity TEXT NOT NULL,
    snapshot_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_balance_account_coin ON balance_snapshots(account_id, coin, snapshot_at);
-- Serves latest_balance_snapshot's MAX(snapshot_at)-per-account lookup; the
-- coin-first index above can't (snapshot_at is its 3rd column).
CREATE INDEX IF NOT EXISTS idx_balance_account_time ON balance_snapshots(account_id, snapshot_at);

CREATE TABLE IF NOT EXISTS permission_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    authorities TEXT,
    ip_whitelist TEXT,
    snapshot_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poll_state (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
    consecutive_auth_failures INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_success_at TEXT,
    last_fast_loop_at TEXT,
    last_slow_loop_at TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    rule TEXT NOT NULL,
    severity TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    confidence TEXT NOT NULL,
    -- How many channels accepted this alert. 0 means it was detected and
    -- reached nobody, which is a different thing from not being detected and
    -- must stay visible as such (#57).
    delivered_to INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT NOT NULL,
    acknowledged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_dedup ON alerts(account_id, dedup_key, sent_at);

CREATE TABLE IF NOT EXISTS traded_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    symbol TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    UNIQUE(account_id, symbol)
);

-- Fills already judged for off-market execution and fee spend. Identity, not
-- time: an exchange that omits a fill's timestamp used to drop it out of the
-- comparison window entirely, so it was never price-checked (G-47). Judging by
-- "have I seen this record before" instead means an undated fill is examined
-- exactly once. `fill_key` is the exchange's order id where there is one and a
-- content digest where there isn't - see baseline.fill_identity.
CREATE TABLE IF NOT EXISTS seen_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    fill_key TEXT NOT NULL,
    symbol TEXT,
    first_seen_at TEXT NOT NULL,
    UNIQUE(account_id, fill_key)
);

CREATE TABLE IF NOT EXISTS seen_transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    transfer_id TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    UNIQUE(account_id, transfer_id)
);

CREATE TABLE IF NOT EXISTS transfer_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    route TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    UNIQUE(account_id, route)
);

-- Operator pages: the product reporting its own faults (G-49). Deliberately a
-- separate table from `alerts` rather than a flag on it.
--
-- `account_id` is NULLABLE and means "about", never "to". A product-level fault
-- - the scheduler falling behind, an exchange dropping timestamps, a failed
-- drill - belongs to no customer, and inventing an account row to carry it
-- would pollute the dashboard, the digest and every per-account query. A page
-- that IS about one account (its key stopped working because our egress IP was
-- blocked) still never reaches that customer: nothing on this path touches
-- ContactRouter.
--
-- Separate table, not a column on `alerts`, because a customer's alert history
-- is something they can read in the portal and something we hand to an
-- exchange as evidence. Our outages do not belong in it.
CREATE TABLE IF NOT EXISTS operator_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER REFERENCES accounts(id),
    rule TEXT NOT NULL,
    severity TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    delivered_to INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT NOT NULL,
    acknowledged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_operator_pages_dedup ON operator_pages(dedup_key, sent_at);
CREATE INDEX IF NOT EXISTS idx_operator_pages_time ON operator_pages(sent_at);

-- Per-coin EMA of fee spend per fast-loop window, stored as JSON {"USDT": "1.23", ...}
CREATE TABLE IF NOT EXISTS fee_baseline (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
    ema_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Per-recipient SMS delivery log. One row per phone number per alert:
-- 'sent' rows carry the provider message id ClickSend/Twilio returned;
-- 'failed' rows carry the provider error for admin review; 'skipped' records
-- an SMS that was due but had no usable destination or was opted out;
-- 'suppressed' records duplicate-SMS protection kicking in.
CREATE TABLE IF NOT EXISTS sms_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    alert_id INTEGER REFERENCES alerts(id),
    rule TEXT NOT NULL,
    phone TEXT,
    provider TEXT,
    provider_message_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('sent', 'failed', 'skipped', 'suppressed')),
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sms_deliveries_account ON sms_deliveries(account_id, rule, created_at);

-- Operator accounts (G-50). Deliberately a separate table from
-- `portal_members`, and reached through a separate login: an operator is not a
-- customer with extra rights, and conflating the two is how an operator ends up
-- reachable through a customer login.
--
-- Disabled rather than deleted on offboarding. Deleting would orphan every
-- action the person ever took, and the point of having names is that the log
-- can still point at one.
CREATE TABLE IF NOT EXISTS operators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    session_epoch INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

-- Session-signing secret for the ops dashboard, kept apart from the portal's
-- so a leak of one cannot mint sessions for the other.
CREATE TABLE IF NOT EXISTS operator_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Member-portal logins. Identity is the customer email: a member sees every
-- account whose alert_email matches (case-insensitive). Passwords are scrypt
-- hashes generated by the operator from the ops dashboard - members never
-- self-register, so there is no signup surface to attack.
CREATE TABLE IF NOT EXISTS portal_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    -- Bumped on every password change. Baked into the session cookie and
    -- checked on every request, so raising it retires every cookie ever issued
    -- to this member. Sessions are stateless HMACs with nothing server-side to
    -- delete, so this column IS "sign out all other sessions" (#33).
    session_epoch INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

-- Small key/value store for portal runtime state (session-signing secret),
-- so member sessions survive container restarts.
CREATE TABLE IF NOT EXISTS portal_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Member-requested pauses (#98, decision 15): request -> 24-hour countdown
-- -> automatic execution unless cancelled. THE TABLE IS THE COUNTDOWN:
-- nothing about a pending pause lives only in memory, so a deploy or a #81
-- restart between request and deadline changes nothing - the executor
-- resumes from these rows on its next tick, and a deadline that passed
-- while the process was down executes on startup rather than being lost.
-- Rows are never deleted: who asked for a pause, who cancelled it, and
-- when it took effect are facts about an account's monitoring history.
CREATE TABLE IF NOT EXISTS pause_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    execute_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'cancelled', 'executed')),
    cancelled_by TEXT,
    cancelled_at TEXT,
    executed_at TEXT,
    expiry_notified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pause_requests_pending ON pause_requests(status, execute_at);

-- Per-account threshold overrides (#17, G-13). One row per (account,
-- setting); absence means the global config answers, so deleting a row IS
-- "back to defaults". `set_by` is the --as operator, nullable before
-- operator accounts exist, same as every attributed write.
CREATE TABLE IF NOT EXISTS account_threshold_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    setting TEXT NOT NULL,
    value TEXT NOT NULL,
    set_by TEXT,
    set_at TEXT NOT NULL,
    UNIQUE(account_id, setting)
);

-- Self-serve Telegram connections (#18). One row per link attempt; the
-- member's CURRENT connection is the newest 'connected' row. The code is a
-- one-time secret with a short expiry, cleared the moment it is used; a
-- disconnect flips status to 'revoked' rather than deleting, because who was
-- connected when is a fact about where alert copies went.
CREATE TABLE IF NOT EXISTS telegram_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_email TEXT NOT NULL,
    chat_id TEXT,
    link_code TEXT,
    code_expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'connected', 'revoked')),
    created_at TEXT NOT NULL,
    connected_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_telegram_links_email ON telegram_links(member_email, status);

-- Customer-requested silences (G-26, #30). One row per snooze, keyed on the
-- alert'"'"'s dedup_key rather than on its rule: snoozing "withdrawal to an
-- unrecognised address" because you recognise THIS address must not silence
-- the next one to a different address, which is the case the rule exists for.
--
-- `expires_at` is NOT NULL on purpose. There is no permanent mute, and the
-- schema is where that is guaranteed - a cap enforced only in the form is not
-- a cap, because the form is a POST that can be retyped.
--
-- Rows are kept after expiry. Who silenced an alarm, and until when, is a fact
-- about a person and a time on an account that may later be the subject of an
-- exchange dispute; it outlives the silence.
CREATE TABLE IF NOT EXISTS snoozes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    dedup_key TEXT NOT NULL,
    rule TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snoozes_lookup ON snoozes(account_id, dedup_key, expires_at);

-- "That was me" (G-24, #28). A customer telling us an alert was their own
-- activity.
--
-- A separate table rather than a column on `alerts`, for the same reason
-- `snoozes` is: the value of this data is the PATTERN. One row per marking,
-- keeping every one, is what lets an operator see that a customer has said
-- "that was me" to the same rule eleven times - which is a statement about the
-- rule, not about the customer. A column would hold only the latest fact and
-- would erase exactly the history that makes it useful.
--
-- Kept after the alert ages out of usefulness for the same reason snoozes are
-- kept: who said a movement on their account was expected, and when, is a fact
-- that may later matter in a dispute with an exchange.
--
-- What this deliberately does NOT do is suppress anything. Marking is a claim
-- about one alert that already fired; it does not silence the rule, the
-- account or the next alert. Suppression is what `snoozes` is for, it is
-- bounded and it expires - and folding "this was expected" into "stop telling
-- me" would let one reassuring click turn a detector off indefinitely.
CREATE TABLE IF NOT EXISTS expected_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL REFERENCES alerts(id),
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    rule TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    note TEXT,
    marked_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    -- One mark per alert per person. A customer clicking twice has not told us
    -- anything twice, and counting it as two would inflate the very pattern an
    -- operator reads to judge a rule.
    UNIQUE(alert_id, marked_by)
);
CREATE INDEX IF NOT EXISTS idx_expected_account ON expected_activity(account_id, created_at);
CREATE INDEX IF NOT EXISTS idx_expected_rule ON expected_activity(rule, created_at);

-- Threshold changes (G-12, #16). A detection threshold is the difference
-- between an alert and silence, so an unrecorded threshold change is an
-- unrecorded change to what this product notices: take tolerance_pct from 1 to
-- 50 and the account stops being watched in the only way that matters while
-- every dashboard still reads healthy.
--
-- Deliberately NOT a row in `events`: that table is per-account and its
-- account_id is NOT NULL, and a threshold is fleet-wide - it belongs to no
-- customer. Relaxing that column would mean rebuilding the forensic log, which
-- is not a migration worth doing to avoid a table.
--
-- `actor` is NULLABLE and means "we cannot say who". An edit made directly to
-- rules.yaml on a server has no identity attached to it, and filling the column
-- in with the process owner or 'system' would make the log claim a person
-- acted. An unattributed threshold change is itself the finding.
CREATE TABLE IF NOT EXISTS config_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    setting TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    actor TEXT,
    changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_config_changes_time ON config_changes(changed_at);

-- Single-use password-reset tokens (G-29, #33).
--
-- Only the HASH of the token is stored. A reset token is a bearer credential
-- for as long as it lives - anyone holding it can take the account - so a
-- database read, a backup or a stray log line must not yield a working link.
-- The plaintext exists exactly once, in one email.
--
-- `used_at` rather than deletion, so a redeemed link is distinguishable from
-- one that never existed when something needs investigating later.
CREATE TABLE IF NOT EXISTS password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_password_resets_hash ON password_resets(token_hash);
CREATE INDEX IF NOT EXISTS idx_password_resets_email ON password_resets(email, expires_at);

-- Two-factor enrolment, for members and operators alike (G-28, #32).
--
-- `subject_kind` + `subject` rather than a foreign key, because operators and
-- portal members are deliberately separate tables reached through separate
-- logins - an operator is not a customer with extra rights - and one 2FA
-- mechanism serving both must not quietly join them.
--
-- `encrypted_secret`, never the plaintext. The shared secret is a credential
-- exactly as much as an exchange API key is, and it is encrypted with the same
-- cipher: a database read must not yield a working authenticator.
--
-- `last_used_step` is replay protection. A TOTP code is valid for up to ninety
-- seconds, which is long enough for somebody who read it over a shoulder;
-- recording the last accepted step makes each one usable exactly once.
CREATE TABLE IF NOT EXISTS totp_enrolments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('member', 'operator')),
    subject TEXT NOT NULL,
    encrypted_secret TEXT NOT NULL,
    -- 0 while enrolling. A secret is stored before it is confirmed so the
    -- customer can scan it, but 2FA is not IN FORCE until they have proved
    -- they can produce a code - otherwise a mis-scanned QR locks them out of
    -- the thing protecting their exchange accounts.
    confirmed INTEGER NOT NULL DEFAULT 0,
    last_used_step INTEGER,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    UNIQUE(subject_kind, subject)
);

-- Single-use codes that bypass the second factor. Hashed, because they are
-- worth exactly as much as the password.
CREATE TABLE IF NOT EXISTS totp_recovery_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('member', 'operator')),
    subject TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_recovery_lookup
    ON totp_recovery_codes(subject_kind, subject, used_at);

-- Minas-side triage of an alert (G-37, #41). Deliberately NOT a column on
-- `alerts`, and deliberately not `acknowledged_at`.
--
-- Acknowledgement is the customer's act: it means the person whose money is
-- moving has seen the alert, and `Escalator` keys on `acknowledged_at IS NULL`
-- to decide whether to escalate a CRITICAL. If an operator working a queue
-- wrote to that column, marking an alert "I have looked at this" would
-- silently stop the customer-facing escalation ladder - an operator's internal
-- bookkeeping disabling the mechanism that exists for when the customer has
-- NOT responded. Two different questions, two different columns.
--
-- One row per alert: triage is a current state ("who is on this, and is it
-- done"), not a history. Who did it and when is recorded here; the durable
-- forensic trail is the `events` row written alongside.
CREATE TABLE IF NOT EXISTS alert_triage (
    alert_id INTEGER PRIMARY KEY REFERENCES alerts(id),
    note TEXT NOT NULL DEFAULT '',
    handled_at TEXT,
    handled_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_triage_handled ON alert_triage(handled_at);
