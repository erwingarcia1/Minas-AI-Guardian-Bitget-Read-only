# Decisions needed

Questions that need a product call before the work behind them can be built.
Each one has the options, a recommendation, and what it blocks. Raised during
autonomous backlog work — see `docs/AUTONOMOUS-LOG.md` for the sessions.

Nothing here is blocked on engineering. Every item is a choice about what the
product should do, and each was skipped rather than guessed at.

**Read this first:** decisions 1 and 2 are worth answering together and unblock
the most. Decision 6 is not a product call at all — it needs a lawyer, and it
is the longest lead time on the list, so it's worth starting even if the rest
waits. Decisions 2 and 7 share a mechanism: both need a scheduled drill, and
whoever builds one should build it to cover both.

---

## Summary

| # | Decision | Blocks | Recommendation |
|---|---|---|---|
| 1 | Escalation timings | #27 (G-23), part of #26/#30 | 15 / 30 / 60 min, secondary at 15 |
| 2 | Fire drill cadence + what customers see | #37 (G-33) | Weekly, drill account, customers see result only |
| 3 | Incident page "what to do next" wording | #29 (G-25) | Three fixed actions, exchange-specific link |
| 4 | Alert channel defaults + quiet hours | #24 (G-20) | Quiet hours non-critical only, 22:00–07:00 local |
| 5 | PDF export for incidents | #29 (G-25) | Defer — print stylesheet now |
| 6 | Retention periods (**needs a lawyer, not you**) | #47 (G-43), #46 (G-42) | Start legal review now; 24 months interim |
| 7 | External uptime monitoring: latency, vendor, what customers hear | #63 | Dead-man's switch, 15 min, third-party vendor |

---

## 1. Escalation timings — blocks #27 (G-23)

**The question.** An unacknowledged CRITICAL alert should escalate. After how
long, to whom, in what order, and how many times before it stops?

`config/rules.yaml` already carries
`alerting.escalation_unacknowledged_minutes: "15"` and the alerts table has an
`escalated_at` column, so one escalation step is half-wired. What doesn't exist
is the ladder.

**What has to be decided:**

- **First escalation delay.** 15 minutes is already in the config. Is that the
  intended value or a placeholder?
- **The ladder.** G-23 says escalation "re-sends on a louder channel", reaches
  the secondary contact, and pages an operator. Are those three separate rungs
  with their own delays, or one combined step?
- **Termination.** Does it repeat forever until acknowledged, or stop after N
  attempts? Forever risks a customer asleep at 3am getting texts until morning;
  stopping risks a real theft going quiet.
- **Night behaviour.** Does escalation respect quiet hours? (Interacts with
  decision 4 — I'd say no, CRITICAL escalation ignores quiet hours, but that's
  the point of asking.)

**Options**

| | Ladder | Termination |
|---|---|---|
| **A** (recommended) | 15 min → louder channel + secondary contact; 30 min → operator paged; 60 min → repeat operator page hourly | Customer-facing stops after the 30-min rung; operator page repeats until acknowledged |
| **B** | Single step at 15 min: everything fires at once | Stops after one escalation |
| **C** | 5 / 15 / 30 min, more aggressive | Repeats until acknowledged |

**Recommendation: A.** It gets a second human involved inside half an hour
without texting a sleeping customer indefinitely, and it puts the
never-give-up behaviour on the operator, who is on shift, rather than on the
customer, who isn't. B is too coarse — "louder channel" and "wake up a Minas
operator" are genuinely different severities of response. C's 5 minutes will
fire on every alert a customer takes a coffee break through.

**Why I didn't just pick one:** these numbers are the product's promise about
how fast someone responds. They belong in the customer agreement, and they're
the sort of thing that's expensive to loosen later.

**Note:** the operator-paging rung needs #53 (G-49, operator alerting channel)
built first — there is no operator paging route in the repo at all today. #53
is on the build list, so this decision won't be blocked by the time it's needed.

---

## 2. Fire drill cadence and what customers see — blocks #37 (G-33)

