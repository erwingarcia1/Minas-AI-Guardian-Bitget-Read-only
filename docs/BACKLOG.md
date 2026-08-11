# Minas AI Guardian — Complete Feature Backlog

**Version** 0.1 · **Date** 11 August 2026 · **Companion to** `guardian-prd.md`

Every feature the product needs, written as user stories with acceptance criteria. Plain language — no code, no jargon.

**How to use this with Claude Code:** each story has an ID like `G-14`. Enter plan mode (`Shift+Tab`), then say *"Implement G-14 from docs/BACKLOG.md. Show me the plan first."* Work one story at a time.

### Status key

| | Meaning |
|---|---|
| ✅ | Built and working |
| ⚠️ | Built but has a known defect — fix before relying on it |
| 🔨 | Partly built, needs finishing |
| ⬜ | Not built |

### The two people in these stories

- **Customer** — the person whose exchange accounts are being protected
- **Operator** — you and your team at Minas

---

## Epic 1 — Getting connected

*The first ten minutes decide whether someone trusts you with keys to their exchange account.*

### G-1 · Customer signs up ⬜
**As a** prospective customer, **I want** to create an account myself, **so that** I can get protected without waiting for someone at Minas to set me up.

**Acceptance criteria**
- [ ] I can sign up with an email address and password
- [ ] I must confirm my email before I can connect any exchange
- [ ] Passwords below a sensible strength are rejected with a clear reason
- [ ] If the email is already registered, I'm told to log in instead of being told the account exists (no account fishing)
- [ ] I land on a "connect your first exchange" screen straight after confirming

> *Today an operator adds customers by hand. That's fine at pilot scale and a bottleneck after.*

### G-2 · Operator adds a customer manually ✅
**As an** operator, **I want** to create a customer account on their behalf, **so that** I can onboard people personally during early access.

**Acceptance criteria**
- [x] I can create a customer with their email and contact details
- [x] The customer receives login details
- [ ] The action is recorded against my operator identity, not anonymously

### G-3 · Customer connects an exchange account ✅
**As a** customer, **I want** to connect my exchange account using a read-only key, **so that** the Guardian can watch it.

**Acceptance criteria**
- [x] I can connect Bitget, Bybit, OKX or Binance
- [x] I paste in my API key and secret
- [x] The system checks the key works before saving it
- [ ] I see plain-English instructions for creating a read-only key on *my specific exchange*, with screenshots
- [ ] If my key is rejected, I'm told exactly which permission caused it and how to fix it
- [ ] I can name each connection, so "Binance — main" and "Binance — savings" are distinguishable

### G-4 · The read-only guarantee is enforced, not promised ✅
**As a** customer, **I want** the Guardian to refuse any key that can trade or withdraw, **so that** handing over a key can't hurt me.

**Acceptance criteria**
- [x] A key with trade permission is **rejected**, not accepted with a warning
- [x] A key with withdrawal permission is rejected
- [x] If the system can't tell what a key can do, it rejects it (never assumes safe)
- [x] Unrecognised new permissions from an exchange cause rejection
- [ ] After connecting, I can see a plain summary: "This key can read balances and history. It cannot trade or withdraw."

> *This is the whole trust model. It already works — the missing piece is showing the customer that it worked.*

### G-5 · Customer connects several accounts ✅
**As a** customer with funds spread across exchanges, **I want** to protect all of them, **so that** I'm not half-covered.

**Acceptance criteria**
- [x] I can add multiple exchange accounts
- [ ] Each account shows its own status: watching, paused, or having trouble
- [ ] I can remove an account, and the system stops watching it immediately

### G-6 · Customer is warned when a connection breaks ⬜
**As a** customer, **I want** to be told if the Guardian loses access to my account, **so that** I never think I'm protected when I'm not.

**Acceptance criteria**
- [ ] If a key stops working, I'm alerted within 15 minutes
- [ ] The alert says what to do about it
- [ ] The account shows as "not being watched" everywhere it appears
- [ ] The alert repeats daily until fixed — silence must never look like safety

> *Partly covered today: a sudden auth failure is treated as a possible tamper signal. This story is the softer case — an expired or deleted key.*

---

## Epic 2 — Watching the account

*The detection engine. Twelve rules already run; the work here is correctness and visibility.*

### G-7 · Unexplained balance drops are detected ⚠️
**As a** customer, **I want** to be alerted when my balance falls in a way my own activity doesn't explain, **so that** theft is caught in minutes.

