PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE,
    topology TEXT NOT NULL CHECK (topology IN ('independent', 'sub_account')),
    encrypted_api_key TEXT NOT NULL,
    encrypted_secret TEXT NOT NULL,
    encrypted_passphrase TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'tamper_suspected', 'disabled')),
    onboarded_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Append-only forensic log. The application never issues UPDATE/DELETE
-- against this table - only INSERT.
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

-- Per-coin EMA of fee spend per fast-loop window, stored as JSON {"USDT": "1.23", ...}
CREATE TABLE IF NOT EXISTS fee_baseline (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
    ema_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
