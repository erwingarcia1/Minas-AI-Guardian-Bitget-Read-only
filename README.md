# Minas AI Guardian — Bitget account-compromise monitoring

> Product name: **Minas AI Guardian**. `sentinel` remains the internal
> package/CLI codename throughout the code.

Read-only, 24/7 monitoring for Bitget accounts: detects funds-drain and
account-takeover patterns and alerts within minutes. Never holds trade or
withdraw permissions — onboarding programmatically rejects any key that
isn't read-only, fail-closed.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env       # fill in SENTINEL_MASTER_KEY + alert channels
.venv/bin/sentinel onboard --label customer-1
.venv/bin/sentinel run     # engine: workers + rate limits + breakers + dashboard
```

Docker deployment: see [docs/deployment.md](docs/deployment.md).

## CLI

| Command | What |
|---|---|
| `sentinel onboard --label X` | Verify read-only key (fail-closed) + seed baseline |
| `sentinel run` | Monitoring engine + status dashboard (:8390) + metrics (:9109) |
| `sentinel digest` | Send the MEDIUM/LOW daily digest |
| `sentinel ack <id>` | Acknowledge an alert (stops CRITICAL escalation) |
| `sentinel triage --label X` | AI incident narrative (optional; additive only) |

## Detection catalog

CRITICAL: withdrawal initiated (incl. in-flight `pending`), withdrawal to
never-seen address, unexplained per-asset quantity decrease.
HIGH: withdrawal burst (structuring), never-seen transfer route,
never-touched pair activity, off-market execution (counterparty drain),
monitoring-key tamper, permission-state change (proxy).
MEDIUM: abnormal fee spend, account unreachable.
LOW: baseline drift.

All thresholds in [config/rules.yaml](config/rules.yaml) — hot-reloaded, no
deploy to tune. Every detector ships with trigger + non-trigger tests.

## Architecture

- `sentinel/engine.py` — scheduler → queue → worker pool; per-account
  idempotent tasks with jitter.
- `sentinel/ratelimit.py` — token buckets per account credential (Bitget
  limits are per-UID) + shared egress-IP bucket, seeded from the verified
  numbers in the Phase 0 audit.
- `sentinel/circuit.py` — per-account breakers with exponential backoff; one
  bad key never stalls the fleet (soak-verified).
- `sentinel/poller.py` — fast loop (money movement; REST-only because Bitget
  exposes no WS channel for withdrawals) + slow loop (permission snapshots).
- `sentinel/ws_monitor.py` — ccxt.pro order/fill/balance streams with
  reconnect + silent-death watchdog; accelerates trading signals, REST stays
  authoritative.
- `sentinel/alerts/` — Telegram / Twilio SMS / SMTP email, severity routing
  from config, dedup + cooldown, unacked-CRITICAL escalation, daily digest.
- `sentinel/triage.py` — optional Claude-powered incident narrative. Runs
  *after* alerts dispatch; has no code path to suppress or downgrade a rule.
- `sentinel/db.py` + `schema.sql` — SQLite (Postgres is the scale-out path);
  append-only event log; Decimal everywhere; UTC everywhere.

## Verification

```bash
.venv/bin/pytest -q                      # unit + integration, mocked CCXT only
.venv/bin/python scripts/soak_test.py    # 100 synthetic accounts through the real engine
```

Latest soak: 100 accounts (5 permanently failing, 3 under scripted attack) —
9.8s full-fleet cycle vs 45s target, 95/95 healthy accounts polled, rate
limiter held at the configured IP budget, and exactly the 3 attacked
accounts fired CRITICALs.

## Docs

- [docs/phase0-capability-audit.md](docs/phase0-capability-audit.md) — what
  Bitget/CCXT actually expose, verified against live sources; proxy designs
  for the gaps; rate-limit math; the topology A/B analysis.
- [docs/customer-runbook.md](docs/customer-runbook.md) — incident response
  steps customers take, incl. what Sentinel *cannot* see.
- [docs/threat-model.md](docs/threat-model.md) — assets, adversaries,
  controls, and explicitly accepted residual risks.
- [docs/deployment.md](docs/deployment.md) — Alibaba Cloud / AWS deployment,
  scaling path, ops reference.
- [docs/data-retention.md](docs/data-retention.md) — **flagged for legal
  review**; engineering defaults until then.

## Known limitations (honest list)

- Topology A (sub-account/broker) endpoints are mapped and schema-ready, but
  its rate-limit cadence questions (Phase 0 audit §7 items 3/4/6) need the
  broker test account before that pipeline is built.
- `unexplained_quantity_decrease` doesn't reconcile futures liquidations yet
  — wire it on for spot/hold accounts only (audit §6).
- New-API-key detection for independent accounts is a documented proxy, not
  a direct signal — Bitget exposes no key inventory for own accounts.
- Login/device/2FA state is invisible to any API consumer; covered by
  customer-side mitigations in the runbook.
