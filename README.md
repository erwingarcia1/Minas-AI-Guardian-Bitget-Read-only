# Minas AI Guardian

**24/7 read-only account-compromise monitoring for crypto exchange accounts.**
Detects funds-drain and account-takeover patterns and alerts within minutes —
across **Bitget, Bybit, OKX Global, and Binance Global**.

> The product is **Minas AI Guardian**; `sentinel` is the internal
> package/CLI codename throughout the code.

[![CI](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/actions/workflows/ci.yml/badge.svg)](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/actions/workflows/ci.yml)
&nbsp;·&nbsp; last tag **1.0.0** (2026-07-05) ·&nbsp; `main` also carries the
untagged **member portal** and **member SMS** work ·&nbsp; see
[CHANGELOG](CHANGELOG.md)

## The one non-negotiable

**Read-only only.** Onboarding programmatically verifies every API key is
read-only on the exchange and **rejects, fail-closed, any key with trade or
withdraw scope** — verified per-exchange against each exchange's own key-scope
signal (Bitget `authorities`, Bybit `readOnly`, OKX `perm`, Binance
`apiRestrictions`). A key that can move money can never be stored. See
[SECURITY.md](SECURITY.md).

## Quick start (local)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env       # SENTINEL_MASTER_KEY + alert channels
.venv/bin/sentinel onboard --exchange bitget --label customer-1
.venv/bin/sentinel run     # engine + dashboard + member portal + metrics
```

`onboard` prompts for the API key/secret (and the passphrase on Bitget and OKX
— Bybit and Binance have none) unless you pass `--api-key/--secret/--passphrase`,
so credentials needn't land in shell history.

| Server | Default port | Default bind | Env vars | Exposure |
|---|---|---|---|---|
| Ops dashboard | 8390 | `127.0.0.1` | `SENTINEL_DASHBOARD_PORT`, `SENTINEL_DASHBOARD_HOST` | **No login** — network-gated only (Tailscale) |
| Member portal | 8391 | `127.0.0.1` | `SENTINEL_PORTAL_PORT`, `SENTINEL_PORTAL_HOST` | Member login; front with TLS |
| Prometheus metrics | 9109 | `127.0.0.1` | `SENTINEL_METRICS_PORT`, `SENTINEL_METRICS_HOST` | Internal |

**All three are reachable only from the machine running them.**
That is deliberate: the dashboard has no login of its own, so a wider default
would put an unauthenticated *Add customer* form — and every customer's contact
details — on every interface of whatever box you ran the quickstart on. The
metrics view is on the same footing: unauthenticated, and it leaks operational
shape (account counts, which rules are firing, which exchanges are
misbehaving). To reach any of them from elsewhere, tunnel rather than widen:
`ssh -L 8390:127.0.0.1:8390 your-host`, Tailscale, or an authenticating reverse
proxy. The `*_HOST` vars above do widen the bind if a deployment genuinely
needs it; the dashboard logs a startup warning whenever it isn't on loopback.

Under Docker all three bind `0.0.0.0` *inside the container* on purpose,
because Docker publishes a port by proxying to the container's interface and
never to its loopback. The `127.0.0.1:` publishes in `docker-compose.yml` are
the gate instead.

Docker (same image as production): `docker compose up -d --build`.
Production deployment (AWS): see [docs/deployment.md](docs/deployment.md).

## Onboarding a customer

- **Web form** — the dashboard's **+ Add customer** button captures the full
  customer profile and API key, verifies read-only, and starts monitoring.
- **CLI** — `sentinel onboard --exchange {bitget|bybit|okx|binance} --label X
  [--alert-email … --alert-phone …]`.

## CLI

| Command | What |
|---|---|
| `sentinel onboard --exchange X --label Y` | Verify read-only key (fail-closed) + seed baseline |
| `sentinel run` | Monitoring engine + ops dashboard + member portal + metrics |
| `sentinel set-contact --label X …` | Set/clear an account's alert destination; `--sms-alerts on\|off` toggles the SMS preference |
| `sentinel digest` | Send the MEDIUM/LOW daily digest |
| `sentinel ack <id>` | Acknowledge an alert (stops CRITICAL escalation) |
| `sentinel triage --label X` | AI incident narrative (optional; additive only) |
| `sentinel validate-config [path]` | Check a rules file against the schema. No database, nothing started. Exit 0 valid / 1 rejected / 2 unreadable — **run it against the server's live file before an upgrade** |

## Detection catalog

**CRITICAL:** withdrawal initiated (incl. in-flight `pending`), withdrawal to
never-seen address, unexplained per-asset quantity decrease.
**HIGH:** withdrawal burst (structuring), never-seen transfer route,
never-touched pair activity, off-market execution (counterparty drain),
monitoring-key tamper, permission-state change (proxy).
**MEDIUM:** abnormal fee spend, account unreachable. **LOW:** baseline drift.

Thresholds live in [config/rules.yaml](config/rules.yaml) — hot-reloaded, no
deploy to tune. Every detector ships with trigger + non-trigger tests.

## Dashboard

Served over **Tailscale** (HTTPS, tailnet-only — no public ports, no tunnel):
fleet KPIs, per-account status, recent alerts, an **Add customer** form, a
per-account **drill-down** (customer profile + security-posture panel + event
history), and a styled **Metrics** page. See
[docs/architecture.md](docs/architecture.md).

## Member portal

A **separate** authenticated server (`:8391`) that lets a customer sign in and
see **only their own** accounts, alerts, events, and security posture — never
another customer's. Identity is the customer email; a member sees every account
whose `alert_email` matches (case-insensitive). Properties:

- **Isolation is the invariant** — every query is scoped to the signed-in
  member's account ids; a drill-down on an id you don't own returns 404 without
  revealing that it exists.
- **No self-registration** — the operator generates a member's password from
  the ops dashboard (per-account **Member portal access** card); it's shown
  once. Passwords are scrypt-hashed; sessions are HMAC-signed `HttpOnly` cookies
  (12 h). Login is throttled per-email and per-IP, with constant-time email
  lookup so timing can't reveal who's registered.
- **Read-only, and on its own port** — it shares nothing with the network-gated
  ops view. Front it with TLS (Tailscale Serve or a reverse proxy) before
  exposing it; the container publishes it on `127.0.0.1:8391` only.

## Alerting

Per-severity routing to your personal **Telegram**, a **VA/team Telegram
group**, **SMS** (ClickSend or Twilio), and **email** (SMTP), plus
per-customer email/phone destinations. Dedup + cooldown, unacked-CRITICAL
escalation, and a MEDIUM/LOW daily digest.

**Member SMS** (ClickSend): when an alert fires on a member's account, the
member's phone gets a short, action-focused text — severity, alert type,
affected account, and a member-portal login link (`SENTINEL_PORTAL_URL`).
Every delivery outcome is recorded in an `sms_deliveries` audit log (ClickSend
message id, provider error on failure, skip/suppress reasons) linked to the
alert. Duplicate texts per rule + account are suppressed within
`alerting.sms_cooldown_minutes` (default 30); a per-account `sms_alerts_enabled`
preference is respected, with CRITICAL overriding an opt-out per
`alerting.sms_critical_overrides_optout`. A CRITICAL with no phone on file is
logged and flagged on the dashboard's security-posture panel.

## Verification

```bash
.venv/bin/pytest -q                      # unit + integration, mocked CCXT only
.venv/bin/python scripts/soak_test.py    # 100 synthetic accounts through the engine
```

The suite (169 tests across 24 files) never touches a live exchange — every
CCXT response is a fixture. CI runs it on every push/PR to `main` and also
validates that `docker compose config` parses. Latest soak: 100 accounts
(5 permanently failing, 3 under scripted attack) — full-fleet cycle well under
the target, only the attacked accounts fired CRITICALs.

## Architecture (short)

- `sentinel/exchange.py` — read-only exchange clients (Bitget/Bybit/OKX/
  Binance) + `make_client` factory; auth errors map to a tamper signal.
- `sentinel/onboarding.py` — per-exchange fail-closed read-only gate.
- `sentinel/engine.py` — scheduler → queue → worker pool with jitter.
- `sentinel/ratelimit.py` / `circuit.py` — per-UID + egress-IP token buckets;
  per-account breakers (one bad key never stalls the fleet).
- `sentinel/poller.py` — fast loop (money movement) + slow loop (config drift).
- `sentinel/detectors/` — the 12 rules, pure functions over unified data.
- `sentinel/baseline.py` — what "normal" is per account (known addresses,
  traded pairs, transfer routes, balance/permission snapshots, fee EMA).
- `sentinel/alerts/` — Telegram / ClickSend / Twilio / SMTP, routing, dedup,
  escalation, digest, per-account contacts.
- `sentinel/dashboard.py` — ops UI; `portal.py` — member portal (auth,
  sessions, per-member scoping); `metrics.py` — Prometheus + styled page.
- `sentinel/config.py` — `rules.yaml` loaded as `Decimal`, hot-reloaded on mtime.
- `sentinel/db.py` + `schema.sql` — SQLite (Postgres is the scale path);
  append-only event log; `Decimal` everywhere; UTC everywhere.
- `sentinel/triage.py` — optional AI incident narrative (off by default).
- `sentinel/ws_monitor.py` — ccxt.pro stream watcher with a staleness watchdog:
  implemented and unit-tested, **not yet wired into the engine**.

Full write-up: [docs/architecture.md](docs/architecture.md).

## Docs

- [docs/architecture.md](docs/architecture.md) — current system architecture.
- [docs/phase0-capability-audit.md](docs/phase0-capability-audit.md) — what
  each exchange/CCXT actually exposes, verified against live sources.
- [docs/deployment.md](docs/deployment.md) — AWS deployment, scaling, ops.
- [docs/customer-runbook.md](docs/customer-runbook.md) — customer incident
  response, incl. what the service *cannot* see.
- [docs/threat-model.md](docs/threat-model.md) — assets, adversaries, controls.
- [docs/data-retention.md](docs/data-retention.md) — **flagged for legal review**.
- [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md)

## Known limitations (honest list)

- Topology A (sub-account/broker) endpoints are mapped and schema-ready, but
  its rate-limit cadence questions need a broker test account first.
- `unexplained_quantity_decrease` doesn't reconcile futures liquidations yet —
  wire it on for spot/hold accounts only.
- New-API-key detection for independent accounts is a documented proxy — no
  exchange exposes a key inventory for an account's own keys.
- Login/device/2FA state is invisible to any API consumer; covered by
  customer-side mitigations in the runbook.
- Per-customer **Telegram-handle** delivery isn't automated (Bot API can't DM
  an arbitrary handle without the recipient opting in); handles are stored.
- Detection is **REST-only**. `ws_monitor.py` is built and tested but not
  wired into the engine — and no exchange streams withdrawals/deposits/
  transfers over WS anyway, so it would only sharpen the trading signals.
- **Member portal identity is `alert_email` only.** An account's
  `alert_email_secondary` receives alerts but cannot be given portal access.
- The engine is one process, one SQLite connection: workers, dashboard, and
  portal all serialize on it. Fine at pilot scale; Postgres + a distributed
  queue is the documented scale-out (the compose `scale` profile is the stub).

## License

Proprietary — see [LICENSE](LICENSE). © 2026 Minas Tech. All rights reserved.
