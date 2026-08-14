# Changelog

All notable changes to Minas AI Guardian are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The internal package/CLI codename is `sentinel`; the product is **Minas AI
Guardian**.

## [Unreleased]

_Pending customer/ops items (not code): read-only Bybit key (previous had
Contract-Trade scope), OKX + Binance keys, Gmail SMTP credentials, ClickSend
or Twilio credentials, terminate the retired us-east-1 instance._

### Changed
- **A rejected rules config no longer stops the service from starting
  (revises G-12, #16).** The original behaviour — refuse to start when no valid
  config has ever loaded — missed the case that matters most: an **upgrade**,
  where a file the old code ran happily on is rejected by a schema that shipped
  after it. Refusing there turns a working monitor into a dead one during a
  deploy, and dying loudly is still not monitoring. Now the process runs on
  every setting that validates, falls back to built-in defaults only for the
  ones that don't (per setting, so one mistyped key doesn't discard an
  operator's whole detector tuning), and raises a **CRITICAL operator page**
  saying the thresholds in force are not the ones in the file. What made
  starting-anyway indefensible before was that the only signal was a log line
  nobody reads; that changed when G-49 landed a paging route, so the decision
  was revisited because its binding constraint went away. Fail-fast moved to
  `sentinel validate-config`, which belongs before a deploy rather than during
  one. A config that *has* loaded validly is still preferred over a pruned one:
  it's both valid and what the operator actually asked for.
- **Alert routing falls back to channels rather than to nothing.**
  `build_channels` read routing with `default=[]`, so a missing or pruned
  routing table meant *no channels for that severity* — alerts stored, deduped,
  and delivered nowhere. Reachable from one mistyped line, silent, and a worse
  failure than anything the schema validation was added to prevent. Now falls
  back to every configured channel, loudly.

### Fixed
- **The customer is told when the Guardian loses access to their account
  (G-6, #11).** The detector existed; what happened to its finding didn't work.
  `account_unreachable` fired at **MEDIUM, and MEDIUM routes to `digest` only** —
  so "your account has stopped being watched" waited for a daily digest sent by
  a host crontab that `docs/deployment.md` itself warns *"fails silently every
  day and the digest never sends"*. That is the issue's fourth criterion
  inverted: silence looking exactly like safety. Now HIGH, so it reaches the
  customer within minutes; the message states plainly that the account is **not
  being watched** and what to do, split by cause — a rejected key is theirs to
  re-issue, an unreachable exchange is not, and telling them to replace a
  working key would be wrong advice. It repeats **daily** while unresolved via a
  new per-rule cooldown, because HIGH's 30 minutes would page twice an hour
  until fixed and no repeat at all leaves an unwatched account looking fine
  forever. And the state is shown where the account appears: a **NOT BEING
  WATCHED** pill on the ops dashboard and a plain statement on the member
  portal, both derived from the same counters the alert fires on so the page and
  the alert cannot disagree. Also fixed: auth failures went through a path that
  fired only the *tamper* signal and never checked reachability, so G-6's actual
  case — an expired or deleted key — could never produce the actionable alert
  this issue exists for. Both now fire; tamper answers "is this an attack", this
  answers "are you covered".
- **A poll that dies partway no longer reports itself as a success (#59).**
  `last_success_at` was stamped the moment the exchange fetches returned, with
  **nine detector invocations still to run**, and `consecutive_failures` was
  reset to `0` alongside it. So a cycle that crashed after the fetches counted
  as a success, never incremented the failure counter, **never let
  `detect_unreachable` fire**, and left the account green on the dashboard with
  a `last_success_at` of seconds ago — while nothing had been examined. An
  account could sit in that state indefinitely: polled every 45 seconds, healthy
  on every indicator, never actually checked. The exception escaped
  `run_fast_loop` entirely and became one log line about a "task failure" from
  the worker's blanket handler. Success is now recorded when the cycle
  *completes*; a cycle that raises after the fetches goes through the normal
  failure path, so the heartbeat detector can do its job. `last_fast_loop_at`
  moves with it, because it bounds which fills are still worth price-checking
  and advancing it over a dead cycle would step the window past fills nothing
  judged. **This is what reopened #14 (G-10)** — *losing sight of an account is
  itself an alert* did not hold while this stood.
- **Two more falsy sentinels, found by sweeping for the pattern rather than
  tripping over it.** *(a)* `record_seen_transfer` returned `False` for a
  transfer with no `id`, which the poller reads as *already seen* — so it never
  reached `detect_new_transfer_route`, the detector whose entire job is noticing
  funds moving over a route the customer has never used. This is G-11's defect
  verbatim, in the function G-11 didn't touch. *(b)* `dispatch._dedup_key` used
  `order_id or coin or ""`, but an `off_market_execution` detection carries
  `symbol`, not `coin`, and its `order_id` is `None` for a fill the exchange
  didn't reference — so the identity collapsed to the empty string and **every
  off-market fill on an account shared one dedup key**, suppressing the second
  symbol's alert for the whole HIGH cooldown. G-47 made that reachable by
  ensuring id-less fills get judged at all. The key is now built from whichever
  identifying fields are actually present, skipping absent ones instead of
  substituting a falsy value. Also: ClickSend returning `SUCCESS` with no
  per-message results no longer records an SMS as delivered that the provider
  never acknowledged.
- **The first slow poll no longer waits on the machine's uptime.**
  `time.monotonic()` is seconds since *boot* on Linux, and the scheduler used
  `0` to mean "this account has never had a slow poll" — so
  `now - 0 >= slow_loop_seconds` was really asking *"has this host been up for
  ten minutes?"*, a question about the machine with nothing to do with the
  account. On a long-lived box monotonic is enormous and it passed trivially,
  which is why it went unnoticed; on a freshly booted one — a CI runner, a
  rebooted VM, a container on a new instance — the **slow loop did not run at
  all for the first ten minutes of the machine's life**, delaying exactly the
  permission-change and balance-snapshot baseline you most want established on
  a host that just came up. "Never polled" is now `None` rather than `0`. Fixed
  in both the engine scheduler and `poller.run_forever`.
- **One failing alert channel no longer loses the alert entirely (#57).**
  `dispatch()` called `channel.send()` unguarded, and *before* writing the
  `alerts` row. A single raising channel therefore skipped every channel after
  it — including the ones configured as backups — wrote no `alerts` row at all,
  never reached `ContactRouter` so the customer's own email and phone went
  untried, and threw far enough to abandon the rest of that account's poll
  cycle. A flaky Telegram API meant the customer was told nothing, on any
  channel, with no record, every cycle, for as long as it lasted — the only
  outward sign being a log line about a "task failure". Each send is now
  guarded individually, and the alert is recorded whether or not anyone took
  it: a new `alerts.delivered_to` count keeps *"we detected this"* and *"we
  managed to tell someone"* as separate facts. When the count is zero — every
  channel refused it, or none was configured for that severity — an operator is
  paged CRITICAL. Not when *a* channel fails, which is merely degraded; only
  when the customer is genuinely uninformed.
- **The database no longer serialises every check, and one account is never
  checked twice at once (G-45, #49).** Two defects that get worse with exactly
  the thing the issue is about — more customers. *Contention:* one `sqlite3`
  connection was shared across the whole worker pool with a single lock around
  it, so every **read** queued behind every other read and write; eight workers
  polling eight accounts spent their time waiting for a connection rather than
  talking to exchanges. Now WAL, `synchronous = NORMAL`, a 5s busy timeout, and
  a connection per thread — the write lock remains (SQLite has one writer
  regardless) but readers no longer wait on it. The test suite dropped from
  ~70s to ~41s as a side effect. *Double-checking:* the scheduler enqueued a
  fast task per active account every `fast_loop_seconds` regardless of whether
  the previous one had finished, so a poll slower than the interval — a slow
  exchange, an open breaker, a backed-up queue — put two workers on one
  account, both reading the same baseline, both judging the same records, both
  racing to write the same `poll_state`. An in-flight claim now covers queued
  *and* running, released in a `finally` so a raising poll can't leave an
  account permanently unschedulable, and fast and slow loops are claimed
  separately so a pending slow loop never holds up the loop watching for
  withdrawals. A skipped cycle logs a warning: it's correct behaviour, but a
  customer whose polls consistently overrun is being watched less often than
  the dashboard implies.
- **`sentinel_untimestamped_records_total` is labelled by exchange, not by
  account (#55).** Every call site passed the *account label* into a metric
  label named `exchange`, so the counter that exists to say *which integration
  changed its response format* said which customer noticed instead — and grew a
  new series for every account signed, where the intended dimension has exactly
  four values. `exchange` and `account_label` are now separate arguments
  through `detect_quantity_drop`: the metric takes the exchange, the log line
  keeps naming the account. Worth doing before #52 binds a paging alarm to this
  series, which is cheap now and a coordinated change later.
- **The second contact can actually use the portal (G-21, #25).** A secondary
  alert email could be recorded and was already receiving alerts, but the
  portal was primary-only in both directions, so the second pair of eyes could
  be told something was wrong and then look nothing up. `member_accounts` — the
  single isolation boundary every portal query goes through — matched
  `alert_email` alone, so a secondary contact signed in to an empty portal; and
  the ops dashboard's "Generate portal password" card read only `alert_email`,
  so there was no way to issue them a password at all. Both contacts now get
  their own row on that card and their own credential (generating one never
  provisions the other), and the boundary matches either contact **per row** —
  being someone's secondary contact confers nothing over their other accounts.
  An empty lookup now returns nothing instead of reaching the query, since
  accounts saved through a form with the field left blank store `''` and
  `lower('') = ''` would have matched every one of them.
- **A mistyped threshold is rejected loudly instead of stopping detection
  (G-12, #16).** `config/rules.yaml` is now validated against a declared schema
  (`sentinel/rules_schema.py`) before it is allowed to take effect. Previously a
  bad value was found by whichever detector read it first — and the failure was
  far wider than the rule being edited. Writing `tolerance_pct: "0,5"` (a
  decimal comma) left the value as a string, which raised `InvalidOperation`
  from the middle of `run_fast_loop`, *after* the poll had already stamped
  `last_success_at = now`, and escaped into the worker's blanket handler in
  `engine.py` to become one log line. No detector ran, for any account, on any
  cycle — the config is shared — while `consecutive_failures` stayed 0,
  `detect_unreachable` never fired, and the dashboard showed every account
  polled successfully seconds ago. One character silently disabled all
  monitoring and left it looking healthy. Now: an invalid file at startup
  refuses to start, and an invalid edit while running keeps the last valid
  configuration in force so detection continues, reported through three
  surfaces — an ERROR naming the setting and the offending value, a red banner
  on the dashboard, and `sentinel_rules_config_valid` dropping to 0 (resting
  value 1, so it is alarmable). Misspelled keys are rejected too, with the
  intended key suggested — `tolerance_pc` is valid YAML, silently falls through
  to the default, and leaves the operator believing their edit applied.
  Constraints are derived from how each value is *used* rather than from an
  opinion about sensitivity, so retuning stays free: `max_deviation_pct: "75"`
  is accepted, `"-1"` is not, because `deviation > -1` is true of every fill
  ever executed.
- **Withdrawals with no reference ID are alerted again (G-11, #15).** A
  withdrawal the exchange returned without an `id` or `txid` was dropped in two
  independent places, and money could leave an account with nobody told.
  `record_seen_withdrawal` returned `False` for such a record, which the poller
  reads as *already seen* — so it never reached the withdrawal-created or
  unseen-address detectors at all. And `dispatch._dedup_key` falls back to the
  *coin* when evidence carries no `order_id`, so two unrelated unreferenced
  withdrawals of the same coin shared one dedup key and the second was
  suppressed for a full CRITICAL cooldown. Both now derive an identity from the
  fields that don't move as a withdrawal settles (coin, amount, destination,
  network, timestamp — deliberately not `status`, `txid` or the raw `info`
  block, all of which change as it goes from pending to success, and digesting
  those would page the customer once per state transition). New
  `sentinel/identity.py` holds that logic as pure functions so the baseline
  writer and the alert-evidence builders cannot drift apart — an identity used
  for storage but not for dedup gets a record past the first gate only to have
  it collapsed at the second.
- **Undated fills are price-checked again (G-47, #51).** A fill the exchange
  returned without a timestamp was read as `timestamp = 0`, which put it before
  every comparison window, which removed it from the only list feeding
  off-market execution and fee-anomaly checking. It was never examined — not on
  that cycle, not on any later one — while the same fill still reached the
  new-pair detector and was written into the traded-pair baseline as ordinary
  activity. A counterparty-drain trade could land on an account that looked
  fully watched. Whether a fill has been *judged* is now answered by identity
  (a new `seen_fills` table, keyed on the exchange order id or a content digest
  where there is none) rather than by its timestamp, so an undated fill is
  checked exactly once instead of never — and instead of re-alerting and
  re-inflating the fee EMA on every 45-second poll. Time still answers the
  separate question of whether the current ticker is valid evidence about a
  fill: one known to predate the last poll stays out, because its deviation
  would be the market's rather than an attacker's. Undated fills continue to be
  counted on `sentinel_untimestamped_records_total{source="fill"}` exactly once
  per pass, by the quantity-drop path that already walks them.

### Added
- **`sentinel validate-config [path]`** — checks a rules file against the
  schema and reports either "valid" or the exact problems, with no database
  opened and nothing started. Built to be run against a *server's* live
  `config/rules.yaml` before an upgrade. Exit codes are the contract for deploy
  scripts: `0` valid, `1` read but rejected, `2` could not be read — 1 and 2
  kept apart so a wrong path isn't reported as a bad config.
- **CI enforces style, coverage, dependency vulnerabilities and version
  agreement (G-46, #50).** Four gates that previously depended on memory.
  **Ruff** with a rule set chosen to catch mistakes rather than impose a house
  style — `FURB157` is off because it would rewrite `Decimal("0")` to
  `Decimal(0)` and the string form is load-bearing in the money code; `SIM118`
  is off because `"col" in row.keys()` on a `sqlite3.Row` is genuinely not
  `"col" in row` (iterating a Row yields *values*), so the "simplification"
  would turn a column-presence check into a value search. **Coverage** floored
  at the measured 78% rather than a round aspiration, since the criterion is
  that it not *silently decline*. **`pip-audit .`**, scoped to the
  dependencies this project declares rather than the whole environment — a bare
  `pip-audit` reports advisories against the runner's own pip, which nobody can
  act on from this repo and which is the fastest way to teach a team to ignore
  a security gate. **Version agreement** as a test rather than a CI-only
  script, so it fails on the machine of whoever forgot. That one found a real
  disagreement: `pyproject.toml` said `0.1.0` while the CHANGELOG's newest
  release heading was `1.0.0` — the package version was never bumped, and
  nothing anywhere would have said so. `sentinel.__version__` is now derived
  from installed metadata rather than written down a third time.
- **An operator is paged when an exchange stops stamping times (G-48, #52).**
  First alarm through the G-49 door. The threshold is *derived* rather than
  chosen: `sentinel_untimestamped_records_total` has a resting value of exactly
  zero, so any increase is anomalous and there is no sensitivity dial to guess
  at — the only judgment is how long a rise must persist to be a format change
  rather than a blip (default: two 15-minute windows, both tunable). Tracked per
  (exchange, record kind), because Bitget dropping timestamps on withdrawals and
  Binance dropping them on fills are two different investigations. Built
  **in-process** rather than as the Prometheus + Alertmanager rules the issue
  recommended — see the issue comment for the full reasoning and the follow-up
  filed to revisit it. Severity is **HIGH, not CRITICAL**, and that changed
  while it was being built: the issue was written when an undated record meant a
  *missed* alert, which G-7 and G-47 have since made untrue. An exchange
  dropping timestamps now degrades quality and adds noise rather than silently
  stopping detection.
- **Operator paging, scoped to Minas rather than to a customer (G-49, #53).**
  The gap was never that we couldn't reach ourselves — `build_channels` already
  wires our own Telegram, the team group, our SMS numbers and our email. It was
  that every route ran through an account: `dispatch()` takes an `account_id`,
  `_dedup_key` is built from it, and the `alerts` row carries it as a foreign
  key. A product-level fault had nowhere to be raised, and anything raised
  against a real account continued through `ContactRouter` and told that
  customer about our problem. New `OperatorAlerter` and an `operator_pages`
  table: `account_id` is nullable and means **about**, never **to**, so a page
  can name the account it concerns (a key that broke because our egress IP was
  blocked) while never reaching that customer. There is no customer path in the
  module at all — not a disabled one, not a conditional one — because a route
  that must never fire is safest when it doesn't exist. Own `operator_alerting`
  routing and cooldowns, separate from the customer `alerting` table, so "our
  product is broken" doesn't inherit the 15-minute cooldown chosen for a
  flapping detection on one account; defaults are wider (30/60/240/720) because
  an outage persists until someone fixes it. Pages are recorded even when no
  channel is configured — a page that couldn't be delivered must not also be a
  page that never happened — and shown in their own dashboard section, kept
  apart from customer alerts because a customer's alert history is something
  they read in the portal and something we hand to an exchange as evidence.
  **This does not mean we would know if Guardian stopped**: nothing in-process
  can report its own death, and that needs external uptime monitoring on
  separate infrastructure.
- **`sentinel_untimestamped_records_total{exchange, source}`** — a Prometheus
  counter for records an exchange sends without a usable timestamp. G-7 makes
  the detector refuse such records, which is correct but degrades quietly: if
  an exchange changes a response format, monitoring keeps looking healthy while
  explanations stop being credited. The counter's resting value is zero, so any
  sustained rise is a format change, catchable before a customer finds it. The
  near-miss probe deliberately doesn't count, so one undated record reads as
  one. `Metrics` now takes an optional `registry` for testing; production still
  uses the global one. The alarm that pages on this counter needs paging
  machinery the deployment doesn't yet have — tracked as G-48 (#52).
- **Member SMS alerts (ClickSend) made auditable and member-aware.** Texts to
  a member's phone now carry the product name, severity, alert type, affected
  account, and a member-portal login link (`SENTINEL_PORTAL_URL`) that
  truncation can never cut — only the free-text detail shrinks. Every outcome
  lands in a new `sms_deliveries` audit table linked to the stored alert id:
  the ClickSend message id on success, the provider error on failure, and
  explicit skip/suppress reasons otherwise. Duplicate texts for the same rule
  + account are suppressed within `alerting.sms_cooldown_minutes` (default 30,
  hot-reloaded) — wider than the dispatch dedup key on purpose, so a burst of
  distinct withdrawals pages the member once with full detail on
  Telegram/email. A new `sms_alerts_enabled` member preference is respected,
  with CRITICAL overriding an opt-out per
  `alerting.sms_critical_overrides_optout` (business rule, default true). A
  CRITICAL alert with no phone on file is logged and recorded instead of
  silently dropped, and the dashboard's security-posture panel now nudges the
  operator to add or verify a number. `sentinel set-contact` gains
  `--sms-alerts on|off`.
- **Member portal** (new server on `:8391`): customers sign in with their
  email + an operator-generated password and see **only their own** accounts,
  alerts, events, and security posture. Cross-member isolation is the hard
  invariant — every query is scoped to the signed-in member's account ids, and
  a drill-down on an unowned/nonexistent id returns 404 without disclosing it.
  Passwords are scrypt-hashed with a constant-time email lookup (no
  member-existence timing oracle); sessions are HMAC-signed `HttpOnly`/`Secure`/
  `SameSite=Lax` cookies (12 h); login is throttled per-email and per-IP. No
  self-registration — the operator issues each password (shown once) from a new
  **Member portal access** card on the ops account drill-down. The portal is a
  separate read-only HTTP server sharing nothing with the network-gated ops
  view. Reviewed by an adversarial security panel (isolation / auth-session /
  web-surface / correctness); the one confirmed finding (a login timing oracle)
  is fixed and regression-tested.

### Fixed
- **The ops dashboard and member portal bound every interface (G-36)**: both
  servers hardcoded `0.0.0.0`, so following the local quickstart put an
  unauthenticated *Add customer* form — and every customer's name, email and
  phone — on every interface of the machine. Only `docker-compose.yml`'s
  loopback publishes made the documented Docker path safe; a bare `sentinel
  run` was exposed. Both now default to `127.0.0.1`, configurable via
  `SENTINEL_DASHBOARD_HOST` / `SENTINEL_PORTAL_HOST`, and the dashboard logs a
  warning at startup whenever it is not on loopback. The container sets
  `0.0.0.0` explicitly, because Docker publishes a port by proxying to the
  container's interface and never to its loopback — the `127.0.0.1:` publishes
  are unchanged and remain the gate there. The metrics exporter got the same
  treatment (`SENTINEL_METRICS_HOST`): the exposition is unauthenticated and
  its styled view leaks operational shape — account counts, which rules are
  firing, which exchanges are misbehaving — which is reconnaissance for anyone
  who can reach it.
- **Old activity could explain away a fresh theft (G-7)**: the quantity-drop
  detector measured a drop between two consecutive balance snapshots — 45
  seconds apart — but was handed `withdrawals` and `fills` fetched with
  `since=None`, i.e. the exchange's full default history. A legitimate sale
  from last week was counted as an explanation for a drain from the last
  minute, and the alert never fired. The explanation window now starts at the
  previous balance snapshot (`latest_balance_snapshot_at`, not
  `last_fast_loop_at` — that is written at the top of the loop while the
  snapshot is written at the bottom, and onboarding seeds a snapshot with no
  poll state at all), widened backwards by
  `detectors.quantity_drop.explanation_grace_seconds` (default 60) to cover
  settlement lag. The detector filters again on `window_start_ms` rather than
  trusting the caller's list. A record whose timestamp is missing or
  unparseable cannot be proven in-window, so it never explains a drop and is
  logged with the exchange name — the same fail-closed principle as the
  read-only key check. Detections that a slightly wider grace would have
  suppressed log a "near miss" so the grace is tuned from evidence.
  Because the anchor is durable state rather than process state, a restart is a
  delay in detection rather than a hole in it: a drain that happens while the
  service is down is caught by the first poll after it comes back, and the
  window widens to cover the outage without crediting anything from before it.
  Locked down by three end-to-end tests (see `tests/test_integration.py`).
- **Dashboard outage (2026-07-06)**: `latest_balance_snapshot` used a
  correlated `MAX(snapshot_at)` subquery — quadratic cost on a table growing
  every fast loop. ~10h after deploy the engine held the SQLite lock nearly
  continuously and dashboard renders stalled past browser timeouts (polling
  and alerting stayed healthy). Query de-correlated, `(account_id,
  snapshot_at)` index added, and snapshots now prune to a 7-day window on the
  slow loop.
- **Bybit onboarding gate rejected every real read-only key**: Bybit's
  `permissions` entries are scopes (what the key may *read*), listed even on
  `readOnly: 1` keys, and reading balances/orders requires them. The
  defense-in-depth scan treated them as write grants, so no functional
  read-only key could pass. `readOnly == 1` remains the enforced bar; only a
  `Withdraw` scope still fails closed. Tests now mock the populated-scopes
  shape Bybit actually returns.
- **Binance false CRITICAL quantity-drop alerts**: closed-order coverage now
  includes quote coins as bases (BTC sells), pairs from the traded-pair
  baseline, and coins held on the previous poll (sold-to-zero assets), with
  held-coin symbols surviving the per-cycle budget ahead of stale hints.
- **Binance transfer blindness**: `fetch_transfers` now sweeps both directions
  of spot⇄funding/futures/margin instead of ccxt's spot→USD-M-futures default,
  cached per poll cycle.
- **First-poll false HIGH `transfer_route_never_seen` alerts**: onboarding now
  seeds pre-existing transfer routes/history (top-10 coins) into the baseline,
  the same way withdrawals and traded pairs were already seeded.
- **OKX IP-allowlist drift detection was inert**: OKX reports the allowlist as
  `ip` (singular); now normalized to the `ips` field the drift detector reads.
- **Off-market detection silently skipped on mixed batches**: a rejected batch
  ticker call (Bybit spot+derivatives) now falls back to per-symbol fetches.
- **Rate-limit accounting**: multi-call pulls (Bybit/OKX category loops,
  Binance per-symbol/per-route loops) now charge the shared egress-IP budget
  per real HTTP call via an `acquire` hook, instead of a flat 4 per loop.
- **Binance gate hardening**: unrecognized *enabled* permission flags now fail
  closed (Binance keeps adding trade-capable flags); CLI onboarding no longer
  prompts for a passphrase Binance doesn't have.
- **ccxt pinned to 4.5.63** — the version every adapter behavior was verified
  against; image rebuilds no longer float to untested releases.

### Infrastructure
- **Deploys are now a `git pull` on the server.** The VM holds a read-only
  GitHub deploy key (root-owned, `/root/.ssh` — pulls run under sudo because
  the checkout is root's), and `/opt/minas-guardian` is a real clone; it had
  been a bare `git archive` extract with no `.git`, which is why pulling was
  never possible before. The `git archive` + scp shipping path is retired.
  Update procedure: pull → build → `validate-config` (new image against the
  live config, in that order) → `up -d`. Read-only on the key is deliberate:
  this box holds decrypted customer credentials, and a compromise of it must
  not be able to push code that our own next deploy would install. Docs and
  the EC2 bootstrap script now describe the key-first setup; the bootstrap
  no longer attempts the unauthenticated clone that a private repo 404s.

First production release. Running on AWS Tokyo (ap-northeast-1), reachable
over a Tailscale-served HTTPS dashboard.

### Added
- **Account drill-down** (`/account?id=N`): per-customer detail page with the
  customer profile (name, license, primary + secondary email/phone, Telegram
  handles), account config, a plain-language **security-posture** panel (what
  keeps the account safe), the full detection catalog, and that account's own
  event + alert history. API secrets are never rendered.
- **Add-customer web form** (`/add`) behind the fail-closed read-only gate:
  a submitted trade/withdraw key is rejected and nothing is stored; the
  rejection reason is shown inline. Cross-origin POSTs blocked; secrets never
  echoed back.
- **ClickSend SMS** provider (verified against ClickSend's API) with a
  provider factory that prefers ClickSend when configured and falls back to
  Twilio — for both the global SMS channel and per-customer delivery.
- **Per-customer alert delivery** to primary + secondary email/phone, in
  addition to global severity routing. Telegram handles are stored (Bot API
  can't DM an arbitrary handle without opt-in).
- **VA/team Telegram group** channel (`telegram_team`): routes CRITICAL/HIGH
  to a shared group in addition to the personal chat.
- `sentinel set-contact` CLI to set/clear an existing account's alert contact.
- Customer schema: `customer_name`, secondary email/phone, two Telegram
  handles, `license` (with online migrations for the deployed database).

- Metrics page nav links are same-origin (`/`, `/metrics/raw`) so they work
  behind the Tailscale HTTPS proxy; added a `/metrics/raw` route serving the
  raw Prometheus exposition on the dashboard port.
- Dashboard/metrics served over one port so a single tunnel reaches both.

### Fixed
- `sentinel_accounts_active` gauge was defined but never updated (stuck at 0);
  the engine now sets it each schedule cycle.

### Infrastructure
- Migrated production from AWS us-east-1 to **ap-northeast-1 (Tokyo)** — the
  US region geo-blocks Bybit, Binance Global, and OKX Global; Tokyo reaches
  all four. Database migrated with baseline + history intact.
- Dashboard exposed via **Tailscale Serve** (HTTPS, tailnet-only) — no public
  ports, no SSH tunnel; VAs are added by tailnet invite.

## [0.4.0] — 2026-07-05

### Added
- **OKX Global** support: `perm`-field read-only gate (accept only
  `read_only`), passphrase required, instType-iterating order coverage.
- **Binance Global** support: `apiRestrictions` read-only gate (all nine
  write-implying flags must be false; a missing flag fails closed), no
  passphrase, balance-derived per-symbol order coverage.
- All four exchanges (Bitget, Bybit, OKX, Binance) selectable at onboarding.

## [0.3.0] — 2026-07-05

### Added
- **Multi-exchange architecture**: `ExchangeClient` base + per-exchange
  clients + `make_client` factory; per-exchange read-only dispatch.
- **Bybit** support with a fail-closed `readOnly`-flag gate plus a
  defense-in-depth write-permission denylist. The gate passed a 0-finding
  adversarial review.
- `exchange` / per-account alert-contact columns; passphrase made optional
  (Bybit/Binance have none).

### Changed
- Styled metrics view served at the dashboard's `/metrics` so a single tunnel
  reaches both pages (raw Prometheus stays on :9109 for scrapers).

## [0.2.0] — 2026-07-05

### Added
- Docker deployment hardening: container `cap_drop: [ALL]`,
  `no-new-privileges`, pid/memory limits, healthcheck, log rotation;
  `.dockerignore`; EC2 `user-data` bootstrap script.
- AWS-first deployment guide with SSH-tunnel dashboard access and a rehearsed
  DB-migration procedure.

### Fixed
- Packaged `schema.sql` (clean installs crash-looped without it).
- Pinned the container DB path so a host `.env` can't redirect the credentials
  DB into the disposable container filesystem.
- Findings from an adversarial deployment-asset review (permissions, sudo,
  `python`→`python3`, false "hot-reload" claims).

## [0.1.0] — 2026-07-02

Initial MVP (Phases 0–3, Bitget only).

### Added
- Phase 0 capability audit verified against live CCXT source and Bitget docs
  (signal-availability matrix, proxy designs, rate-limit math).
- Fail-closed read-only key onboarding with encrypted credential storage.
- 12-rule detection catalog with trigger/non-trigger tests per rule.
- Scheduler → queue → worker pool; per-UID + egress-IP token buckets;
  per-account circuit breakers (100-account soak).
- ccxt.pro WebSocket monitor with reconnect + dead-stream watchdog.
- Alerting: Telegram / Twilio SMS / SMTP email with severity routing, dedup +
  cooldown, unacked-CRITICAL escalation, daily digest; optional AI triage.
- Branded status dashboard + Prometheus metrics; Docker Compose; docs (threat
  model, customer runbook, deployment, data retention).

[Unreleased]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.4.0...v1.0.0
[0.4.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/releases/tag/v0.1.0