The issue calls this "← highest-value new feature", and I agree with its
reasoning: an expired Telegram token, a dead SMS account and a broken detector
all look exactly like a quiet week. But almost every choice in it is a product
decision.

**What has to be decided:**

**a) Cadence.** Weekly, daily, monthly? Trade-off is confidence versus alert
fatigue and SMS cost (every drill that travels "the entire path" sends a real
text to a real phone, and ClickSend charges per message).

**b) Whose account?** Options:

- A dedicated Minas-owned drill account with a real exchange key. Cleanest —
  no customer sees a simulated theft on their own account — but it only proves
  the path works for *that* account's configuration.
- Each customer's real account, with synthetic events injected. Proves each
  customer's real delivery path, which is the actual claim being made. But it
  writes simulated theft events into a real customer's forensic log, which is
  a genuine problem: that log exists to be evidence.

**c) Do customers receive drill alerts?** G-33 says the drill must reach "my
phone" and be "clearly labelled as a drill". So the customer gets a text saying
`[DRILL]` — weekly, forever. That is either reassuring or infuriating, and
which one it is depends on the customer.

**Options**

| | Cadence | Account | Customer sees |
|---|---|---|---|
| **A** (recommended) | Weekly | Dedicated drill account | Portal shows last drill + result + latency; no drill texts |
| **B** | Weekly | Each customer's account | A `[DRILL]` message on every channel, weekly |
| **C** | Monthly full drill + daily channel health-check | Dedicated drill account | Portal only |

**Recommendation: A**, with an on-demand "test my alerts" button so a customer
who wants the proof on their own phone can have it whenever they like. That
delivers the evidence G-33 is really about — the portal line saying *"last
drill: 3 hours ago, detected in 1.2s, delivered in 4.1s"* — without a weekly
text nobody asked for and without writing fake theft into real forensic logs.
It technically doesn't satisfy the criterion as literally written ("delivery to
my phone"), which is exactly why this is your call and not mine.

**Also needs deciding:** does a failed drill page an operator as an outage
(criterion 4 says yes) — that again needs #53.

---

## 3. Incident page "what to do next" wording — blocks #29 (G-25)

G-25 requires the incident page to state "in plain words what to do next
(revoke the key, contact the exchange, move funds)".

**Why this isn't an engineering choice.** This is the most consequential text
in the product. It is read by a frightened person who has just been told their
money may be moving, and it tells them to take irreversible actions. Getting
the order wrong makes it worse: telling someone to move funds *before* revoking
the attacker's key can hand the attacker the destination.

There is also a liability edge. "Contact the police" and "contact the exchange"
are advice, and if the wording implies Minas has verified a theft that turns
out to be the customer's own trading bot, that's a problem.

**What has to be decided:**

