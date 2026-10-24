# Security Policy

Minas AI Guardian monitors customer exchange accounts for signs of compromise.
Because it holds customer API credentials, the service itself is a sensitive
asset. This document states the security invariants, how secrets are handled,
and how to report a vulnerability.

## The non-negotiable invariant

**The service only ever holds read-only API keys.** Onboarding programmatically
verifies read-only scope against each exchange's own key-metadata signal and
**rejects, fail-closed, any key with trade or withdraw permission**:

| Exchange | Read-only signal | Accept only when |
|---|---|---|
| Bitget | `authorities` (per-module `…r`/`…w` codes) | every token is read-scope; any write/trade/withdraw token → reject |
| Bybit | `readOnly` flag + `permissions` | `readOnly == 1` **and** no write sub-permission (defense-in-depth) |
| OKX | account-config `perm` | `perm == read_only` exactly (any `trade`/`withdraw` → reject) |
| Binance | `apiRestrictions` flags | `enableReading` true **and** all nine write-implying flags false; a missing flag fails closed |

Any error, unparseable response, or ambiguity during verification is treated
as a rejection, never a pass. This invariant is enforced in one place
(`sentinel/onboarding.py`), exercised by trigger/non-trigger tests, and the
Bybit gate additionally passed a 0-finding adversarial review. The web
onboarding form runs the identical gate — it cannot bypass it.

## Secret handling

- Customer API credentials are **encrypted at rest** (Fernet/AES) with a
  master key supplied only via `SENTINEL_MASTER_KEY` (env / secret manager) —
  never in code, config, or git. Decrypted only in worker memory for the
  duration of a single exchange call.
- Secrets are **redacted from error messages** before they can be logged.
- The onboarding web form never echoes submitted secrets back and never
  renders stored keys on any page.
- `.env`, the credentials database (`sentinel_data/`), and SSH keys are
  git-ignored and must never be committed.
- IP allow-listing on exchange keys is recommended (a stolen read-only key is
  then useless off the monitoring egress IP).

## Network & access posture

- The ops dashboard requires an operator sign-in with a **mandatory second
  factor** (TOTP; #87, decision 19), and that sign-in is throttled. In
  production it is served at `https://guardian.minastrade.com/admin` by
  Caddy, the host's only public listener; the dashboard, portal and metrics
  servers default to `127.0.0.1` (under compose they bind `0.0.0.0` inside
  the container and the `127.0.0.1:` publishes in docker-compose.yml are
  the gate), and the metrics endpoint is never proxied. There is no IP allowlist
  on `/admin` (decision 22c item 5, answered no): the sign-in, its second
  factor and the throttle are the protection. Access is granted with
  `sentinel operator add` and revoked with `sentinel operator disable`,
  which ends every session at once. The host itself is reached by SSH on
  port 22 with the EC2 key pair, from the addresses in the security group's
  SSH rule only; an SSH tunnel to `127.0.0.1:8390` is the break-glass path
  to the dashboard.
- Container runs non-root with `cap_drop: [ALL]`, `no-new-privileges`, and
  pid/memory limits.
- The AI triage layer is additive: it runs **after** alerts dispatch and has
  no code path to suppress or downgrade a deterministic rule.

## Supported versions

The latest release on `main` is the only supported version. See
[CHANGELOG.md](CHANGELOG.md).

## Reporting a vulnerability

This is a private, proprietary repository. Report suspected vulnerabilities
**privately** — do not open a public issue:

- Email: **erwin.garcia@minastech.ca** (subject: `SECURITY — Minas AI Guardian`)

Please include reproduction steps and impact. You'll get an acknowledgement
and a remediation timeline. Given the service handles credentials, treat any
suspected key exposure as urgent and rotate the affected keys immediately.
