# Data Retention — FLAGGED FOR LEGAL REVIEW (not yet reviewed)

Status: **Legal/compliance has NOT reviewed this offering** (confirmed at
project kickoff, 2026-07-02). This document exists so engineering choices
don't silently become policy. Nothing here is legal advice; it is a list of
the decisions a lawyer needs to make, with the current engineering defaults.

## What we store, today, by default

| Data | Where | Current retention |
|---|---|---|
| Customer API keys (encrypted) | `accounts` table | Until account deleted |
| Per-asset balance history | `balance_snapshots` | Unbounded |
| Withdrawal/deposit/transfer/trade events | `events`, `seen_withdrawals`, `seen_transfers` | Unbounded, append-only by design |
| Alert history | `alerts` | Unbounded |
| Customer contact identifiers (Telegram chat IDs, phone numbers, emails) | env config | Until changed |

## Questions legal must answer before GA

1. **Jurisdiction**: company operates from Canada (operator email .ca);
   hosting starts on Alibaba Cloud — which region's data-residency rules
   apply to customer financial-activity records stored there? PIPEDA at
   minimum; possibly provincial + PRC data rules depending on region chosen.
2. **Retention period**: financial-activity logs often carry *minimum*
   retention obligations (AML-adjacent) and *maximum* ones (privacy). The
   append-only forensic log is a security feature; a deletion obligation
   (e.g. customer offboarding / right-to-erasure request) conflicts with it.
   Engineering can implement crypto-shredding (per-account encryption keys,
   destroy key on erasure) if legal wants deletion-equivalent without
   breaking the append-only property — decision needed.
3. **Customer consent**: monitoring agreement must cover what we collect,
   that staff can see activity data, and the AI-triage processing (event
   data is sent to Anthropic's API when triage is enabled — currently OFF by
   default partly for this reason).
4. **Breach notification**: thresholds and timelines per jurisdiction if the
   event log (financial privacy) is exposed.

## Engineering defaults until legal says otherwise

- Triage (third-party LLM processing of event data) ships **disabled**.
- No retention pruning implemented — data accumulates; revisit before the
  100-account soak becomes production reality.
- Offboarding today = `status='disabled'` (stops polling, keeps history).
  True deletion path deliberately not built until the erasure-vs-forensics
  question is answered.