- The canonical action order. My engineering view: **revoke the API key first**
  (stops the bleeding, reversible, costs nothing if it's a false alarm) →
  **check the exchange's own withdrawal history** (confirms independently) →
  **contact exchange support** (they can freeze/reverse in some cases) → **move
  funds** last (irreversible, and pointless while a live key exists).
- How certain the page is allowed to sound. A detection is evidence, not proof.
- Whether the wording differs by rule — a `withdrawal_to_unseen_address` and a
  `never_touched_pair_activity` do not warrant the same response.
- Who signs it off. This reads as legal/comms as much as product.

**Recommendation:** write it as three fixed steps in the order above, phrased as
"if this wasn't you", with a per-exchange deep link to the key-revocation page
(those URLs I can look up and build). Hold the per-rule variation until the
common path is agreed.

**What I can build without this:** everything else on that page — the timeline,
the balances, detection → notification → acknowledgement. Only the advice block
is blocked. Say the word and I'll build the page with the advice block stubbed.

### Update 2026-08-13: one defect in the shipped text fixed, the wording still open

The alert runbook (`sentinel.alerts.base.runbook`) said, as step 2, *"revoke/
rotate the API key(s) on this account"*. **The Guardian's own key is one of
those keys.** It is read-only and cannot move funds, so revoking it takes
nothing away from an attacker; what it does is switch the monitoring off at the
moment it matters most — and silently, because nobody connects "I revoked my
keys" with "I am no longer being watched". A frightened customer revoking
everything is the likely reading, not an edge case.

That is a safety defect rather than a phrasing preference, so it was fixed
without waiting for this decision: step 2 now says to revoke keys you do not
recognise, states that the Guardian's key is read-only, and says plainly that
revoking everything leaves the account unwatched until a new key is issued. It
closes with a link to the portal to check whether monitoring is still running.

**This does not close decision 3.** Still owned by legal/comms, unchanged:

- whether the text is advice ("consider") or instruction ("do this now");
- "contact Bitget support" and "freeze the account" — the liability wording;
- how certain any of it may sound, given a detection is evidence, not proof;
- whether it should vary per rule.

The runbook is now a single function so that review has one place to land, and
the customer-facing gating (CRITICAL/HIGH only) is separate from the wording.
There is still **no self-serve page for replacing a revoked key** — onboarding
is operator-driven — so the link points at the portal front door rather than a
flow that does not exist. That page is worth building before this advice gets
much traffic.

---

## 4. Alert channel defaults and quiet hours — blocks #24 (G-20)

**What has to be decided:**

- **Defaults for a new customer.** `config/rules.yaml` currently routes
  CRITICAL to telegram + team + sms + email, HIGH to telegram + team + email,
  MEDIUM/LOW to digest. Is that the per-customer default, or is it just the
  operator's own routing?
- **Quiet hours.** G-20 says non-critical only. Which timezone — the customer's
  local time (we'd need to store it; there's a local-time display commit in the
  history suggesting we have some of this) or a fixed one? What's the default
  window, and are quiet hours on or off by default?
- **The floor.** "Critical alerts cannot be fully switched off — at minimum one
  channel stays on." Which channel is the one that can't be turned off, and what
  happens if the customer removes the destination for it (deletes their phone
  number)? That's the same class of hole as the SMS opt-out that CRITICAL
  overrides today.
- **What happens to a suppressed non-critical alert** — dropped, or queued and
  delivered when quiet hours end? Dropping is simpler; queueing is what people
  expect.

**Recommendation:** quiet hours **off** by default, non-critical only, customer
local time, default window 22:00–07:00 when enabled; suppressed alerts **queued
and delivered in the next digest** rather than dropped; the un-disableable
CRITICAL channel is **email**, because it's the one destination we can always
reach and the one that costs nothing per message. If a customer removes their
email, that's an onboarding-blocking validation error rather than a silent
downgrade.

---

## 5. PDF export for incidents — blocks part of #29 (G-25)

G-25's fourth criterion: "I can export it as a PDF for the exchange or the
police."

**The question is whether this is worth a dependency.** The repo has no PDF
library and no headless browser. Adding one (WeasyPrint, ReportLab) is a real
dependency with a real supply-chain surface on a security product, for a
feature whose actual requirement is "something official-looking I can send to
someone".

**Options:** (A) print stylesheet + "Print to PDF", zero dependencies, works
today in every browser; (B) server-side PDF generation, exact control,
new dependency; (C) defer.

**Recommendation: A now, revisit if a customer actually asks for B.** A browser
print-to-PDF produces a perfectly acceptable document for an exchange support
ticket. I'd build the print stylesheet as part of the incident page and tick
the criterion with a note. Flagging rather than deciding, since "export as PDF"
may mean something more specific to you.

---

## 6. Retention periods — blocks #47 (G-43), touches #46 (G-42)

**This one is not yours to answer alone — it needs a lawyer**, and it has the
longest lead time on this list. Worth starting now even if everything else
waits.

`docs/data-retention.md` already exists and is honest about being unreviewed;
it lists the questions well. What's missing is the answers, and the code that
enforces them.

**Current state in code:** `prune_balance_snapshots` keeps **7 days** and is
the only retention that exists. `events`, `alerts`, `seen_withdrawals`,
`seen_transfers` and `sms_deliveries` grow forever.

**The genuine conflict** (already identified in the doc, restating because it
drives the engineering): the append-only forensic log is a *security* feature —
it's what reconstructs an incident — and a right-to-erasure request is a legal
obligation to destroy it. Those cannot both be fully honoured.

**Options for resolving that conflict:**

- **A — Crypto-shredding.** Per-account encryption keys; erasure destroys the
  key, leaving rows in place but unreadable. Preserves the append-only property
  and row counts. Most work, and the cleanest answer to "delete my data"
  without lying about it. Needs legal to accept it as deletion-equivalent.
- **B — Hard delete on request**, accepting the forensic gap.
- **C — Time-boxed retention**: everything older than N months is deleted on a
  schedule, erasure requests just accelerate it.

**Recommendation:** ask the lawyer specifically whether **A** counts as erasure
in the relevant jurisdiction, because it's the only option that doesn't trade
one obligation for the other. Meanwhile set an interim engineering default of
**24 months** for `events` and `alerts` — long enough to cover any plausible
AML-adjacent minimum, short enough to be defensible as not-indefinite — and
implement the pruning behind a config value so the number changes without a
deploy once the real one is known.

**What I can build without the legal answer:** the pruning mechanism itself,
driven by a configurable period, applied to every unbounded table. That's most
of criterion 3 and it's useful regardless of what number goes in. Blocked
without it: publishing the policy (criterion 4) and the lawyer sign-off
(criterion 2, which no amount of engineering will produce).

---

## 7. External uptime monitoring — blocks #63

**The question.** Nothing tells us if the Guardian itself stops running. G-10
(#14) covers losing sight of an *account* while the Guardian is up; it cannot
cover the process or the host dying, because no in-process alarm can — an alarm
that depends on the thing it monitors is not an alarm.

The failure is total and completely silent: the box goes away, every customer
stops being monitored, and the only signal is an absence of alerts, which is
precisely what a quiet week looks like.

Until now this was prose inside #53's scope note, which is how it stayed
untracked for so long.

### The constraint that shapes it

The deployment is closed inbound on purpose: dashboard, portal and metrics bind
loopback and publish only to `127.0.0.1`, and the security group allows SSH from
an admin IP only. So **an external monitor cannot poll a health endpoint**
without weakening that, and the dashboard can't be the endpoint — it has no auth
and shows customer names and contact details.

### What has to be decided

**a) Detection latency.** How long may the Guardian be down before someone is
told? This is a promise about the product, not a tuning knob — it is the
window in which a customer is unwatched and nobody knows. Too tight pages on
every deploy; too loose and an hour of no monitoring passes unnoticed.

**b) Vendor or self-hosted.** The parked hosting decision, finally written down:

