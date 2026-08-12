# Autonomous work log

Running log of unattended work on the backlog. Newest session at the bottom
until the run finishes, at which point a summary goes at the top.

Written for someone who wasn't here: what was done, every judgment call and why,
what was skipped and why, and anything about the codebase that was surprising.

Related: `docs/DECISIONS-NEEDED.md` holds the questions that need a product
decision before anything can be built.

---

# Summary — session of 2026-08-12

**Thirteen issues closed, one partially, across three sessions. `main` is green
at 365 tests, lint clean, coverage 82.6%. Nothing is half-finished.**

> **Follow-up sessions (same day):** added `sentinel validate-config`;
> **reversed the refuse-to-start-on-a-bad-config decision** I made in G-12;
> then four audits that found substantially more than expected — see
> "What the audits found" below — plus **#57** (a p1 silent failure) and
> **#54** (operator identity). Full write-ups at the bottom of this file.

## Read these first

1. **`docs/DECISIONS-NEEDED.md`** — six product decisions, each with options and
   a recommendation. Decisions 1 and 2 unblock the most and are worth answering
   together. **Decision 6 (retention) needs a lawyer, not you** — it's the
   longest lead time on the backlog, worth starting even if the rest waits.
2. **Two judgment calls where I went against what an issue said**, both flagged
   on the issues themselves:
   - **#52** recommended Prometheus + Alertmanager; I built the alarm
     in-process. Reasoning on the issue, follow-up filed as **#56** so it isn't
     buried.
   - **#50** — `pyproject.toml` said `0.1.0` while the CHANGELOG's newest
     release heading was `1.0.0`. I set pyproject to **1.0.0**, choosing the
     CHANGELOG as the record of what actually shipped. Reverse it if 0.1.0 was
     the truth.
3. **I pushed a red `main` once** (`609fe1d`), for about two minutes. Own
     section below. It surfaced a real one-millisecond defect, which is fixed.
4. **A rejected rules config no longer stops the service starting.** I had it
   refuse; that was wrong for the upgrade case, which is the one that actually
   happens. It now runs on the settings that validate, defaults for the rest,
   and raises a CRITICAL operator page. Fail-fast moved to
   **`sentinel validate-config <path>`** — run it on the server before you
   deploy. Writing the test for it also found a trap: routing was falling back
   to *no channels*, which would have detected perfectly and told nobody.

## What the audits found

Four sweeps, and three of them found more than I expected:

| Audit | Finding |
| --- | --- |
| Did the rewritten tests soften? | All 11 survive a full revert — but only **2 of 11** pinned the "value is not in force" property I claimed of all of them. Strengthened; now 10. |
| Defaults that silently disable | **4 divergences** beyond the known routing one. `alerting.cooldown_minutes` fell back to a flat 60 against a shipped CRITICAL of 15 — quieter, on the severity where quieter is worst. Fixed with a registry so the class can't recur. |
| Tested but never wired | **8 of 12 detectors** were unpinned, including `detect_tamper`. All logic unit-tested; nothing failed when the poller stopped calling them. Plus 2 calls that could kill a thread. |
| Constraint-lifted sweep | 19 sites filed as **#58**, and one genuine defect — **#57**, where a single failing channel lost the alert on *every* channel and truncated the poll. |

**The recurring fault, four times now:** testing what a component decides and
never testing that anything asks it. Coverage doesn't show it — every one of
those eight detectors was "covered", because coverage counts lines executed and
a unit test executes the detector's lines without the caller's. The cheap check
is to delete the call site and see whether the suite notices.

## What was closed

| Issue | What it was |
| --- | --- |
| #51 (G-47) | Undated fills never price-checked or fee-checked — silent |
| #15 (G-11) | Withdrawals with no reference ID dropped — **two** separate gates |
| #16 (G-12) | A mistyped threshold silently disabled **all** detection — partial, see below |
| #25 (G-21) | Second contact couldn't use the portal — **two** separate gates |
| #55 | Undated-record metric labelled by account instead of exchange (found by me) |
| #53 (G-49) | Operator paging channel — foundational, unblocked five issues |
| #52 (G-48) | Paging when an exchange stops stamping times |
| #50 (G-46) | CI: style, coverage floor, dependency scanning, version agreement |
| #49 (G-45) | DB contention + the same account checked twice at once |
| #57 | One failing channel lost the alert on every channel (found by me, p1) |
| #54 (G-50) | Operator identity — accounts, dashboard sign-in, actor attribution |
| #56, #58 | *(filed, not built)* Prometheus alarms; the logged-and-swallowed sweep |

Queue items 1 and 2 needed no work — the metrics loopback bind was already done
in `aa6cfa6`, and #54 already existed with #40 already retitled and rescoped.

## Still open, and why

- **#16 (G-12)** — validation is done; "record **who** changed a threshold" is
  blocked on #54 (no operator accounts exist, and `rules.yaml` is edited on
  disk, so there is nobody to attribute a change to). Should be finished as
  part of #54.
- **#40 (G-36)** — attribution and sign-in are now done via #54. **Only 2FA
  remains**, and that is #32's work, which is on your do-not-build list.
- **#41, #16** — both unblocked by #54 now. #16's last criterion (record *who*
  changed a threshold) is straightforwardly buildable.
- **#37, #29, #27, #24, #47** — parked on decisions 1–6 above. #29 and #47 are
  *partial* blocks: most of #29 (timeline, balances, the
  detection→notification→acknowledgement chain) and #47's pruning mechanism are
  buildable now.
- **#43, #44, #32, #33** (billing, payments, 2FA, password reset) — untouched
  by instruction. No code written.

## A pattern worth knowing about this codebase

Three of the four Phase 1 defects had **the same shape, and each came in a
pair**: a record has to survive both a *"have I seen this?"* gate and a
*"should this alert fire?"* gate, and those gates live in different files and
consult different fields. Fixing only the one the issue names leaves the
customer just as uninformed.

- **G-11**: `record_seen_withdrawal` dropped id-less records, *and*
  `dispatch._dedup_key` collapsed two of them into one alert.
- **G-21**: `member_accounts` didn't match the secondary contact, *and* the ops
  dashboard couldn't issue them a password.
- **G-48**: found a live instance of it in my own day-old #53 code — two
  exchanges sharing one dedup key.

If you take one thing from this session into the next: when an issue describes
a record being dropped, look for the second place it gets dropped.

## What surprised me

- `poll_state.last_success_at` is written near the **top** of the fast loop,
  right after the exchange fetches, so "last success" means *the exchange
  answered*, not *the account was checked*. Anything raising in between leaves
  an account looking freshly and successfully polled while nothing was
  examined. The dashboard's healthiest-looking column is its weakest claim.
- `prune_balance_snapshots(keep_days=7)` is the **only** retention anywhere.
  `events`, `alerts`, `seen_withdrawals`, `seen_transfers`, `sms_deliveries`
  and now `seen_fills` all grow forever, at fast-loop cadence.
- `fills` in the fast loop are CCXT **closed orders**, not trades, despite the
  name used everywhere.
- A generated portal password is scoped to an **email**, not an account — so
  "Generate portal password" on account A also grants account B if that person
  is a contact there. Coherent, but not what the button's placement suggests.
- The G-45 concurrency fix cut the test suite from ~70s to ~38s. That lock was
  costing roughly 40% of wall-clock, which is a fair proxy for what it cost the
  poller.

## Why I stopped (first session)

Not because the buildable work ran out — **#54 is next and is ready to start**.
I stopped because starting an issue that size risked leaving it half-finished,
which is worse than a clean handover. Everything committed is complete,
tested and revertable as a single unit per issue.

*(The follow-up session after this one added `validate-config` and reversed the
fail-closed startup decision — see the last section of this file.)*

---

## Session — 2026-08-12

### Starting state

Working tree clean, `main` at `aa6cfa6`, full suite green at 194 passed.

Two queue items were already done by earlier work and needed no code:

1. **metrics.py loopback bind** — done in `aa6cfa6` ("G-36: bind the metrics
   exporter to loopback too"). Verified: no `ThreadingHTTPServer(("0.0.0.0"…))`
   remains anywhere in `sentinel/`; the only `0.0.0.0` literals left are in
   `docker-compose.yml` (deliberate — container networking, with the
   `127.0.0.1:` publish as the actual gate), in docstrings explaining that, and
   in `tests/test_bind_address.py` where the override is asserted.
2. **Operator identity issue + #40's remainder** — #54 (G-50 · Operator
   identity) already exists, correctly filed under epic-07-operator-tools /
   p2 / Phase 2, and #40 has already been retitled to "The operator dashboard
   is protected — sign-in and attribution", moved to Phase 2, with its two
   network-binding criteria ticked and a note pointing the remainder at #54.

Nothing to redo, so the session starts at queue item 3.

---

### G-47 (#51) · Undated fills excluded from off-market and fee checking

