# Decisions needed

Questions that need a product call before the work behind them can be built.
Each one has the options, a recommendation, and what it blocks. Raised during
autonomous backlog work — see `docs/AUTONOMOUS-LOG.md` for the sessions.

Nothing here is blocked on engineering. Every item is a choice about what the
product should do, and each was skipped rather than guessed at.

**Open today (2026-10-26):** 6 (needs the lawyer; #47), 7's vendor (#63),
8's dropdown durations (after real usage), 11 and 18 (owner reconsidering),
21b/21e/21h/21i, 26d, 26e and 26b's two further tripwires, and 27's open
facts and approval (the privacy policy). Everything else
below is answered and built, and deployed where there is a server to deploy
to - the app waits on the store accounts (section 21, "Not built yet"); the
sections stay as the record.

---

## Summary

| # | Decision | Blocks | Recommendation |
|---|---|---|---|
| 1 | Escalation timings | ~~#27~~ | **ANSWERED 2026-08-19** — see section 1 |
| 2 | Fire drill cadence + what customers see | ~~#37~~ | **ANSWERED 2026-08-27** — A: weekly, dedicated drill account, portal result, no customer texts, on-demand test button; real-delivery recency derived per customer; see section 2 |
| 3 | Incident page "what to do next" wording | ~~#29~~ | **ANSWERED 2026-08-27** with 9 — one canonical text; reversible steps only; observed facts, no inferences; see section 3 |
| 4 | Alert channel defaults + quiet hours | ~~#24~~ | **ANSWERED 2026-08-27** — off by default, non-critical, local time 22:00–07:00, queued to digest, email un-disableable, unknown tz = cannot enable; see section 4 |
| 5 | PDF export for incidents | ~~#29~~ | **ANSWERED 2026-08-27** — print stylesheet + browser print-to-PDF, no library; see section 5 |
| 6 | Retention periods (**needs a lawyer, not you**) | #47 (G-43), ~~#46~~ (G-42) — lawyer pending | **ANSWERED 2026-08-27 interim** — lawyer engaged (crypto-shredding question); 24 months configurable; erasure = hard delete + recorded gap + told; **6b ANSWERED 2026-10-26**: an erased person's details on other people's accounts are replaced with [erased], and the account is told; see section 6 |
| 7 | External uptime monitoring: latency, vendor, what customers hear | #63 (vendor still owner's) | **ANSWERED 2026-08-27** — dead-man's switch, third party, 15 min, gaps in the digest; see section 7 |
| 8 | How long may a customer silence a CRITICAL? | — | **ANSWERED 2026-08-13** — 1 h / 4 h / 24 h caps, `alerting.snooze_cap_hours`; shipped; see section 8 |
| 9 | What the incident page tells a customer to do next | ~~#29~~ | **ANSWERED 2026-08-27** with 3 — see section 9 |
| 10 | Portal credential scoped to an account | ~~#61~~ | **ANSWERED 2026-08-27** — per-account grants, sooner than when asked; backfill from contact rows; built, v1.10.0; see section 10 |
| 11 | Billing: provider, plans, prices, lapse | #43 (G-39), #44 (G-40) (both closed not-planned 2026-08-27; the decision stays open) | **OPEN — owner reconsidering 2026-08-27**; plan-only, nothing dependent to be built |
| 12 | Restart-on-unhealthy: sidecar, systemd timer, or page-only | ~~#81~~ | **ANSWERED 2026-08-21** — systemd timer accepted; see section 12 |
| 13 | Browser push notifications | ~~#23~~ | **ANSWERED 2026-08-21** — won't-do; no-JS posture stands; see section 13 |
| 14 | Exchange key-creation instructions: screenshots or text | ~~#8~~ | **ANSWERED 2026-08-21** — text-only, dated and interface-named; see section 14 |
| 15 | Member self-serve pause | ~~#10~~ (reworded) | **ANSWERED 2026-08-23** — 24-hour cancellable countdown, never immediate; built, v1.10.0; see section 15 |
| 16 | Deep incident links in alert emails | ~~#20~~ (reworded) | **ANSWERED 2026-08-23** — no links; non-clickable reference + portal lands on the incident; built, v1.10.0; see section 16 |
| 17 | Self-serve signup | ~~#6~~ (closed not-planned 2026-08-27; waitlist is the recorded fallback) | **ANSWERED 2026-08-23** — no; behind billing + legal, waitlist as fallback; see section 17 |
| 18 | Sibling-alert definition width + production backfill | ~~#99~~ (built conservatively) | **OPEN — owner reconsidering 2026-08-27**; nothing dependent to be built |
| 19 | Dashboard 2FA: mandatory or opt-in, and last-operator recovery | ~~#87~~, ~~#82~~ | **ANSWERED 2026-08-27** — mandatory; host-access recovery, attributed and logged; see section 19. *From 2026-10-23 the host access is SSH with the key pair at the Elastic IP (22e)* |
| 20 | The member portal is published to the internet on Tailscale Funnel | ~~#88~~ (closed 2026-10-24; evidence in its closing comment) | **ANSWERED by the owner, August 2026; recorded 2026-09-23** — Funnel for the portal only; dashboard and metrics stay Serve; see section 20. *Superseded in full 2026-10-23 by 22: no Tailscale, removed in one go* |
| 21 | Native iOS and Android apps, and the JSON API they need | the apps | **PARTLY ANSWERED 2026-10-22** — customers only, React Native, in this repo, company branding, sign-in and v1 API scope as recommended; push and the web portal open; see section 21 |
| 22 | One public address, guardian.minastrade.com; no Tailscale | ~~#88~~ (the address); the hostname the apps are built against | **ANSWERED 2026-10-22 and 2026-10-23** — one hostname; the dashboard at `/admin` behind its sign-in and mandatory second factor, no IP allowlist; TLS by Caddy on the instance; Tailscale removed in one go, host access by SSH with the key pair at the Elastic IP; no overlap period for the old address; slow gates waived for the release that ships it; see section 22. **Built and deployed 2026-10-24 (v1.14.0)**; the email to members went the same day (22d); left: the Tailscale admin-console removal (the owner's) |
| 23 | A QR-code encoder (`segno`) in the two-factor enrolment path | the QR PR | **ANSWERED 2026-10-24** — `segno` accepted by the merge of the QR PR (#130); the three renderings read by Google Authenticator the same day; shipped in v1.15.0 and deployed; see section 23 |
| 24 | The ops dashboard carries the Minas identity, dark like the portal (reverses the dashboard half of decision 2 on #82) | the dashboard redesign PR | **ANSWERED by the owner 2026-10-24** — brand colours and logo on every dashboard page on the portal's dark ground; the anti-impersonation intent moves to an operations marking (the Operations lockup, the operator's name, the h1, the /admin path); see section 24. **Deployed 2026-10-24 (v1.15.0)** |
| 25 | The portal's front door is a landing page; the sign-in moves to `/login`; an ended session lands there | the landing-page PR | **ANSWERED by the owner 2026-10-24** — what the Guardian is, how it works and what it cannot do before any sign-in, on the web and in the app; the one address named on it; every path without a session sent there (303); a `ui-parity` skill and hook so a UI change lands on every surface; see section 25. **Deployed 2026-10-24 (v1.16.0)** |
| 26 | A contact move and the old address's portal login; what a refused notice pages; where a reset link goes | the reset and email-signals PR; the notice-pages PR | **PARTLY ANSWERED** — 26a: a contact move revokes the old address's scoped access (decided in session; **deployed 2026-10-25, v1.17.0**); 26b: a refused password-changed, two-factor-off or alert-email-changed notice pages Minas at CRITICAL, whatever a connected Telegram chat took (**answered by the owner 2026-10-25; deployed 2026-10-26, v1.18.0**); 26c: the reset link by email only (**answered by the owner 2026-10-25; deployed 2026-10-26, v1.18.0**); open: 26d (a "no login yet" email), 26e (connecting a Telegram chat tells the member nothing) and 26b's two further tripwires; see section 26 |
| 27 | The privacy policy at `/privacy` (Google Play needs it in the listing and in the app) | the Play production release | **PARTLY ANSWERED 2026-10-26** — drafted on the site from the owner's answers (contact, "Minas AI", Anthropic named, keys and contacts kept until the customer asks, erasure fixed by 6b); eight facts open for the owner and the lawyer, then approval; see section 27 |

---

## 1. Escalation timings — **ANSWERED 2026-08-19** (was: blocks #27)

**The answer, from the owner** (triggered by a real complaint — a secondary
contact receiving every alert for accounts she is an emergency contact on):

A secondary contact is an **emergency contact, not a copy-to**, and the rule
is about the contact, not the channel. Two gates: **severity** (LOW and
MEDIUM never reach a secondary at all) and **time-and-acknowledgement** (the
primary is told immediately as today; the secondary only if still
unacknowledged after CRITICAL 15 min / HIGH 60 min, both from config —
`alerting.escalation_unacknowledged_minutes` and
`escalation_unacknowledged_minutes_high`). Acknowledging a CRITICAL sends
the secondary exactly one closing note — acknowledgement must not silently
silence the emergency contact, or a compromised primary session could. Cap:
two messages per incident to a secondary. The escalation message states why
they are receiving it, and carries no balances, amounts, addresses or
one-tap links. Built 2026-08-19; the original open question below is kept
for the record.

### The original question (now answered)

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

## 2. Fire drill cadence and what customers see — **ANSWERED 2026-08-27** (was: blocks #37)

> **Answer (owner, 2026-08-27): option A.** Weekly drill on a DEDICATED
> Minas drill account; the result shown in the portal; NO weekly customer
> texts; plus an on-demand "test my alerts" button for a customer who wants
> the proof on their own phone. A failed drill pages an operator.
>
> **Reasoning.** A weekly `[DRILL]` text nobody asked for trains customers to
> skim the channel that must never be skimmed, and injecting simulated theft
> into a real customer's forensic log corrupts the evidence that log exists
> to be. The dedicated account proves the machinery end to end without either
> cost. **The addition that closes the gap A leaves:** the drill proves the
> machinery, not that a given customer's address still accepts mail. So
> derive "last successful delivery per channel per customer" from REAL
> traffic — `alerts.delivered_to` and `sms_deliveries` — and surface
> staleness in the portal and to operators. Real deliveries are the evidence
> for the customer; the drill is the evidence for the pipeline. Criterion 4
> (a failed drill is an outage) is accepted as written; it shares decision
> 7's dead-man's-switch drill machinery.


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

## 3. Incident page "what to do next" wording — **ANSWERED 2026-08-27** with 9 (was: blocks #29)

> **Answer (owner, 2026-08-27), jointly with decision 9: ONE canonical text
> for the runbook and the incident page, from one source so they cannot
> drift.** Order: revoke unrecognised keys → check the exchange's own
> withdrawal history → contact exchange support with a reference. "Move your
> funds" is NOT a step; it is mentioned only as something to raise with the
> exchange.
>
> **The line: instruct for the reversible, never for the irreversible.**
> Revoking an unrecognised key and reading a history are reversible and cost
> nothing if the alert was the customer's own bot; moving funds is not, and
> during a live compromise it can hand the attacker a destination.
>
> **Tone: certain about what was observed, never about what it means.** "A
> withdrawal was initiated to an address this account has never used" is a
> fact and may be stated flatly. "Your account is compromised" is an
> inference and may not. This settles the advice-vs-instruction question
> WITHOUT hedging language — the Gmail summariser (update above) proved that
> hedges do not survive summarising, so the safety has to live in what is
> asserted, not in qualifiers around it. Each sentence must read correctly
> in isolation; a test pins that. Per-rule variation is held. Legal reviews
> later and does not block.


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

### Update 2026-08-13: the reading environment strips our hedging

Worth having on the record before the advice-vs-instruction question is
settled, because it changes what "advice" can even mean here.

Gmail's AI Overview summarised our test alert as:

> "You must immediately freeze account, revoke unrecognized API keys"

The alert said no such thing. It carried a conditional heading — *"If this was
not you:"* — followed by numbered steps written as imperatives. The summariser
dropped the heading and kept the imperatives, converting advice we hedged into
an instruction we never gave. A customer who reads only the summary — and on a
phone, many will — receives a command from us.

**The consequence for decision 3:** choosing "advice" wording is not enough,
because a conditional *preamble* does not survive summarising, forwarding,
screen-reading or skimming. The hedge has to be inside each sentence, where
removing it would change the sentence rather than just drop a line.

The runbook is now written that way — every step begins with its own "If…" and
reads correctly in isolation — and a test pins it. That is a structural
property, so it was fixed without waiting. **It does not decide the question**:
whether these are "consider" or "do this now" is still yours, and the same
hazard applies to any wording that replaces them. Whatever legal signs off has
to survive being read one sentence at a time.

Related: the alert is plain text on purpose. HTML mail is the phishing norm,
and a plain-text security alert is harder to imitate convincingly. If multipart
HTML+text is ever wanted, that is a decision with a phishing-surface argument
attached, not a formatting preference.

**This does not close decision 3.** Still owned by legal/comms, unchanged:

- whether the text is advice ("consider") or instruction ("do this now");
- "contact Bitget support" and "freeze the account" — the liability wording;
- how certain any of it may sound, given a detection is evidence, not proof;
- whether it should vary per rule.

The runbook is now a single function so that review has one place to land, and
the customer-facing gating (CRITICAL/HIGH only) is separate from the wording.

### The advice depends on a page that does not exist — #69

> **Update 2026-08-14: the page exists now, and the note has been rewritten.**
> #69 shipped a self-serve replacement flow, so the monitoring note no longer
> says "ask us for a new key" — it says a signed-in customer can put a new
> read-only key in place themselves, and to ask us if anything there does not
> work. The dependency below is discharged; the rest of decision 3 (how much
> the runbook may instruct rather than advise, how certain it may sound, and
> whether it varies per rule) is untouched and still yours.
>
> One deliberate constraint on any future wording: **the note must not link
> directly to the replacement form.** A message from us that lands a reader on
> "paste your API key here" is the phishing shape we would be teaching them to
> trust, and this is the one message where the opposite habit — sign in, then
> look — can be taught. A design-guard probe fails if an edit turns it into a
> deep link.
>
> The section below is kept as written, because it is the reasoning that made
> #69 a p2 rather than a nicety.

There is still **no self-serve way for a customer to replace a revoked Guardian
key.** Onboarding is operator-driven end to end, so the link in the monitoring
note points at the portal front door rather than at a flow, and "ask us for a
new key" is the entire recovery path. Filed as **#69** (epic-01, p2, Phase 3).

This is a dependency of decision 3 rather than a note beside it. The note tells
a customer, correctly, that revoking every key leaves the account unwatched —
and step 2 exists because we expect a frightened person to revoke everything.
So the advice can put a customer into a state the product cannot recover from
on its own, during a live incident, with the wait measured by whether an
operator is awake.

Whichever way the advice-versus-instruction question is settled, the wording of
that note should be chosen knowing whether #69 exists by then: "ask us for a
new key" is an honest sentence today and a poor one the moment there is a page
that does it.

(This section was written when the monitoring consequence was step 6 of the
numbered list. It is now a note below the list — items 1–5 are actions, and
numbering a consequence among them asked the reader to perform it. Nothing
above changes except where the sentence sits.)

---

## 4. Alert channel defaults and quiet hours — **ANSWERED 2026-08-27** (was: blocks #24)

> **Answer (owner, 2026-08-27): the recommendation, with one fail-closed
> addition.** Quiet hours OFF by default; non-critical only; the customer's
> local time; 22:00–07:00 when enabled; suppressed alerts are QUEUED into
> the next digest, never dropped; email is the un-disableable CRITICAL
> channel; removing the email is an onboarding-blocking validation error,
> not a silent downgrade.
>
> **Fail-closed addition: an unknown timezone means quiet hours cannot be
> enabled at all — never defaulted to UTC.** A window defaulted to a
> timezone the customer does not live in silences alerts at the wrong hours,
> which is the one failure quiet hours must not have; refusing to enable
> them until the timezone is known is the safe direction.


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

## 5. PDF export for incidents — **ANSWERED 2026-08-27** (was: blocks part of #29)

> **Answer (owner, 2026-08-27): option A.** Print stylesheet plus the
> browser's print-to-PDF. No PDF library: the requirement is "something
> official-looking I can send to someone", and a dependency with a
> supply-chain surface on a security product is the wrong price for it.
> Revisit only if a customer asks for server-side generation by name.


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

## 6. Retention periods — **ANSWERED 2026-08-27** interim; lawyer engaged (was: blocks #47, touches #46)

> **Answer (owner, 2026-08-27).** A lawyer is to be engaged, and asked
> specifically whether crypto-shredding (option A) counts as erasure in the
> relevant jurisdiction — it is the only option that does not trade the
> forensic obligation for the legal one. **Interim:** 24 months for every
> unbounded table, configurable so the real number lands without a deploy.
>
> **Addition — the interim answer to an erasure request is honest, not
> deferred:** hard-delete on request (option B) now, record the forensic gap
> explicitly as its own event, and tell the customer that is what happened.
> A request answered "we are waiting for legal" is a request not answered;
> a deletion whose gap is written down can be reasoned about later.
> Publishing the policy and the sign-off (criteria 2 and 4) still wait for
> the lawyer.
>
> **6b — an erasure and other people's records (owner, 2026-10-26: "Redact,
> and say so").** Found while drafting the privacy policy: the confirmation
> promised "nothing that identifies you", but the person's address stayed
> as the actor of the tombstone's own entry, in the confirmation's delivery
> row, and on other customers' accounts where they were the emergency
> contact or their login reached: the "emergency contact changed" entries,
> their own password changes and sign-ins, deliveries, snoozes and marks.
> Those accounts' activity page promises its record is "never edited or
> removed". The choice was between redacting those entries (the promise
> names an exception) and leaving them (the confirmation and the policy
> say the details remain in other people's history). **Answer:** replace
> the person's email address, phone numbers, Telegram identity and phone
> names with `[erased]` everywhere; keep the entries; add one
> `contact_details_erased` entry on each account touched; and name the
> exception in the activity page's promise, on the web and in the app.
> Built in `sentinel/erasure.py`; `docs/data-retention.md` describes the
> matching rules. Backups made before an erasure and the server logs are
> not edited, and the confirmation says so; how long they are kept is
> still open.


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

## 7. External uptime monitoring — **ANSWERED 2026-08-27** (vendor still the owner's; was: blocks #63)

> **Answer (owner, 2026-08-27): option A.** A dead-man's switch with a
> third party, 15 minutes. Gaps in monitoring go to customers in the DIGEST,
> not as alerts — a gap is not an emergency to wake someone for, and hiding
> it is worse when discovered than when disclosed. Its drill shares decision
> 2's machinery. **The vendor is still the owner's choice**; the mechanism is
> built vendor-agnostic behind it. Reasoning as recorded below: the monitor
> must not share fate with the monitored, and only A has that separation by
> default without opening an inbound port.


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

> **Note 2026-10-23.** The paragraph above is the posture of August 2026
> and is history: the dashboard has had an operator sign-in with a
> mandatory second factor since #87 (decision 19), Caddy holds the
> host's public 80 and 443 since decision 22b, and with 22e step 3 the
> dashboard answers at `/admin` on the public address. None of it
> changes the answer. The monitor must not share fate with the
> monitored, and a health endpoint behind our own proxy on our own box
> still does; B stays rejected for that reason, not for a closed port.

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

## 9. What an incident page tells a customer to do next (G-25, #29) — **ANSWERED 2026-08-27** with 3

> **Answer (owner, 2026-08-27): see decision 3.** One canonical text, one
> source, for the runbook and the incident page: revoke unrecognised keys →
> check the exchange's own withdrawal history → contact exchange support
> with a reference; "move your funds" only as something to raise with the
> exchange. Instruct for the reversible, never the irreversible; state what
> was observed, never what it means.


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

## 10. Should a portal credential be scoped to an account? (#61) — **ANSWERED 2026-08-27**

> **Answer (owner, 2026-08-27): option 2 — per-account grants — and sooner
> than "when a customer asks".** The affordance fix removed the acute risk,
> but the section's own last paragraph decides the timing: the migration
> touches live credentials and only gets more expensive as customers
> accumulate accounts with shared contacts. Existing members are backfilled
> from their current contact rows, so nobody loses access on the day it
> lands.


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

## 11. Billing: provider, plans, prices, and what a lapse does (G-39/#43, G-40/#44) — **owner reconsidering, 2026-08-27**

> **Status 2026-08-27: OPEN — owner reconsidering.** No position recorded
> as decided. Nothing that depends on this is to be built. Note for the
> reconsideration: the text below reasons about VAT on "a UK-based service";
> the repo's only jurisdiction statement (docs/data-retention.md) says the
> company operates from Canada, and the UK claim entered this file in
> b6ca5c2 (2026-08-13) without a source — see the investigation in
> docs/AUTONOMOUS-LOG.md, 2026-08-27.


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

## 12. What acts on an unhealthy container (#81) — **ANSWERED 2026-08-21**

**The question.** The compose healthcheck correctly reported unhealthy for
six weeks (#79, FailingStreak 148) and nothing consumed the verdict —
`restart: unless-stopped` acts on exit, not health. Sidecar, host timer, or
page-only?

**Options and the recommendation** are on the issue
([#81](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/81#issuecomment-5334893642)),
in short: a **systemd timer** on the host (restart on unhealthy, capped to
once per N minutes so a flapping container becomes visible instead of
perpetually revived, journald as the record). **Not** the autoheal sidecar —
a third-party image with docker.sock (root-equivalent) access next to a
credential-holding product is a large supply-chain surface for 15 lines of
shell. Page-only is not an alternative but #63's other half: restart is
mitigation, the dead-man's switch is detection, and decision 7 already
covers it — a restart loop nobody knows about is #79's six weeks with
extra steps.

**What it blocks:** #81.

---

**The answer (owner, 2026-08-21): ACCEPTED.** Build the systemd timer that
restarts the container when its healthcheck has been failing, with a restart
cap. Not the docker.sock sidecar — mounting that socket into a container
hands it root on the host. Two requirements that make it useful rather than
decorative, specified on #81: every restart is **recorded and visible** (a
service quietly restarting hourly is a hidden fault — #79's container
reported unhealthy 148 consecutive times and nothing acted), and reaching
the cap must be **LOUD** — a cap that silently stops trying returns to the
original silence with more steps; the who-hears answer is #63's paging half
(decision 7), linked from the issue. Buildable; not built before v1.6.1.

---

## 13. Browser push notifications (#23) — **ANSWERED 2026-08-21: won't-do**

**Closed, and the reasoning recorded so nobody relitigates it**: browser
push only reaches someone whose browser is already open, which is not the
case this product exists for — and it buys that by adding a JavaScript
surface to a product whose central claim is that it cannot touch anyone's
money. The no-JS posture stands (decision on #82, restated here).

**The condition that would reopen it**: a deliberate decision to change the
no-JS posture for some other reason — never this one.

---

## 14. Exchange key-creation instructions (#8) — **ANSWERED 2026-08-21: text-only**

No screenshots. Written steps that stay accurate beat pictures that quietly
stop matching what the customer is looking at, and maintaining screenshots
across four exchanges is permanent work. **One requirement**: date each
instruction set and name the exchange interface it was written against —
text goes stale the same way screenshots do, just less visibly, which is
worse. How drift gets noticed, and the revisit trigger (onboarding support
load), are on the issue. Buildable; not built before v1.6.1.

---

## 15. May a portal member remove (pause) their own account? (#10, #45) — **ANSWERED 2026-08-23: 24-hour cancellable countdown, never immediate**

**The decision.** No immediate self-serve pause. A member may REQUEST a
pause; it executes automatically at T+24h unless cancelled — by the member
or by Minas. The immediate quiet pause is the button an attacker with a
stolen session presses before moving funds, and every genuine customer
reason (closing the exchange account, key already revoked, leaving)
tolerates a day.

**The design, owner-specified** (full build spec on the new pause issue):

- The countdown is REAL, not decorative: it executes automatically at
  T+24h with no operator in the loop, so the customer's guarantee does not
  depend on anyone being awake.
- The notification email carries NO link (decision 16's rule) — it says
  contact Minas. A human channel is ample inside 24 hours, and it must
  work for an owner locked out of the portal because the attacker changed
  the password.
- Emails every contact at request time, and again near expiry.
- Visible everywhere while pending: #34's banner treatment on the home
  screen, and a line in the proof-of-life digest — a pending pause is
  "about to not be watched".
- Re-requesting restarts a full 24 hours; the clock never shortens.
- The fast path stays human-verified: a customer calls Minas and an
  operator pauses immediately, attributed. Deliberately inverted from
  what an attacker would want out of each path: the anonymous path is
  slow and loud, the immediate path requires a human who knows the
  customer.

**#45's Minas-side revoke is DROPPED, not deferred.** The account page
already states the real kill switch is at the exchange — which is truer
than a button that only makes Minas look away.

**#10's removal criterion is reworded** to match: request-then-countdown,
never "stops watching it immediately".

---

## 16. Deep incident links in alert emails (#20) — **ANSWERED 2026-08-23: NO — non-clickable reference + portal lands on the incident**

**The decision.** No links to incidents in alert emails, ever. The
sign-in page teaches customers never to click through from email, and
these customers are crypto holders — a phisher's "view your incident"
link is indistinguishable from a real one, and training the click defeats
the training the product already does.

**Instead, two halves** (queued on #20, criterion reworded):
1. The alert email carries a NON-CLICKABLE incident reference —
   "incident #370" — so the customer can find and name it.
2. The portal home surfaces the most recent unacknowledged incident at
   the top, so opening the portal directly lands on it in one step.

**Gap assessment against what #34 already built** (checked in code,
2026-08-23): the home's alert list is newest-first with acknowledgement
state, but nothing PINS the most recent unacknowledged incident above the
fold, and customer email copy carries no incident reference (it was
deliberately free of internal identifiers; the row id is a support
reference, not an internal identifier). Both halves are gap — small ones.

---

## 17. Self-serve signup (#6) — **ANSWERED 2026-08-23: NO — ordered behind billing and legal**

**The decision.** No public registration. Strangers handing over exchange
credentials with nobody at Minas in the loop, while billing (#43/#44)
does not exist, abuse controls for a public endpoint do not exist, and
#46/#47 are unresolved, is the wrong trade at this scale.

**What it would trade away, in the owner's words**: the jaypee CRITICAL
resolved in under an hour because the owner could phone him. That is a
property of having fifteen known customers, and it is worth more than a
funnel right now.

**The recorded fallback if funnel pressure becomes real**: a WAITLIST —
public interest capture, operator-approved activation. Interest is
captured without anyone handing us a key unvetted.

**#6 stays open and unbuilt on purpose**, with the ordering stated on the
issue: billing (#43/#44) and legal (#46/#47) first, waitlist as the
middle step, self-serve last if ever.

---


## 18. How wide is "the same customer action"? (#99) — **owner reconsidering, 2026-08-27**

> **Status 2026-08-27: OPEN — owner reconsidering.** No position recorded
> as decided; the conservative definition stays as built and neither the
> widening nor the production backfill is to be started. Fact for the
> reconsideration: `withdrawal_burst`'s evidence carries only
> `count_in_window`, `window_minutes`, `threshold` and `timestamps` — no
> order ids — so widening by burst membership cannot be done by order
> reference as the text below assumes.


Sibling acknowledgement shipped with the conservative definition: same
account + same non-empty exchange `order_id` + 24 hours. That is provable
sameness (the measured case: #369/#370 shared order 424427676), and a
false co-acknowledgement removes attention from an unrelated alert, so
false negatives are the safe failure direction.

**What needs the owner**: whether to widen, and how far -
- shared destination ADDRESS within a window (a burst to one address is
  one action to a human, but two withdrawals to a saved address on
  different days are not);
- burst membership (`withdrawal_burst` alerts carry no single order_id -
  should acknowledging the burst acknowledge the per-withdrawal alerts
  inside its window, and vice versa?);
- plain time-clustering (catches everything, over-groups dangerously -
  recommended against).

**Also the owner's, because production is read-only to the run**: whether
to backfill the ~199 existing unacknowledged CRITICALs whose siblings
were already acknowledged. Sizing query (read-only) and the co-ack are
one operator session; the run left the data untouched.

---

## 19. Dashboard two-factor: policy and the last-operator recovery (#87, gates #82's dashboard pass) — **ANSWERED 2026-08-27**

> **Answer (owner, 2026-08-27): MANDATORY, not opt-in.** Enrolment is part
> of `sentinel operator add` and a first-login step; an operator without a
> device cannot sign in. Recovery codes at enrolment. **Last-operator
> recovery is host access** — a host-only reset subcommand run on the VM
> over the tailnet, attributed and event-logged, so the person who holds
> the box is never locked out and every use of the break-glass is on the
> record. Reasoning: the dashboard is the higher-privilege surface and it
> currently has no authentication at all while being reachable by ten
> devices; opt-in would let the weakest operator set its security.
> Unblocks #87, and #82's dashboard pass lands with it.

> **Update 2026-10-23 (decision 22e).** "Over the tailnet" is history:
> Tailscale leaves the project, and the break-glass path is the same
> host access by SSH on port 22 with the EC2 key pair, at the Elastic IP
> `57.181.51.14`, from an address in the security group's SSH rule (the
> box answered to exactly that the evening of 2026-10-23). Nothing else
> here changes: the reset is still `sentinel operator reset-2fa <name>
> --as <name>` on the box, attributed and event-logged
> (`sentinel/operators.py`, `reset_second_factor`), and still the only
> path that removes an operator's second factor. Its docstring and
> docs/deployment.md "Operator accounts and two-factor" say "tailnet";
> both change in the release this update describes (22e step 8).


**The question.** The ops dashboard - the higher-privilege surface: it
adds customers, reads every contact, attributes operator actions - has a
password-only login while the member portal carries #32's TOTP. The
mechanics are reusable as-is (sentinel/totp.py, sentinel/twofactor.py,
the shared LoginThrottle). What is not engineering:

1. **Mandatory or per-operator opt-in?** Opt-in means the weakest
   operator sets the surface's security; mandatory means enrolment is
   part of `sentinel operator add` and a first-login step, and an
   operator without a device cannot sign in at all.
2. **Recovery when an operator loses their device.** The portal's
   answer - recovery codes, plus Minas as the human fallback - does not
   transfer cleanly: operators can reset each other, but the LAST (or
   only) operator cannot. Production has zero operator accounts today,
   so "the only operator" is the launch state, not an edge case.

**Recommendation**: mandatory, with recovery for the last operator
being host access - `sentinel operator add` / a reset subcommand run on
the VM over the tailnet, which is already the root of trust for
everything else (deploys, the restart timer). That keeps the dashboard
never weaker than the portal and never locks out the person who holds
the box. Opt-in is recommended against for a surface that can read
every customer's contact details.

**What it blocks**: #87 itself, and #82's dashboard design pass, which
the owner ordered to wait for the access model so login and look land
together.

---

## 20. The member portal is published to the internet on Tailscale Funnel (#88) — **ANSWERED by the owner, August 2026; recorded 2026-09-23; SUPERSEDED in full 2026-10-23 by 22**

> **Superseded in full, 2026-10-23: decision 22.** Tailscale leaves the
> project in one go - Funnel, Serve and Tailscale SSH together - in the
> sequence 22e's evening status records, with SSH by the Elastic IP
> answered first. There is no overlap period: the Funnel address is
> withdrawn, not kept up with a notice, and members are emailed the new
> address before it goes (22d). The answer below is what ran from August
> 2026 until that day; the two commands in it (`serve status`, `funnel …
> off`) are the ones 22e step 6 runs, read against the box at the time,
> and nothing else in it describes what remains afterwards. The
> 2026-10-22 banner under this one is kept for the record.

> **Superseded in part, 2026-10-22: decision 22** retires Tailscale from
> the project and moves the dashboard to the public address. Until 22 is
> built, the answer below is what runs.

> **Answer (owner, August 2026; written down 2026-09-23).** The member
> portal (port 8391) is published to the open internet with **Tailscale
> Funnel**, deliberately, since August 2026. The ops dashboard (8390) and
> the metrics endpoint (9109) stay on **Tailscale Serve**: tailnet-only,
> never public. The portal is the surface customers must reach from their
> own phones and laptops - a tailnet they are not on cannot be the way a
> customer acknowledges a withdrawal alert - and it carries its own
> defence: a password login, TOTP two-factor (#32), the shared login
> throttle, CSRF origin checks, and no operator capability at all.

**Why this is recorded late.** docs/deployment.md said "`serve`, never
`funnel`" and scripts/aws-user-data.sh said "serve, NEVER funnel", both
written when the only published surface was the dashboard, which has no
customer-facing reason to exist off the tailnet. The portal was published
via Funnel in August and no document, log entry or issue recorded it; the
doc went on forbidding what production was doing. The 2026-09-22 session
found the conflict while reporting on item 5 of the incident batch, and
the owner's brief of 2026-09-23 ordered it written down. Both files now
say what runs.

**What it means for the threat model** (docs/threat-model.md, A1 and the
residual-risk list):

- The portal's login is internet-reachable, so credential stuffing and
  password spraying against member accounts are real, not theoretical.
  Defences in place: the shared LoginThrottle, TOTP for members who
  enrolled (not mandatory for members: five members, none enrolled, at the
  2026-09-08 measurement), and the address-less anti-phishing copy (#88).
  Two-factor
  enrolment for every member is the next control to consider; it is the
  same question decision 19 answered for operators.
- The portal is the only public HTTP surface. Anything the portal process
  can do is now the attack surface of an internet-facing service: it must
  never gain an operator capability (add a customer, read every contact,
  change thresholds) - those stay on the dashboard, behind the tailnet.
  The route-classification guard in tests/test_member_roles.py is the
  registry that keeps that boundary explicit.
- The portal is reachable on TWO addresses, read from the box on
  2026-09-25 (`sudo tailscale serve status`): the public one,
  `https://minas-guardian.tail0fdd4a.ts.net:8443` (Funnel, listener 8443,
  to 127.0.0.1:8391), and the tailnet-only one on port 8391 (Serve, same
  target). Funnel can only listen on 443, 8443 or 10000, so the public
  port is never the portal's own. The dashboard (`/`, to 8390) and metrics
  (9109) are tailnet-only. Customers' browsers resolve the public name to
  a Tailscale Funnel relay, not to this host: the relay opens an encrypted
  TCP proxy to the node over the tailnet and, in Tailscale's words, "hides
  the IP address of your local device". So the Elastic IP is NOT the
  portal's ingress; it is the exchanges' allowlisted egress only, and the
  two are independent (an earlier version of this row said the opposite -
  reasoned, not measured; corrected 2026-09-25).
- Funnel terminates TLS at Tailscale's relay with a certificate for the
  `*.ts.net` name; the relay cannot decrypt the proxied traffic. The
  portal therefore has no certificate to manage and no port open on the
  security group; the exposure is through Tailscale's identity, and the
  public listener alone is withdrawn with
  `sudo tailscale funnel --https=8443 8391 off` - the listener port and
  the target exactly as `serve status` prints them; the first version of
  this row said `--https=8391`, a port Funnel cannot listen on, which
  matched nothing. `tailscale funnel reset` is NOT the fallback: the live
  page says only that it "clears the current tailscale funnel
  configuration" and does not say what it leaves standing, so whether it
  also drops the Serve entries for the dashboard and metrics is unverified
  and a reset offered as the safe option would be worse than none.

**What it does NOT decide.** The customer-facing domain (#88) is still
open: customers today reach the portal by the `*.ts.net` name they were
given, and the anti-phishing copy stays address-less until that is
chosen. This row records the exposure model only.

**Related:** #88 (address), #32 (member two-factor), decision 19
(operator two-factor, host-access recovery).

---

## 21. Native iOS and Android apps, and the JSON API they need — **PARTLY ANSWERED 2026-10-22: audience, framework, repository, branding, sign-in and API scope decided; push, the web portal, lost refresh answers and two-factor-off still open**

> **Answer so far (2026-10-22).**
> - **The apps are for customers** (members). Operators keep the web
>   dashboard. (21a)
> - **React Native**, so iOS and Android share one codebase and are
>   maintained once.
> - **The app lives in this repository**, so the API and the app change
>   together, in one commit when needed. (21f)
> - **The apps carry the company's colours and logo.** (21g)
> - **Sign-in and the first API version as recommended** (21c, 21d):
>   bearer tokens with required TOTP for the app; v1 reads everything a
>   member can see, plus acknowledge and push registration.
> - The apps and the JSON API are both urgent, and the API comes first.
>   Everything is served from `https://guardian.minastrade.com` (decision
>   22).
>
> Still open, each with a recommendation below: 21b (does the web portal
> change), 21e (push), 21h (a refresh whose answer is lost) and 21i (does
> turning two-factor off end app sign-ins). None blocks the API.

**What this changes.** The portal's first rule (docs/portal-design.md;
owner, 2026-08-19, on #82) reads: "No framework. Plain server-rendered
HTML + CSS; the no-JS posture is security posture… Anything beyond needs
a decision, not a diff." Decision 13 kept that posture and named the one
thing that reopens it: a deliberate decision to change it for some other
reason. This is that decision. The apps are a JavaScript codebase with an
npm dependency tree, and they hold a member's session on a phone. Two
points keep that honest:

- **Native push is not browser push.** Decision 13 rejected browser push
  because it only reaches a browser that is already open. Native push
  reaches a locked phone in a pocket, which is the case this product
  exists for. Decision 13 still stands for the browser.
- **The app never holds an exchange credential**, provided 21d holds:
  key replacement (`/key`) stays on the web. Even so, a compromised app
  or a stolen session can read a member's alerts and can ACKNOWLEDGE one,
  and an acknowledgement stops CRITICAL escalation. That is the harm to
  design against: someone holding a member's session can silence the
  alarm about their own withdrawal. 21c carries the mitigation.

### 21a. Who the apps are for — **ANSWERED 2026-10-22: customers**

Members only. Operators keep the web dashboard. This keeps decision 20's
line: the member surface never gains an operator capability (add a
customer, read every contact, change thresholds), and an operator app
would have put exactly those on a phone. Once decision 22 is built,
operators can reach the dashboard from a phone's browser.

### 21b. Does the browser portal change? — open

**Recommendation: no.** The web portal stays server-rendered, and the
no-JS posture still holds for the browser. This decision limits the
posture change to the native apps. Reasons:

- The portal already works on phones; it has been mobile-first since
  round 2 of #82.
- It is pinned by 1,600+ tests and the design guards.
- A rewrite would put two unfinished UIs in front of customers at once.
- React on the web does not speed up the apps. React Native draws native
  views, not HTML, so the app's screens get written for React Native
  either way.

If a web build of the app is wanted later, Expo can produce one from the
same screens. That is its own decision, taken after the apps ship.
21b does not block the API.

### 21c. How the apps sign in — **ANSWERED 2026-10-22: as recommended**

The portal's session is an `HttpOnly; Secure` cookie, with CSRF judged
on `Sec-Fetch-Site` and `Origin` (#92). A native app sends neither header
reliably, and cookies are awkward there, so the API needs a credential of
its own. Accepted as recommended:

- **The same sign-in as the web**: email, password, and the member's TOTP
  where enrolled, all through the same `LoginThrottle`. No new way in.
- **Two tokens.** A short-lived access token (15 minutes), plus one
  refresh token per device. The refresh token lasts 30 days and is
  replaced on every use; if a used one comes back, that device is
  revoked. Both live in the phone's Keychain or Keystore and travel as
  `Authorization: Bearer`, never as a cookie. That keeps the CSRF gate
  out of `/api/`, and the web's CSRF gate is not weakened to make room.
- **Members can see and cut sessions.** The portal's Security page lists
  every signed-in device with a revoke button, and a sign-in from a new
  device emails the member. This answers the stolen-session harm above:
  a member can find and end a session they don't recognise.
- **Member two-factor.** Decision 20 named mandatory member TOTP as the
  next control to consider; five members, none enrolled, at the
  2026-09-08 measurement. A 30-day session on a phone raises the stakes,
  so TOTP enrolment is required for app sign-in. The web keeps its
  current rule until decision 20's question is answered.

### 21d. What the first API version does — **ANSWERED 2026-10-22: as recommended**

**Read everything a member can see, plus acknowledge, plus push
registration. Nothing else in v1.** The API is served by the portal
process, under `/api/v1/`.

| v1 route | Mirrors |
|---|---|
| `POST /api/v1/session`, `POST /api/v1/session/refresh`, `DELETE /api/v1/session` | new: sign-in and sign-out (21c) |
| `GET /api/v1/accounts` | `/`, `/status`: each account, its status, and whether it is being watched |
| `GET /api/v1/alerts`, `GET /api/v1/alerts/{id}` | `/alerts`, `/incident`, including the what-to-do-next text (decisions 3 and 9) |
| `GET /api/v1/activity` | `/activity` |
| `GET /api/v1/limits` | `/limits` |
| `GET /api/v1/security` | `/security`: posture, plus the device list from 21c |
| `POST /api/v1/alerts/{id}/acknowledge` | `/acknowledge`, the one act every contact may perform (#25) |
| `POST /api/v1/devices`, `DELETE /api/v1/devices/{id}` | new: push token registration (21e) |

The primary-only changes follow in v2, one at a time: `/preferences`,
`/snooze`, `/expected`, `/pause/*`, `/account/name`, `/digest/frequency`,
`/telegram/*`, `/drill` and `/erase`. **`/key` stays web-only** until
there is a reason to move it, because it is the one route that accepts
exchange credentials.

The existing guards apply unchanged:

- Every API route joins the default-deny classification in
  `sentinel/portal.py` (`PRE_AUTH`, `NON_PRIMARY_ALLOWED`,
  `PRIMARY_ONLY`), so `tests/test_member_roles.py` covers it.
- Every response is scoped through `member_accounts`. An id the member
  does not own returns 404, exactly as on the web.
- API GETs fall under the no-GET-writes test.
- Responses reuse the portal's queries instead of re-deriving them, so
  the app and the web cannot disagree about whether an account is
  watched.
- The contract is one OpenAPI file in this repo (21f). The server is
  tested against it.

Adding the routes is a MINOR release.

### 21e. Push notifications — open

**Recommendation: a new alert channel, additive, and never the only
channel for a CRITICAL.**

- **It follows every rule the other channels follow**: the member's
  per-level channel choices and quiet hours (decision 4), cooldowns, and
  a delivery record per recipient per channel.
- **The payload is like the member SMS**: severity, alert type, and the
  account's name. No amounts, no addresses, no balances. Push travels
  through Apple and Google, and Expo if it is used, and it lands on a
  lock screen.
- **Never the only path for a CRITICAL.** We cannot count on knowing a
  push was seen, and a member who turns off notifications or deletes the
  app goes silent to us. SMS and email keep their current roles.
- **Provider: Expo's push service for v1.** It is one integration for
  both platforms and the fastest to ship. The cost is one more party
  carrying the payload, which is already minimal. Switch to APNs and FCM
  directly later if that cost comes to matter.

### 21f. Where the app lives — **ANSWERED 2026-10-22: in this repository**

The app goes in `mobile/` at the root of this repo (Expo / React Native,
TypeScript). An API change and the app change that uses it can then land
in the same commit. What that takes, so the server's gates stay what they
are:

- **The server image does not change.** The Dockerfile copies only
  `sentinel/`, `config/` and `pyproject.toml`. Add `mobile/` to
  `.dockerignore` as well, so the app is not even in the build context.
- **CI gains an app job** (type check, tests, `npm audit`) next to the
  Python job. The Python gates (ruff, pytest, coverage, mutation,
  design-guard probes) are scoped to `sentinel/` and `tests/` and do not
  see `mobile/`.
- **The API contract is shared, not copied.** There is one OpenAPI file.
  The server is tested against it, and the app's API client is generated
  from it, so a server change that breaks the app breaks the build.
- **App releases are not server releases.** The stores take their own
  version numbers, so the app gets its own tag prefix (for example
  `mobile-v1.0.0`) and its own changelog under `mobile/`.
- **Before the first app tag, the deploy gate must accept release tags
  only.** `scripts/deploy-gate.sh` checks
  `git describe --exact-match --tags HEAD`, which matches *any* tag.
  Today a commit carrying only an app tag would pass the gate. A commit
  carrying both kinds of tag could report the app tag as the server's
  version, and deploy.sh's version assertion would then fail after the
  switchover. The fix is one `--match 'v*'`, with a test.
- **One session per working tree still applies** (CONTRIBUTING rule 7).
  The app and the server now share the tree.

**Publisher accounts.** The apps need an Apple Developer Program account
and a Google Play account in the company's name. Enrolling an
organisation includes identity verification, which takes time, so start
it now, before the app is ready. Which legal entity publishes is the
owner's call.

> **Answered 2026-10-23: the app is published as
> `com.minastrade.guardian`**, the iOS bundle identifier and the Android
> package both. `tests/test_mobile_app.py` pins it, because a store ties
> an app to it for good. Neither publisher account exists yet (checked
> with the owner, 2026-10-23); enrolment is the next step, and the app
> cannot reach customers through a store until both are done.
> `mobile/eas.json` holds the build profiles for when they are.

**App review.** The app shows exchange account activity and moves no
funds. Read the current App Store Review Guidelines on cryptocurrency
apps before submitting. UNVERIFIED: how those guidelines apply to a
monitoring-only app.

### 21g. Branding — **ANSWERED 2026-10-22: company colours and logo**

The apps carry the Minas identity, the same one the portal carries
(#82):

- Gold `#f0b429`.
- Violet `#8b5cf6` and `#6d28d9`.
- The dark surfaces from `design/portal/styles.css` (`--bg #0d0f13`,
  `--surface #151921`).
- The Minas logo, supplied by the owner on 2026-10-22 (it replaces the
  infinity mark): `design/brand/`, served by the portal from
  `sentinel/static/`.

What that takes:

- **One source for the identity.** `sentinel/brand.py` exists "so the
  brand cannot drift into two spellings of gold". A JavaScript app cannot
  import a Python module, so the identity colours move to one tokens
  file (for example `design/tokens.json`). `brand.py` and the app both
  read it, and a test fails if they disagree. This is tokens only, not a
  stylesheet: `tests/test_surface_styles.py` refuses a shared stylesheet,
  and that rule stays.
- **The store artwork exists (2026-10-22).** `design/brand/` holds the
  app icon (1024, opaque), Android's adaptive foreground and monochrome
  layers, and the splash image. All are derived from the supplied logo by
  `scripts/brand-assets.py`. `design/brand/README.md` names the Expo key
  for each file and the background colour to use with them.
- **The ops dashboard stays unbranded** (#82). An operator screen must
  not be usable to impersonate a customer page, and that matters more
  now that both share one address (decision 22).
  *Superseded 2026-10-24 by decision 24: the dashboard wears the identity
  on the same dark ground; the marking and the path tell the surfaces
  apart.*

### 21h. A refresh whose answer is lost — open (raised 2026-10-23)

21c revokes a device when its replaced refresh token comes back. The
server commits the new token before it sends the answer. If the answer
never reaches the phone, the phone still holds the replaced token, and
presenting it again revokes the device. That happens when the connection
drops at the wrong moment, when the app is killed or reloaded during a
refresh, or when the answer arrives cut short. The member then has to sign
in again with password and code. Their activity record says the sign-in
"was used from two places at once", a false theft alarm that teaches them
to ignore the real one. A development reload during app start-up
reproduces it.

The app already does what a client can. It keeps the newest token in
memory, and it gives a refresh 30 seconds before it stops waiting. When
the secure store reports that writing the new token failed, the app
removes the replaced one from storage. That is best effort: a failed
removal is not retried. And on Android, expo-secure-store does not report
a failed write of a token to disk, so a phone whose storage is full can
still restart on the replaced token. A lost answer can only be fixed on the
server. The options:

- **(a) Keep 21c as it is.** Every replay revokes. This is the simplest
  option, and its cost is the false alarms above.
- **(b) A short grace window — recommended.** If the replaced token comes
  back within 60 seconds of its rotation, the server rotates again instead
  of revoking. Racing retries are handled with a conditional update, so
  only one wins. Outside the window, a replay revokes as now. This covers
  a retry, a reload and a quick reopen. The change is confined to
  `rotate_refresh`. The trade-offs: a thief who replays within the same
  60 seconds gets a working token rather than tripping the alarm, and a
  token that was never delivered becomes a plain refusal rather than a
  reuse alarm.
- **(c) Re-rotate until the new pair is first used.** The server would
  record the first use of an access token from the newest pair, and treat
  a replay as theft only after that. This covers every lost answer, at any
  delay. It needs a write on every authenticated read, which `authenticate`
  forbids on purpose ("no GET may write"), so it reopens that rule.

None of (a)-(c) closes the Android case above. The replay comes after a
restart, possibly hours later, which is outside (b)'s 60 seconds, and
under (c) the new pair has already been used, so the replay counts as
theft. Nothing on the phone closes it either. A secure-store change that
reported the failed write would only trigger the delete, and the delete is
a disk write too, so it fails the same way on a full disk. (The store does
report a failed delete; it is the failed write it does not report.)

### 21i. Does turning two-factor off end the app's sign-ins? — open (raised 2026-10-23)

21c makes two-factor the condition for signing in to the app. Today it is
checked at sign-in only. A member who turns two-factor off on the portal
stays signed in on every phone for as long as that phone keeps being
opened: each refresh starts a fresh 30 days, so only a phone left
unopened for 30 days signs out. Until then, only signing the phone out
(in the app, or from the portal's Security page) or a password change
ends those sign-ins. The app's Security tab now shows the true state
("Off") and says the phone cannot sign in again until it is turned back
on.

- **(a) Leave it.** Two-factor gates new sign-ins only.
- **(b) Turning it off signs every phone out — recommended.** The portal's
  two-factor disable would revoke the login's app devices, with an
  activity entry and the usual email. The condition 21c set then holds
  for the whole of a session, and it closes a stolen portal session's
  route to a quieter phone session.

### What it blocks

The apps, end to end. Nothing blocks the API now that 21c and 21d are
answered. Push (21e) waits on its own answer, and the apps are built
against decision 22's hostname.

### Built so far — 2026-10-22, branch `api-v1` (merged)

- **Sign-in (21c), the v1 reads and acknowledge (21d)** are built, as
  answered, in `sentinel/api.py`, served by the portal process under
  `/api/v1/`.
- **The route registry lives in `sentinel/api.py`**, not in the portal's
  sets as 21d first said. API routes carry a method and path ids, which
  the portal's path-only sets cannot hold. The buckets are the portal's:
  `PRE_AUTH`, `MEMBER_READ`, `NON_PRIMARY_ALLOWED`, with `PRIMARY_ONLY`
  arriving with v2. `tests/test_api_routes.py` covers the registry the
  way `tests/test_member_roles.py` covers the portal's.
- **`DELETE /api/v1/devices/{id}` is built as "sign a device out"** (21c).
  Push-token registration (`POST /api/v1/devices` in the table above)
  waits on 21e.
- **The portal's Security page lists signed-in phones** with a sign-out
  button (`POST /security/devices/revoke`, own-login plumbing, so
  non-primary allowed).
- **The contract is `api/openapi.yaml`**, held to the server by
  `tests/test_api_contract.py`.
- **The app is started (2026-10-22, branch `mobile-app`, merged)** in `mobile/`.
  It is Expo SDK 58 with React Native, and covers sign-in, accounts,
  alerts, incident and acknowledge, security and devices, activity and
  limits. It has its own CI workflow (`.github/workflows/mobile.yml`) and
  its own changelog. The deploy gate now accepts only `v*` tags, the
  precondition 21f set for the first `mobile-v*` tag. `mobile/` is in
  `.dockerignore`. See `mobile/README.md`.
- **The app was reviewed before its first phone test (2026-10-23).** Six
  reviewers covered it, with every finding checked by a second agent. The
  fixes are in `mobile/` and `mobile/CHANGELOG.md`. Two of the findings
  need the owner's answer: 21h and 21i.
- **Not built yet:**
  - push (21e);
  - the primary-only writes (v2);
  - ~~the app's bundle identifier and package name, which wait on the
    publishing entity (21f, the owner's call)~~ — answered 2026-10-23, see
    21f;
  - store accounts and builds.

**Related:** decision 13 (browser push, no-JS posture), decision 20
(member surface boundary, member two-factor), decision 22 (the address),
#25 (non-primary role), #82 (portal design rules and the brand), #92
(CSRF). No issue filed yet.

---

## 22. One public address, https://guardian.minastrade.com, and no Tailscale (#88) — **ANSWERED: 2026-10-22 one hostname for everything, no Tailscale anywhere, host access by SSH; 2026-10-23 TLS by Caddy on the instance (22b); 2026-10-23, later: the dashboard at `/admin` behind its sign-in and second factor, no allowlist, Tailscale removed in one go, the slow gates waived for the release that ships it**

> **Answer so far (2026-10-22).**
> - **`https://guardian.minastrade.com` is the project's one address.**
>   DNS is being set up.
> - **The admin dashboard shares that hostname.** There is no separate
>   admin hostname. (22a)
> - **No more Tailscale for this project**: not for the portal (Funnel),
>   not for the dashboard or metrics (Serve), and not for SSH. (22e)
> - **Host access is SSH with the EC2 key pair**, which is already in use.
>   (22e)
>
> **And (2026-10-23): Caddy on the instance, as one more compose
> service** (22b, as recommended). The owner wants the app API live at the
> new address the same day: that is 22e steps 1 and 2, the proxy with a
> certificate and the portal answering behind it. Built that day, branch
> `public-address`; the steps to bring it up are the owner's, in
> docs/deployment.md "The public address".
>
> This answers #88's open half (the customer-facing address) and
> supersedes decision 20's exposure model. The preconditions in 22c are
> not options; items 1 and 5 are still the owner's, and both are needed
> before the dashboard goes public (22e step 3), not before the portal
> does.

> **And (2026-10-23, later): remove everything Tailscale now, and give
> the dashboard its route.** The owner's words: "remove all things
> tailscale from the project. admin should have it's route. i need the
> whole server working and running at https://guardian.minastrade.com
> ASAP". The product is not launched. Recorded as four decisions:
> - **22a is decided, not recommended: the dashboard's route is
>   `/admin`**, served by the same Caddy as the portal, with the
>   operator sign-in and its mandatory second factor (decision 19, #87)
>   and the login throttle as its protection.
> - **22c item 5 is answered: no IP allowlist.** Not a precondition; it
>   can be added at the proxy later without a code change, and nothing
>   in the application may assume it.
> - **22d has no overlap period.** The Funnel address is withdrawn when
>   Funnel goes; members are emailed the new address before that, and
>   `SENTINEL_PORTAL_URL` carries it in every SMS and email from 22e
>   step 2 on.
> - **22e runs in one go**, with the same step numbers and the sequence
>   recorded in its evening status. The one step that is never skipped
>   is step 4, the SSH proof: it is what keeps the box reachable after
>   step 7, and its connection half was taken that evening.
>
> **The gates.** The owner waived the slow gates - the probes and the
> mutation floor (CONTRIBUTING, "Where the slow gates canonically run")
> - for the release that ships steps 3 and 8. Ruff and the suite still
> run: CLAUDE.md's "never leave `main` with a failing suite" is the
> owner's own standing rule and was not withdrawn. The deploy stays the
> owner's.
>
> **The code ships in the release this entry describes.** The base
> path, the cookie path, the `Referer` check, the Metrics page's links,
> the Caddy route and the two compose settings are one change set with
> the document changes of 22e step 8, pinned by
> `tests/test_dashboard_prefix.py` (the dashboard under a base path) and
> `tests/test_bind_address.py` (what the proxy forwards, what it never
> does, and both trusted-proxy settings). 22a lists what each piece does
> and how to tell it worked.
>
> **What the box said that evening; the decision depends on both:**
> - DNS answers `57.181.51.14`; the security group admits 80 and 443;
>   nothing listens on them yet, because v1.13.0 (which carries Caddy
>   for the portal) is cut and not deployed.
> - **`tailscaled` holds port 443 on the Tailscale address.** A probe
>   binding `0.0.0.0:443` answered "Address already in use", and that is
>   the bind Docker's `443:443` publish for Caddy makes. So the Tailscale
>   teardown precedes Caddy in the runbook's order (docs/deployment.md):
>   Serve and Funnel are withdrawn and `tailscaled` is gone before the
>   release that carries Caddy is started. Safe in that order because the
>   lockout guard, SSH at the Elastic IP with the key pair, answered the
>   same evening (22e step 4).
> - **The `sentinel` container has been stopped since 2026-09-28**, by
>   the owner's decision: alerting paused for the beta's credit cost. The
>   portal and the dashboard run in that container, so neither has
>   answered since then - the Funnel address has forwarded to a stopped
>   upstream for almost a month, and there is no running service for the
>   teardown-before-Caddy gap to interrupt. The restore order is in
>   docs/deployment.md "Restoring after the 2026-09-28 pause"; 22c item
>   1's reading needs that container running.
> - The box answered on port 22 at `57.181.51.14` to the EC2 key pair.
>   That settles which address is current: the `ec2-54-168-212-44…`
>   hostname and `ssh ubuntu@minas-guardian` in the SSH examples are
>   stale (22e, row 4).

**What it reverses, so nobody reads the old line as current.** Decision
20 says: "The ops dashboard (8390) and the metrics endpoint (9109) stay
on Tailscale Serve: tailnet-only, never public". Tailscale also appears
in:

- docs/deployment.md, docs/architecture.md, and docs/threat-model.md
  (A4: "network-gated instead").
- The README's server table.
- SECURITY.md ("Access is granted/revoked by tailnet membership").
- `.env.example`.
- `scripts/aws-user-data.sh`, which installs Tailscale with SSH and
  Serve.
- The dashboard's own footer ("Tailscale-gated", `sentinel/dashboard.py`)
  and docstrings in `sentinel/dashboard.py`, `sentinel/metrics.py` and
  `sentinel/operators.py`.

Each one changes in the commit that makes it untrue, not before.

**Status 2026-10-23, evening:** every item above still describes
Tailscale, and under the new order they all change in ONE release - the
one that ships `/admin` (22e steps 3 and 8 together) - because it is
also the release after which Tailscale is gone. Read that day
(`git grep -il 'tailscale\|tailnet\|funnel\|ts\.net'`), the list also
includes: CONTRIBUTING.md (rule 6's Funnel example is history and
stays; the `ssh ubuntu@minas-guardian` and `ec2-54-168-212-44…` lines
under the deploy command go), `.env.example` (the `SENTINEL_PORTAL_URL`
and `SENTINEL_DASHBOARD_TRUSTED_PROXIES` comments), `docker-compose.yml`
(the comments on the 8390 and 8391 publishes, and `SENTINEL_BIND_GATE`),
`deploy/caddy/Caddyfile` (its header), `tests/test_bind_address.py` and
`tests/test_portal_address.py` (its docstring; the `OLD` fixture is a
historic other name and stays); and, without naming Tailscale, the
comments that describe the dashboard as not yet under `/admin`:
`sentinel/edge.py` and `sentinel/liveness.py` (the dashboard's
trusted-proxy setting) and `tests/test_login_lockout.py`
(`test_the_portals_proxy_is_not_the_dashboards`, "until"; renamed in that
release `test_the_dashboard_does_not_inherit_the_portals_trusted_proxy`,
the assertion unchanged). In
docs/deployment.md the places are "Reaching the dashboard", "Remote
access: Tailscale", "The public address" (step 7 and the closing
paragraphs) and "Operator accounts and two-factor" - as the guide stood
that morning: "Remote access: Tailscale" became "Reaching the box: SSH
with the EC2 key pair", and the then-step 7 ("Nothing on Tailscale
moved") became the step-4 teardown in the renumbered section.
docs/phase0-capability-audit.md is a dated record and is not changed.

The dashboard is the higher-privilege surface (decision 19): it adds
customers and reads every customer's contact details. With no tailnet
and no separate hostname, its login is the only thing standing between
the internet and those. The boundary between the two surfaces still
holds: the member portal never gains an operator capability. What
changes is that the operator surface becomes reachable from the internet
too, defended by its own login.

### 22a. One hostname — **ANSWERED 2026-10-22: the dashboard shares it; 2026-10-23: at `/admin`, no allowlist**

A separate admin hostname was recommended, for origin isolation, and was
declined. What sharing the hostname takes:

- **The dashboard moves under a path; recommended: `/admin`.** Today the
  dashboard serves from `/`, and so does the portal, and both have a
  `/login`. The dashboard needs a base-path setting that every link,
  form action and redirect goes through. The proxy then sends `/admin/*`
  to the dashboard (8390) and everything else to the portal (8391).
- **The cookies do not collide.** The dashboard's is `ops_session` and
  the portal's is `minas_portal` (checked 2026-10-22). The dashboard's
  cookie gets `Path=/admin`, and `Secure` (22c).
- **Same origin means the CSRF gate cannot tell the two apart.** The gate
  judges `Sec-Fetch-Site` and `Origin`, and a form on any portal page
  posting to `/admin` counts as same-origin. Nothing on the portal can be
  made to do that today: it runs no JavaScript and escapes every value a
  member supplies. Even so, the dashboard's POST gate should also require
  the `Referer` path to start with `/admin`, so the boundary does not
  rest on the portal staying clean. `Referrer-Policy: same-origin`
  already sends that header.
- **The anti-impersonation rule (#82) now rests on looks and the path
  alone.** The dashboard stays plain and unbranded, and every dashboard
  page shows that it is the ops view.
  *Superseded 2026-10-24 by decision 24: the dashboard wears the identity
  on the same dark ground; the marking and the path tell the surfaces
  apart.*
- **Extra controls apply by path.** Any IP allowlist (22c, item 5) goes
  on `/admin/*` at the proxy.

**Decided 2026-10-23: `/admin`, and how the dashboard serves it.** Read
from the code that morning, nothing implemented it: `sentinel/dashboard.py`
had no base-path setting, every route was matched on its absolute path
(`do_GET`: `/healthz`, `/login`, `/logout`, `/metrics/raw`, `/metrics`,
`/triage`, `/add`, `/account`, `/favicon.ico`, `/`; `do_POST`: `/login`,
`/login/code`, `/login/enrol`, `/portal-access`, `/portal-grant`,
`/triage`, `/add`), and the pages wrote absolute paths: ten `href="/…"`,
six `action="/…"`, three `Location:` (`/login` on sign-out, `/triage`
after triage, `/` after sign-in), plus the Metrics page's `href="/"` and
`href="/metrics/raw"` in `sentinel/metrics.py`. The code ships in the
release this entry describes, in one change set with the proxy route and
the documents, and it does this:

- **One setting, `SENTINEL_DASHBOARD_BASE_PATH`.** Default empty: the
  dashboard serves at `/` exactly as before, so the existing tests, the
  demo server and a bare `sentinel run` do not change. docker-compose.yml
  pins it to `/admin`. Empty and `/` mean the root; `admin`, `/admin`
  and `/admin/` all mean `/admin`. `serve_dashboard` sets it once at
  startup, on every start, so a later default server is back at the
  root.
- **The application serves at `/admin` in truth.** Caddy passes `/admin`
  and `/admin/*` through with the path unchanged (`handle` with a named
  matcher `@admin path /admin /admin/*`, never `handle_path`), so a test
  pins the behaviour without Caddy and the proxy rewrites nothing - the
  reason the headers live in the application too (22c item 3). `/admin`
  and `/admin/` both render the root; there is no redirect between them
  and none at the proxy. Every emitted `href`, `action` and `Location`
  goes through one helper that prefixes the base path, and the Metrics
  page takes the base path as a keyword for its two links (the
  standalone 9109 server passes nothing, so its links stay as they are).
- **Outside the base path.** `/healthz` keeps answering at the bare
  path, before any routing: it is the compose healthcheck, probed from
  inside the container, and it must not learn the prefix.
  `/admin/healthz` is NOT a health route; it meets the sign-in gate like
  any other path, so no Caddy rule withholds it. With a base path set, a
  bare `GET /` answers 303 to `/admin/` (a convenience for the SSH
  tunnel; through Caddy that path reaches the portal, never the
  dashboard), and any other request outside the base path, GET or POST,
  answers 404. The fallthrough inside the base path (an unknown gated
  GET renders the fleet page) is kept, so nothing changes at the root.
- **`Path=/admin` on the cookie.** Both `Set-Cookie` lines (issue and
  the sign-out clear) carry `Path=` the base path, or `/` when it is
  empty, with `HttpOnly; Secure; SameSite=Lax; Max-Age` as before. The
  names already differ (`ops_session`, `minas_portal`), so this tightens
  rather than fixes.
- **The `Referer` path on dashboard POSTs, built in the same release.**
  `_same_origin` still judges `Sec-Fetch-Site` and `Origin`; when a base
  path is set and a `Referer` header is present, it also refuses unless
  the referer's path is the base path or starts with it and a `/`. No
  `Referer` stays as it was (non-browser clients, curl, the tests).
  `Referrer-Policy: same-origin` already supplies the header.
- **How to tell it worked.** `tests/test_dashboard_prefix.py` pins the
  dashboard under a base path (routes, links, cookie path, the bare
  `/healthz`, the 303 and the 404s, the `Referer` rule). On the box,
  `https://guardian.minastrade.com/admin/` shows the operator sign-in
  page (a 401 until signed in), `https://guardian.minastrade.com/admin/healthz`
  shows the same sign-in page and not `ok`, and `http://localhost:8390/admin/`
  through the tunnel does what `/admin/` does.
- **The ops-view marking.** The header already reads "Ops dashboard"
  with "internal · Minas staff only"; the footer's "Tailscale-gated"
  changes in the same release to name what gates it now.
- **Item 5: no allowlist** (the answer above).

### 22b. TLS and the way in — **ANSWERED 2026-10-23: Caddy on the instance, as one more compose service**

> **Built 2026-10-23, branch `public-address`** (22e steps 1 and 2: the
> portal, not yet the dashboard):
> - **`caddy` in docker-compose.yml.** Image `caddy:2.11.7` pinned with
>   its digest (the latest stable release, checked that day). Ports 80
>   and 443 are the host's only public listeners, TCP only (HTTP/3 is
>   off, so nothing needs UDP). Named volumes `caddy-data` (certificates)
>   and `caddy-config`; `restart: unless-stopped`; sentinel's hardening as
>   far as the image allows - every capability dropped except
>   `NET_BIND_SERVICE`, because the image's binary carries that file
>   capability and with all of them dropped exec itself fails ("operation
>   not permitted", measured), plus no-new-privileges, pids, memory and
>   file limits, and rotated logs.
> - **`deploy/caddy/Caddyfile`**, mounted read-only:
>   `guardian.minastrade.com` forwards to `sentinel:8391` - the portal's
>   pages and `/api/v1` - and to nothing else. No `/admin`, no 8390, no
>   9109: the dashboard stays on Tailscale Serve until step 3. Checked
>   with the pinned image: `caddy validate` (valid), `caddy fmt` (clean),
>   `caddy adapt` (one upstream). `docker compose config` accepts the
>   compose file.
> - **The `edge` network**, `10.231.0.0/24`, between Caddy and the portal.
>   Caddy has the fixed address `10.231.0.2`, outside the range Docker
>   hands out, which is the one address the servers trust (22c item 6).
>   This departs from the recommendation below on purpose: Caddy does not
>   use the portal's `127.0.0.1` publish. A container cannot reach the
>   host's loopback, and the portal must see Caddy at one fixed address it
>   can trust. The publish stays as it was, because Funnel uses it.
> - **End to end, on a private Docker network with nothing published**:
>   the pinned image and the real site block (a local CA standing in for
>   Let's Encrypt), and this branch's portal behind it. A client guessing
>   wrong with a spoofed `X-Forwarded-For` was logged and throttled at
>   its own address, not the spoofed one and not Caddy's. A second client
>   behind the same proxy still signed in. Every answer carried the 22c
>   item 3 headers exactly once. `/healthz` and `/metrics` through Caddy
>   reached the portal's sign-in page, and port 80 redirected to HTTPS.
>
> **Still the owner's, in this order** (docs/deployment.md "The public
> address"): the DNS `A` record, the security group's 80 and 443, cutting
> and deploying the release, the checks, and `SENTINEL_PORTAL_URL` in the
> host's `.env`.

> **2026-10-23, the dashboard too (22e step 3):** the Caddyfile gains a
> second upstream, `sentinel:8390`, for the named matcher `@admin path
> /admin /admin/*` (`handle`, so the path reaches the dashboard
> unchanged; bare `/admin` is matched, not redirected), with the same
> `request_buffers 16KiB` and a cap of 32 connections beside the
> portal's 64; the portal keeps everything else; 9109 stays unproxied.
> `tests/test_bind_address.py`
> `test_the_proxy_forwards_to_the_portal_and_nothing_else` is rewritten
> as `test_the_proxy_forwards_admin_to_the_dashboard_the_rest_to_the_portal_and_never_the_metrics`:
> exactly those two upstreams, the matcher's path equal to the compose
> base path, never the metrics port, and `SENTINEL_BIND_GATE` in
> docker-compose.yml saying so in the same diff (the test reads it).
> `SENTINEL_DASHBOARD_TRUSTED_PROXIES: "10.231.0.2"` joins the sentinel
> environment beside `SENTINEL_TRUSTED_PROXIES`, and
> `test_the_portal_trusts_exactly_the_proxys_fixed_address`, renamed
> `test_both_servers_trust_exactly_the_proxys_fixed_address`, holds both
> equal to Caddy's address and to each other. Caddy reaches the dashboard over `edge` as
> `sentinel:8390`, as it reaches the portal - the recommendation's "on
> their existing `127.0.0.1` publishes" was departed from for the portal
> and is departed from the same way here. Two lines in the block above
> end with this release: "the dashboard stays on Tailscale Serve until
> step 3", and "the publish stays as it was, because Funnel uses it" -
> the `127.0.0.1` publishes stay anyway, for the SSH tunnel.

**Recommendation (adopted): Caddy on the instance**, as one more compose service.
It gets certificates from Let's Encrypt, renews them automatically, and
proxies `/admin/*` to the dashboard and everything else to the portal,
both on their existing `127.0.0.1` publishes.

- **DNS:** an `A` record for `guardian.minastrade.com`, pointing at the
  Elastic IP.
- **Security group:** opens 443, plus 80 if a plain-HTTP redirect is
  wanted. These are the first public web ports this host has had. Today
  the only inbound rule is SSH from an admin IP, and the portal arrives
  through Tailscale's relay.

Alternatives considered:

- An AWS load balancer with an AWS-managed certificate. No certificate
  on the box, but more AWS configuration and a monthly cost.
- Cloudflare in front. DDoS protection and a WAF, but a third party
  decrypts every member's traffic.

Caddy has the fewest moving parts for one VM.

One consequence to keep in view: the Elastic IP becomes the way in as
well as the exchanges' allowlisted way out. Changing it already breaks
every key's allowlist (2026-09-21); after this, it also means changing
DNS.

### 22c. What must be true before the dashboard is public

These are not options. They are preconditions, and each one can be
checked:

**Status 2026-10-23, evening, in one line:** items 2, 3, 4 and 6 are
built and pinned (item 6's dashboard half, the compose setting, ships in
the release this entry describes); item 5 is answered "no" (not a
precondition); item 1 is the one reading left, and it stays before the
switch: an un-enrolled operator account on a public URL lets whoever
holds its password enrol their own device at first sign-in
(`sentinel/dashboard.py`, `_login` sends an un-enrolled operator to
enrolment). An EMPTY operator table is not a hole: `_gate` has no
waiver, so the dashboard answers the sign-in page with a 401 to
everyone, and recovery is `sentinel operator add` on the box (decision
19).

1. **Every production operator is enrolled in TOTP.** Mandatory
   two-factor is built (#87, decision 19). Measure enrolment on the box
   before the switch, not after it.
   **Status 2026-10-23: still the owner's**, and needed before the
   dashboard goes public (22e step 3), not before the portal does. The
   measurement is `sudo docker compose exec -T sentinel sentinel operator
   list`: every active row's 2FA column on.
   **Evening of 2026-10-23:** the command needs the `sentinel` container
   running, and it has been stopped since 2026-09-28 (section 22's
   answer block). The reading is taken on the restored container, before
   any operator is given the `/admin` address.
   **Read again 2026-10-24, 12:55 UTC**, with a second operator
   (`jimrox.odezi`, the owner's, enrolled at creation): both rows `on`.
2. **The dashboard's session cookie is marked `Secure`.** Today it is
   `HttpOnly; SameSite=Lax` without `Secure` (`sentinel/dashboard.py`,
   `_issue_session`); the portal's cookie has the flag. That is harmless
   over the tailnet's HTTPS and wrong on the internet.
   **Built 2026-10-23**: `Secure` on the cookie both sign-in paths issue,
   and on the sign-out that clears it. Pinned by
   tests/test_operator_two_factor.py `test_the_session_cookie_is_secure`,
   `test_the_cookie_issued_at_enrolment_is_secure_too` and
   `test_signing_out_clears_the_cookie_with_the_same_attributes`, and by
   the probe "dashboard: the session cookie loses Secure". The SSH
   tunnel's `http://localhost` still signs in: browsers exempt localhost
   (docs/deployment.md, "Reaching the dashboard").
3. **Both servers send the security headers they don't send today:**
   `Strict-Transport-Security`, and clickjacking protection
   (`Content-Security-Policy: frame-ancestors 'none'`, or
   `X-Frame-Options: DENY`). Checked 2026-10-22: the only header of this
   kind either server sets is the portal's `X-Content-Type-Options:
   nosniff`. Set them in the application, where a test can pin them, not
   only at the proxy.
   **Built 2026-10-23**, both of them rather than either: on every
   response of both servers - pages, redirects, the standard library's own
   error replies, the logo files, `/healthz`, the JSON API - from one
   place, `end_headers` in `sentinel/edge.py`.
   `Strict-Transport-Security: max-age=31536000` (a year, with no
   `includeSubDomains` and no `preload`: other minastrade.com hosts are not
   ours to bind), `Content-Security-Policy: frame-ancestors 'none'`
   (merged into a route's own policy if one ever sends one, never a second
   header) and `X-Frame-Options: DENY`. Pinned by tests/test_csrf_origin.py
   `test_every_kind_of_portal_response_carries_the_edge_headers`,
   `test_every_kind_of_dashboard_response_carries_the_edge_headers`,
   `test_a_route_policy_is_merged_and_cannot_weaken_the_edge` and
   `test_frame_ancestors_is_added_or_replaced_never_duplicated`, and by one
   probe per header.
4. **Only the proxy listens publicly.** The host-side publishes in
   docker-compose.yml stay on `127.0.0.1`.
   **Built 2026-10-23**: Caddy's 80 and 443 are the only publishes not on
   `127.0.0.1`. Pinned by tests/test_bind_address.py
   `test_only_the_proxy_listens_publicly` and
   `test_the_proxy_forwards_to_the_portal_and_nothing_else` (which also
   holds the `SENTINEL_BIND_GATE` declaration to the proxy; renamed in the
   `/admin` release
   `test_the_proxy_forwards_admin_to_the_dashboard_the_rest_to_the_portal_and_never_the_metrics`).
5. **Optional, recommended: an IP allowlist on `/admin/*`**, at the
   proxy, for office or VPN addresses. It is a lighter gate than the
   tailnet was, and it keeps password-spraying traffic away from the
   operator login. The owner's call.
   **Status 2026-10-23: still the owner's call.** Nothing is behind
   `/admin` yet, so there is nothing to allowlist; it is decided before
   22e step 3.
   **Answered 2026-10-23, later that day: no allowlist.** The owner wants
   the dashboard reachable from wherever an operator is, and named the
   sign-in and its mandatory second factor, with the login throttle, as
   the protection. Not a precondition; it can be added at the proxy
   later without a code change, and nothing in the application assumes
   it.
6. **Each server must know the real client address behind the proxy.**
   Both servers take the address from the TCP connection
   (`client_address`), and no code reads a forwarded header (checked
   2026-10-22). Behind a proxy, every visitor arrives from the proxy's
   address. The login throttle's per-address limit then becomes a single
   shared bucket: five wrong passwords from anyone block every sign-in,
   member and operator, for fifteen minutes. The fix is to trust
   `X-Forwarded-For` from the proxy's own address only, with a test.
   This may already be true in production: Funnel and Docker's port
   forwarding are both proxies. UNVERIFIED; the portal logs every failed
   sign-in with its source address, so the server's log answers it.
   **Built 2026-10-23**: `SENTINEL_TRUSTED_PROXIES`, exact addresses, read
   once at startup. Unset or blank trusts no proxy, which was the old
   behaviour. From a trusted peer, the client is the right-most
   `X-Forwarded-For` address that is not itself a trusted proxy (Caddy
   writes the last entry; anything left of it is the client's own typing).
   From any other peer the header is ignored. A malformed entry falls back
   to the peer, never to anything further left. Every address the servers
   derive goes through it: the portal's and the dashboard's throttles,
   their logs, the app's sign-in (`sentinel/api.py`) and the operator
   enrolment audit row. Compose pins it to Caddy's `10.231.0.2` for the
   portal. The dashboard reads its own `SENTINEL_DASHBOARD_TRUSTED_PROXIES`,
   unset until it is behind Caddy at `/admin` (step 3), so today it
   believes nobody's header, not even Caddy's. Pinned by
   tests/test_login_lockout.py (the section "the real client behind the
   proxy": spoofing from an untrusted peer changes nothing; behind the
   proxy one visitor's failures do not block another on the web sign-in,
   the app's sign-in or the dashboard's; malformed headers; the enrolment
   audit row), by tests/test_bind_address.py
   `test_the_portal_trusts_exactly_the_proxys_fixed_address` (renamed in
   the `/admin` release
   `test_both_servers_trust_exactly_the_proxys_fixed_address`), and by
   probes for trusting an untrusted peer and for taking the left-most
   address. **The Funnel address is not covered.** Its connections come
   through the loopback publish, from Docker's gateway, which is not
   trusted, so its visitors keep sharing one bucket until Funnel goes (22e
   step 6). The UNVERIFIED line above still stands for that path.
   **Ships with step 3** (the release this entry describes):
   `SENTINEL_DASHBOARD_TRUSTED_PROXIES: "10.231.0.2"` in
   docker-compose.yml, so the dashboard's throttle keys on the visitor
   and not on Caddy; the startup log line then reads "dashboard:
   X-Forwarded-For is believed from 10.231.0.2 only". The code default
   stays unset: blank still trusts nobody, and `tests/test_login_lockout.py`
   `test_the_dashboard_does_not_inherit_the_portals_trusted_proxy`
   (formerly `test_the_portals_proxy_is_not_the_dashboards`; its docstring
   stops saying "until") keeps testing exactly that;
   `tests/test_bind_address.py`
   `test_both_servers_trust_exactly_the_proxys_fixed_address` holds both
   settings equal to Caddy's fixed address and to each other. The
   operator throttle's account bucket is keyed on address and username
   together from this release (found in review: a bare-username bucket
   was a lockout button for any stranger who guessed a name, and it
   blocked the SSH tunnel too). The Funnel caveat above ends with Funnel, which under
   the evening's sequence goes before Caddy starts (22e).

### 22d. Moving customers to the new address — **2026-10-23: no overlap period; the email goes before Funnel does**

- `SENTINEL_PORTAL_URL` changes to the new address, so SMS links point
  there.
- Keep the old Funnel address up for a stated period, with a notice on
  its sign-in page, and email members the new address. The old address
  goes when Tailscale goes (22e, step 6).
- The anti-phishing copy has stayed address-less because there was no
  address worth naming (#88). Now it can say the one thing that works:
  "We will only ever send you to guardian.minastrade.com."
- #88's issue text is amended in the same commit as the answer
  (CONTRIBUTING: retired criteria are amended where they live).

**Built 2026-10-23, behind the setting:** the copy and the notice. With
`SENTINEL_PORTAL_URL` set, the sign-in page's hint reads "Open
guardian.minastrade.com directly" and its anti-phishing note "Our only
address is guardian.minastrade.com" - as text to type, never a link - and
a sign-in page reached by any other name (the Funnel address) says "Our
address has changed" and names the new one. Unset, the pages read as
before, so nothing shows until docs/deployment.md "The public address"
step 8 sets it (step 9 since the same evening's renumbering, when the
Tailscale teardown became a step of its own). The host comes from the
setting alone:
`tests/test_portal_address.py` holds that no module under `sentinel/`
carries the domain as a literal, which is #88's own rule. Still the
owner's: the stated period for the old address and the email to members.

**Changed 2026-10-23: no overlap period.** The owner removes Funnel
outright, so "keep the old Funnel address up for a stated period" is
withdrawn, and with it the period that was still the owner's. What
follows:

- **The notice on the old address will not be seen.** The Funnel name
  resolves to Tailscale's relay, never to this host (decision 20); when
  Funnel is withdrawn the name stops answering, and a member with the
  old bookmark gets a failed connection, not "Our address has changed".
  After Tailscale goes, Caddy serves the one site block, so no other
  name reaches the portal at all. The notice code stays - it costs
  nothing and `tests/test_portal_address.py` pins it - but it is not how
  members learn the address. (And since 2026-09-28 the old address has
  forwarded to a stopped container, so no member has seen a sign-in
  page there for almost a month either - section 22's answer block.)
- **So the email is the only announcement, and it goes BEFORE Funnel is
  withdrawn**: 22e step 5 precedes step 6 and is not optional. Text to
  type, never a link (decision 16): "Our only address is
  guardian.minastrade.com." Still the owner's: sending it.
- **Sent 2026-10-24, 12:12 UTC**, on the owner's instruction, from the
  support mailbox (support@minastech.ca) to the five portal members, one
  plain-text message each, no signature, no link; the alert sender the
  text names was read from the box's `SMTP_SENDER` first
  (alerts@minastrade.com). Three independent reviews changed the draft
  before it went: it no longer claims "we never email links" (every alert
  ends with the front-door URL, `portal_action_line`) nor that alerts
  "keep coming" (the pause, and this morning's refusals); it teaches the
  rule the product keeps - we will only ever send you to
  guardian.minastrade.com, type it yourself even when it looks like a
  link, a message from us never asks for a password, a code or a key -
  and points members at the home page for each account's real state.
  The three alert contacts without a portal login did not get it; they
  never had an address to change. The text, as the record of the only
  announcement:

  Subject: Minas AI Guardian has a new address: guardian.minastrade.com

    Hello,

    The Minas AI Guardian member portal has a new address:
    guardian.minastrade.com

    From now on, type it into your browser yourself whenever you sign in.
    Do not search for it, and do not follow a link to it from any message,
    this one included. Check the spelling before you sign in: anything
    other than exactly guardian.minastrade.com is not ours. If you saved
    the old address anywhere - a bookmark, a note, an icon on your phone -
    replace it. The old address no longer works.

    Nothing needs confirming, re-entering or verifying. You sign in as
    before, with the same email address and password, and if you turned
    on two-factor, your authenticator app and any unused recovery codes
    still work. You will be asked to sign in again the first time, because
    the address is new. Your accounts, alerts and settings are all there,
    and once you are signed in the home page shows whether each of your
    accounts is being watched right now and what, if anything, it needs
    from you.

    How to tell a message from us: not by the address it comes from, which
    can be forged, but by what it does. It only ever sends you to
    guardian.minastrade.com, never anywhere else, and it never asks for
    your password, your two-factor code or your exchange API key. Alert
    emails come from alerts@minastrade.com and tell you to sign in to see
    the details. Your mail app may show the address as a link; type it in
    yourself anyway. If a message ever looks wrong, do not tap or click
    anything in it: type guardian.minastrade.com yourself and sign in. If
    the alert is real, it will be waiting for you there.

    If you would rather not take an email's word for any of this, type
    guardian.minastrade.com into your browser: the sign-in page says the
    same thing.

    If anything is unclear, just reply to this email.

    Minas AI Guardian
    support@minastech.ca

- **From 22e step 2 on, every SMS and email already carries the new
  address** (`SENTINEL_PORTAL_URL`), so a member who follows an alert
  lands on the right site before the email arrives.
- #88 closed 2026-10-24 on the closing comment's evidence.

### 22e. Removing Tailscale — **ANSWERED 2026-10-22: all of it; host access by SSH with the EC2 key pair. 2026-10-23: in one go, the teardown before Caddy starts, SSH at the Elastic IP answered that evening**

Tailscale does five jobs today. Each one needs a replacement before
Tailscale goes:

| Tailscale does today | After |
|---|---|
| The portal's public address (Funnel, `:8443`) | `guardian.minastrade.com`, through the proxy |
| The dashboard (Serve, `/`) | `guardian.minastrade.com/admin`, behind operator sign-in and two-factor. **Decided 2026-10-23**; the code ships in the release this entry describes, pinned by `tests/test_dashboard_prefix.py` and `tests/test_bind_address.py` (22a, 22b) |
| Metrics (Serve, `:9109`) | **Not published.** The endpoint has no login. It stays on `127.0.0.1`, and the dashboard's Metrics page is the view operators use. Publishing the raw endpoint later is its own decision. *2026-10-23:* after the move that page is `/admin/metrics` (raw text at `/admin/metrics/raw`), behind the sign-in; the other way to the raw endpoint is the SSH tunnel's `-L 9109:127.0.0.1:9109`. |
| SSH, the daily way onto the box (`tailscale up --ssh` in `scripts/aws-user-data.sh`) | **SSH on port 22 with the EC2 key pair**, which is already in use (answered 2026-10-22). This is the break-glass path in docs/deployment.md promoted to daily use. Its security-group rule must admit named admin IPs only; read the rule in AWS before Tailscale goes, rather than trusting the deployment guide. **Settled 2026-10-23:** the address is the Elastic IP, `ubuntu@57.181.51.14` with `Minas-AI-Guardian-Key-Tokyo.pem`; the box answered to it that evening. The `ec2-54-168-212-44.ap-northeast-1…` hostname and `ssh ubuntu@minas-guardian` in the SSH examples are stale and go in the same release. The break-glass path to the dashboard is the tunnel `ssh -i Minas-AI-Guardian-Key-Tokyo.pem -L 8390:127.0.0.1:8390 -L 9109:127.0.0.1:9109 ubuntu@57.181.51.14`, then `http://localhost:8390/admin/`. |
| Decision 19's last-operator recovery ("host access, over the tailnet") | The same host access, by whichever path replaces SSH - SSH at the Elastic IP with the key pair, settled 2026-10-23 |

**The order matters, because the box can be locked out.** Tailscale is
the daily way onto the host. Remove it before its replacement is proven
and nobody can reach the host: no deploys, and no last-operator
recovery. So:

1. DNS for `guardian.minastrade.com` points at the Elastic IP, and the
   proxy is up with a certificate.
2. The portal answers at the new address, and `SENTINEL_PORTAL_URL`
   changes.
3. The dashboard answers at `/admin`, with every 22c precondition met.
4. SSH without Tailscale is proven: connect with the key pair by the
   instance's public address, not its tailnet name, and run a read-only
   command (`sudo docker compose exec -T sentinel sentinel version`).
   This is rule 6's read-only twin.
5. Members are moved to the new address (22d).
6. Read `sudo tailscale serve status`, then withdraw Funnel and Serve.
7. Disconnect and uninstall Tailscale, and remove the machine from the
   Tailscale admin console. Verify each command on the box at the time
   (rule 6).
8. Docs, SECURITY.md, `.env.example` and `scripts/aws-user-data.sh` stop
   describing Tailscale in the release that ships the code for steps 2
   and 3. docs/DEPLOYMENTS.md records each step with its evidence.

**Status 2026-10-23:** the code for steps 1 and 2 is built (22b above),
and running them is the owner's, in docs/deployment.md "The public
address". Steps 3 to 8 are untouched, and nothing Tailscale-related was
removed: Funnel, Serve and Tailscale SSH keep working beside the new
address until their own steps.

**Status 2026-10-23, evening, and the sequence as now run (same step
numbers, so earlier references hold):** the box answered on port 22 at
`57.181.51.14` to the EC2 key pair, `tailscaled` holds 443 on the
Tailscale address (a probe binding `0.0.0.0:443` answered "Address
already in use"), and the `sentinel` container has been stopped since
2026-09-28 by the owner's decision (section 22's answer block). Those
three facts set the sequence:

- **Step 4 first, and its connection is answered**: SSH by the Elastic
  IP with the key pair, from an address in the security group's SSH
  rule. 22e's read-only command
  (`sudo docker compose exec -T sentinel sentinel version`) needs the
  container running, so the runbook proves the login with two that do
  not: `sudo git describe --tags` and `sudo docker compose ps -a` in
  `/opt/minas-guardian` (docs/deployment.md "The public address", step
  1), and runs `sentinel version` once the restore order has brought the
  container back. This is the step that keeps the box reachable after
  step 7, and it is why the rest can run in one go.
- **Step 5 next**: email members the new address, before Funnel goes; no
  waiting period (22d). **Owner's.**
- **Steps 6 and 7, one session on the box, BEFORE step 1's proxy
  starts**: `sudo tailscale serve status`, then withdraw Funnel and the
  Serve entries with the command block in docs/deployment.md "The public
  address" step 4 (`funnel reset`, then `serve reset` - decision 20's
  warning against `funnel reset` is withdrawn on purpose, Serve goes
  too), each checked against `serve status` at the time (rule 6); then
  disconnect, uninstall, and remove the machine from the admin console. They come first because `tailscaled` holds
  443 and Caddy's publish needs it, and the move costs no running
  service: nothing in the container has answered since 2026-09-28.
- **Steps 1, 2 and 3 are one deploy**: DNS is **done by the owner
  2026-10-23** (`guardian.minastrade.com` → `57.181.51.14`, no AAAA; the
  security group admits 80 and 443); the release that carries Caddy AND
  `/admin` (one release, not two: v1.13.0 carries the portal half and is
  not deployed) is deployed per docs/deployment.md "The public address"
  and "Restoring after the 2026-09-28 pause", `SENTINEL_PORTAL_URL` is
  set, and the dashboard answers at
  `https://guardian.minastrade.com/admin/` with 22c items 2, 3, 4 and 6
  holding, item 5 answered no, and item 1 read on the restored
  container. **Owner's to run.** The code for step 3 ships in that
  release (22a, 22b).
- **Step 8 is in the same release**: docs, SECURITY.md, `.env.example`,
  `scripts/aws-user-data.sh`, the dashboard footer and the docstrings
  stop describing Tailscale, because after step 7 none of it is true.
  docs/DEPLOYMENTS.md records each step with its evidence.

The order of 2026-10-22 above is kept as written; this is the same list
run with the facts of the evening. "The order matters, because the box
can be locked out" still holds: step 4's connection is answered before
anything is removed.

**Run on the box the same night (2026-10-23, UTC):** step 4 - the login
with the key pair at `57.181.51.14` proven with `git describe --tags` and
`docker compose ps -a` on the stopped stack; steps 6 and 7 - Funnel and
Serve reset (`No serve config`), `tailscale down`, the package removed
and purged, `/var/lib/tailscale` deleted, no `tailscaled` socket, no
`tailscale0` device (the admin-console removal is the one click left);
and the runbook's step 5 - `held/` moved to
`/var/backups/minas-guardian/held-2026-09-28/`, nothing on 80 or 443, the
`edge` subnet free, the checkout clean. The evidence, command by command,
is in docs/DEPLOYMENTS.md. Steps 1 to 3 and 8 ship with the release this
record describes; the box waits for it at the runbook's step 6.

**Deployed 2026-10-24: v1.14.0, every 22e step done but two.** PR #122
merged by the owner, v1.14.0 cut (commit `2b7b6c8`, the slow gates
covered by the dispatched run on the merged head), and the runbook's
steps 6 to 9 run by the owner's hand the same morning, each read before
the next (docs/DEPLOYMENTS.md, 2026-10-24): `deploy: OK — v1.14.0 is
live and verified`; Caddy on 80 and 443 and nothing else public; both
servers believing `X-Forwarded-For` from `10.231.0.2` only; a Let's
Encrypt certificate for `guardian.minastrade.com` (to 2027-01-22); the
portal at `/` with its headers, the API answering 401 unauthenticated,
http redirecting with 308; the dashboard at `/admin/login` with its six
headers, `401` on `/admin`, `/admin/` and `/admin/healthz`, its form
posting to `/admin/login`; `sentinel operator list` with one operator,
2FA `on` (22c item 1 holds); `SENTINEL_PORTAL_URL` set and the sign-in
page saying "Our only address" (#88 closed on that evidence); the two
timers and the digest cron back on. So: steps 1, 2, 3, 4, 6, 7 and 8 are
done; **step 5 - the email to members with the new address - went on
2026-10-24** (22d holds the text); the machine's removal in the Tailscale
admin console (a browser click, the tail of step 7) is still the owner's.
22d's notice on the old address was never shown to anyone, as expected: the
old address stopped answering at step 6 of the teardown.

**Reviewed before it faces the internet (2026-10-24).** Four readers went
through the change adversarially - the prefix code, the proxy and compose
and their tests, every document against the code, and the exposure
itself. Fixed in the same release, each with a test: a reflected script
in the fleet page's sort link (the `?state=` filter written raw into an
attribute; it now echoes only a bucket the page counts, escaped); the
operator throttle's bare-username bucket (a lockout button for any
stranger, which also blocked the SSH tunnel; now keyed on address and
username); `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`
and `X-Robots-Tag: noindex, nofollow` on every dashboard response;
`LAST LOGIN` stamped after the second factor, not at the password step;
the build line and the empty-table hint kept off the public sign-in page;
a junk base path refused at startup; the CSRF gate requiring a browser's
POST to carry a `Referer` under the base path (a planted same-origin form
can suppress its referrer, so "present and outside" was not enough) and
saying why it refused, in the body and the log; a stale design-guard
probe re-anchored. Recorded as open, in docs/threat-model.md: operator
sessions have no epoch, so a stolen cookie outlives a password change or
a 2FA reset for up to twelve hours and `disable` then `enable` revives
it (break-glass: replace `operator_meta.session_secret`); a distributed
guesser is held by the per-address throttle only; each password check is
scrypt in the engine's process behind a cap of 32 proxy connections; and
22c item 1 is a hard gate for the same reason it always was - a `NOT
YET` operator on a public URL belongs to whoever presents the password
first.

### What it blocks

The apps (decision 21), which are built against this hostname - unblocked
once steps 1 and 2 have been run on the box; #88's anti-phishing copy; and
every document that describes how the system is reached.

From 2026-10-23 it also blocks the dashboard's own reachability: once
Serve goes, `/admin` through Caddy and the SSH tunnel
(`http://localhost:8390/admin/`) are the only ways to it.

**Related:** decision 19 (operator two-factor, host-access recovery),
decision 20 (the exposure model this supersedes), decision 21 (the
apps), #82 (ops screens must not impersonate customer pages), #88 (the
address).

---

## 23. A QR-code encoder in the two-factor enrolment path — **ANSWERED 2026-10-24: `segno`, accepted by the merge of #130; deployed in v1.15.0**

Enrolment showed the TOTP secret as text only: the 32-character secret
and the `otpauth://` URI, to be typed into the authenticator app. On
2026-10-24 the owner failed to enrol an operator account from a phone
that way and asked for a QR code; authenticator apps scan the otpauth
URI. The repository's precedent keeps third-party code out of the
authentication path: `sentinel/totp.py` writes HOTP/TOTP itself rather
than take `pyotp`, and decision 5 refused a PDF library as a
supply-chain surface on a security product. A QR encoder on the
enrolment page is that question again, so the dependency is recorded
here as a decision and not only as a line in `pyproject.toml`.

**Options:**

(a) **`segno`, as built.** Pure Python, no dependencies of its own on
Python >=3.10, BSD-3, about seven modules, read in full for this change.
Called only through in-memory buffers (`svg_inline` and `terminal`); it
sees the otpauth URI - the same secret the page already shows - and
nothing else. Pinned `>=1.6,<2`; covered by the existing `pip-audit`
gate and the next image build. The package also ships a `segno` CLI and
a `show()` that writes a temp file and opens a browser; neither is
called.

(b) **An in-tree encoder.** Byte mode, versions to about 20, error
level M, Reed-Solomon and the masking rules: roughly 300 lines, tested
against known vectors. No dependency - and more code in the
authentication path to maintain.

(c) **Text only, as before.** The route the owner failed with.

**Recommendation: (a), as built.** The trade is the one the product
already makes for `cryptography` and `requests`, and the surface is
small: one module's worth of calls, no socket, no file, and the secret
it handles already on the page. The answer is the owner's, and it is
given by the PR rather than by a line here: merging the PR accepts (a);
asking for (b) replaces one module (`sentinel/qr.py`) and nothing else;
(c) is the status quo.

**Before merging, one thing only the owner can do:** scan the three
renderings once with the authenticator app(s) members use - the dark
portal page, the light dashboard page and the terminal block - and say
which app. Whether they scan was not checked with a device when this
was written; the tests pin the markup to segno's, not a phone's reading
of it.

**Scanned 2026-10-24**, by the owner, with Google Authenticator, from the
branch's rendering of each page and of the terminal block: all three
read. What is left to decide is the dependency, and it is decided by
merging or not merging the PR.

**Decided 2026-10-24** by the merge of #130 (the owner's merge, 15:18 UTC)
and shipped in v1.15.0 the same day, deployed 16:58 UTC
(docs/DEPLOYMENTS.md).

### What it blocks

Nothing is built behind it: the PR is the whole change, and what waits
is the merge.

**Related:** decision 5 (a PDF library refused on the same ground),
decision 19 (operator two-factor, mandatory), docs/threat-model.md A5
(where `segno` sits in the supply-chain surface).

---

## 24. The ops dashboard carries the Minas identity — **ANSWERED by the owner 2026-10-24** (reverses the dashboard half of decision 2 on #82)

> **The owner's words (2026-10-24):** "let everything about the admin
> dashboard have the Minas colours and logo. the current dashboard is
> mostly plain white, dry and boring and no company logo even." Offered a
> light variant: "the dashboard should be dark like the portal. remember
> that this is a thorough revamp of the project." And on how the logo
> reaches the page: "no /admin/brand nonsense. keep it simple and just do
> what i ask you."

**What this reverses.** Decision 2 on #82 (2026-08-19) had two halves:
"brand the portal" and "deliberately do NOT brand the ops dashboard ...
no logo". The second half is withdrawn, and with it its restatements:
21g's "the ops dashboard stays unbranded" (2026-10-22), 22a's "the
anti-impersonation rule now rests on looks and the path alone"
(2026-10-23) and the guard's "light against dark". Its reasoning - an ops
screenshot must not be usable to impersonate a customer page, and a
mispasted internal URL should look visibly wrong - is kept, and carried
by an explicit operations marking instead of by the absence of the brand
or by a different ground.

**What stays decided.** Decision 1 on #82: no framework, no JavaScript
beyond `_LOCAL_TIME_SCRIPT`, no external request. Decision 3: the
identity lives in `sentinel/brand.py`, which the dashboard now draws
from exactly as the portal does - one spelling of gold. The two surfaces
keep separate stylesheets, asserted in both directions. The dashboard
stays a dense, tables-first control surface, not a reading page.

**The operations marking (required on every dashboard page, signed in
or not).** With the ground shared, these four tell an operator screen
from a customer page:

1. The lockup: the Minas mark, the wordmark "Minas AI Guardian" and the
   qualifier **Operations** in its own class (`ops__tag`, never the
   portal's `brand__tag`, whose word is "Member portal").
2. The signed-in operator's name and a sign-out control in the header
   ("Signed in as erwin · Sign out"); the sign-in family shows the lockup
   without a name. No customer page ever shows an operator's name.
3. `<h1>Ops dashboard</h1>` with "internal · Minas staff only" in the
   header, visible on every page; the title `Ops dashboard — internal`
   and the footer sentence are byte for byte what they were, so the
   deploy runbook's smoke check still holds.
4. The path: everything lives under `/admin`, and the title says
   "internal".

No customer-facing copy in the shell: no tagline, no limits link, no
reassurance sentence, never the words "Member portal".

**The logo** is the portal's `/brand/*` file on the same host, reached
through `brand.MARK_HTML` and `brand.ICON_LINKS` as they are - no new
route, no new helper; through the SSH tunnel, where only the dashboard
answers, the image is absent, and it is decorative.

**The metrics page** (`/admin/metrics`) joins the dashboard shell; the
retired infinity mark, the gradients and the pulse go with it.

**The guards are inverted, not deleted.** `tests/test_surface_styles.py`'s
dashboard half now requires the brand hues, equal to `brand.py`'s, and
still refuses gradients, glow, motion, any external request and a shared
stylesheet; the four marking parts are asserted on `_page`; both grounds
are `#0d0f13` on purpose; the portal never emits `ops__tag` and the
dashboard never emits `brand__tag` or "Member portal"; every emitted
class is styled; the design note names this decision and agrees with the
sheet. `design/dashboard/DESIGN-SYSTEM.md`, `docs/portal-design.md`
decision 2, `sentinel/brand.py`'s docstring, `design/brand/README.md`
and docs/threat-model.md A3 are amended in the same commit.

**Deployed 2026-10-24** in v1.15.0 at 16:58 UTC, the same day the owner
approved the rendered pages (docs/DEPLOYMENTS.md).

### What it blocks

Nothing.

**Related:** decision 5 (print stays the browser's own; the dashboard's
print sheet still flips to white paper and black ink), decision 19
(operator two-factor, which gated #82's dashboard pass), #82 (the design
decisions of 2026-08-19), docs/threat-model.md A3 (the anti-phishing
posture).

---

## 25. The portal's front door is a landing page; the sign-in moves to /login; an ended session lands there — **ANSWERED by the owner 2026-10-24**

> **The owner's words (2026-10-24):** "a very important update. when a
> user visits guardian.minastrade.com, it is the login page, instead of
> like a landing page that should contain link to login or something. the
> landing page ought to be what the system is about. this should also be
> in the mobile apps too. when a user login session expires, it can take
> them to like that page. finally, i want you to create a skill that fires
> when a change is made to the UI, the change should reflect in both web
> UIs and the mobile UIs when necessary."

Four instructions, taken in the owner's order.

**1. The front door.** `GET /` without a session is a landing page, not
the sign-in form. It says what the system is about, in the portal's own
primitives and nothing new: the statement ("Minas AI Guardian watches
your exchange accounts around the clock and tells you within minutes when
money starts leaving."), the lead on the read-only key, "How it works" in
three steps with the polling cadence read from the running configuration
at render time, the "Minas AI Guardian cannot stop an attack." callout
and the whole of what it does and cannot do, the one address named as
text with the anti-phishing rule under it, and the way in: a "Sign in"
link in the top bar and the page's one gold button, both to `/login`.
There is nothing to sign up for and the page says so - "Minas sets
monitoring up with you. There is nothing to sign up for here." The
sign-in page is unchanged, now explicitly routed at `/login`; signed in,
`/login` goes home (303) where it used to 404. The renderer is
`_render_landing` in `sentinel/portal.py`, the page is pinned by
`id="landing-title"` as the sign-in page is by `signin-title`, and the
mockup is `design/portal/landing.html`.

**2. The app.** `mobile/src/app/landing.tsx` ("Welcome", route
`/landing`) is the first screen a phone without a sign-in sees: the same
statement, lead and setup sentence, the same three steps (without the
numbers - the app cannot read the configuration before sign-in), the same
callout and limits, and a "Sign in" button to `/sign-in`, which gains a
header and a way back. The sentences the two surfaces share are pinned in
the server suite: `tests/test_mobile_app.py` reads the screen and asserts
the portal's constants appear in it verbatim.

**3. An ended session.** A member whose cookie is presented and rejected
- expired, the session epoch bumped, tampered, the member gone - is sent
to the landing page (303 `Location: /`) from any other page they asked
for; `/` itself answers the landing page directly. Either way the page
opens with "You were signed out." and when a session ends (the hours
from `SESSION_IDLE_SECONDS` and `SESSION_TTL_SECONDS`, never typed in,
and a password change), then clears the stale cookie (`Max-Age=0`). The
redirect leaves the cookie alone, since the landing page needs to see it
to say so; a visitor who arrives without one sees the plain page, no
notice, no `Set-Cookie`. Every other path without a session - a member
page, an unknown one such as `/healthz` - answers the same 303, and so
does a POST on a member route (the acknowledgement or snooze it carried
is not applied); the pre-auth flows (`/login`, `/login-code`, `/forgot`,
`/reset`) keep their own, a login error stays on the form, and a
password reset lands on `/login` so the member lands on the form.
Sign-out lands on the plain landing page. In the app, "This phone was
signed out." moves from the sign-in screen to the landing; signing out
from Security lands there without it.

**4. A UI change lands on every surface.** The `ui-parity` skill
(`.claude/skills/ui-parity/SKILL.md`) carries the map of surfaces - which
portal page, constant or token corresponds to which app screen and which
API route carries it, and which surfaces have no counterpart (the
dashboard; the print sheet and the pause, snooze, erase and Telegram
settings, portal-only by 21d; push notifications, app-only and open on
21e) - the decision procedure, the rules every surface obeys, the guards
to run and the records to write. `scripts/ui-parity-reminder.py` is a
PostToolUse hook, registered in the committed `.claude/settings.json`:
when an edit, a write or a writing shell command touches a user-facing
file, it names the surface, the counterparts and the skill to invoke
before finishing, and otherwise prints nothing; it never raises and
always exits 0. `tests/test_ui_parity_hook.py` runs it as a subprocess
and holds the skill and the script to the same list of surfaces.
CONTRIBUTING.md has the section "A UI change lands on every surface".

**What stays decided.**

- Decision 1 on #82: no framework, no JavaScript beyond
  `_LOCAL_TIME_SCRIPT`, no external request. The landing page is
  server-rendered HTML + CSS.
- Decision 17: no self-serve signup. The landing page offers no "sign
  up"; it says Minas sets monitoring up with the member.
- #88 / 22d: the public address exists in exactly one place, the setting
  (`portal_host()` in `sentinel/edge.py`); copy names it as text, never a
  link; the "Our address has changed" notice appears on any host that is
  not the one address - on the landing page as on the sign-in page.
- #48 / G-44: no prevention claim ever reaches a customer.
  `tests/test_no_prevention_claims.py` scans every string literal in
  `sentinel/portal.py`, the landing copy included; its verbs are watches,
  reads, tells, alerts, keeps the record.
- Numbers come from the running configuration, never typed into copy
  (`polling_cadence(rules)`, `SESSION_IDLE_SECONDS`,
  `SESSION_TTL_SECONDS`).
- The portal's design system (docs/portal-design.md,
  design/portal/DESIGN-SYSTEM.md): screens compose existing primitives;
  one gold `.btn--primary` per screen (the hero's - the top bar's "Sign
  in" is a link); every emitted class is styled in `_PORTAL_STYLE`; no
  tables; no motion; mobile-first; touch floors.
- The ops dashboard (`sentinel/dashboard.py`, `/admin`) is unchanged.
- The app's sign-in mechanics (`tokens.ts`, `session.tsx`, 21c) are
  unchanged; only the routing and the screens change.
- 21b stays open as it was: the browser portal is still server-rendered.
  A landing page is a screen, not a framework.

**What the old rule protected is kept.** "Every other unauthenticated GET
lands on the login page" existed so that a visitor without a session
cannot tell which paths exist. The 303 to `/` keeps that: a member page
and an unknown path answer identically.

**Deployed 2026-10-24** in v1.16.0, between 23:52 and 23:56 UTC, the
evening the owner merged #135 (docs/DEPLOYMENTS.md).

### What it blocks

Nothing.

**Related:** decision 17 (no self-serve signup), 21b (the browser
portal), 21c (the app's sign-in), 21d (the limits copy the app reads),
22d (the one address and the moved notice), docs/threat-model.md A3 (the
anti-phishing posture), docs/portal-design.md and
design/portal/DESIGN-SYSTEM.md (the landing's composition),
CONTRIBUTING.md ("A UI change lands on every surface").

---

## 26. A contact move and the old address's portal login; what a refused notice pages; where a reset link goes — **PARTLY ANSWERED: 26a decided in session (deployed in v1.17.0); 26b and 26c ANSWERED by the owner 2026-10-25 (deployed in v1.18.0); 26d and 26e open**

> **The owner's words (2026-10-25):** "password reset email on members
> portal not coming through. tried resetting password on my members
> portal account. didn't receive any emails." Then: "it was not an admin
> password i was trying to reset. i was my members portal password."

**What happened.** Both requests (2026-10-24 23:58 UTC and 2026-10-25
10:09 UTC) were logged by production as "password reset requested for an
unknown address": the address typed is the alert contact on two accounts
and has no portal login - logins were issued for four other accounts
only. `/forgot` answers the same page whether or not an address has a
login and sends nothing for one without (#33), so nothing was sent and
nothing failed; email to the provider was healthy the whole time. A
login comes only from an operator, on the account page ("Generate portal
password", decision 10), and that was the answer given. The
investigation behind it found six latent defects on the same paths. The
owner chose to build the separate reset limiter, sending the reset after
the answer, email-failure visibility (the liveness fix and a dashboard
card, with paging held for 26b), the false CRITICAL pages, contact moves
against logins (26a) and the signed-in 404 on the recovery pages; not the
"no login yet" email (26d) and not filing the rest as issues. All of it is
in CHANGELOG.md `[Unreleased]`; the handoff is in docs/AUTONOMOUS-LOG.md
(2026-10-25).

### 26a. Does a contact move take the old address's portal access? — **DECIDED IN SESSION 2026-10-25: revoke; built; the owner can reverse it**

`sentinel set-contact` moved an account's contacts in `accounts` alone.
Since #61 a login issued from an account page opens exactly its grants,
so a scoped login for the OLD address kept opening the account - reading
it, and acknowledging its alerts, which stops the escalation ladder -
while the destination-change notice told that address it "can no longer
see the account in the Minas portal". The question put to the owner
offered both remedies ("the old address's access gets revoked or its
notice corrected, your choice of which"); the owner chose the fix and
named neither, so the session chose revoke and built it:

- **Revoke.** The move revokes the old address's grant for that account
  when the address is no longer a contact on it (`revoke_account`,
  recorded as `member_account_revoked` against the `--as` operator). A
  mailbox somebody moved away from, perhaps because it was compromised,
  must not keep access - and the notice had already promised it. An
  address kept on as the other contact keeps its access; the login's
  password and other accounts are untouched; a legacy (unscoped) login
  is left alone, since its contact rows already dropped the account.
- **Tell the operator.** The command prints, for each moved contact,
  whether the new address has no login yet, a login that does not open
  this account, or one that does, and one line per old address whose
  access ended. Nothing is issued or granted: scope stays explicit and
  operator-issued (decision 10).
- **A truthful notice.** Each clause is read after the change: the
  portal sentence only when `member_accounts` agrees, and an address kept
  on as the emergency contact is told so instead of "Alerts about this
  account will no longer be sent to this address".
- And `set-contact --alert-phone` without `--alert-email` is refused: it
  used to write the primary email NULL (decision 4's floor had a gap),
  and with this change it would also have taken the customer's own
  access.

The other remedy - leave the grant and reword the notice to say the
login still opens the account - is one call site to switch to, if the
owner prefers it. Production has recorded no `alert_contact_updated` or
`secondary_contact_updated` event (read 2026-10-25), so `set-contact` has
never moved a contact there and left no stale grant behind; only a hand
edit in SQL could have, and the read-only query for one is in
docs/AUTONOMOUS-LOG.md (2026-10-25).

**Deployed 2026-10-25** in v1.17.0, between 14:54 and 14:57 UTC
(docs/DEPLOYMENTS.md).

### 26b. Should a refused notice page Minas? — **ANSWERED by the owner 2026-10-25: option 2; deployed 2026-10-26 (v1.18.0)**

A notice - the reset link, "your password was changed", "two-factor was
turned off", the destination change, the emergency-contact note - is
LOW, and the refused-email page (`_page_if_refused`, the 2026-09-22
rule) fires only for a customer's CRITICAL or a coverage alert. So a
refused notice pages nobody; since 2026-10-25 the "Failed email
deliveries" card on the ops overview is the only place it shows.

1. **Leave it** - the card, read when someone looks.
2. **Page on a refused security notice** (password changed, two-factor
   off, destination changed): these are the tripwires an attacker hopes
   never arrive, and a refusal means the member was not warned.
3. **Page on every refused notice**, the reset link included.

**Recommendation: 2.** A refused reset link is self-correcting - the
member asks again or calls Minas - but a refused tripwire is silent by
construction. It changes the 2026-09-22 paging rule, which is why it
waits for the owner.

**The answer (2026-10-25):** the owner's "go ahead" to the recommendation,
asked as "if a 'password changed', 'two-factor turned off' or 'alert email
changed' notice is refused, should that page Minas? I'd say yes". Built:

- The three notices - `password_changed`, `two_factor_disabled`,
  `alert_destination_changed` - are `TRIPWIRE_NOTICE_RULES` in
  `sentinel/alerts/contact.py`. When the email provider REFUSES one (an
  SMTP 4xx/5xx reply), Minas gets a CRITICAL `security_notice_undelivered`
  page, deduplicated per notice, address and account. A send that never
  reached the provider is not a refusal and stays a log line, as for an
  alert (#58): the question was asked about refusals.
- The page always fires on a refusal, whatever the Telegram leg did. A
  first fix round made a connected chat that took the copy skip the
  page; both of its verifiers proved that anyone holding the member's
  session can connect a chat of their own (connecting emails the member
  nothing - 26e) and so switch the page off for the very change it
  exists to catch. Withdrawn: the page reports how many connected chats
  took the copy (`telegram_accepted`) and that such a chat may not be
  the member's.
- The page carries the address masked, the account (or, for a login
  notice, the accounts the login opens, at most ten by name), the SMTP
  code and the server's words with any address in them masked, and
  `failed_at` - exactly the failed delivery row's `created_at`, where the
  full address is.
- Wiring, in every process that sends one: the portal's `POST /reset`
  and `POST /security/2fa/disable` pass `serve_portal`'s own operator
  alerter; `sentinel set-contact` builds one for that notice and, if it
  cannot, warns once and moves the contact anyway.
  `tests/test_security_notice_delivery.py` pins each through its real
  path, and its `WIRED` registry fails until a new rule has such a test.

**Still open within 26b:** two more notices call themselves tripwires in
their docstrings - "Telegram alerts were disconnected"
(`telegram_disconnected`) and the app's new-phone sign-in
(`app_signed_in`, decision 21c) - and were left out because the owner was
not asked. **Recommendation: add both**, by the same reasoning (each is
what an attacker does after getting in). Each takes its line in
`TRIPWIRE_NOTICE_RULES`, an operator alerter on its sender (both senders
build `ContactRouter(db=db)` today, and the app's `Api` has none to
pass) and a `WIRED` execution test.

### 26c. Should the reset link go to a connected Telegram chat? — **ANSWERED by the owner 2026-10-25: email only; deployed 2026-10-26 (v1.18.0)**

A notice bypasses the member's channel preferences, so the contact router
sends it to every channel the address has, and the reset link reaches a
connected Telegram chat as well as the mailbox. `_send_reset_email`'s
docstring says the plaintext token exists in one email only.

1. **Email only** - the link proves control of the mailbox, which is what
   a reset is meant to prove.
2. **Keep the Telegram copy** - and correct the docstrings.

**Recommendation: 1.** With the copy, whoever holds the member's Telegram
can take over their portal login without the mailbox.

**The answer (2026-10-25):** the owner's "go ahead" to option 1. Built:
`_send_reset_email` sends its notice `email_only`, and the contact
router gives such a notice no Telegram copy and no text, so the
plaintext token exists outside the mailbox nowhere but in the mail
provider's hands. Every other notice keeps every channel - the "your
password was changed" notice that follows a reset still reaches a
connected chat.

### 26d. A "no login yet" email — open (not chosen 2026-10-25)

When `/forgot` is asked for an address that is an alert contact without a
login - this very case - email that inbox "there is no portal login for
this address; ask Minas to issue one". The page would not change, so the
requester learns nothing, but a stranger could trigger it (rate-limited by
the reset limiter) and it spends email quota (the Resend free plan allows
100 a day). Not chosen on 2026-10-25; recorded so the option is not lost.

### 26e. Connecting a Telegram chat tells the member nothing — open (raised 2026-10-25)

Found by the review of 26b. `POST /telegram/link` needs only a portal
session: it mints a link code, the bot confirms it from whatever chat
sends `/start <code>`, and the member gets an activity row ("Telegram
alerts connected") and no email. A second chat does not replace the
first. And a password reset retires the login's sessions but not its
Telegram links, so a chat somebody else connected outlives the member's
recovery and keeps receiving every alert and notice the address gets.

1. **Leave it** - the activity page shows the connection.
2. **A "Telegram connected" tripwire email** to the login's address on
   every new connection, with the chat's name, and in
   `TRIPWIRE_NOTICE_RULES` so a refused one pages Minas.
3. **2, and a completed password reset disconnects every chat** (the
   member reconnects the ones that are theirs).

**Recommendation: 3.** A reset is what a member does when they think
someone else got in, and today it leaves that person's chat connected.

### What it blocks

Nothing. 26d, 26e and the two further tripwires in 26b are each a small
change once answered.

**Related:** decision 4 (the primary email is the floor), decision 10 and
#61 (scoped logins), #33 (the reset flow), #31 (the throttle keys),
docs/threat-model.md (the portal's reset request), CHANGELOG.md
`[Unreleased]`.

---

## 27. The privacy policy at /privacy — **PARTLY ANSWERED 2026-10-26: drafted on the site; eight facts and the approval are the owner's and the lawyer's**

> **Owner, 2026-10-26**, publishing the Android app: Google Play's User
> Data policy wants a privacy policy's address in the store listing and in
> the app. The owner's answers: draft it **on the site**
> (`/privacy`), for the owner and the lawyer to approve before it ships;
> contact **support@minastech.ca**; the developer is **"Minas AI"**;
> **name Anthropic** as a processor, for the optional incident summary
> (triage) and for Minas's own operating and support work; keep keys and
> contacts after monitoring stops **"until they ask; the policy says
> so"**; and **fix erasure to match** its "nothing that identifies you"
> promise (done: decision 6b).

**What is built (the privacy-page PR, held as a draft until approval).**
`GET /privacy`, public and the same with a session, in the portal's
frame; every footer links to it, the sign-in page too, and the app opens
it in the browser from its welcome, sign-in and Settings screens. The
text is drawn from the data inventory of 2026-10-26 and says only what
the code makes true: numbers (session lengths, the app's 30 days, every
retention period) and whether the AI summary is on come from the code
and the configuration; the app's server is named from the setting.
`PRIVACY_APPROVED = False` shows "Draft for review."; a test refuses the
flag while any "[To confirm" mark is left.

**Open — each is a "[To confirm: …]" on the page:**

1. Minas AI's registered legal name and postal address.
2. Whether SMS alerts are being sent, and the SMS provider's name.
3. The data-processing terms with Amazon Web Services, Resend and
   Anthropic, and the safeguards the governing law requires for data
   leaving Japan (and Canada).
4. How quickly a request is answered (PIPEDA's limit is 30 days).
5. Which data-protection authority a member can complain to (the
   governing law: section 6's jurisdiction question).
6. The minimum age.
7. How long server logs are kept (the application sets nothing).
8. How long backups are kept (no rotation is written down; an erasure
   does not edit a backup).

**Commitments to confirm as written** (not marked, but the owner's
promises): members are told by email before a change to what is collected
or who handles it; Minas staff's AI tools can read the service's records
while operating and supporting it; the portal loads nothing from any
other site.

**To approve:** answer each item, replace its mark, set `PRIVACY_DATED`
to the date of approval, and set `PRIVACY_APPROVED = True`
(`sentinel/portal.py`); merge, and the next release carries it. Then the
store listing takes `https://<the one address>/privacy`, and the app build
that goes to production carries the links.

**Related:** decision 6 and 6b (retention and erasure), decision 21
(the app), docs/data-retention.md, `mobile/README.md` ("Before Play
production").

---

## Not a decision, but needs saying: #16's remaining criterion

**Resolved 2026-08-13 (v1.1.0):** operator identities exist (#54) and
threshold changes are recorded against a name; #16 is closed. Kept as
written for the record.

G-12's third criterion — "threshold changes are recorded, showing **who**
changed what and when" — is not a product decision, it's a dependency. There
are no operator accounts (#54), and `rules.yaml` is edited on disk, so there is
nobody for the process to attribute a change to. Recorded here only because
someone reading this list will wonder why #16 is still open. Full reasoning is
on the issue.