**Acceptance criteria**
- [x] Balances are checked every 45 seconds
- [x] Drops explained by my own trades and withdrawals don't alert
- [ ] **Only activity in the same time window counts as an explanation** ← currently broken
- [ ] A test proves a large sale from last week can no longer explain away a drop from the last minute

> **⚠️ This is the most important fix in the entire backlog.** Today, old activity can cancel out a fresh theft, so real drains can pass silently. Ask Claude Code for a *failing test first*, then the fix.

### G-8 · Suspicious trades are detected ✅
**As a** customer, **I want** to know if trades happen at prices far from the market, **so that** I catch someone draining me through bad trades rather than withdrawals.

**Acceptance criteria**
- [x] Trades executed well away from market price raise an alert
- [x] Only activity in the relevant time window is considered
- [x] Unusual fee patterns are also flagged

### G-9 · Permission changes are detected ✅
**As a** customer, **I want** to know if my API key's permissions change, **so that** I catch an attacker upgrading a key before they use it.

**Acceptance criteria**
- [x] Key permissions are re-checked every 10 minutes
- [x] Any change from what was approved at signup raises an alert
- [ ] The alert states exactly what changed, in plain words

### G-10 · Losing sight of an account is itself an alert ✅
**As a** customer, **I want** an alert if the Guardian is suddenly blocked from reading my account, **so that** an attacker can't blind the watchdog before draining me.

**Acceptance criteria**
- [x] Authentication failures are treated as a possible attack, not just a technical error
- [x] I'm alerted rather than the error being logged silently

### G-11 · Every withdrawal is noticed ⚠️
**As a** customer, **I want** to know about every withdrawal from my account, **so that** nothing leaves without me seeing it.

**Acceptance criteria**
- [x] New withdrawals raise an alert
- [ ] **Withdrawals the exchange reports without a reference ID are still alerted** ← currently dropped silently
- [ ] Withdrawals to a destination never used before are marked as higher risk

### G-12 · Detection thresholds can be tuned without a deploy ✅
**As an** operator, **I want** to adjust sensitivity while running, **so that** I can quiet a noisy rule immediately.

**Acceptance criteria**
- [x] Thresholds live in a config file that reloads automatically
- [ ] **A typo in a threshold is rejected loudly instead of silently disabling the rule** ← currently a bad value can switch a detector off without anyone noticing
- [ ] Threshold changes are recorded, showing who changed what and when

### G-13 · Thresholds can differ per customer ⬜
**As an** operator, **I want** different sensitivity for different customers, **so that** an active trader and a long-term holder each get appropriate alerting.

**Acceptance criteria**
- [ ] Any threshold can be overridden for one customer
- [ ] Customers without an override use the defaults
- [ ] The customer's settings screen shows their effective sensitivity in plain terms

---

## Epic 3 — Getting told

*A detection nobody receives is the same as no detection.*

### G-14 · Alerts arrive by Telegram ✅
**As a** customer, **I want** alerts in Telegram, **so that** I get them instantly on my phone.

**Acceptance criteria**
- [x] Alerts are delivered to Telegram
- [ ] I connect Telegram myself from my settings page, guided
- [ ] A test message confirms it works before I rely on it
- [ ] If delivery fails, the system tries another channel

### G-15 · Alerts arrive by SMS ✅
**As a** customer, **I want** critical alerts by text message, **so that** I'm reached even without internet.

**Acceptance criteria**
- [x] SMS alerts are delivered
- [ ] I verify my phone number with a code before SMS is enabled
- [ ] SMS is reserved for critical alerts only — texts cost money and lose meaning if routine
- [ ] Delivery failures are recorded and surfaced to an operator
- [ ] Costs per customer are visible to operators

### G-16 · Alerts arrive by email ✅
**As a** customer, **I want** email alerts, **so that** I have a written record.

**Acceptance criteria**
- [x] Email alerts are delivered
- [ ] Emails are readable on a phone and state the essentials in the first line
- [ ] Every alert email links to the full incident in the portal

### G-17 · A regular digest confirms all is well ✅
**As a** customer, **I want** a periodic summary, **so that** I know the system is alive even when nothing happens.

**Acceptance criteria**
- [x] A digest is sent on a schedule
- [ ] It states plainly: accounts watched, checks performed, issues found
- [ ] I can choose daily, weekly, or off

> *Underrated feature. For a product whose success looks like silence, the digest is the main proof of life.*

### G-18 · Repeat alerts don't become spam ✅
**As a** customer, **I want** one ongoing problem to produce one alert, **so that** I don't mute the channel.

