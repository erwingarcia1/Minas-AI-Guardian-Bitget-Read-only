# Sentinel — Threat Model

Premise (from the product brief): **a breach of Sentinel must not enable
theft.** This document walks the assets, adversaries, and controls, and
states plainly where residual risk remains.

## Assets

| Asset | Sensitivity | Why |
|---|---|---|
| Customer read-only API keys | High | Read access to balances/history; NOT funds movement (verified read-only at onboarding, fail-closed) |
| `SENTINEL_MASTER_KEY` | Critical | Decrypts every stored credential |
| Event log / balance history | High | Detailed financial activity per customer — privacy, not theft |
| Alert channel credentials (Telegram/Twilio/SMTP) | Medium | Impersonation of our alerts; alert suppression |
| The detection pipeline itself | High | If silently disabled, customers believe they're protected when they're not |

## Adversaries & scenarios

### A1. Full compromise of the Sentinel host
Attacker gets root on the VM. They obtain: encrypted credentials + (if the
process env is readable) the master key → effectively all customer read-only
keys.
- **Impact ceiling: surveillance, not theft.** Keys are verified read-only at
  onboarding; the check fails closed. The residual theft path would require
  Bitget honoring a write action from a read-only key (exchange-side bug) —
  out of our control, why key scope is non-negotiable.
