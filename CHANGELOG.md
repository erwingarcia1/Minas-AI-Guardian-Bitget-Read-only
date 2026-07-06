# Changelog

All notable changes to Minas AI Guardian are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The internal package/CLI codename is `sentinel`; the product is **Minas AI
Guardian**.

## [Unreleased]

_Pending customer/ops items (not code): read-only Bybit key (previous had
Contract-Trade scope), OKX + Binance keys, Gmail SMTP credentials, ClickSend
or Twilio credentials, terminate the retired us-east-1 instance._

## [1.0.0] — 2026-07-05

First production release. Running on AWS Tokyo (ap-northeast-1), reachable
over a Tailscale-served HTTPS dashboard.

### Added
- **Account drill-down** (`/account?id=N`): per-customer detail page with the
  customer profile (name, license, primary + secondary email/phone, Telegram
  handles), account config, a plain-language **security-posture** panel (what
  keeps the account safe), the full detection catalog, and that account's own
  event + alert history. API secrets are never rendered.
- **Add-customer web form** (`/add`) behind the fail-closed read-only gate:
  a submitted trade/withdraw key is rejected and nothing is stored; the
  rejection reason is shown inline. Cross-origin POSTs blocked; secrets never
  echoed back.
- **ClickSend SMS** provider (verified against ClickSend's API) with a
  provider factory that prefers ClickSend when configured and falls back to
  Twilio — for both the global SMS channel and per-customer delivery.
- **Per-customer alert delivery** to primary + secondary email/phone, in
  addition to global severity routing. Telegram handles are stored (Bot API
  can't DM an arbitrary handle without opt-in).
- **VA/team Telegram group** channel (`telegram_team`): routes CRITICAL/HIGH
  to a shared group in addition to the personal chat.
- `sentinel set-contact` CLI to set/clear an existing account's alert contact.
- Customer schema: `customer_name`, secondary email/phone, two Telegram
  handles, `license` (with online migrations for the deployed database).

### Changed
- Metrics page nav links are same-origin (`/`, `/metrics/raw`) so they work
  behind the Tailscale HTTPS proxy; added a `/metrics/raw` route serving the
  raw Prometheus exposition on the dashboard port.
- Dashboard/metrics served over one port so a single tunnel reaches both.

### Fixed
- `sentinel_accounts_active` gauge was defined but never updated (stuck at 0);
  the engine now sets it each schedule cycle.

### Infrastructure
- Migrated production from AWS us-east-1 to **ap-northeast-1 (Tokyo)** — the
  US region geo-blocks Bybit, Binance Global, and OKX Global; Tokyo reaches
  all four. Database migrated with baseline + history intact.
- Dashboard exposed via **Tailscale Serve** (HTTPS, tailnet-only) — no public
  ports, no SSH tunnel; VAs are added by tailnet invite.

## [0.4.0] — 2026-07-05

### Added
- **OKX Global** support: `perm`-field read-only gate (accept only
  `read_only`), passphrase required, instType-iterating order coverage.
- **Binance Global** support: `apiRestrictions` read-only gate (all nine
  write-implying flags must be false; a missing flag fails closed), no
  passphrase, balance-derived per-symbol order coverage.
- All four exchanges (Bitget, Bybit, OKX, Binance) selectable at onboarding.

## [0.3.0] — 2026-07-05

### Added
- **Multi-exchange architecture**: `ExchangeClient` base + per-exchange
  clients + `make_client` factory; per-exchange read-only dispatch.
- **Bybit** support with a fail-closed `readOnly`-flag gate plus a
  defense-in-depth write-permission denylist. The gate passed a 0-finding
  adversarial review.
- `exchange` / per-account alert-contact columns; passphrase made optional
  (Bybit/Binance have none).

### Changed
- Styled metrics view served at the dashboard's `/metrics` so a single tunnel
  reaches both pages (raw Prometheus stays on :9109 for scrapers).

## [0.2.0] — 2026-07-05

### Added
- Docker deployment hardening: container `cap_drop: [ALL]`,
  `no-new-privileges`, pid/memory limits, healthcheck, log rotation;
  `.dockerignore`; EC2 `user-data` bootstrap script.
- AWS-first deployment guide with SSH-tunnel dashboard access and a rehearsed
  DB-migration procedure.

### Fixed
- Packaged `schema.sql` (clean installs crash-looped without it).
- Pinned the container DB path so a host `.env` can't redirect the credentials
  DB into the disposable container filesystem.
- Findings from an adversarial deployment-asset review (permissions, sudo,
  `python`→`python3`, false "hot-reload" claims).

## [0.1.0] — 2026-07-02

Initial MVP (Phases 0–3, Bitget only).

### Added
- Phase 0 capability audit verified against live CCXT source and Bitget docs
  (signal-availability matrix, proxy designs, rate-limit math).
- Fail-closed read-only key onboarding with encrypted credential storage.
- 12-rule detection catalog with trigger/non-trigger tests per rule.
- Scheduler → queue → worker pool; per-UID + egress-IP token buckets;
  per-account circuit breakers (100-account soak).
- ccxt.pro WebSocket monitor with reconnect + dead-stream watchdog.
- Alerting: Telegram / Twilio SMS / SMTP email with severity routing, dedup +
  cooldown, unacked-CRITICAL escalation, daily digest; optional AI triage.
- Branded status dashboard + Prometheus metrics; Docker Compose; docs (threat
  model, customer runbook, deployment, data retention).

[Unreleased]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.4.0...v1.0.0
[0.4.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/releases/tag/v0.1.0