**Acceptance criteria**
- [x] Duplicate alerts are suppressed
- [x] A cooldown prevents rapid repeats
- [ ] **Two simultaneous checks can't both slip an alert through** ← rare duplicates possible today
- [ ] Suppressed alerts still appear in the portal, so nothing is hidden

### G-19 · Push notifications ⬜
**As a** customer, **I want** push notifications, **so that** I get alerts without needing Telegram or paying for SMS.

**Acceptance criteria**
- [ ] Browser push works when I'm signed in to the portal
- [ ] I choose which alert levels use push
- [ ] Push failures fall back to email

### G-20 · I choose what reaches me, and how ⬜
**As a** customer, **I want** control over channels per alert level, **so that** critical things wake me and minor things don't.

**Acceptance criteria**
- [ ] A grid lets me pick channels for each severity
- [ ] **Critical alerts cannot be fully switched off** — at minimum one channel stays on
- [ ] I can set quiet hours for non-critical alerts only
- [ ] Changes take effect immediately and are confirmed on screen

### G-21 · A second contact can be alerted ⚠️
**As a** customer, **I want** a trusted second person to receive alerts, **so that** I'm covered when unreachable.

**Acceptance criteria**
- [x] A secondary alert email can be recorded
- [ ] **The secondary contact can actually sign in to the portal** ← currently they can't
- [ ] The secondary contact can view alerts but not change settings or remove accounts

---

## Epic 4 — Responding to an alert

*Everything after "your phone buzzed" is currently missing, and it's where the product becomes useful rather than merely noisy.*

### G-22 · I can acknowledge an alert ⬜
**As a** customer, **I want** to confirm I've seen an alert, **so that** everyone knows it's being handled.

**Acceptance criteria**
- [ ] One tap from the alert marks it acknowledged
- [ ] The portal shows who acknowledged and when
- [ ] Acknowledging stops escalation
- [ ] Time from alert to acknowledgement is recorded

### G-23 · Unacknowledged critical alerts escalate ⬜
**As a** customer, **I want** an unanswered critical alert to escalate, **so that** a missed notification doesn't mean a missed theft.

**Acceptance criteria**
- [ ] If a critical alert isn't acknowledged within a set time, it re-sends on a louder channel
- [ ] Escalation reaches my secondary contact
- [ ] A Minas operator is paged too
- [ ] Escalation stops the moment anyone acknowledges

### G-24 · I can say "that was me" ⬜
**As a** customer, **I want** to mark an alert as my own activity, **so that** the system stops flagging it and learns.

**Acceptance criteria**
- [ ] I can mark an alert as expected activity, with an optional note
- [ ] Marked alerts are excluded from precision metrics as false positives
- [ ] Operators can see patterns of what customers keep marking

### G-25 · I can see the full story of an incident ⬜
**As a** customer, **I want** a clear timeline, **so that** I understand what happened and can act.

**Acceptance criteria**
- [ ] Each incident has its own page: what was detected, when, on which account, what the balances were
- [ ] The timeline shows detection → notification → acknowledgement
- [ ] It states in plain words what to do next (revoke the key, contact the exchange, move funds)
- [ ] I can export it as a PDF for the exchange or the police

### G-26 · I can snooze a known issue ⬜
**As a** customer, **I want** to silence an alert I'm already dealing with, **so that** I'm not buzzed every few minutes.

**Acceptance criteria**
- [ ] I can snooze a specific alert for a chosen duration
- [ ] Snoozes always expire — there is no permanent mute
- [ ] Snoozed alerts stay visible in the portal
- [ ] A snooze on one alert type never silences a different one

---

## Epic 5 — The customer portal

### G-27 · I can log in securely ✅
**As a** customer, **I want** a secure sign-in, **so that** my monitoring data stays private.

**Acceptance criteria**
- [x] Password sign-in with securely stored passwords
- [x] Sessions are protected against tampering
- [x] Repeated failed attempts are throttled
- [ ] **Someone else guessing my email can't lock me out** ← currently possible
- [ ] Sessions expire after a period of inactivity

### G-28 · Two-factor on my Guardian account ⬜
**As a** customer, **I want** 2FA on the portal itself, **so that** the thing protecting my exchange accounts isn't the weak link.

**Acceptance criteria**
- [ ] I can enable app-based 2FA
- [ ] I'm given recovery codes and told to store them safely
- [ ] Disabling 2FA requires the current code and sends me an email
- [ ] Operator accounts **must** have 2FA — not optional

