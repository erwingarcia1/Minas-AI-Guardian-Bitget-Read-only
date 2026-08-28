# Data Retention — FLAGGED FOR LEGAL REVIEW (not yet reviewed)

Status: **Legal/compliance has NOT reviewed this offering** (confirmed at
project kickoff, 2026-07-02; still true as of 2026-08-17). This document
exists so engineering choices don't silently become policy. Nothing here is
legal advice; it is a list of the decisions a lawyer needs to make, with the
current engineering practice stated accurately underneath them.

> **2026-08-17: the practice section below was rewritten to match the running
> system.** The previous version said "no retention pruning implemented — data
> accumulates". That had stopped being true: pruning has run hourly in the
> engine since the retention work landed, with windows set in
> `config/rules.yaml`. A policy document that understates what the system
> deletes is as misleading to a lawyer as one that overstates it — the
> erasure-vs-forensics question reads very differently against "we keep
> everything forever" than against "we already delete on a schedule
> engineering chose". The legal questions are unchanged and remain open.

## What we store, and for how long — current practice

Every window below comes from `config/rules.yaml` (`retention:`) or the
declared defaults in `sentinel/rules_schema.py`, and is enforced hourly by
`prune_all` (`sentinel/retention.py`). These are engineering defaults awaiting
legal review, not reviewed policy. A live, customer-readable summary derived
from the same config is on the member portal (`/limits`).

**Enforcement assurance.** Retention is enforced hourly by `prune_all`. A
failure of that job pages the operator (HIGH) and drops the
`sentinel_retention_ok` metric to 0 until a pass succeeds, so the deletion
described below cannot stop silently (#73).

| Data | Where | Current retention |
|---|---|---|
| Forensic event log (withdrawals, trades, logins, config changes) | `events` | 730 days |
| Alerts and their triage notes | `alerts`, `alert_triage` | 730 days |
| Operator pages, delivery records (every recipient, every channel) | `operator_pages`, `deliveries` | 730 days |
| "That was me" marks, threshold changes, password-reset tokens, snoozes | `expected_activity`, `config_changes`, `password_resets`, `snoozes` | 730 days |
| Dedup records (seen fills/transfers/withdrawals) | `seen_*` | 365 days — bounded below by correctness: shorter than dedup and a re-polled record re-alerts |
| Balance snapshots | `balance_snapshots` | 7 days (latest per account kept) |
| Permission snapshots | `permission_snapshots` | 730 days (latest per account kept) |
| Customer API keys (encrypted at rest) | `accounts` | Until the account is deleted |
| Customer contact details (emails, phones, Telegram handles) | `accounts` | Until changed or the account is deleted |
| Baselines (known addresses, traded pairs, transfer routes, fee EMA) | never pruned | Deleting a baseline row invents an anomaly |
| Identity & auth (operators, portal members, 2FA enrolments) | never pruned | Offboarding is `disable`, never deletion (G-50) |

Hosting: AWS Tokyo (ap-northeast-1) — the Alibaba Cloud plan this document
originally described was never used. Data residency questions below should be
answered for AWS Japan.

## Questions legal must answer before GA

1. **Jurisdiction**: company operates from Canada (operator email .ca);
   hosting is AWS Tokyo (ap-northeast-1) — which region's data-residency
   rules apply to customer financial-activity records stored there? PIPEDA at
   minimum; possibly provincial rules plus Japanese law (APPI) for the
   hosting region.
2. **Retention period**: financial-activity logs often carry *minimum*
   retention obligations (AML-adjacent) and *maximum* ones (privacy). The
   730-day windows above are engineering defaults, not answers to either. The
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

## Engineering practice until legal says otherwise

- Triage (third-party LLM processing of event data) ships **disabled**.
- Retention pruning runs hourly with the windows above; `0` in config means
  "keep forever" and is a legitimate per-table choice.
- Offboarding today = `status='disabled'` (stops polling, keeps history).
  True deletion path deliberately not built until the erasure-vs-forensics
  question is answered (#46 tracks it).
- The customer-visible summary (`/limits` on the portal) is derived from the
  same config the pruner reads, so the page and the practice cannot disagree.


## Erasure on request — the interim answer (decision 6, 2026-08-27)

Until the lawyer answers whether crypto-shredding counts as erasure, an
erasure request is answered by **hard deletion, now**: from the portal's
security page (password, and the second factor when on) or by Minas on a
request that arrives by phone or email (`sentinel erase <email> --as <who>`).

What goes, immediately: the monitoring credentials and every record for the
accounts the person is the primary contact of, their portal login and its
settings, and their contact details. On accounts where they are only the
second contact, they are removed as a contact.

What remains, deliberately: the account row as a tombstone — label replaced,
nothing identifying on it — carrying one final event, `data_erased`, that
states the date, the number of records removed, and that nothing before it
can be reconstructed. **The forensic gap is recorded as a gap**, never left
as silence that looks like nothing happened. The person receives a written
confirmation saying exactly this.