**Root cause.** `poller.py` filtered fills to those newer than the previous
fast loop with `(f.get("timestamp") or 0) >= last_run_ms`. A fill the exchange
didn't stamp became `0`, which sorts before every window, so it was dropped
from `recent_fills` — and `recent_fills` was the sole input to
`detect_off_market_fills` and `sum_fees_by_coin`. The fill was never
price-checked on that cycle or any later one, because the window only ever
moves forward. The same fill still reached `detect_new_pair_activity` and
`record_traded_pair`, which read `fills` directly, so its symbol was written
into the baseline as ordinary account activity. Nothing logged, nothing
alerted, dashboard healthy.

**The fix.** Two questions were being answered by one timestamp comparison, and
they are not the same question:

- *Have I already judged this fill?* Now answered by identity — a new
  `seen_fills` table and `record_seen_fill`, following the
  `record_seen_withdrawal` / `record_seen_transfer` pattern the issue pointed
  at. This is what makes an undated fill checkable at all, and checkable
  exactly once.
- *Is the current ticker valid evidence about this fill?* Still answered by
  time. `detect_off_market_fills` compares a fill against the ticker as it is
  *right now*, so a fill known to predate the last poll must stay excluded —
  measured against a price that has since moved, its "deviation" would be the
  market's and not an attacker's.

An undated fill can only be answered by the first question, so it is judged on
first sighting. That presumes it is recent, which is exactly what its
appearance in a fresh exchange response is evidence for — and it is the same
presumption `record_seen_withdrawal` already makes about an undated withdrawal.

#### Judgment calls