### G-29 · I can reset a forgotten password ⬜
**As a** customer, **I want** to reset my password, **so that** I'm not locked out permanently.

**Acceptance criteria**
- [ ] Reset link sent by email, single-use, expires within an hour
- [ ] The response is identical whether or not the email exists
- [ ] Resetting signs out all other sessions
- [ ] I'm emailed whenever my password changes

### G-30 · I can see everything at a glance ✅
**As a** customer, **I want** one screen showing my protection status, **so that** I know I'm covered.

**Acceptance criteria**
- [x] A portal shows my accounts and alerts
- [ ] Each account shows: watching / paused / trouble, and when it was last checked
- [ ] Recent alerts appear with severity and acknowledgement state
- [ ] A prominent banner appears if *any* account isn't being watched

### G-31 · I can see my alert history ⬜
**As a** customer, **I want** a searchable history, **so that** I can look back at past incidents.

**Acceptance criteria**
- [ ] All alerts listed, newest first
- [ ] Filter by account, severity, and date
- [ ] Each entry links to its incident page
- [ ] Exportable as a spreadsheet

### G-32 · I can see who has touched my account ⬜
**As a** customer, **I want** a record of changes to my settings, **so that** I'd notice unauthorised changes.

**Acceptance criteria**
- [ ] Every settings change is recorded with who and when
- [ ] Operator actions on my account are visible to me
- [ ] The record cannot be edited or deleted

---

## Epic 6 — Proving it works

*The hardest problem in this product: when it's working correctly, nothing happens.*

### G-33 · Automatic fire drills ⬜ **← highest-value new feature**
**As a** customer, **I want** the system to regularly test itself end to end, **so that** I know it would actually reach me.

**Acceptance criteria**
- [ ] On a schedule, a simulated theft pattern is injected against a test account
- [ ] The drill must travel the entire path — detection, alerting, delivery to my phone
- [ ] The drill alert is clearly labelled as a drill
- [ ] A failed drill immediately pages an operator as an outage
- [ ] The portal shows: last drill, result, and time from detection to delivery
- [ ] I can trigger a drill myself, on demand

> **Why this matters more than it looks.** A silent failure is invisible: an expired Telegram token, a broken detector, a dead SMS account all look exactly like "nothing bad happened." Drills turn that invisible risk into a scheduled, observed event — and give the customer the only concrete evidence they ever get that they're paying for something real.

### G-34 · System status is visible ⬜
**As a** customer, **I want** to see that the Guardian itself is healthy, **so that** I can trust the silence.

**Acceptance criteria**
- [ ] A status page shows whether monitoring is running normally
- [ ] It shows when each of my accounts was last successfully checked
- [ ] Ongoing problems are stated plainly, not hidden

---

## Epic 7 — Operator tools

### G-35 · Operator dashboard ✅
**As an** operator, **I want** to see all customers and accounts, **so that** I can run the service.

**Acceptance criteria**
- [x] All customers, accounts and recent alerts in one view
- [x] Add and manage customers
- [ ] Sort and filter by problem state, so trouble surfaces first

### G-36 · The operator dashboard is protected ⚠️
**As the** business owner, **I want** the ops dashboard locked down, **so that** customer data isn't exposed.

**Acceptance criteria**
- [x] Restricted to the private network in production
- [ ] **It doesn't expose itself on all network interfaces by default** ← currently the documented quickstart does exactly this
- [ ] Operator sign-in with 2FA, even on the private network
- [ ] Every operator action is recorded against a named person

> *One-line fix, and worth doing today. Right now following your own quickstart guide puts an unauthenticated "add customer" form on every network interface.*

### G-37 · Operator alert triage ⬜
**As an** operator, **I want** to work through alerts across all customers, **so that** nothing is missed.

**Acceptance criteria**
- [ ] A queue of unacknowledged alerts across all customers
- [ ] I can add a note and mark an alert as handled
- [ ] Alerts older than a threshold are highlighted
- [ ] I can see which customers are muting channels — the early warning for noise problems

### G-38 · Technical health monitoring ✅
**As an** operator, **I want** system metrics, **so that** I can spot trouble before customers do.

**Acceptance criteria**
- [x] Metrics are published for monitoring tools
- [ ] Alarms fire when checks fall behind schedule
- [ ] Alarms fire when an account has been unwatchable for too long

---

## Epic 8 — Money

### G-39 · Customer subscribes and pays ⬜
**As a** customer, **I want** to subscribe, **so that** I can use the service.

