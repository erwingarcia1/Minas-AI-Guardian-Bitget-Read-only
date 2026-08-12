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