- Controls: non-root container; master key injected from KMS/Secrets Manager
  at boot rather than sitting in a dotfile; no inbound ports except SSH
  (from the security group's admin addresses) and the proxy's 80 and 443
  (decision 22b), which reach the member portal and, under `/admin`, the
  ops dashboard behind its sign-in and second factor; dashboard, portal and
  metrics are published on loopback only (inside the container they bind
  wide; the `127.0.0.1:` publishes are the gate), and the metrics endpoint
  is never proxied.
- Response: rotate master key, notify customers to rotate their keys,
  forensics from the append-only event log (which the app can only INSERT
  into by construction).

### A2. Compromise of a customer's Bitget account (the case we exist for)
Covered by the detection catalog. Two designed-in properties matter:
- The attacker's cleanup step — deleting our monitoring key — is itself a
  HIGH detection (`monitoring_key_tamper_suspected`), not a blind spot.
- All withdrawals funnel through one endpoint (no separate futures
  withdrawal path on Bitget), so drain attempts can't route around the
  fast loop.

### A3. Alert-channel compromise
Telegram bot token or Twilio creds leak → attacker can send fake alerts
(panic/phishing) or read alert traffic (learns which accounts are monitored).
- Controls: tokens only in env/secret manager; alerts never include API keys
  or full addresses beyond what the customer needs; anti-phishing guidance in
  the customer runbook.
- Gap (accepted for now): alerts are not cryptographically signed. Customers
  should treat alert *instructions* as "check the app", never "click this
  link".

### A4. Malicious/compromised insider
Staff with DB access can read event history and (with the master key) customer
keys.
- Controls: append-only audit trail via the events table; role separation is
  organizational at this scale (flagged honestly: no RBAC exists — every
  operator has the same power; the dashboard is behind operator sign-in with
  a mandatory second factor; operator actions are attributed in the
  append-only event log (`events.actor`), and operator-account security
  events - enrolment, a 2FA reset - in `operator_events`).

### A5. Supply chain (dependency compromise)
`ccxt`, `requests`, etc. are the real attack surface for key exfiltration.
- Controls: pinned minimum versions, container rebuilds are deliberate acts,
  no auto-update. Recommended next step (Phase 3+): pip hash-pinning
  (`--require-hashes`) and an egress allowlist at the firewall so the worker
  can only reach Bitget/alert endpoints — that turns "dependency steals keys"
  into "dependency must also beat network policy".

### A6. Sentinel-as-oracle (abuse of our own service)
Someone onboards a victim's stolen read-only key with us to surveil them.
- Control: onboarding is operator-mediated in Phase 2 (no self-serve API);
  KYC-matching the key owner to the customer is a business-process control to
  formalize before self-serve onboarding exists.

## Deliberate design properties (defense summary)

1. **Read-only keys, verified programmatically, fail closed** — the load-bearing wall.
2. Credentials encrypted at rest (Fernet); decrypted only in worker memory
   per call; redacted from all error paths (tested).
3. Append-only event log — the application has no UPDATE/DELETE path against
   it.
4. Secrets only via env/secret manager; `.env` is git-ignored; `.env.example`
   ships empty.
5. LLM triage is additive-only: it runs *after* dispatch and has no code path
   to suppress/downgrade an alert; prompt states rules are authoritative.
6. Deterministic detection: every alert traces to a rule + raw evidence, so a
   "why did/didn't it fire" question is always answerable from the event log.

## Accepted residual risks (explicit)

- Master key in process memory: a root attacker wins; KMS injection narrows
  the window, doesn't eliminate it.
- No signed alerts (A3).
- No RBAC on the dashboard (A4) — one operator role behind sign-in and
  two-factor, and since decision 22 reachable from the internet at `/admin`;
  acceptable at pilot scale, not at 1,000 accounts with staff turnover.
  Revisit in Phase 3.
- SQLite file readable by host root until Postgres + disk encryption.
- The member portal is internet-reachable at
  `https://guardian.minastrade.com`, through Caddy on the host (decision
  22b): its login is exposed to credential stuffing, mitigated by the login
  throttle and TOTP where enrolled; the portal must never gain an operator
  capability (tests/test_member_roles.py holds the boundary). The ops
  dashboard is internet-reachable too, at `/admin` (decisions 22a, 22e),
  with no IP allowlist in front of it (22c item 5, answered no): operator
  sign-in with a mandatory second factor and the login throttle are the
  protection. A browser's POST to a dashboard route (one carrying
  `Sec-Fetch-Site` or `Origin`) must also carry a `Referer` whose path is
  under `/admin`, or it is refused and logged: that is what tells the
  dashboard's own forms from one planted on a same-origin portal page,
  and it binds nothing that is not a browser. Behind Caddy both throttles
  key on the real client address, which each server takes from
  `X-Forwarded-For` only when the connection comes from Caddy's fixed
  address (22c item 6: `SENTINEL_TRUSTED_PROXIES` for the portal,
  `SENTINEL_DASHBOARD_TRUSTED_PROXIES` for the dashboard, both pinned in
  docker-compose.yml); the operator throttle's account bucket is keyed on
  address and username together, as the portal's is, so a stranger who
  guesses a username locks out only himself. The Caddy container can
  reach the login-less metrics endpoint (9109) on the compose network
  though it does not proxy it: a compromised proxy could read the
  metrics. The name resolves to the host's one Elastic IP, which is also
  the exchanges' allowlisted egress - losing it unmonitors every
  allowlisted account (2026-09-21) and takes the address down until DNS
  follows.
- What the public operator login still leaves open (read in the review
  of 2026-10-23, decision 22): a guesser who rotates addresses is held
  only by the per-address throttle (five failures per fifteen minutes per
  address, in memory, reset on restart), and failed operator sign-ins are
  logged but raise no page. An operator session cannot be revoked short
  of disabling the account: there is no session epoch, so a stolen
  `ops_session` outlives a password change or a 2FA reset for up to
  twelve hours, and `disable` then `enable` revives it - the break-glass
  until an epoch exists is to replace `operator_meta.session_secret`
  (`UPDATE operator_meta SET value = <32 random bytes, hex> WHERE key =
  'session_secret'`), which ends every operator session at once. Each
  password check costs about 100 ms of scrypt in the same process as the
  detection engine, behind a proxy cap of 32 connections on `/admin`: a
  sustained flood is a CPU sink before it is a break-in. First-login
  enrolment binds the second factor to whoever first presents an
  un-enrolled operator's password, which is why no `NOT YET` row may
  exist while the address is public (docs/deployment.md "The public
  address", step 8). TOTP is phishable in real time (no WebAuthn), and
  recovery codes bypass it.
