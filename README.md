# Minas AI Guardian

**24/7 read-only account-compromise monitoring for crypto exchange accounts.**
Detects funds-drain and account-takeover patterns and alerts within minutes —
across **Bitget, Bybit, OKX Global, and Binance Global**.

> The product is **Minas AI Guardian**; `sentinel` is the internal
> package/CLI codename throughout the code.

[![CI](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/actions/workflows/ci.yml/badge.svg)](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/actions/workflows/ci.yml)
&nbsp;·&nbsp; version **1.0.0** ·&nbsp; see [CHANGELOG](CHANGELOG.md)

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
.venv/bin/sentinel run     # engine + dashboard (:8390) + member portal (:8391) + metrics (:9109)
```

Production deployment (Docker on AWS): see [docs/deployment.md](docs/deployment.md).

## Onboarding a customer

- **Web form** — the dashboard's **+ Add customer** button captures the full
  customer profile and API key, verifies read-only, and starts monitoring.
- **CLI** — `sentinel onboard --exchange {bitget|bybit|okx|binance} --label X
  [--alert-email … --alert-phone …]`.

## CLI

| Command | What |
|---|---|
| `sentinel onboard --exchange X --label Y` | Verify read-only key (fail-closed) + seed baseline |
| `sentinel run` | Monitoring engine + status dashboard + metrics |
| `sentinel set-contact --label X …` | Set/clear an account's alert destination; `--sms-alerts on\|off` toggles the SMS preference |
| `sentinel digest` | Send the MEDIUM/LOW daily digest |
| `sentinel ack <id>` | Acknowledge an alert (stops CRITICAL escalation) |
| `sentinel triage --label X` | AI incident narrative (optional; additive only) |

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

The suite (126 tests) never touches a live exchange. Latest soak: 100
accounts (5 permanently failing, 3 under scripted attack) — full-fleet cycle
well under the target, only the attacked accounts fired CRITICALs.

## Architecture (short)

- `sentinel/exchange.py` — read-only exchange clients (Bitget/Bybit/OKX/
  Binance) + `make_client` factory; auth errors map to a tamper signal.
- `sentinel/onboarding.py` — per-exchange fail-closed read-only gate.
- `sentinel/engine.py` — scheduler → queue → worker pool with jitter.
- `sentinel/ratelimit.py` / `circuit.py` — per-UID + egress-IP token buckets;
  per-account breakers (one bad key never stalls the fleet).
- `sentinel/poller.py` — fast loop (money movement) + slow loop (config drift).
- `sentinel/detectors/` — the 12 rules, pure functions over unified data.
- `sentinel/alerts/` — Telegram / ClickSend / Twilio / SMTP, routing, dedup,
  escalation, digest, per-account contacts.
- `sentinel/dashboard.py` + `metrics.py` — HTTP UI + Prometheus.
- `sentinel/db.py` + `schema.sql` — SQLite (Postgres is the scale path);
  append-only event log; `Decimal` everywhere; UTC everywhere.

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

## License

Proprietary — see [LICENSE](LICENSE). © 2026 Minas Tech. All rights reserved.
