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
  at boot rather than sitting in a dotfile; no inbound ports except SSH;
  dashboard/metrics loopback-only.
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
  organizational at this scale (flagged honestly: no RBAC dashboard exists in
  Phase 2 — the dashboard is read-only and network-gated instead).

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
- No RBAC on the dashboard (A4) — network-gated instead, acceptable at pilot
  scale, not at 1,000 accounts with staff turnover. Revisit in Phase 3.
- SQLite file readable by host root until Postgres + disk encryption.