| | Approach | Trade |
| --- | --- | --- |
| **A** (recommended) | Dead-man's switch — the box pushes a heartbeat, a third party alerts on silence | Fits the egress-only posture exactly, no inbound at all, catches a *wedged* process as well as a dead one. Costs an outbound allowance and a third party knowing the service exists (no customer data) |
| **B** | External poller against a minimal `/healthz` | Requires opening a port that doesn't exist yet, and widens the attack surface of a box holding customer credentials |
| **C** | CloudWatch instance checks + alarm | No third party, but it lives in the same account and region as the thing it watches — only partly independent |
| **D** | A second Guardian watching the first | Most infrastructure, and correlated failure unless it's genuinely separate — at which point it is A with more moving parts |

The property that decides it: **the monitor must not share fate with the
monitored.** Same process is useless, same host is useless, same provider and
region is partial. Only A has that separation by default.

**c) What customers are told.** If the Guardian was down for an hour, those
customers were not being watched. Do they hear about it, and when? Touches #48
(G-44, customers understand the limits) and #38 (G-34, system status is
visible).

### Recommendation

**A, at 15 minutes, with a third-party dead-man's switch.** Fifteen minutes is
short enough that a real outage is caught inside one fast-loop customer
expectation and long enough to survive a deploy or a reboot. Revisit from the
first month of real data rather than from this paragraph.