**Identity for a fill with no order id — content digest, not "skip".**
`record_seen_withdrawal` returns `False` for a record with no id, which reads
as "not new" and drops it from examination entirely. That is precisely the
G-11 (#16) defect, next in the queue. Repeating that pattern here would have
reintroduced the silence this issue exists to remove, so `fill_identity` falls
back to a SHA-256 of the whole record. Two distinct fills collide only if every
field matches including fee and the raw `info` block — and if they do, they
produce identical evidence, so the alert the second would raise is the one the
first already raised. The collision costs nothing an operator would see; the
alternative cost was another silent miss.

**No second metric increment, despite the acceptance criterion.** AC 3 asks
that undated fills be "counted and surfaced". They already are: the
quantity-drop path walks the same `fills` list on the same pass and emits
`sentinel_untimestamped_records_total{source="fill"}` for each one. Adding an
increment in the off-market path would have counted the same record twice per
pass on the same series — the exact "phantom trend under the alarm" that
`163021e` avoided when it kept the near-miss probe silent. So the criterion is
met by existing coverage plus a new operator-facing log line, and the test pins
the count at **exactly one per undated fill per cycle** — it now fails both if
the record stops being counted and if someone later adds the double count.
This one is worth a second opinion; it is the only AC I met by arguing it was
already met.

**Scope held.** No detector was modified. The change is confined to which
fills the poller hands to the detectors, plus a new table and helper. An
undated fill that is off-market raises the same rule at the same severity and
confidence as a dated one — I considered lowering confidence for undated fills
on the grounds that their recency is presumed rather than known, and decided
against it: that is a detector semantics change, outside this issue.

**Schema.** `seen_fills` is a new `CREATE TABLE IF NOT EXISTS` in `schema.sql`,
which `Database._init_schema` applies on every open. Additive and reversible
(`DROP TABLE`); no existing table or column is altered and no data is
backfilled.

#### Rollout note for the deployed Bitget account

On the first poll after this ships, `seen_fills` is empty for existing
accounts, but `last_fast_loop_at` is set — so dated backlog fills are still
excluded by the time rule and only genuinely undated fills get judged. No alert
storm, and no re-judging of history against today's prices. Worth watching the
first cycle anyway.

#### Verification

- 7 new tests in `tests/test_undated_fills.py`, written first. Confirmed
  failing for the right reason (fill never examined: no alert, and no fee
  baseline row at all).
- Mutation check: reverted `poller.py`, `baseline.py`, `schema.sql` via stash —
  the 4 undated-fill tests went red, the 3 regression guards stayed green
  (correct: they describe behaviour that already worked). Restored, re-ran.
- Full suite green: 201 passed.

#### Surprising

`fills` in the fast loop are CCXT **closed orders** (`fetch_closed_orders`),
not `fetch_my_trades` results, despite the name used throughout the poller and
the detectors. The unified order shape carries `average`/`price`/`filled`,
which is why `detect_off_market_fills` reads `average or price`. Worth knowing
before anyone assumes trade-level granularity is available here.

#### Filed while here

**#55** — `exchange=label`: every call site passing the metric's `exchange`
label actually passes the *account label*, so the series is keyed by customer
account rather than by exchange, and its cardinality grows with the customer
count. I kept my new code consistent with the existing call rather than fixing
it inline, because one metric emitting two different label vocabularies is
worse than one consistently wrong. Wants fixing before #52 binds a paging alarm
to that exact metric.

---

### A note on the queue's issue numbers

Two entries in the queue point at the wrong issue number. The G-numbers and the
descriptions are right, so I went by those:

| Queue said | Actually |
| --- | --- |
| G-11 (#16) — withdrawals with no reference ID | **#15** (#16 is G-12) |
| G-12 (#18) — bad threshold values | **#16** (#18 is G-14, Telegram alerts) |

G-21 (#25) and G-47 (#51) were correct. Flagging in case the same off-by-one is
in another list somewhere.

---

### G-11 (#15) · Withdrawals dropped when they have no reference ID

**Root cause — and there were two, in series.** Fixing either alone still
leaves the customer with a withdrawal nobody told them about, which is why the
issue's own note ("currently dropped silently") only describes the first:

1. `record_seen_withdrawal` returned `False` for a record with no `id` or
   `txid`. The poller treats that return as *already seen*, so the record never
   reached `detect_new_withdrawals` or `detect_unseen_address` at all. This is
   the one the issue names.
2. `dispatch._dedup_key` uses `evidence["order_id"] or evidence["coin"] or ""`.
   An unreferenced withdrawal has no `order_id` in its evidence, so the key
   falls back to the coin — identical for every BTC withdrawal on the account.
   Two distinct unreferenced withdrawals therefore collapse into one alert, and
   the second is suppressed for the whole 15-minute CRITICAL cooldown.

I found the second only because I went looking for where the derived identity
would need to travel. It is worth knowing that this class of bug comes in
pairs: a record has to survive both the "have I seen it" gate and the "is this
a repeat alert" gate, and they consult different fields.

**The fix.** A new `sentinel/identity.py` with pure functions — no database, no
exchange client — so the baseline writer and the alert-evidence builders draw
on the same answer and can't drift apart.

#### Judgment calls

**Withdrawal identity digests only settling-invariant fields.** A fill (G-47)
can be digested whole because a closed order is terminal. A withdrawal is not:
`status` walks Bitget's pending → pending_review → success machine, `txid` and
`updated` appear partway through, and the raw `info` block carries all of it.
Digesting the whole record would make every state transition read as a brand
new withdrawal and page the customer three times for one movement. The key uses
coin, amount, address, tag, network, type and timestamp. There is a test for
exactly this (`test_status_change_on_an_id_less_withdrawal_does_not_refire`),
because it is the obvious wrong fix and it would look like it worked.

**Residual collision, stated rather than hidden.** Two withdrawals identical in
coin, amount, destination, network *and* millisecond timestamp collapse into
one. With a timestamp present that is effectively impossible. With the
timestamp *also* missing it stops being impossible — but that case means the
exchange is sending records with neither a reference nor a time, which is a
format breakage an operator should be looking at directly rather than something
to paper over. Documented in the function's docstring.

I considered and rejected a multiset scheme (keying by content plus an
occurrence index, so N identical unreferenced withdrawals produce N alerts).
It handles the drain-by-repeated-identical-withdrawals case, but it breaks
under a sliding response window — once the oldest record ages out of the
exchange's response the indices shift and a genuinely new withdrawal reuses a
key that's already been seen, which is a *new* silent miss. Trading a
near-impossible miss for a plausible one is a bad trade.

**Touching two detectors, deliberately.** The working rules say to stop if a
change touches a detector outside the issue's scope. I judged this inside it:
the criterion is "withdrawals reported without a reference ID are still
alerted", and an alert that is created and then deduped away has not alerted
anyone. The change is confined to the `order_id` evidence field in
`detect_new_withdrawals` and `detect_unseen_address`, and
`evidence_reference()` returns the exchange's own reference whenever there is
one — so for every withdrawal that already worked, the evidence is
byte-identical and no dedup key moves. Flagging it because it is a judgment
call against a stop rule, not because I'm uncertain about it.

**Third criterion ticked without new code.** "Withdrawals to a destination
never used before are marked as higher risk" was already implemented in
`detect_unseen_address` and wired in the poller — it was unchecked because it
inherited the same drop: an unreferenced withdrawal never reached it.
`test_id_less_withdrawal_to_an_unseen_address_is_still_flagged` now covers the
case that was missing, asserting CRITICAL.

#### Verification

- 6 tests in `tests/test_withdrawal_no_reference_id.py`, written first, 5
  failing for the right reason (zero alerts).
- Mutation check run **per layer**, since a single revert would have hidden
  which test binds to which fix:
  - Reverting the `record_seen_withdrawal` identity fallback → 5 red.
  - Reverting only the evidence/dedup change → exactly 1 red
    (`test_two_distinct_id_less_withdrawals_both_alert`, 1 of 2 alerts
    delivered), everything else green.
- Full suite green: 207 passed.

#### Surprising

`git checkout <file>` to undo a mutation also discards the real fix when both
live in the same file. Cost me a re-apply of `baseline.py`. For the second
mutation I copied the files aside first — worth doing by default.

---

### G-12 (#16) · Bad threshold values silently disabling detectors

The issue says a bad value "can switch a detector off without anyone noticing".
I probed it against the real poller before designing anything, and the actual
behaviour is considerably worse than that.

**What really happened.** An operator writes `tolerance_pct: "0,5"` — a decimal
comma, which is what half the world's keyboards produce. `_to_decimal_leaves`
can't parse it, so it stays a string and nothing complains at load. The next
poll calls `get_decimal`, which raises `InvalidOperation` from inside the
detector section of `run_fast_loop` — *after* the exchange fetches have already
written `last_success_at = now` into `poll_state`. The exception escapes
`run_fast_loop` (its try/except only wraps the client calls) and is swallowed
by the worker's blanket `except Exception` in `engine.py:143`, which logs one
line and moves on to the next task.

Net effect, confirmed by probe:

- no detector runs — not the edited one, *all* of them
- for every account, not just one — `RulesConfig` is shared
- `consecutive_failures` stays 0, so `detect_unreachable` never fires
- `last_success_at` is freshly stamped, so the dashboard shows every account
  polled successfully seconds ago
- `poll_ok()` is never reached, so `sentinel_polls_ok_total` flatlines — but
  nothing alarms on it today

One mistyped character stops all monitoring and leaves it looking healthy.

**The fix.** Validate at load against a declared schema; never let an unparsed
value reach a detector. At startup an invalid file raises and the process
refuses to come up — coming up with unchecked thresholds looks exactly like
monitoring, which is the failure mode. While running, an invalid edit is
rejected and the last valid configuration stays in force, so detection
continues.

#### Judgment calls

**Keeping last-known-good is louder, not quieter.** This is the one that
touches the "don't make a failure quieter" stop rule, so it's worth being
explicit. The old behaviour was not loud — it was a swallowed exception and
silently dead detection. The new behaviour keeps detection alive *and* reports
the rejection three ways. The risk it introduces is different: an operator
believes their new threshold applied when it didn't. That risk is exactly what
the three surfaces are for, and it is strictly better than the old risk, which
was that nothing was being detected at all.

**Three surfaces, because a log line is not a signal.** ERROR log naming the
setting and the value; a red banner on the dashboard; and
`sentinel_rules_config_valid` (resting value 1, drops to 0). The metric follows
the precedent `163021e` set for undated records, and for the same stated reason
— nobody reads logs on a quiet day. The dashboard banner matters because every
other indicator on that page stays green during exactly this failure.

**Constraints derived from usage, never invented sensitivity.** This is the
design rule of `rules_schema.py` and it's written at the top of the file. The
user story is "quiet a noisy rule immediately", so validation that
second-guesses tuning would break the feature it protects.
`max_deviation_pct: "75"` is accepted — wide, but meaningful. `"-1"` is
rejected because `deviation_pct > -1` is true of every fill ever executed and
there is no tuning intent that expression could express. Every constraint in
the spec carries the expression it comes from. This also keeps me on the right
side of the "don't guess at a security-relevant number" rule: I am rejecting
values at which the formula has no meaning, not choosing how sensitive a
detector should be.

**Unknown keys are rejected, not warned about.** A misspelled key is the
quietest failure in the set: `tolerance_pc` is valid YAML, the reader falls
through to its default, and the operator leaves believing the change took
effect. The message names the near-match (`difflib`), so the rejection carries
its own fix.

**`is_valid` reloads before answering.** Caught by a test I expected to pass
trivially. The properties were plain attribute reads, so the dashboard could
have reported "config healthy" from a file state two edits old — reporting
stale health is the same failure mode the validation exists to close.

**`_reject` advances `_mtime`.** Every `get()` calls `reload()`, and one poll
cycle reads a dozen thresholds per account. Without advancing the mtime, one
bad edit would re-parse and re-log on every read and bury its own message.
There's a test pinning one ERROR per bad edit. The operator's next edit changes
the mtime again and is re-validated normally, so a rejected file never wedges
until restart — also tested, because hot reload is the thing this issue exists
to protect.

#### What I did not build, and why

**Criterion 3 — "Threshold changes are recorded, showing who changed what and
when" — is not done, and I left it unticked.**

The "who" half cannot be built yet: there are no operator accounts (#54,
G-50 · Operator identity), and `config/rules.yaml` is edited on disk, so from
inside the process there is nobody to attribute a change *to*. The available
proxies are guesses dressed as attribution — the file's owning uid is not who
edited it — and #54's own philosophy is explicit that rows without an actor
should not be backfilled with a guess.

I could have built the "what and when" half now (a `config_changes` table, diff
on successful reload) and left "who" for later. I decided against it: it is
roughly as much work again — new table, callback plumbing out of the config
lock to avoid a lock-ordering hazard against the DB write lock, CLI wiring,
dashboard display — for half a criterion that still could not be ticked, and it
would be reworked when actors land. Half an audit trail is the kind of thing
that looks like progress and isn't.

So **#16 stays open** with criterion 2 ticked and criterion 3 outstanding,
noted on the issue as blocked on #54. It should be finished as part of #54,
where the actor exists and the whole criterion can be met at once.

#### Verification

- 20 tests in `tests/test_rules_validation.py`, written first.
- One is the behavioural proof at the level the defect actually bit
  (`test_detection_keeps_running_after_a_bad_edit`): break the config, then
  steal from the account, and require the withdrawal alert.
- One asserts the shipped `config/rules.yaml` validates — a spec that rejects
  the product's own config would stop the process from starting.
- Mutation check: replacing `problems = validate(converted)` with
  `problems = []` turns 17 of the 20 red, including the behavioural one. The 3
  that stay green are the ones that describe behaviour holding either way
  (legitimate retune accepted, shipped config valid, plain reads).
- Full suite green: 227 passed.

#### Surprising

`poll_state.last_success_at` is written near the *top* of the fast loop, right
after the exchange fetches, and the balance snapshot is written at the bottom.
So "last success" means "the exchange answered", not "the account was
checked" — anything that throws in between leaves an account looking freshly
and successfully polled while nothing was examined. `latest_balance_snapshot_at`
already documents a version of this trap for its own purposes. It is worth
knowing that the dashboard's healthiest-looking column is the weakest claim on
the page.

---

### G-21 (#25) · The second contact couldn't sign in to the portal

**Root cause — primary-only in both directions.** A secondary alert email could
be recorded and was already being alerted (`_account_contact` reads it, and
criterion 1 was correctly ticked). But:

1. `member_accounts` filtered on `lower(a.alert_email) = ?` alone. That
   function's own docstring calls it "THE isolation boundary: all portal
   queries go through the ids returned here" — so a secondary contact who had a
   password would sign in successfully to a portal showing zero accounts.
2. `_portal_access_card` read only `alert_email`, so the operator had no way to
   *issue* the secondary contact a password in the first place.

Either one alone is enough to make the criterion false, which is why both are
in the fix. The second is the one the issue doesn't mention and the one I'd
have missed by reading only `portal.py`.

The shape is the same as G-11's: the record survives one gate and dies at
another, and the two gates live in different files.

#### Judgment calls

**Widening the isolation boundary, carefully.** This is the most
security-sensitive change of the session — `member_accounts` is what stops one
customer seeing another's account. The match is per row (`alert_email` OR
`alert_email_secondary` on *that account*), so being someone's secondary
contact grants exactly that account and nothing else they own. There is a test
for precisely that (`test_a_secondary_only_contact_is_not_given_the_primarys_
other_accounts`) alongside one asserting an unrelated email still sees nothing.

**The empty-email guard is a real hole, not defensiveness.** Accounts saved
through a form with the second contact left blank store `''` rather than NULL.
Under the new OR clause, `lower('') = ''` is *true* — so a lookup with an empty
email would have returned every account in the fleet with a blank secondary.
`member_accounts` now returns `[]` before reaching the query. Found by writing
`test_an_empty_email_matches_nothing` before the fix, which is the argument for
writing the boundary tests first.

**The contact selector names a column, so it is never interpolated from input.**
It resolves through a fixed `PORTAL_CONTACTS` map, and an unrecognised value is
a 400 rather than a fall-back to the primary — quietly defaulting would hand
one person a credential meant for another. Tested with `nonsense`,
`alert_email` (a real column name) and `../primary`. An *absent* selector still
means the primary, so the form as it posted before this change keeps working.

**Criterion 3 was already true, and I asserted it rather than assuming it.**
"Can view alerts but not change settings or remove accounts" holds because the
member portal has no mutating routes at all — `/login` and `/logout` are the
only POSTs and neither touches an account. That is a property satisfied by an
*absence*, which is exactly the kind that stops holding without anyone
noticing. So the test signs in as the secondary contact and POSTs to seven
plausible mutating paths (`/settings`, `/accounts/1/delete`, `/contacts`,
`/thresholds`, …), requiring 404/405/403 on each, then asserts the accounts
table is untouched. It is written to fail the day someone adds a member-facing
settings form — which is the point, per the standing instruction to write tests
that catch a thing silently breaking rather than tests that it works today.

#### Verification

- 14 tests in `tests/test_secondary_contact_portal.py`, written first, 9
  failing for the right reason.
- Mutation check per layer again:
  - reverting the `member_accounts` OR clause → 4 red (all the "can see it"
    tests)
  - reverting the provisioning half → 5 red (all the "can be given a password"
    tests)
  - the isolation tests stay green under both, which is what you want: they
    describe a property neither change was supposed to alter.
- Full suite green: 241 passed.

#### Surprising

The generated portal password is scoped to an *email*, not to an account —
`set_member_password(db, email, …)` writes one `portal_members` row, and
`member_accounts` then returns every account that email is a contact on. So
"Generate portal password" on account A also grants that person account B if
they are a contact there too. That is coherent and is what the card's "covers
every account under this email" copy said, but it means the button is not
scoped the way its placement on a single account's page suggests. Nothing to
fix for this issue; worth knowing before anyone builds per-account sharing.

---

### #55 · Undated-record metric labelled with the account, not the exchange

My own finding from G-47, fixed before it could set. The counter exists so a
response-format change reaches an operator before a customer finds it as a
missed alert — and the one field that would say *which integration to go and
look at* said which customer noticed. Cardinality grew with the customer count
instead of staying at the four values the dimension actually has.

**Root cause:** `exchange` and the account label were one argument.
`detect_quantity_drop(..., exchange=label)` in the poller, and inside
`_in_window` that single value fed both `logger.warning` and
`metrics.untimestamped_record`. Two different questions sharing one parameter,
which is how they drifted — the same pattern as G-47's timestamp doing double
duty and G-11's identity being needed at two gates. Three for three today.

**Judgment call:** I split the argument rather than changing what the log line
says. An operator reading a warning wants the account; an alarm binding to the
metric wants the exchange. Both are right, and the fix is that they stop
sharing. `_account_exchange` keeps `_load_client`'s existing default-to-bitget
guard for pre-migration rows — a label of `"unknown"` there would be worse than
a slightly optimistic one, because it would split the series an alarm binds to.

Checked the issue's fourth criterion directly: `sentinel_untimestamped_records_
total` is the only metric with an `exchange` label, and the remaining
`exchange=` call sites in the tree are onboarding paths passing the real thing.

**Verification:** 3 tests written first, all red. Mutation (restoring
`exchange=label`) turns the poller-level one red while the two unit-level ones
stay green — correct, since they call the detector directly and the conflation
lived in the caller. Full suite green at 244.

---

### Log-and-skip pass · five product decisions written up

Wrote `docs/DECISIONS-NEEDED.md` covering the five items flagged as needing a
product call — G-33 (#37), G-23 (#27), G-25 (#29), G-20 (#24), G-43 (#47) —
plus a sixth entry (incident PDF export) that I judged to be in the same class
once I read G-25's criteria properly. Each has the options, a recommendation
and what it blocks, and each issue has a comment pointing at it so nobody has
to find the doc to know why the issue is untouched.

Did this before starting the large Phase 2 builds rather than after, so the
decisions are waiting whenever they're read rather than sitting behind however
far I get.

#### What unblocks what

Two things worth pulling out, because they're the reason to read decisions 1
and 2 together:

- **#53 (G-49, operator alerting) gates the operator-paging rung of both G-23
  and G-33.** There is no operator paging route in the repo at all — every
  channel today is customer-scoped. Both issues have a criterion that says "a
  Minas operator is paged", and neither can be finished without it. #53 is on
  the build list, so it shouldn't end up the binding constraint, but the
  decisions should be made knowing it's a prerequisite.
- **Decision 4 (quiet hours) and decision 1 (escalation) interact.** Whether
  CRITICAL escalation respects quiet hours has to be answered once, not twice,
  or the two features will disagree.

#### Judgment calls

**Recommended in every case rather than only listing options.** The instruction
was options plus a recommendation, and a list of options with no opinion is
just the same decision handed back. Where I recommended something that does
*not* satisfy a criterion as literally written I said so explicitly — decision
2's fire drills are the clear case: I'd rather customers didn't get a `[DRILL]`
text every week forever, and G-33 criterion 2 says they should.

**Added a sixth item that wasn't on the skip list.** G-25's PDF-export
criterion would mean adding a PDF library or a headless browser to a security
product. That's a supply-chain surface for a feature whose real requirement is
"something official-looking I can send to an exchange", and a print stylesheet
does that with no dependency. That is a product judgment about what "export as
PDF" means, so it went in the doc rather than getting decided by me.

**Split G-25 rather than blocking all of it.** Only the "what to do next"
wording is actually a product decision. The timeline, the balances and the
detection → notification → acknowledgement chain are all buildable now. Said so
on the issue and offered to build it with the advice block stubbed. Two of the
five are partial blocks like this rather than total ones, which is worth knowing
before treating the whole list as parked.

**G-43 is the one to start today.** It's the only item on the list that isn't
mine or the user's to decide at all — it needs a lawyer, which is the longest
lead time in the backlog. I also flagged the one question that most changes the
engineering: whether crypto-shredding counts as erasure. Every other resolution
of the append-only-log-versus-right-to-erasure conflict sacrifices one
obligation for the other; that one doesn't. And I noted that the pruning
mechanism itself is buildable now behind a configurable period, so the legal
answer changes a number rather than a design.

#### Surprising

`prune_balance_snapshots(keep_days=7)` is the *only* retention that exists
anywhere in the codebase. `events`, `alerts`, `seen_withdrawals`,
`seen_transfers` and `sms_deliveries` are all unbounded — and `seen_fills`,
which I added earlier today, now joins them. Worth noting that the append-only
tables grow at the rate the fast loop polls, which on the current 45-second
cadence is not slow.

---

### G-49 (#53) · Operator alerting channel

The foundational one. Five other issues have a criterion that can't be met
without it (#42, #52, #37, #27, #11), so it was the right thing to do first in
Phase 2.

The issue's own analysis was accurate and I didn't find anything to add to it:
Minas-side *destinations* already existed, and the gap was that everything had
to hang off an account. `dispatch()` takes an `account_id`, `_dedup_key` is
built from it, the `alerts` row carries it as a foreign key, and
`AccountPoller._emit` calls `dispatch()` *and* `ContactRouter.send()` — so
anything raised against a real account reached that customer.

#### Judgment calls

**A separate table, not a flag on `alerts`.** The deciding argument is what
`alerts` is *for*: a customer reads their alert history in the portal, and we
hand it to an exchange as evidence. Our outages do not belong in it. A boolean
column would have worked mechanically and would have needed a filter on every
existing query, each of which is a place to forget one.

**No customer path in the module at all.** Not a disabled one, not one behind a
flag — `OperatorAlerter` has no ContactRouter and no way to acquire one. A
route that must never fire is safest when it doesn't exist. The test asserts
the attribute is absent rather than that some mock wasn't called, which is the
stronger claim.

**`account_id` means "about", never "to".** Stated in the schema comment, the
module docstring and the method docstring, because it is the one thing about
this design that someone could get wrong later in a way that emails a customer
about our outage.

**The account is in the dedup key.** Two accounts going unwatchable are two
facts. Collapsing them by rule alone would report the first and hide the rest
of a fleet-wide outage — which is precisely the shape of problem this exists to
catch. Mutation-tested.

**Cooldown defaults are wider than the customer side (30/60/240/720 vs
15/30/720/1440).** The reasoning: customer cooldowns are tuned for a flapping
detection where re-firing quickly matters; an operator page reports a condition
that persists until someone fixes it, so re-sending every 15 minutes is noise
rather than urgency. These are the one set of numbers I chose rather than
derived. I'm comfortable with that — a paging cooldown isn't a security
threshold, it's operational hygiene, it's hot-reloadable, and criterion 3
required *some* value. Flagged in `DECISIONS-NEEDED.md` decision 1 as worth
revisiting alongside the escalation ladder, since the two interact.

**An undeliverable page is still recorded.** If no channel is configured for a
severity, the row is written anyway and the dashboard marks it `undelivered`.
Otherwise a misconfigured route is invisible in exactly the situation where it
matters most — which is the same silent-failure shape as everything else fixed
today.

**Added to `rules_schema.py`, necessarily.** G-12's validation now rejects
unknown keys, so a new config section that isn't declared would make the
product refuse to start. There's a test asserting the shipped `rules.yaml`
still validates *and* that `operator_alerting.routing` is readable — the two
changes could have collided silently.

#### What I deliberately did not build

**No callers.** The conditions that should page — the untimestamped counter
rising (#52), a failed drill (#37), the scheduler falling behind (#42),
unacknowledged escalation (#27), a fleet-wide key failure (#11) — each belong
to their own issue, and each involves a threshold decision that isn't mine to
make here. `Engine` holds the alerter so those issues plug in without
re-plumbing. That leaves one unused constructor argument on `main` today, which
I'd normally avoid; it's justified here because the issue is explicitly
groundwork and the alternative is inventing detector behaviour under the wrong
issue number.

**Not in scope, restated because it would be easy to assume otherwise:** this
is the product reporting its own faults *while running*. It cannot report the
process or the machine dying — nothing in-process can, and an alarm that
depends on the thing it monitors isn't an alarm. Closing #53 does not mean we
would know if Guardian stopped. That needs external uptime monitoring on
separate infrastructure, which is a parked hosting decision. Said in the module
docstring and the CHANGELOG as well as here.

#### Verification

15 tests, written first. Mutation check on three separate properties, each
red-lighting exactly its own guard:

- dedup key ignoring the account → the fleet-wide-outage test only
- cooldown read from `alerting` instead of `operator_alerting` → the
  independence test only
- undeliverable pages not recorded → both the recording and the dashboard test

Full suite green: 259 passed.

---

### G-48 (#52) · Paging when exchange records lose their timestamps

The first alarm through the door #53 opened, and the completion of an arc that
ran through this whole session: G-7 made the detector refuse undated records →
`163021e` added the counter → G-47 fixed the mirror failure → #55 fixed the
counter's label → #53 built the route → this watches it.

#### The judgment call that needs your eyes: I did not follow the issue's recommendation

**#52 recommends option (1): ship Prometheus rules + Alertmanager in
`docker-compose`. I built option (2), in-process.** Reasons, in the order they
weighed:

1. **The issue's own argument against (2) doesn't apply to this condition.** It
   says "an alarm that depends on the process it's monitoring goes quiet exactly
   when it matters" — true, and decisive for *"is Guardian running?"*. But this
   condition is "the process is running and receiving records without
   timestamps". If the process is down, this alarm is irrelevant and what's
   needed is external uptime monitoring, which #53 explicitly parks as a
   hosting decision. Option (2) is not weaker for *this* alarm.
2. **I can't test or deploy option (1).** It means writing `docker-compose`
   services, Prometheus rules and Alertmanager routing that need operator
   credentials in a second config — and I'm not to touch deployment config
   beyond what an issue requires, not to touch `.env`, and not to deploy. I'd
   be committing untested infrastructure and calling the criteria met.
3. **Criterion 3 asks the alarm path to be exercised by a test or a documented
   fire drill.** In-process gives a real automated test, which is the stronger
   half of that "or".
4. **Criterion 4 asks for reusability.** `OperatorAlerter` is the seam, and it
   stays the seam under either option — a future Alertmanager webhook lands on
   the same call. So choosing (2) now doesn't foreclose (1); it makes (1) a
   delivery-mechanism change rather than a redesign.

**This is reversible and I've filed it** so it isn't buried in a log. If you
want the Prometheus path, the alarm logic moves out and the page call stays.

#### The other thing that changed: severity

Set to **HIGH, not CRITICAL**, and the reasoning shifted *while I was building
it*. #52's motivating fear is "a customer whose theft alert never fired". That
was true when it was written. It isn't any more:

- G-7 makes quantity-drop *refuse* to credit an undated record as an
  explanation — so a drop alerts rather than going quiet. Fail-safe.
- G-47 (landed this session) makes undated fills judged on first sighting
  rather than dropped — so they're still price-checked.

An exchange dropping timestamps now produces **more** alerts and degraded
quality, not silence. That is urgent, not wake-at-3am, and pretending otherwise
would train an operator to ignore the channel. Said in the code comment, the
page's own message text, and the issue.

#### A defect in my own #53 code, caught by my own test

`test_each_exchange_and_kind_is_tracked_separately` failed, and it was right to.
`_dedup_key(rule, account_id)` produced the same key for "Binance stopped
stamping fills" and "Bitget stopped stamping withdrawals" — one rule,
`account_id=None` twice — so the second page would be suppressed for a full
hour. I'd reasoned carefully about the account being in the key and completely
missed that a page with *no* account needs the same treatment for a different
discriminator. Fixed by adding `dedup_scope`. Worth noting how it was caught:
not by review, but by writing a test for a property I'd already convinced
myself was handled.

#### A test that passed for the wrong reason

`test_the_window_is_respected` passed under the mutation that removed the window
guard entirely. Written with a single record before the loop, the delta went to
zero on the second tick and the streak reset, so no page happened either way —
the test was asserting nothing. Rewritten to emit a record before *each* tick,
the way a real format change looks, at which point removing the guard pages at
90 seconds instead of 30 minutes and the test fails properly. The mutation check
is what found this; it would have shipped as false coverage otherwise.

#### Verification

10 tests, written first. Three mutations, each red-lighting only its own guard:
streak not resetting on a clean window; the window not enforced (after the test
was fixed); `dedup_scope` dropped. Full suite green: 269 passed.

---

### G-46 (#50) · Automated code quality

Four unchecked criteria, all now enforced in CI. The interesting part of this
one was choosing what *not* to enforce.

#### Judgment calls

**Ruff's rule set is chosen to catch mistakes, not to impose a house style.**
Run with everything on, it reported 128 findings — most of them arguments with
decisions this codebase made on purpose. The two I explicitly turned off, with
the reasoning written into `pyproject.toml` so nobody re-enables them without
reading it:

- `FURB157` (39 hits) would rewrite `Decimal("0")` to `Decimal(0)`. The string
  form is load-bearing — `config/rules.yaml`'s own header says thresholds are
  strings so they load as Decimal and never float. Enabling it would fight the
  one convention the money code depends on.
- `SIM118` (2 hits) wants `"col" in row.keys()` simplified to `"col" in row`.
  On a `sqlite3.Row` that is **not the same expression**: iterating a Row
  yields its *values*, so the "simplification" silently converts a
  column-presence check into a value search. Both sites are the pre-migration
  guards that decide which column to read off an old database. This one is
  worth knowing about generally — it's a lint rule that will introduce a bug if
  applied blindly.

Also off: `UP017` (33 hits, `timezone.utc` → `datetime.UTC` — valid but pure
churn) and `BLE001` (blind excepts that are deliberate, already carrying noqa
and a comment about one bad account not stalling the fleet).

**E402 was a real finding, and it was mine.** 45 hits, all from a pattern I'd
followed from `tests/fixtures/ccxt_responses.py`: `from __future__ import
annotations` *then* a triple-quoted string. That string is not a docstring — a
docstring must be the first statement — so it's invisible to `help()`, to
tooling, and to anyone using introspection. Twelve files fixed mechanically by
moving the string above the future-import, which is where it belongs and where
it becomes a real docstring. Four of those files were ones I created earlier
today.

**The style gate caught my own dead code**, on its first run:
`conn.getresponse if False else None`, an editing artefact left in the G-21
portal test. It did nothing and looked deliberate. Exactly the thing an
automated check is for, and a small argument for having added it.

**The coverage floor is measured, not aspirational.** The suite is at 78.87%,
so the gate is 78. The criterion is that coverage must not *silently decline* —
a round 80 would fail on day one, and a round 50 would let half the suite rot
without a word. Raise it when the number rises.

**`pip-audit .`, not bare `pip-audit`.** The bare form audits the whole
environment and, run here, reported six advisories against the runner's own
`pip`. Nobody can act on those from this repo, and a security gate that fires
on things you cannot fix is the fastest way to teach a team to ignore it.
Scoped to the project's declared dependencies, it is clean — and it will be
loud for a real reason. Worth noting that `ccxt` is pinned to an exact version
on purpose (adapter behaviour was verified against it), so a future advisory
there is a deliberate re-verification rather than a bump.

**Version agreement is a test, not a CI step.** It fails on the machine of
whoever forgot, at the moment they forgot, rather than three minutes later in a
pipeline they have already stopped watching.

#### The disagreement it found

`pyproject.toml` said `0.1.0`. `CHANGELOG.md`'s newest release heading is
`[1.0.0] — 2026-07-05`. The package version was never bumped through four
releases, and nothing anywhere would have said so — which is precisely the
criterion. **I set `pyproject.toml` to `1.0.0`** to match the CHANGELOG, on the
grounds that the CHANGELOG is the record of what was actually released. Calling
it out because it's a judgment call about *which* of two disagreeing sources is
right, and I picked the one with dates in it.

`sentinel/__init__.py` was empty; it now derives `__version__` from installed
metadata rather than writing the number down a third time.

#### Verification

4 new tests. Mutation checks: reverting `pyproject.toml` to `0.1.0` turns two
red; appending a small unused-import/unused-variable function to a module turns
ruff red. Coverage gate verified to pass at 78 and to have failed at 79 (the
real number is 78.87 — I set the floor from the measurement rather than the
other way round). `pip-audit .` clean. Full suite green: 273 passed.

---

### G-45 (#49) · Database contention, and the same account checked twice

Two criteria, two independent defects, both of which get worse with exactly the
thing the issue is about.

**Contention.** One `sqlite3` connection shared across the whole worker pool,
with `Database.cursor()` taking a single lock around it. That lock was held for
every *read* as well as every write, so eight workers polling eight accounts
serialised against each other on the database while their actual work was
waiting on exchange HTTP. The issue says this has already caused one incident;
the mechanism is right there in the constructor comment ("One connection shared
across the worker pool: serialize access").

**Double-checking.** `_schedule_cycle` enqueued a fast task per active account
every `fast_loop_seconds`, with no reference to whether the previous one had
finished. Any poll slower than the interval — a slow exchange, an open circuit
breaker, a backed-up queue — meant two workers on one account.

#### Judgment calls

**WAL plus a connection per thread, keeping the write lock.** WAL gives one
writer and many concurrent readers, which is exactly the shape of this
workload. The write lock stays because SQLite allows one writer regardless, and
holding a Python lock is better than burning the busy timeout and surfacing
`SQLITE_BUSY` as a failed poll.

**I did not split it into a read/write lock**, which was the obvious next
optimisation. `cursor()` yields an opaque block: a caller can read *and* write
inside one `with`, and `record_seen_withdrawal` does precisely that — SELECT to
check, INSERT if absent. Classifying blocks by intent would need every call site
audited, and getting one wrong reintroduces a race in the code that decides
whether a withdrawal is new. The contention that mattered was reads blocking
reads, and WAL fixes that without the risk.

**`synchronous = NORMAL`.** The documented companion to WAL: durable across
process crashes, at risk only of losing the last transactions in a power loss.
Everything written here is a snapshot or an append-only observation the next
poll re-derives, so the trade is worth the per-write fsync it saves. Stated
because it *is* a durability reduction and shouldn't be discovered later as a
surprise.

**`:memory:` keeps a single shared connection.** A second connection to
`:memory:` is a second, empty database — thread-local connections there would
silently lose every row written. There's a test for it, because this is the
kind of thing that passes every unit test and then loses data in the one place
it's used.

**A skipped cycle logs a warning.** Skipping is correct, but silently skipping
would mean a customer whose polls consistently overrun the interval is watched
less often than the dashboard implies — the same class of "looks fine, isn't"
this session has been clearing all day.

**Release lives in a `finally`, and there's a test for the raising case.** A
leaked claim would stop that account being checked for the lifetime of the
process: a silent, permanent monitoring gap for one customer, which is strictly
worse than the double-poll the guard exists to prevent.

**Fast and slow loops are claimed separately.** They read different things and
write different state. Sharing one claim would let a slow loop (permissions,
balance snapshot — 10-minute cadence) hold up the fast loop that watches for
withdrawals.

#### Verification

10 tests, written first, 6 failing. One is a genuine concurrency exercise:
sixteen threads writing and reading in a loop, requiring all 320 rows and no
errors. Mutations, each red-lighting only its own guard: forcing the shared
connection; making `_claim` always succeed; moving `_release` out of the
`finally`. Full suite green: 283 passed, coverage up to 79.18%.

#### Surprising

The suite went from ~70s to ~41s. The lock this removed was costing roughly
40% of test wall-clock, which is a reasonable proxy for what it was costing the
poller under a full worker pool.

---

### Correction: I pushed a red main, and fixed it in the next commit

**What happened.** On the G-45 commit I ran the full suite and the commit in a
single chained shell command. The suite reported `1 failed, 282 passed` and the
commit went ahead anyway, because I had chained on the *commit* succeeding
rather than gating on the test result. `609fe1d` landed on main with
`test_dated_recent_fill_is_still_price_checked` failing.

That breaks the rule I was given — never leave main with a failing suite — and
it broke it in the most avoidable way, by not reading output I had explicitly
asked for. Fixed in the following commit; main was red for about two minutes.

**What the failure actually was**, since it turned out to be a real defect and
not just a flaky test. The test passed in isolation and failed in the full run.
`last_run_ms` was `datetime.fromisoformat(...).timestamp() * 1000` — a float
carrying sub-millisecond precision — while exchange timestamps are integer
milliseconds. A fill stamped inside the boundary millisecond compares as
*older* than the last poll and is excluded, permanently, because the window
only moves forward.

A one-millisecond hole, but exactly the same never-examined shape as the bug
G-47 rewrote this filter to fix. Fixed by flooring `last_run_ms` to whole
milliseconds, which errs towards examining. The test also now uses a timestamp
a second past the settling poll, since it is about the ordinary recent fill
rather than the boundary.

**Why it surfaced today and not before:** the G-45 concurrency work cut the
suite from ~70s to ~38s, which changed the timing enough to land inside that
millisecond. The bug was there the whole time.

**What I changed about how I work:** run the suite as its own command and read
the result before committing. Chaining them reads as one step and isn't.

---

## Follow-up session — `validate-config`, and reversing the fail-closed decision

Two requests: a pre-deploy config checker, and a challenge to the
refuse-to-start behaviour I built into G-12. The second one was right and I've
reversed it.

### I got the startup decision wrong

My reasoning in G-12 was: *coming up with thresholds nobody has checked looks
exactly like monitoring, so refuse.* That is a real risk and the argument still
holds for a fresh install. What it missed is the **upgrade**, which is the case
that actually happens:

A file the old code ran happily on gets rejected by a schema that shipped after
it. `rules_schema.SPEC` gains entries as detectors are added, so this isn't
hypothetical — it's the expected consequence of the feature. Refusing there
turns a working monitor into a dead one, during a deploy window, on a service
whose whole purpose is to be watching. **Dying loudly and not monitoring is
still not monitoring**, and the customer being drained is not comforted by the
clarity of the log line.

Starting on defaults strictly dominates refusing on coverage: some detection
beats none, in every case. What made it the wrong trade *before* was that the
only signal available was a log line nobody reads. **That changed when G-49
(#53) landed a paging route** — which I built later the same session. So the
decision gets revisited because its binding constraint went away, not because
the risk did. Worth noting as a pattern: a safety decision made under a
constraint should be re-examined when the constraint lifts, and nothing
prompts that automatically.

### What it does now

- **Never refuses.** Runs on every setting that validates.
- **Per-setting fallback, not all-or-nothing.** `prune_invalid` drops only the
  leaves the schema rejects, so the readers fall through to their own defaults
  for those and keep the operator's values everywhere else. All-or-nothing
  would discard someone's entire detector tuning because they mistyped
  `triage.max_events`, which is its own kind of surprise.
- **A known-good config still wins.** Once a valid configuration has loaded,
  it beats a pruned version of a broken file — it's both valid *and* what the
  operator asked for. The running-reload behaviour is unchanged.
- **CRITICAL operator page**, keyed on the rejection, so a persistent problem
  pages once and a *new* mistake pages again. This is the thing that makes
  starting-anyway defensible rather than merely quiet.

### The trap I nearly shipped

"Start with defaults" is not uniformly safe, and I only found this by writing
the test for it. `build_channels` read routing with `default=[]`. For a
threshold, falling back to a default means falling back to *a number*. For
`alerting.routing`, it means falling back to **no channels** — alerts stored,
deduped, and delivered nowhere.

So a single mistyped line in the routing table could have produced a system
that detects perfectly and tells nobody: silent, total, and *worse than the
failure this whole change is fixing*. Now falls back to every configured
channel, and says so at ERROR. Applies to the operator routing too, where it's
worse still — the page it would drop is the one telling us the config is
broken.

### `sentinel validate-config [path]`

Reads a file, runs `rules_schema.validate`, prints either "valid" or every
problem with its full dotted path and offending value. No database opened,
nothing started, nothing written.

**Exit codes are the contract**, since this goes in a deploy script: `0` valid,
`1` read but rejected, `2` could not be read. 1 and 2 are deliberately separate
— a typo in the config and a typo in the *path* need different reactions, and a
script that conflates them reports a missing file as a bad config.

It also prints **what deploying anyway would do**. That is not decoration: the
answer changed with the decision above, and "it won't come up" is exactly what
an operator would otherwise reasonably assume.

### Two wiring gaps the mutation checks found

Both the same shape, and worth recording because I'd written the standard for
it myself in #52 ("an alarm that exists but was never connected is the same as
no alarm") and then failed to meet it twice:

1. Removing `_check_config_health()` from the scheduler broke **no test** — my
   tests all called it directly.
2. Removing the untimestamped-record alarm's `evaluate()` call from the
   scheduler also broke no test, for the same reason. That gap shipped in #52.

Fixed by extracting `_scheduler_tick()` as a testable seam and pinning both
calls. **Testing a component's behaviour and testing that anything calls it are
different tests**, and the second one is the one that goes missing.

While there: the metrics and alarm calls previously sat *outside* the
scheduler's `try`, so an exception in either would have killed the scheduler
thread and stopped all polling — the loudest possible failure expressed as
total silence. Now inside it, with a test.

### Tests that had to change

Nine tests in `test_rules_validation.py` asserted `pytest.raises(RulesConfigError)`
at startup. They were pinning the decision I just reversed, so they're rewritten
rather than deleted — and to something **stronger**: that the rejected value is
not merely reported but *not in force*, with the reader falling back to its
default. Asserting an exception only proved it stopped; asserting the effective
value proves what it does instead.

### Verification

27 new tests across two files, written first. Six mutations: all-or-nothing
pruning; treating a pruned config as known-good; dropping the health check from
the scheduler; dropping the alarm from the scheduler; routing falling back to
nothing; and the CLI's exit-code split. Each red-lights only its own guard.
Full suite green at 311, lint clean.

Also exercised by hand against a deliberately-broken copy of the production
config shape (decimal comma plus a stale key) to check the output is readable
by someone under deploy pressure.

---

## Task 1 · Verifying the rewritten tests by mutation

Asked to prove, by mutation rather than by reading, that the nine tests I
rewrote when reversing the fail-closed decision still pin something. They do —
but the check found that **my description of them was overstated**, and the
overstatement was in exactly the direction that matters.

### Mutation A — remove rejection handling entirely

`validate()` returns no problems, and `_reject` records nothing and prunes
nothing. Result: **all 11 rewritten instances go red.** (18 of the file's 20;
the two survivors are correctly the ones describing behaviour that holds either
way — the shipped config validating, and a legitimate retune being accepted.)

So none of them were vacuous. That is the question I was asked, and the answer
is clean.

### Mutation B — the one that mattered

"All red" only proves they pin *something*. I told the user I had rewritten them
"to something stronger: that a rejected value is not merely reported but not in
force". So I mutated exactly that claim: **keep** the reporting (`is_valid`
False, message set, log, page) and remove **only** the pruning, so rejected
values stay in force.

Under that, **only 2 of the 11 went red.**

The other nine asserted that the rejection was *reported* and nothing more. They
would have caught validation being deleted. They would **not** have caught a
config that complains about a bad value and then hands it to a detector anyway —
which is the failure mode with actual teeth, because it looks correct from
every angle except the one that matters.

My claim was true of two tests and I stated it of all of them.

### Fixed

Added a "not in force" assertion to each: the misspelled key and unknown section
must not be *served*, and the six formula cases now carry their full key path so
absence can be asserted directly. Mutation B now turns **10 red instead of 2**.

One remains structurally unable to pin it: the malformed-YAML case has no parsed
data to leak, so there is nothing for pruning to remove. That is inherent rather
than a gap, and it is noted in place.

### What I take from it

"All red under a full revert" is a weak check that reads like a strong one. The
useful mutation is not *remove the feature* — it is **remove the specific
property the test claims to pin**, one property at a time. A test suite can be
100% mutation-killed against a coarse revert while pinning half of what its
names promise.

---

## Task 2 · Sweep: defaults that silently disable

`build_channels` fell back to `[]` for `alerting.routing`, where "fall back to
the default" meant *no channels* rather than a safe value. Asked to audit every
`rules.get(..., default=X)` for the same shape.

### The criterion I used

Not "is this default sensible" — that invites opinion. Instead: **does the
fallback behave exactly as the shipped `config/rules.yaml` says it should?**
Anything else is a silent behaviour change, and it is the worst kind, because
the setting is missing: there is no line to read and no diff to review.

### The list (26 settings)

| Setting | Fallback | Verdict |
| --- | --- | --- |
| `alerting.routing` | `[]` → no channels | **switch-off** (fixed previously) |
| `operator_alerting.routing` | `[]` → no channels | **switch-off** (fixed previously) |
| `alerting.cooldown_minutes.*` | flat `60` vs 15/30/720/1440 | **unsafe, quieter** |
| `quantity_drop.tolerance_pct` | implicit `0` vs `0.5` | **unsafe, noisier** |
| `quantity_drop.tolerance_absolute` | implicit `0` vs `1e-8` | **unsafe, noisier** |
| `polling.jitter_seconds` (sequential runner) | `0` vs `10` | cosmetic |
| *(20 others)* | — | agree with the shipped config |

**The cooldowns are the one I'd have missed by eye.** A flat 60 looks like a
reasonable number. Against a shipped CRITICAL of 15 it quadruples how long a
repeat CRITICAL stays suppressed — quieter, on the severity where quieter is
worst, and only visible by putting the two lists side by side.

**The tolerances fail in the opposite direction and are still wrong.** Both were
read with *no default argument at all*, so they landed on `get_decimal`'s own
`Decimal("0")`. Zero tolerance alerts on every dust movement and rounding
artefact. That is fail-loud in direction, which is why it survived review — but
an alert storm buries a real alert exactly as thoroughly as silence does, so it
is a switch-off by another route.

### The fix: a registry, not three patches

Patching three call sites would have left the class intact. `rules_schema.DEFAULTS`
now declares what every setting does when absent, and `RulesConfig`'s readers
consult it when the caller passes no explicit default — so a call site can no
longer invent its own. An explicit `default=` still wins; a caller that passes
one means it.

Two tests give it teeth:

- **every entry in `DEFAULTS` must equal the shipped value** — the drift guard,
  and the one that would have caught all four;
- **every leaf in `SPEC` must appear in `DEFAULTS`** — so adding a setting
  forces someone to answer "and what if it's missing?", which is precisely the
  question nobody answered for `tolerance_pct`.

Loudness is applied where it belongs. Falling back to the shipped default is
*designed* behaviour and logs nothing — it would be noise on every unconfigured
install. Falling back for `routing` still logs ERROR, because that one means
degraded delivery rather than a documented value.

### One thing worth flagging

The first version of the drift test reported a false mismatch:
`Decimal("0.00000001")` renders as `1E-8`, and I was comparing strings. A test
whose entire job is to be believed cannot cry wolf, so it compares numerically
now. Caught because I read the failure rather than assuming the code was wrong.

### Verification

8 tests, written first. Four mutations — cooldowns drifting back to a flat 60,
tolerance drifting back to zero, a new SPEC setting with no declared fallback,
and the readers no longer consulting the registry — each red-lighting only its
own guard. Full suite green at 319.

---

## Task 3 · Sweep: tested but never wired

Two call sites were found unpinned while building the last change. Asked to
check every alarm, health check and detector the same way — by deleting the call
site and seeing whether anything fails.

### Method

A script that blanks each `for detection in detect_x(...)` call site in turn
(replacing the iterable with `[]`, so the loop still runs and nothing else
changes), runs the **whole** suite, records pass/fail, and restores. Passing
means nothing anywhere noticed the detector had stopped being called.

### Result: 8 of 12 detectors were unpinned

| Detector | Was pinned? |
| --- | --- |
| `detect_new_withdrawals` | yes |
| `detect_unseen_address` | yes |
| `detect_off_market_fills` | yes |
| `detect_quantity_drop` | yes |
| `detect_withdrawal_burst` | **no** |
| `detect_baseline_drift` | **no** |
| `detect_new_pair_activity` | **no** |
| `detect_fee_anomaly` | **no** |
| `detect_new_transfer_route` | **no** |
| `detect_permission_state_change` | **no** |
| `detect_tamper` | **no** |
| `detect_unreachable` | **no** |

Every one of the eight has thorough unit tests for *how it decides*. None had a
test that anything *asks it*. Deleting the call for `detect_tamper` — the
detector that notices the monitoring key itself has been revoked — left the
suite entirely green.

The four that were pinned were pinned by accident: they happen to be the ones
the end-to-end integration tests exercise, because those tests were written
about withdrawals and thefts. Nobody chose which detectors were covered.

### Fix

`tests/test_detector_wiring.py`: one test per previously-unpinned detector,
each driving the real poller with input that detector should fire on and
asserting its rule comes out. They deliberately assert almost nothing about
*how* each detector decides — that is well covered elsewhere, and duplicating it
here would make these tests fail for reasons that have nothing to do with
wiring.

Re-ran the sweep afterwards: **unpinned: none.**

### The other half — calls that could kill a thread

Two found, both in `engine.py`, both with the same consequence: the process
stays up, the dashboard keeps serving, the metrics endpoint keeps answering,
and nothing is polled ever again.

1. **`_scheduler_loop` read the poll interval outside the guard.** That read
   goes through `RulesConfig`, which touches the filesystem and re-parses YAML,
   so it *can* fail. If it did, the scheduler thread would die and no work
   would ever be queued again. Now inside `_scheduler_pass`, which cannot
   raise.
2. **The worker's own error handler could throw.**
   `self.breakers.for_account(...).record_failure()` runs *because* something
   already went wrong, and it sat inside the `except` rather than under its own
   guard — so a failure there escaped the handler whose entire job is stopping
   one bad account from killing a worker. A pool that loses workers one at a
   time ends up polling nothing, with no single moment where anything looks
   broken. Now guarded, with `task_done()` in an inner `finally` so the queue's
   accounting can't be left wrong either.

The worker test drives the real `_worker_loop` on a real thread and asserts it
takes a *second* task after the first one poisons it. "The thread is still
alive" is the actual guarantee, and the synchronous drain used by other tests
does not have it.

### What I take from this

The pattern is now three-for-three across this codebase: **testing what a
component decides, and never testing that anything asks it.** It produces suites
that look thorough and pin half of what their names promise. Coverage does not
show it — every one of these detectors was "covered" — because coverage counts
lines executed, and a unit test executes the detector's lines without the
caller's.

The cheap check is the one used here: delete the call, run everything, and see
whether the suite notices.

### Verification

10 tests, written first. The detector sweep re-run end to end (12 call sites ×
full suite). Two further mutations on the thread guards, each red-lighting only
its own test. Full suite green at 329.

---

## Task 4 · The constraint-lifted sweep

Filed as **#58**, plus **#57** for a defect the sweep turned up. Nothing changed.

### The premise, and why it generalises

Guardian was built with no operator paging route at all — every channel was
customer-scoped. That absence didn't merely leave a gap; it *shaped decisions*.
Over and over, the honest available option was "log it and carry on", and that
is what the code does. #53 removed the constraint, and nothing automatically
revisits the choices it forced.

Same reasoning as the fail-closed reversal: **a decision made under a constraint
should be re-examined when the constraint lifts, and nothing prompts that.** It
is worth doing deliberately, once, rather than rediscovering it one issue at a
time.

### What I looked for

Not "this logs an error" — that would have returned every handler in the tree.
Specifically: *the customer or the system is worse off, nobody is told, and the
reason nobody is told is that there was nobody to tell.*

Nineteen sites in four groups:

- **A — a customer was not reached, and nobody knows** (6). The strongest group:
  an alert was correctly detected and then failed to arrive. Per-account email
  failures leave nothing but a log line; SMS at least leaves an
  `sms_deliveries` row.
- **B — detection silently didn't run** (6). Worker and scheduler failures,
  ticker fetches falling back to a 10-symbol cap, per-coin transfer failures.
- **C — an account was onboarded with an incomplete baseline** (2). Both
  best-effort seeds swallow entirely. The reasoning is sound — a seeding failure
  shouldn't block onboarding — but the consequence lands *later*, as
  false-positive new-pair alerts for pairs the customer has always traded.
- **D — capability silently absent** (4). Notably `prometheus_client` missing
  means every metric-based alarm never fires, and MEDIUM/LOW alerts depend on a
  host crontab that `docs/deployment.md` already warns "fails silently every day
  and the digest never sends".

### One issue, not nineteen

Deliberate. Paging on all of these would train an operator to ignore the
channel, which is the same outcome as not paging at all — so the *work* is the
triage, and splitting before triaging would be busywork. The issue records the
four questions each entry needs answered (page or not, what severity, page or
metric-plus-alarm, what dedup scope) rather than presuming the answer.

### The thing the sweep actually found: #57

`dispatch()` calls `channel.send()` **unguarded**, and **before** writing the
`alerts` row. One raising channel therefore means:

1. the remaining channels are never tried — including the ones configured as
   backups;
2. no `alerts` row is written, so the alert is absent from the customer's
   history, the portal, the dashboard, and anything handed to an exchange;
3. `ContactRouter` is never reached, so the customer's own email and phone are
   never attempted either;
4. the exception aborts the rest of that account's poll cycle;
5. one log line, from a handler whose message is about *task failure*.

A flaky Telegram API means the customer is told nothing, on any channel, with no
record, and the rest of that account's detection stops — every cycle, for as
long as it lasts.

Filed **p1 / Phase 1** under the standing rule for silent failures. It is not a
missing page; it is a missing `try`. **I will build it before #54**, because
Phase 1 precedes Phase 2 in milestone order and this is a customer-facing silent
miss.

The one mitigation: with no `alerts` row there is no dedup key, so the next
cycle re-detects and re-sends. Recovery works once the channel returns. But
while it is down, detection is truncated every cycle and nothing says so.

---

## #57 · One failing channel lost the alert entirely

Built before #54 despite #54 being the queued next item: this is p1 / Phase 1,
Phase 1 precedes Phase 2 in milestone order, and it is a customer-facing silent
miss. Flagged in the task-4 entry above before starting.

### Root cause

`dispatch()` called `channel.send()` **unguarded**, and **before** writing the
`alerts` row. Five consequences from one exception, in order:

1. remaining channels never tried — CRITICAL routes to four, and the other
   three exist precisely as backups;
2. no `alerts` row — absent from the customer's history, the portal, the
   dashboard, and anything handed to an exchange as evidence;
3. `ContactRouter` never reached, because `_emit` calls `dispatch()` and *then*
   the router — so the customer's own email and phone went untried too;
4. the rest of that account's poll cycle abandoned;
5. one log line, from a handler whose message is about *task failure* rather
   than about a customer not being told.

### Judgment calls

**Record the alert even when nobody took it.** The deciding argument is that
"we detected this" and "we managed to tell someone" are different facts, and
sharing a row's *existence* made an undelivered alert indistinguishable from
one that never happened. `delivered_to` follows the precedent `operator_pages`
already set.

**Dedup still applies to an undelivered alert**, and there is a test for it.
This was the one real risk in recording undelivered alerts: the row is what
dedup reads, so getting it wrong would turn a flaky channel into a repeat-alert
storm. Recording it means the retry happens on the *next detection*, not on
every cycle.

**Page only when the count is zero.** Not when *a* channel fails — dispatch
already tried the others, and that is degraded rather than silent. Zero means
every configured channel refused it, or none was configured for that severity
at all. Paging on anything weaker trains an operator to ignore the channel,
which costs more than it buys.

**Dedup scope is the severity, not the rule.** A channel outage affects every
rule at that severity, so scoping per-rule would page once per detector during
a Telegram outage. Per severity, per account.

### A test that passed for the wrong reason, again

Two tests initially failed because my mock client returned the same records to
*onboarding* as to the poll — so `seed_baseline` had already recorded them and
no detection fired. The tests were green-adjacent for a reason that had nothing
to do with the code under test. Fixed by having onboarding see a quiet account,
which is also what actually happens.

### Verification

10 tests, written first, all red. Three mutations — sends unguarded again,
`delivered_to` hardcoded to 1, and the page removed — each red-lighting only
its own guards. Full suite green at 339.

---

## #54 (G-50) · Operator identity

The largest remaining unblocker: #40, #41, #32 and G-12's last criterion all
wait on it.

### Judgment calls

**Separate tables, separate logins, separate session secrets.** The issue is
explicit that conflating operator and customer identity is how an operator ends
up reachable through a customer login, so `operators` is its own table reached
through its own form, and its session secret lives in `operator_meta` rather
than `portal_meta` — a leak of one must not mint sessions for the other. A test
asserts each credential fails against the other's login.

Only the *primitives* are shared, moved into `sentinel/passwords.py`. Sharing a
scrypt call is not conflating identity; duplicating the crypto so the two could
drift would be worse. (This also resolved a circular import — `portal` already
imports `dashboard`, so the login throttle had to live somewhere neutral.)

**Sign-in becomes mandatory only once an operator exists.** This is the config
reversal's lesson applied before it could bite: shipping operator identity must
not lock an existing deployment out of its own dashboard during an upgrade.
Until somebody runs `sentinel operator add`, the page behaves exactly as it
always has — and carries a red banner saying it is unprotected, which was true
before this change too and was written down nowhere the operator would see it.

Same shape for `sentinel ack`: `--as` is required once operators exist and not
before, so a deployment with no accounts keeps working and attribution becomes
mandatory the moment there is somebody to attribute to.

**Disable, never delete.** Deleting an operator would orphan every action they
ever took, and the entire point of having names is that the log can still point
at one. `disable_operator` also had to revoke *live* sessions, not just block
new sign-ins — otherwise a session minted before offboarding stays valid for
its full 12 hours and `disable` is a promise rather than a control. So
`session_operator` re-checks status on every request rather than trusting the
token. Mutation-tested both ways.

**Adding a customer wrote no event at all.** The issue lists it as an
unattributed action, which I read as "the row has no actor" — it is worse than
that: there was no row. `onboard_account` now appends a `customer_added` event,
so there is something for an actor to hang off.

**Nullable actors, nothing backfilled.** A detector firing has nobody behind
it. Filling the column in with a guess would make the log say a person acted
when none did, which is worse than saying nothing — and the issue asks for
exactly this. There is a test that an unattributed shell ack records
`acknowledged_by = NULL` rather than inventing a name.

**A 12-character password floor, not a policy.** These accounts gate every
customer's contact details and every stored API key. I did not add complexity
rules, expiry, or history — those are opinions, and this is a floor.

### What this does *not* do

2FA. #32's fourth criterion ("operator accounts must have 2FA") is now
buildable — there is an account to protect — but it is that issue's work, and
the standing instruction is not to build 2FA flows without eyes on them.

### Verification

26 tests, written first. Four mutations — removing the auth gate, trusting the
session without re-checking status, letting a disabled operator's password
still verify, and dropping the `--as` requirement — each red-lighting only its
own guards. Full suite green at 365; coverage up to 82.6%.
