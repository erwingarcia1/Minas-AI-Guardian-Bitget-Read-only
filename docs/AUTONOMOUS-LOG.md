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

`exchange=label` — every call site passing the metric's `exchange` label
actually passes the *account label*, so the series is keyed by customer account
rather than by exchange, and its cardinality grows with the customer count. I
kept my new code consistent with the existing call rather than fixing it
inline, because one metric emitting two different label vocabularies is worse
than one consistently wrong. Filed separately (see below) — it wants fixing
before #52 binds a paging alarm to that exact metric.
