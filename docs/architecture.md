# Architecture

Current-state architecture of Minas AI Guardian (v1.0.0). For the exchange
capability research behind it, see
[phase0-capability-audit.md](phase0-capability-audit.md).

## System overview

```
                 Tailscale (HTTPS, tailnet-only)
   operator/VAs ─────────────────────────────►  Dashboard :8390  ── /            (fleet + accounts)
                                                 (sentinel/         ── /add         (onboarding form)
                                                  dashboard.py)     ── /account?id  (drill-down)
                                                                    ── /metrics      (styled)
                                                                    └─ /metrics/raw  (Prometheus)

   ┌───────────────────────── engine (sentinel/engine.py) ─────────────────────────┐
   │  scheduler ──► queue ──► worker pool (jitter, idempotent per-account tasks)    │
   │      │                        │                                               │
   │      │                        ├─ rate limiter (per-UID + shared egress IP)     │
   │      │                        ├─ circuit breaker (per account, exp. backoff)   │
   │      ▼                        ▼                                               │
   │  poller.py: fast loop (money movement) + slow loop (config/permission drift)   │
   └───────┬──────────────────────────────────────────────────────┬────────────────┘
           │ read-only CCXT calls                                  │ detections
           ▼                                                       ▼
   exchange.py: Bitget / Bybit / OKX / Binance clients      detectors/ (12 rules)
           │                                                       │
           ▼                                                       ▼
   exchanges' REST/WS APIs                              alerts/ (Telegram, team group,
                                                        ClickSend/Twilio SMS, SMTP email)
                                                                   │
                                                        db.py + schema.sql (SQLite):
                                                        accounts, baselines, append-only
                                                        event log, alerts, poll_state
```

## Components

- **`exchange.py`** — `ExchangeClient` base + `BitgetClient`, `BybitClient`,
  `OKXClient`, `BinanceClient`, and `make_client(exchange, …)`. Read-only
  surface only; CCXT `AuthenticationError`/`PermissionDenied` map to a
  `TamperSignal`. Per-exchange quirks handled (Bybit wallet-type fallback +
  category iteration; OKX instType iteration; Binance per-symbol orders from
  held balances).
- **`onboarding.py`** — the fail-closed read-only gate, per exchange, plus the
  `onboard_account` flow (verify → encrypt → store → seed baseline) and label
  slugging for the web form.
- **`engine.py`** — scheduler → in-process queue → thread worker pool. The
  `Task` shape and idempotency are designed so a Redis-backed distributed
  queue is a drop-in at scale. Updates the active-accounts gauge each cycle.
- **`ratelimit.py` / `circuit.py`** — token buckets keyed by credential (UID)
  and egress IP, seeded from the Phase-0 numbers; per-account breakers with
  exponential backoff so one bad key never stalls the fleet.
- **`poller.py`** — fast loop (withdrawals/deposits/trades/transfers/balance —
  REST-only, since no exchange exposes withdrawals over WS) + slow loop
  (permission/IP-allowlist snapshot). Emits detections, writes the event log,
  and fires per-account contact delivery.
- **`detectors/`** — 12 pure-function rules over CCXT-unified structures, so
  most detections work across all four exchanges unchanged.
- **`alerts/`** — severity routing from `config/rules.yaml` to Telegram (+ team
  group), SMS (ClickSend preferred, Twilio fallback), and SMTP email; dedup +
  cooldown; unacked-CRITICAL escalation; MEDIUM/LOW digest; per-account
  destinations via `ContactRouter`.
- **`triage.py`** — optional Claude incident narrative; additive, runs after
  dispatch, cannot suppress a rule.
- **`dashboard.py` / `metrics.py`** — one HTTP server (port 8390) serves the
  dashboard, add-customer form, account drill-down, styled metrics, and raw
  Prometheus at `/metrics/raw`; a separate `:9109` serves scrapers.
- **`db.py` + `schema.sql`** — SQLite with online `ALTER` migrations;
  append-only `events` log; `Decimal` and UTC throughout.

## Data model (accounts)

Per account: `label`, `exchange`, `topology`, encrypted API key/secret/
passphrase, `customer_name`, primary + secondary `alert_email`/`alert_phone`,
two `telegram_handle`s, `license`, `status`, timestamps. Baselines live in
`known_addresses`, `traded_pairs`, `seen_withdrawals`, `transfer_routes`,
`permission_snapshots`, `balance_snapshots`, `fee_baseline`.

## Production topology

- **AWS EC2, ap-northeast-1 (Tokyo)** — a US region geo-blocks Bybit, Binance
  Global, and OKX Global; Tokyo reaches all four.
- **Docker Compose**, `restart: unless-stopped`; SQLite on a named volume.
- **Tailscale Serve** publishes the dashboard as tailnet-only HTTPS — no
  public ports beyond SSH (locked to admin IPs).
- Scale path: raise worker pool → Postgres (`SENTINEL_DB_DSN`, planned) →
  multiple egress IPs / one stack per IP with a Redis-backed queue.