On (c): tell the customer, in the digest rather than as an alert — a gap in
monitoring is not an emergency to wake them for, and hiding it is the kind of
thing that is much worse when discovered later than when disclosed.

### The part that needs building carefully either way

The external monitor's alert destination **cannot route through the Guardian**,
so it needs its own credentials — which can drift out of sync with the operator
channels and will not be exercised except during a real outage. #63 carries an
acceptance criterion requiring a scheduled drill for exactly this, sharing the
mechanism with G-33 (#37). An alerting path exercised only when it matters is
one you should assume is broken.

---

## 8. How long may a customer silence a CRITICAL? — **ANSWERED 2026-08-13**

**Answer: CRITICAL 1 hour, HIGH 4 hours, MEDIUM and LOW 24 hours.** Shipped.

### The reasoning, recorded because the numbers are meaningless without it

Acknowledging already stops escalation and already says "I have seen this". A
snooze only suppresses **repeats**. So the only legitimate reason to suppress
repeats of a CRITICAL is that you are dealing with it *right now* — which is a
short window by definition.

A drain unfolds over minutes to hours. At 24 hours, a CRITICAL snooze is long
enough for the rest of one to finish in silence. Re-snoozing costs one tap and
forces a fresh decision, which is the right friction to put in front of
continuing to ignore a CRITICAL.

The 24 on MEDIUM and LOW keeps the original derivation: it is the digest
cadence, so no snooze outlives the next summary the customer receives.

### Where it lives

`alerting.snooze_cap_hours` in `config/rules.yaml`, so the caps are tunable
without a deploy (G-12) and every change to them is now recorded against a name
(#16). `sentinel/snooze.py` carries the same table as the built-in fallback.

A configured cap of **0** means that severity cannot be snoozed at all — option
3 from the original writeup, still available without a code change. That
required care: 0 is a falsy value standing for a real decision, so the lookup
uses `is not None` rather than truthiness. Read the other way, a deliberate
zero looks like "not set" and the built-in one-hour cap comes back, silently
re-enabling the suppression the owner had turned off. That is the falsy-sentinel
pattern in CONTRIBUTING.md, and it is pinned by a probe.

An unrecognised severity gets the **shortest** cap, not the longest: for a
feature whose purpose is suppression, the only safe direction to fail in is
towards less of it.

### Still open, smaller

The durations offered in the dropdown are 1h / 4h / 24h, filtered by the
severity's cap. Same species of question as G-23's escalation timings, and same
answer needed from real usage rather than from this file.

---

## 9. What an incident page tells a customer to do next (G-25, #29)

The incident page is built and shipped — one page per incident, the
detection → notification → acknowledgement timeline, balances as they were at
the time, and print-to-PDF export. **Three of the four criteria are done.**

The fourth is not engineering. "It states in plain words what to do next
(revoke the key, contact the exchange, move funds)" is advice with
consequences, and the wording *is* the deliverable.

### Why I did not write it

Every candidate sentence commits us to something:

- **"Revoke your API key."** Almost always right, and it destroys the
  attacker's access — and ours. Monitoring stops at the moment the customer
  most needs it, and a customer in a panic will not be reading about how to
  issue a replacement.
- **"Move your funds."** Moving funds during an active compromise can hand the
  attacker a fresh destination to watch, and a customer moving to a wallet
  they set up in a hurry is a second incident waiting to happen.
- **"Contact the exchange."** Safe, and nearly useless on its own without
  telling them what to ask for. Which reference do they quote? Do they ask for
  a withdrawal freeze? That is exchange-specific.
- **Saying nothing** leaves a page that describes a theft and offers no next
  step, which is its own kind of failure.

There is also a liability dimension I am not in a position to weigh. Advice
that is followed and goes badly is different from advice that was never given.

### What is needed

The literal text, per severity or per rule, and a decision on whether it is
advice ("consider") or instruction ("do this now"). Once there is text, wiring
it in is an hour: the page already has the slot.

### Recommendation

Start with the narrowest true thing and expand later: for a CRITICAL
withdrawal-path alert, name the exchange's own support route and the reference
to quote, and say plainly that Minas monitors and does not act. Leave "revoke"
and "move funds" out until there is a considered position on both.

---

## 10. Should a portal credential be scoped to an account? (#61)

The **affordance** is fixed and shipped: the ops dashboard now lists, by name,
every account a portal credential will open, before the operator clicks. An
operator can no longer share three accounts while believing they shared one.

The **model** is unchanged, and changing it is your call.

Today portal identity is the email address. One credential, and
`member_accounts` returns every account that address is a contact on. It is
coherent, and it is what a customer with several accounts probably wants.

### The question

Is "one person, one login, everything they are a contact on" the right model,
or should a credential grant exactly the accounts someone chose?

### Options

1. **Keep it.** Now honest about its scope. No migration. A customer with four
   accounts signs in once. The risk is that sharing access to one account is
   always sharing access to all of them, and the only remedy is to remove the
   person as a contact — which also stops their alerts.
2. **Per-account grants.** A join table between `portal_members` and
   `accounts`; a credential opens exactly what was granted. Correct, and a
   migration. Existing members would need grants backfilled from their current
   contact rows, which is mechanical.
3. **Per-account credentials.** One password per (email, account). Most
   isolated, worst to live with — four accounts means four passwords for one
   person.

### Recommendation

**2, and not urgently.** It is the right model and the migration is
backfillable, but the affordance fix removes the acute risk: nobody can now
share more than they intended without being told. Do it when a customer
actually asks for account-scoped access, not before — the cost of guessing
wrong here is a data model that has to change again.

### Why it becomes expensive later

Once customers routinely have several accounts with shared contacts, changing
the scoping model is a migration that touches live credentials. It is cheaper
now than it will be, which is an argument for deciding early even if building
late.

---

## 11. Billing: provider, plans, prices, and what a lapse does (G-39/#43, G-40/#44)

**No code written**, per the standing instruction. Full implementation plans are
in the issues, ready to build once these are answered.

### What I need

1. **Provider.** Stripe and Paddle differ in whether they are merchant of
   record, which decides who owes VAT on a UK-based service selling worldwide.
   That is a tax question before it is an engineering one.
2. **Plans and prices**, and specifically **per account or per customer**. The
   data model differs and it is not a small change afterwards.
3. **Trial?** And what happens at the end of one.
4. **What a lapse does.** The honest options are different products:
   keep monitoring and chase; stop monitoring after a grace period; or —
   never — keep monitoring and stop alerting.
5. **Grace period length and notice schedule**, if there is a grace period.

### The engineering constraint, whichever you choose

**Guardian must never stop monitoring because billing broke**, and **a customer
must never believe they are monitored while they are not.**

Detection reads `accounts.status`; billing must never write it. A lapse sets a
separate flag that an explicit, attributable action acts on. The indirection
exists so a webhook bug cannot silently switch off monitoring for the fleet —
which is the same failure this project has now found technically three times
(#57, #67, the ten-hour red CI), arrived at commercially.

### Recommendation

Grace period, then stop, with the stopping loud and repeated: a banner on
**every** portal page, not a billing page; an email that says *monitoring has
stopped* in those words rather than "your subscription has ended"; and an
operator page, because ending monitoring for a customer who merely changed
cards is a business event somebody should see.

---

## Not a decision, but needs saying: #16's remaining criterion

G-12's third criterion — "threshold changes are recorded, showing **who**
changed what and when" — is not a product decision, it's a dependency. There
are no operator accounts (#54), and `rules.yaml` is edited on disk, so there is
nobody for the process to attribute a change to. Recorded here only because
someone reading this list will wonder why #16 is still open. Full reasoning is
on the issue.