**Acceptance criteria**
- [ ] Clear plans with what's included
- [ ] Card payment with recurring billing
- [ ] Invoices available to download
- [ ] Cancel any time from the portal, no email required

### G-40 · Lapsed payment is handled honestly ⬜
**As a** customer, **I want** clear warning before protection stops, **so that** I'm never unknowingly unprotected.

**Acceptance criteria**
- [ ] Failed payment triggers an email and a portal banner
- [ ] A grace period where monitoring continues
- [ ] **If monitoring is going to stop, I'm told explicitly and unmistakably**
- [ ] Reconnecting after payment doesn't require re-adding keys

> *This one is an ethics issue, not just a billing flow. Silently ceasing to watch someone's account while they believe they're protected is the worst thing this product could do.*

---

## Epic 9 — Trust, privacy and the law

### G-41 · My credentials are stored safely ✅
**As a** customer, **I want** my API keys protected, **so that** trusting Minas isn't a new risk.

**Acceptance criteria**
- [x] Keys are stored encrypted
- [x] Keys never appear in error messages or logs
- [ ] I can see when each key was last used, and revoke it from the portal

### G-42 · I can delete my data ⬜
**As a** customer, **I want** to remove my data, **so that** I control my own information.

**Acceptance criteria**
- [ ] I can delete my account from the portal
- [ ] Deletion removes credentials immediately and everything else within a stated period
- [ ] I can download my data first
- [ ] I receive written confirmation

### G-43 · Data retention is defined and lawful 🔨
**As the** business owner, **I want** a reviewed retention policy, **so that** we're compliant.

**Acceptance criteria**
- [x] A written retention policy exists
- [ ] **It has actually been reviewed by a lawyer** ← the document is currently self-flagged as unreviewed
- [ ] Old data is automatically deleted per the policy
- [ ] The policy is published where customers can read it

### G-44 · Customers understand the limits ⬜
**As a** customer, **I want** to know exactly what the Guardian can and cannot do, **so that** my expectations are correct.

**Acceptance criteria**
- [ ] Signup states plainly: it detects and alerts; it **cannot** stop an attack
- [ ] The read-only limitation is presented as a safety feature, with the reason
- [ ] The alerting time target is stated honestly
- [ ] No claim of prevention appears anywhere in the product or marketing

---

## Epic 10 — Reliability

*Invisible to customers, but they're what make the promise keepable.*

### G-45 · The system handles many customers ⚠️
**As the** business owner, **I want** the system to scale, **so that** growth doesn't degrade protection.

**Acceptance criteria**
- [x] Exchange rate limits are respected per account and across the whole system
- [x] One broken account can't take down others
- [ ] **The database handles many simultaneous checks without contention** ← known bottleneck, has already caused one incident
- [ ] **The same account is never checked twice at once** ← currently possible

### G-46 · Code quality is enforced automatically 🔨
**As the** business owner, **I want** automated checks on every change, **so that** quality doesn't depend on memory.

**Acceptance criteria**
- [x] Tests run automatically on every change
- [ ] Code style is checked automatically
- [ ] Test coverage doesn't silently decline
- [ ] **Dependencies are scanned for known vulnerabilities** ← notable gap for a service holding customer credentials
- [ ] Version numbers agree across the project

---

## Suggested order

**Right now — fix what's silently broken.** G-7 (windowed drop detection), G-36 (network binding), G-11 (dropped withdrawals), G-12 (bad threshold values). All four are silent failures: things that look fine while not working.

**Then — make it a real service.** G-33 fire drills, G-22 and G-23 acknowledge and escalate, G-25 incident pages, G-6 connection-loss warnings, G-45 database concurrency.

**Then — let it run without you.** G-1 self-signup, G-28 and G-29 2FA and password reset, G-20 channel preferences, G-39 and G-40 billing, G-3 exchange setup guides.

**Then — polish and prove.** G-13 per-customer tuning, G-31 history, G-34 status page, G-37 triage queue, G-42 through G-44 privacy and honest limits.

---

## Two things to hold onto

**Silent failure is the enemy.** Nearly every ⚠️ in this backlog is the same shape: something that appears to work while quietly not working. A crashed service gets noticed in minutes. A detector that stopped detecting gets noticed never. When you're deciding what to build next, weight anything that converts a silent failure into a loud one.

**Silence is the product.** When the Guardian works, nothing happens — which means customers have no evidence they're getting value, and will eventually cancel. The digest (G-17), fire drills (G-33), and status page (G-34) exist to make working-correctly *visible*. They're retention features, not engineering nice-to-haves.
