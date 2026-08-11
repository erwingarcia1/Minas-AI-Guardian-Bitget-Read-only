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
