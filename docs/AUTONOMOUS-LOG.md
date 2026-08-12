# Autonomous work log

Running log of unattended work on the backlog. Newest session at the bottom
until the run finishes, at which point a summary goes at the top.

Written for someone who wasn't here: what was done, every judgment call and why,
what was skipped and why, and anything about the codebase that was surprising.

Related: `docs/DECISIONS-NEEDED.md` holds the questions that need a product
decision before anything can be built.

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
