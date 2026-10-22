# Changelog

All notable changes to Minas AI Guardian are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The internal package/CLI codename is `sentinel`; the product is **Minas AI
Guardian**.

## [Unreleased]

_Pending customer/ops items (not code): read-only Bybit key (previous had
Contract-Trade scope), OKX + Binance keys, Gmail SMTP credentials, ClickSend
or Twilio credentials, terminate the retired us-east-1 instance, install the
#81 restart timer on the host (docs/deployment.md "Acting on an unhealthy
container")._

### Changed detection behaviour

- **A tamper accusation is never sent on the first eligible failure while
  another account is failing authentication.** The tamper rule fires at two
  consecutive refusals and a host-level incident needs three accounts, so
  when refusals spanned two sweeps the first account could be accused before
  the cluster formed. Now, when an account reaches the tamper threshold
  while any other account is in a run of authentication failures, the
  accusation waits one more check; if the cluster has formed by then it is
  held for good, and if not it goes out. A genuinely isolated tamper with
  no other account failing is accused exactly as before. Measured cost: one
  poll interval (45 seconds plus up to 10 of jitter; two intervals if the
  account's own poll overran and skipped a cycle), and only while some
  other account is failing. The host-level page now goes out when a member
  reaches the tamper threshold, one sweep after the cluster started, so it
  names everyone who failed in that sweep.

- **During a host-level incident the customer is told the cause is ours and
  asked to do nothing.** The coverage notice's own advice - check the key,
  put a new read-only key in place - is right when the key is the problem
  and wrong when our host is: a customer who follows it rotates a working
  credential to a new key with no allowlist entry either, so they stay dark
  and the credential has changed under us. Advice is now a lookup over the
  rule AND the established cause (`CAUSE_ADVICE` in sentinel/alerts/base.py;
  the cause rides on the alert's evidence), so the alert email, the operator
  format, the escalation copy and the incident page all say the same thing.
  A member of a host-level auth incident now receives: "Your account is NOT
  being watched. The exchange is refusing our read-only key because of a
  change on our side - not because of anything you did, and not because of
  anything wrong with your key. Nothing is needed from you: leave your key
  exactly as it is. Minas is putting it right and will tell you when the
  checks succeed again." An isolated refusal keeps the rule's own advice.

- **A simultaneous authentication failure across accounts is one host-level
  finding, not N tamper accusations.** On 2026-09-21 nine accounts on four
  exchanges, sharing nothing but this host, were refused in the same second
  after the host's public address changed, and nine customers were told
  hourly that their monitoring key may have been tampered with; one was told
  seventeen times. Every accusation was wrong, and the evidence was in the
  refusal: Bitget answers `Invalid IP,Current request IP 57.181.51.14`. Now
  the product keeps the exchange's words for a refused key on `poll_state`
  (`last_auth_error`, with the start of the current run of failures), reads
  the requesting address out of them where the exchange names it (Bitget
  does; Bybit does not), and when `operator_alerting.fleet_coverage_min_accounts`
  or more accounts START failing authentication within
  `fleet_coverage_window_seconds` of each other - the same two settings as
  the coverage incident - the tamper alert is held for those members (the
  detection is recorded as an event; no customer is told) and Minas is paged
  ONCE, naming every account, the exchanges, the shared cause and the
  observed address, and any customer accused before the cluster formed. The
  later coverage-fleet page for the same accounts is recorded as paged
  rather than sent. An isolated refusal still raises the per-account tamper
  alert exactly as before, and each customer's "your monitoring stopped"
  notice still goes out. No outbound call learns the address: it is read
  from the refusal the product already holds.

- **Every coverage alert repeats on the daily reminder cadence, not on its
  severity's cooldown.** On 2026-09-21 nine keys were refused by address in
  one minute. `account_unreachable` reminded daily as designed; its sibling
  `monitoring_key_tamper_suspected` had never been given the cadence,
  because it was applied per rule, and re-fired every 62 minutes with a
  customer email each time - 145 repeats in sixteen hours, 85 emails to
  one address in a day. The cadence is now a property of the coverage
  class (`COVERAGE_RULES`), so the next coverage rule cannot miss it, and
  a test enumerates the set.

- **A fleet-wide coverage failure is one event, not one per account.** On
  2026-09-21 the instance's public address changed and nine accounts' keys,
  allowlisted to the old address on two exchanges, were refused in the same
  minute; the product treated that as nine facts - nine operator pages an
  hour, nine customers emailed hourly. Now, when
  `operator_alerting.fleet_coverage_min_accounts` (default 3) or more active
  accounts enter coverage failure within
  `operator_alerting.fleet_coverage_window_seconds` (default 600) of each
  other, Minas receives ONE page naming all of them and the count, each
  customer is told once (their first coverage alert goes out exactly as
  before), and the per-account alarm and the daily repeats are held for the
  members until their checks succeed again. Simultaneity is the whole
  signal: nothing reads error text or infers a cause; the operator reads
  the page and decides. The page is sent once per incident, whatever the
  pager's cooldown.

- **A refused customer alert email pages Minas on a channel that is not
  email.** From 2026-09-21 23:41 the SMTP provider answered every send with
  "550 You have reached your daily email sending quota" - operator pages
  first, customer alert emails from 10:48 the next morning - and nothing
  said so: the alarm had consumed the capacity it needed to alarm. The
  existing no-delivery page did not fire because it counts the alert's
  operator legs, where Telegram kept delivering; the customer's email leg
  was recorded as failed and paged nobody. Now, when the provider REFUSES
  (an SMTP 4xx/5xx reply - a quota, a hard bounce, a rejected sender; a
  connection error is still one log line) a customer-audience email for a
  CRITICAL or a coverage alert, Minas is paged at CRITICAL with the
  provider's words in the evidence, once per refusal code per cooldown.
  The page's own email leg fails the same way, which is why the CRITICAL
  operator route carries Telegram and SMS. Remaining capacity is not
  estimated: SMTP exposes none, and the evidence says so rather than
  inventing a number.

- **A withdrawal to a destination this account has withdrawn to before is
  a receipt, not a CRITICAL.** Measured 2026-09-08: 60 of the 119
  unacknowledged withdrawal CRITICALs were to addresses the same account
  had withdrawn to before, on 18 account-address pairs - the customer's
  own routine, paged as theft. `withdrawal_initiated` now fires at MEDIUM
  when the destination is in the account's WITHDRAWAL history (never a
  deposit address: someone sending from there proves nothing about where
  the customer sends), judged before the withdrawal itself is recorded. A
  record with no address, or an address with no withdrawal history, stays
  CRITICAL. The receipt is never pushed - no SMS, no Telegram - and no
  email of its own when the member's digest will carry it; the member
  digest lists each one (label and time, no amounts, no addresses), the
  ops digest lists it as before, and a member whose digest is off or an
  address with no portal login gets a plain email instead. MEDIUM never
  escalates. `withdrawal_to_unseen_address`, `withdrawal_burst` and every
  other rule are untouched.

### Added

- **Sign-in for the iOS and Android apps (decision 21c).** The member
  portal now serves a JSON API under `/api/v1/` for the customer apps.
  This release carries its sign-in: the web's email, password and TOTP,
  counted on the portal's own login throttle, and only for a member with
  two-factor on - the app is refused with "turn on two-factor" otherwise.
  The app holds a 15-minute access token and a refresh token that is
  replaced on every use; an app left unopened for 30 days signs out. A
  replaced refresh token presented again ends that sign-in at once, and a
  password change signs every phone out, as it already signed out every
  browser. Every app sign-in emails the member and appears on their
  activity page with the device's name, as does every sign-out.
  `GET /api/v1/security` lists the live sign-ins; a member can end any of
  their own (`DELETE /api/v1/devices/{id}`) and never anyone else's.
  Nothing changes for the web portal or for alerting. New table:
  `api_devices` (additive; erased with the member, listed in the data
  export without its token hashes).

- **What the apps read (decision 21d).** `GET /api/v1/accounts` (each
  account, whether it is being watched and why not, and what this login
  may change on it), `/alerts` (newest first; filter by `severity`,
  `account`, `from`, `to`; up to 200 per page with `limit` and `offset`),
  `/alerts/{id}` (the incident record), `/activity` and `/limits`. Each
  answers from the portal's own query for the same page: the incident
  page, the limits page's sentences and sensitivity table, and the
  activity page's wording are now data that the page and the app both
  render, so the two cannot disagree. Every read is scoped to the
  member's own accounts, and another member's alert is a 404
  indistinguishable from one that does not exist. A filter the API cannot
  understand is refused, never ignored. Quantities stay strings, never
  floats.

- **Acknowledging from the app, and signing a phone out from the browser
  (decisions 21c, 21d).** `POST /api/v1/alerts/{id}/acknowledge` runs
  through the portal's own scoped writer, so it stops a CRITICAL's
  escalation exactly as the web button does, any contact on the account
  may use it (#25), and the first acknowledgement stands. The portal's
  Security page lists every phone signed in to the login, with a button
  to sign each one out; it appears only once a phone has signed in.

- **The app API's contract, `api/openapi.yaml` (decision 21f).** The one
  description the server and the apps share; the apps' client is to be
  generated from it. The server is held to it: the routes must be exactly
  the served ones, and every answer the server gives on every route,
  success and error, must match the file's schema for its status, with
  closed objects, so an undocumented field fails the build as surely as
  a missing one.

### Changed

- **The member portal wears the Minas logo.** It replaces the stroked
  infinity mark in the top bar and on the sign-in page. The portal now
  has a browser-tab icon and an iPhone home-screen icon; `/favicon.ico`
  used to answer with nothing. The logo files are served by the portal
  itself (`/brand/...`, cached for a day), so no page makes an external
  request. Every size is derived from one master by
  `scripts/brand-assets.py`, which also produced the app-store icons in
  `design/brand/`. The ops dashboard stays unbranded on purpose. Nothing
  changes for alerting.

- **The unpinned method decisions are being retired in named batches, the
  floor climbing behind each (#104).** Batch A, the escalator: the stale
  edge in both comparisons, the live-window edge and its two skips, a
  customer with a phone and no email, the secondary-contact failure log
  line, and the audience of a backlog record - nine decisions, six tests,
  eight ledger rows gone; 510 of 535, floor 92 to 93. Batches B and C, the
  contact router and the operator pager with the watch alarms: the two
  skips in the preference loops, the SMS record's guard and its leg, a
  router with no database, the SMS cooldown edge, the pager's cooldown
  edge and label and its undeliverable log line, the alarm's two edges and
  its skips, and the fleet member skip - fifteen decisions, thirteen
  tests, twelve rows gone and one argued equivalent; 528 of 535, floor 93
  to 96. Batch D, the config loader and ClickSend: the valid-again
  warning only after a rejection, a change with no recorder, a receipt
  that falls back to the requested number, and fewer results than numbers -
  five tests, four rows gone and one argued equivalent. The `unpinned`
  kind is empty: every survivor the gate accepts is an argued equivalent,
  seven rows, and the population is the honest one.

- **The mutation gate measures class methods, and its floor is the honest
  number (#104).** The classifier matched only column-zero functions, so no
  method - the contact router, the escalator, the operator pager, the watch
  alarms, the config loader - had ever been in its population; "341
  decision-point mutants" was read as the whole scope. The population is now
  535 (197 in methods; one dead method deleted). Measured honestly the
  score is 93%, so the floor was reset by hand from 97 to 92 - the one
  time it has ever gone down, by the owner's decision, in preference to
  pinning thirty gaps to protect a number over a population that was never
  honest. The ratchet is unchanged. The 29 surviving method decisions are
  in the ledger under a new kind, `unpinned`, each naming its batch (A
  escalation, B contact, C operator and watch alarms, D config and
  ClickSend); those rows are the count that must come down, and the weekly
  report carries it. The Telegram link worker's loop, name lookup and poll
  gain tests; they were reached by nothing.

- **A survivor's fingerprint carries its function (#104, closed).** The
  ledger keyed an accepted survivor by file plus the changed lines, so the
  same `continue` to `break` in two methods of one file was one row, and
  pinning one of the pair left the row covering the other - hit four times
  while the #104 batches were retired. The mutant's function (its name
  without the number, read from mutmut's own header) is in the key; the
  seven accepted rows were re-keyed, and a diff without a name fails
  closed.

- **The mutation gate's test selection is complete by construction, and an
  unjudged mutant is named.** The gate runs a fixed list of test files, and
  twice in one day a new test file landed outside it - its mutants were
  either unjudged or survived unseen until someone noticed. Now every file
  under `tests/` must be in the selection or in
  `scripts/mutation-selection-excluded.txt` with a written reason, and a
  test fails the build on one that is neither. The 39 files that import a
  mutated module and were outside the selection are in it (80 files
  selected, 60 excluded with reasons); the mutants copy carries the repo
  files those tests read (`also_copy`), which is what had kept them out.
  When a selected mutant reaches no selected test, the gate now prints its
  name and the marker's meaning before refusing the run on the count, as it
  already did. Measured: the score over the existing population is 98%
  with either selection.

- **The watched/unwatched decision lives in one place and reads every
  failure counter.** The dashboard's watch cell, the portal's member status
  and the engine's failed-poll check each read both counters on their own;
  one summary line, the dashboard's headline health percentage, read only
  `consecutive_failures`. All of them now go through `sentinel/watching.py`,
  which enumerates every failure counter on poll_state, and a test compares
  that list to the table's actual columns - a counter added to the schema
  and consulted by nothing fails the build. A deduplication plus a
  completeness guard; the per-account decisions were not wrong.

### Fixed

- **The activity page names each connection by the member's own name
  (#8),** as every other page already did. Its query never fetched the
  name, so it showed the internal slug.

- **The deployment guide described a system that is not running.** It said
  "serve, never funnel" while the member portal has been published to the
  internet on Tailscale Funnel, deliberately, since August 2026 - recorded
  nowhere until now (decision 20, with what it means for the threat model).
  And the Elastic IP that every customer key is allowlisted to,
  `57.181.51.14` on i-0faad6c4d361b127d, appeared in no document: it is now
  beside the onboarding steps, with the rule that a new key's allowlist
  must carry it, the fact that releasing it unmonitors every allowlisted
  account (nine on 2026-09-21), and the order of operations for moving it
  on purpose.

- **The liveness gate reports email DEAD when its last sends were refused,
  not only when SMTP is unconfigured.** For twelve hours of quota refusals
  the gate read "SMTP set; last accepted email <yesterday>" and called the
  channel live. It now reads the newest five email delivery records and
  declares the channel dead when every one of them failed, quoting the
  provider's refusal; one accepted send among them keeps it live.

- **The liveness gate no longer reports three settled decisions as dead
  mechanisms.** `config/liveness-holds.yaml` held only the drill account;
  the SMS provider, the dead-man's switch and per-account thresholds were
  decided on hold at the same time and never written, so every gate run
  since 2026-08-31 - and the operator page after the v1.12.0 deploy -
  named four dead mechanisms when one (the database backup) was. The
  three holds are written with their reasons, and a test now evaluates
  every holdable entry against a fresh deployment's facts and fails on
  one that is neither held nor live, so a hold decided and not written
  cannot pass again.

## [1.12.0] — 2026-09-08

### Changed — on upgrade

- **Re-enable `minas-restart-on-unhealthy.timer` on upgrade**:
  `sudo systemctl enable --now minas-restart-on-unhealthy.timer`. It was
  stopped by hand on 2026-08-30 when the healthcheck went red the moment
  the first operator existed, and is disabled as of 2026-09-08; this
  release's `/healthz` fix (the probe no longer rests on the dashboard
  being unauthenticated, and asserts the engine is alive) is what makes
  switching it back on safe. Until this release is deployed the container
  reads unhealthy on purpose, so the timer must stay off.

- **Re-enable `minas-liveness.timer` on upgrade**:
  `sudo systemctl enable --now minas-liveness.timer`. It was stopped by
  hand on 2026-09-02 because every hourly run paged HIGH about the same
  seven mechanisms, and each page escalated, and is disabled as of
  2026-09-08; the change-based paging in this release (below) is what
  makes switching it back on safe.

- **Clear the restart-cap and liveness notices from `/etc/motd` on
  upgrade.** The #81 flap evidence from August and the liveness gate's
  marker stay by design until a human removes them; on upgrade run
  `sudo sed -i '/MINAS RESTART CAP/d;/MINAS LIVENESS/d' /etc/motd` (or
  edit by hand) so the banner reflects this deploy, not those incidents.

### Changed detection behaviour

**The detectors and severities themselves are unchanged.** Changes to who
hears what, and what they are told:

- **An escalation now reads as the alert it escalates.** The customer's
  copy of an unacknowledged alert kept the escalator's internal prefix
  and a borrowed CRITICAL, so a lost API key escalated as "Urgent:
  Unusual activity was detected on this account" - 42 times to one
  customer on 2026-08-30. The subject, headline and advice are now the
  original rule's, the severity is the alert's own ("Please check:
  Contact with this account has been lost" for a HIGH coverage alert),
  and the body says in one sentence that the alert has not been
  acknowledged and is being sent again. What Minas receives is
  unchanged.

- **A stale alert no longer escalates to a customer; it is reported to
  operators as a backlog.** On 2026-08-30 a routing change made coverage
  alerts escalate, and seven seconds after the switchover the escalator
  found 41 five-week-old unacknowledged alerts on one account and emailed
  the customer 42 times in eight minutes. An unacknowledged alert that
  has NEVER escalated and is older than `escalation_stale_after_hours`
  (default 4: four missed 60-minute windows, which only a stopped
  scheduler or a routing change produces) is now a backlog: one operator
  page names the count and the accounts, each alert is recorded as
  suppressed for its contacts, and nobody is emailed. Reported once,
  and never again after a restart.

- **One customer cannot receive more than a bounded number of escalation
  emails in a window.** Whatever the cause, an account's primary contact
  receives at most `escalation_customer_burst_max` (default 3) escalation
  emails per `escalation_customer_burst_window_minutes` (default 60),
  then ONE summary naming how many alerts remain unacknowledged; the rest
  are recorded as suppressed. The emergency contact is bounded by the
  same count. Minas's own channels are not bounded - the bound is on the
  customer's inbox, not on what Minas hears.

- **The liveness gate pages on a change, not on a state.** From
  2026-08-31 the hourly timer paged HIGH on every run about the same seven
  mechanisms - all off for days, none able to change without a human act -
  and each page escalated at 60 unacknowledged minutes, so the owner
  received a message every ~30 minutes on two channels until the timer was
  stopped by hand. Now a mechanism that NEWLY fails pages HIGH once, naming
  it; a failing set that has not changed does not page again, however long
  it stays; a mechanism coming back (or being declared held in
  `config/liveness-holds.yaml`) is reported once at MEDIUM, which reaches
  the ops Telegram and escalates nothing; a held mechanism never pages at
  all. Journald and `/etc/motd` already worked this way.

- **A liveness check run by hand no longer pages the operators.** On
  2026-08-31 a person typing `sentinel liveness` at a prompt raised
  operator page #217 for a report they were already reading. The bare
  command now prints its report and exits non-zero without paging; the
  systemd timer, its one automated caller, passes `--page` and pages
  exactly as before. Nothing about what the timer detects or how loudly
  it says so has changed.

- **The "what to do next" steps appear only on alerts they apply to.**
  The runbook - revoke API keys you do not recognise, check the exchange's
  withdrawal history, ask the exchange to hold a withdrawal - was attached
  to the message format, so every rule carried it: on 2026-08-31 an
  operator page about a missing backup directory and an unset heartbeat
  URL arrived under those three steps. Told to a customer, that would have
  had them revoking exchange keys and phoning exchange support because
  Minas had not installed a cron job. Advice is now a property of the
  rule (`RULE_ADVICE` in `sentinel/alerts/base.py`): the withdrawal,
  balance, transfer, trading, permission, two-factor and key-tamper
  detections carry the steps as before, on the email and the incident
  page. The "contact with this account has been lost" alert now carries
  advice of its own instead: what was observed (the read-only checks are
  failing, the account is unwatched until they succeed; nothing in the
  record says anything happened to the funds), then check the key at the
  exchange, replace it yourself from the portal, or ask us - never the
  withdrawal steps, never "if you revoked every key". The "withdrawal
  record had no destination" notice points at the exchange's own record.
  Every account notice, every operator page and every drill carry no
  advice block at all - not a generic one. A test enumerates every rule
  in the source and fails on one that has neither advice nor an explicit
  "none".

- **Operator pages and the ops channels no longer carry the customer's
  copy.** The same page told MINAS-OPS to "sign in to Minas AI Guardian
  and open the account to put a new read-only key in place yourself; ask
  us if anything there does not work" - the customer's note, embedded in
  the operator format. The operator format (ops email, ops Telegram,
  every operator page) now carries rule, confidence, evidence and, only
  where the rule carries advice, one operator-voiced line saying what the
  customer was advised. The guard in `tests/test_customer_channel_guard.py`
  now holds both directions: an operator-audience body carries none of
  the customer's sentences, and a customer-audience body carries no rule
  name, confidence or evidence - including an
  operator-page-shaped message pushed through a customer channel.

### Changed

- **The weekly drill is off pending the drill-account decision (#37).**
  `sentinel onboard --drill` verifies a real exchange key, so the drill
  account is an owner decision, not a command. `drills.enabled: false`
  and the liveness gate's matching hold travel in this one commit; with
  neither, the schedule would page HIGH ("no drill account") once per
  interval. On-demand drills still work. Flip both back together once
  the account exists.

- **The liveness host script talks to a person who runs it.**
  `scripts/host/liveness.sh` wrote only to journald, so someone running
  it by hand saw the same silence whether everything was live or the
  script was broken. When stdout is a terminal the full report is
  printed; from the timer stdout stays empty and journald keeps the
  record exactly as before.

### Fixed

- **The container healthcheck now asserts the engine is alive, not that
  a web server answered.** It fetched the ops dashboard root
  unauthenticated and passed only while the empty-operator-table waiver
  held the door open - creating the first operator turned the container
  unhealthy - and it read healthy straight through a wedged poller. The
  engine now stamps the end of every completed scheduler cycle and
  `GET /healthz` (answered before the operator gate by design; two words,
  no data, every other route still 401) returns 200 only while that stamp
  is younger than three fast-loop cycles, 503 otherwise. The compose
  healthcheck probes `/healthz`, so a wedged engine turns the container
  unhealthy in about four minutes instead of never.

- **A recreated container no longer clears the restart cap.** On
  2026-08-30 the deploy's new container reported `starting` two minutes
  after the cap, the restart script read that as recovery, cleared its
  state, and the flap got a second restart the design refuses. The state
  now remembers which container it capped: only a `healthy` verdict
  clears the ladder, `starting` and `unknown` decide nothing, and a
  changed container id while capped is logged as a replacement and keeps
  the cap until the replacement actually reports healthy.

- **The liveness gate reports every change in the failing set, not only
  the moment it turns red.** Red-to-red was silent: on 2026-08-31 the
  dead set went from seven mechanisms to six - the gate's own timer
  switching on - and journald recorded only the facts line. The host
  script's state file now holds the failing set itself; anything newly
  dead is logged at `err`, anything newly live at `info`, the single
  `/etc/motd` marker is refreshed with the new count, and an unchanged
  red set stays quiet.

- **Release notes no longer lose a space between two adjacent code
  spans.** The composer's line-wrap rejoin matched a wrap between two
  closed backtick spans and ate the space (v1.11.1's published body had
  "`account_unreachable`, `monitoring_key_tamper_suspected`" run
  together; corrected in place). The rule now joins a wrap only inside an
  open span.

## [1.11.1] — 2026-08-29

### Changed detection behaviour

**The detectors and severities themselves are unchanged.** One change to
who hears what, and how often:

- **A broken key no longer pages everyone every day.** Measured against
  1.11.0 as tagged: a key that broke and stayed broken cost the customer
  2 emails a day, the emergency contact a note a day, and Minas 9
  messages a day (3 direct, 3 escalation, 3 page) for as long as it
  stayed broken - one customer's did for 31 days. Now the FIRST coverage
  alert of an incident (`account_unreachable`,
  `monitoring_key_tamper_suspected`) is exactly as loud as 1.11.0 made
  it: every direct channel, escalation after 15 minutes, the emergency
  contact, an operator page. Every re-fire inside the next 30 days is a
  REPEAT of that incident: it reaches the customer's own email once a
  day (the reminder that silence is not safety) and nobody else - no
  global channel, no SMS, no escalation, no emergency-contact note, no
  page. After 30 days a still-broken key is a new incident and is loud
  again. In the first 24 hours a customer now receives 2 emails (the
  alert and its escalation), the emergency contact 1 note, Minas 9
  messages; on every following day the customer 1 email and nobody
  else anything.

### Fixed

- **Repeats of a coverage alert point at the alert they repeat**
  (`alerts.repeat_of`, additive migration), never escalate, never page
  "delivered to nobody" for reaching no global channel on purpose.

## [1.11.0] — 2026-08-29

### Changed — on upgrade

**Three things the operator does, in this order, before or during the
deploy.** (By CONTRIBUTING's letter the first two are MAJOR-shaped - an
act before upgrading, a one-way migration; the owner named this release
1.11.0 on 2026-08-29 knowing that. The acts are the same either way.)

1. **Create the first operator account before the container switches over**
   - `sudo docker compose exec -T sentinel sentinel operator add <username>`
   (works with an empty table and no session; on this build the person
   then enrols a second factor at their first dashboard sign-in). With
   zero operators the ops dashboard admits NOBODY. That is intended.
2. **The database migration is one-way.** On first start the old
   `sms_deliveries` and `contact_deliveries` tables are copied row for row
   into the new `deliveries` table and become views. A rollback to 1.10.0
   afterwards still reads everything, but its SMS bookkeeping writes into
   what is now a view and is refused (logged, never fatal). Take the usual
   pre-deploy backup; do not plan on rolling back.
3. **Rename `retention.sms_deliveries_days` to `retention.deliveries_days`
   in `rules.yaml` at your convenience.** On upgrade the old name is
   deprecated but still accepted, with a warning naming the new one;
   setting both is rejected. Nothing breaks if you leave it.
4. **Install the liveness timer on the host after deploying.** On upgrade
   run `sudo cp scripts/host/minas-liveness.{service,timer}
   /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl
   enable --now minas-liveness.timer`; until then `sentinel liveness` runs
   only by hand and reports itself dead.

- **The ops dashboard no longer opens itself when no operator exists.**
  The empty-operator-table waiver ("sign-in is required only once an
  operator exists") was fail-open by code path: production ran six weeks
  with zero operators and therefore no dashboard sign-in at all (#103).
  Now no operators means no dashboard access, full stop. **On upgrade:**
  deploying this to a production with zero operators makes the dashboard
  unreachable until the first operator is created, which is intended -
  create the operator BEFORE or DURING the deploy (`sentinel operator add
  <username>` from the host; it needs no session and no existing
  operator), not after. The login page says exactly this when the table
  is empty.

### Changed detection behaviour

**The detectors and severities themselves are unchanged.** Which alerts
fire, and at what level, is exactly as in 1.10.0. Who hears about them,
and when, changes in three ways:

- **Quiet hours begin holding non-critical alerts - off by default.** A
  member who switches on quiet hours (#24, the portal's security page)
  has HIGH/MEDIUM/LOW alerts held during those hours and released in
  their next digest; CRITICAL and coverage alerts are never held, and
  email for CRITICAL cannot be switched off. Nobody has quiet hours on
  until they choose them.
- **The weekly fire drill begins running, and pages when it fails.**
  Every seven days a simulated theft runs against the dedicated drill
  account (#37, decision 2) through detection, alerting and delivery; a
  failed drill - or no drill account to run against - pages Minas HIGH.
  Customers do not receive drill traffic. On upgrade: decide the drill -
  onboard a drill account (`sentinel onboard ... --drill`) or set
  `drills.enabled: false`, or it pages HIGH once per interval.

- **An unreachable account now reaches you like a CRITICAL, whatever its
  level.** `account_unreachable` and `monitoring_key_tamper_suspected`
  mean the product has gone blind on a customer. They are delivered on
  the CRITICAL channel set, cannot be held by a member's quiet hours or
  muted by their channel grid, and **escalate after 15 minutes
  unacknowledged (the CRITICAL window, not HIGH's 60)** to the emergency
  contact with an operator page, worded "coverage alert". The severity
  shown is unchanged. Before this, a MEDIUM unreachable went only to a
  digest - which on production has never run - and a HIGH one waited an
  hour.
- **Every alert leg is now recorded per recipient** (who, which channel,
  accepted / failed / skipped and why). Nothing about delivery itself
  changes; what changes is that "was this customer told" now has an
  answer on the dashboard and in the database.

### Added

- **A member can name each connection (#8).** "Binance - savings" instead
  of `binance-jaypee-2`, set from the account page by the primary contact
  and used everywhere a person reads the account: alert subject and body,
  the digest, the portal, the incident page, the CSV export, the note to
  the emergency contact. The operator slug stays the internal identity -
  operators, the operator-format alert, the CLI and every lookup keep it;
  the name is display only and is never a key. Renames appear on the
  member's activity page.

- **The liveness gate (#103 item 4).** Every mechanism that needs a human
  switch-on step is declared in `sentinel/liveness.py` with the act that
  switches it on and the signal that proves it is running: operator
  accounts, the member digest (cron line AND a digest sent in 48 h), a
  backup newer than 48 h, the drill account, an SMS provider, per-account
  thresholds, the dead-man's switch, the restart timer, Telegram, SMTP,
  and the gate's own timer. CI proves the declaration is complete (a
  `.env.example` variable, an `enabled:` flag or a host timer with no
  entry fails the build); `sentinel liveness` on production proves
  liveness and exits non-zero on any mechanism with no evidence of
  running - `unknown` counts. Deliberate holds live in
  `config/liveness-holds.yaml` with a reason and report as `held`.
  `scripts/host/liveness.sh` + `minas-liveness.timer` run it hourly,
  gather the three host-only facts, and go loud once per red transition
  (journald + /etc/motd). The verdict is guaranteed to land where no
  checked mechanism can hide it - the `operator_pages` row on the
  dashboard, journald and /etc/motd from the host script, the timer's
  failed unit - and also, best effort, on the operator page's Telegram and
  email legs (which share their bot and SMTP with the mechanisms being
  judged, and are declared as such); never through the digest or SMS. First run on production
  (read-only, 2026-08-28): 8 of 11 mechanisms DEAD, its own timer among them.

### Fixed

- **A renamed config key no longer rejects the file (2026-08-20's shape).**
  `retention.sms_deliveries_days` is still accepted, with a warning naming
  `retention.deliveries_days`, for at least one release; setting both is
  an error rather than a silent winner. The deploy no longer depends on
  editing rules.yaml in the same minute as the switchover.

- **An unreachable account reaches a human directly, whatever its level
  (#103 item 3).** `account_unreachable` and `monitoring_key_tamper_suspected` mean the
  product has gone blind on a customer. 41 of them on
  production were routed, by severity, to a digest that had never run.
  Routing is now decided by MEANING (`sentinel/alerts/meaning.py`): a
  coverage alert is delivered on the CRITICAL channel set, is judged as a
  CRITICAL by the member's preferences (never held for quiet hours, email
  is the floor), and escalates at the CRITICAL window to the emergency
  contact with an operator page. The severity is not changed - a HIGH
  stays a HIGH - so severity keeps meaning "how urgent".

- **Every delivery is recorded per recipient per channel (#103 item 2).**
  "Was this customer actually told" was unanswerable for every alert in
  the database: `alerts.delivered_to` counted the global channels only and
  the per-member email/Telegram/SMS legs left nothing behind. A new
  `deliveries` table - `sms_deliveries`' shape generalised with a channel,
  a recipient, an audience (customer / secondary / operator) and a kind
  (alert / escalation / notice / digest / drill / operator page) - now
  takes one row per leg: accepted, failed (with the transport's words),
  skipped (with the reason: no address, no provider, SMTP unset,
  preference off, snoozed) or held. `sms_deliveries` and
  `contact_deliveries` live on as views over it, so every existing reader
  keeps working; `delivered_to` is unchanged. **On upgrade:** the old
  tables are migrated row for row on first open (idempotent);
  `retention.sms_deliveries_days` is now `retention.deliveries_days`.

- **An emergency contact can no longer change anything (#25).** A secondary
  contact signed in to the portal could replace the monitoring key, delete
  the member's data, change alert preferences, pause monitoring, snooze
  and mark "that was me" - every mutating route, because the portal made
  no primary/secondary distinction and the test that said otherwise posted
  to paths that never existed. Decided role (decision 1): a non-primary may
  read and may acknowledge, nothing else. The portal now classifies every
  POST route as primary-only or non-primary-allowed (an allowlist, complete
  by test), judges account-scoped routes against the account named, and
  refuses with a plain page - same page whether or not the account exists.
  Acknowledgements by a secondary were, and are, recorded under the
  secondary's own address.

- **The mutation gate regenerates only what changed, and proves it.** The
  from-scratch rebuild cost 2h57m and 6h34m for eighteen changed lines;
  a changed file regenerates in seconds and the test map in under a
  minute. When sources change, the gate now regenerates only the files the
  manifest names, re-collects the test map, and validates every recorded
  per-function hash against the current source before trusting anything —
  a failed or unmappable validation rebuilds from scratch, never passes.
  Proven in the hollow pass's own shape. `sentinel.config` joins the
  mutation scope (with `settings_diff`'s tests in the selection) so
  `env_setting`, the chokepoint every channel decision routes through, is
  measured on every run. CONTRIBUTING now records that #93's release
  predicate is sufficient only together with the survivor ledger.
- **A half-filled Telegram configuration no longer looks configured**
  (#101). A whitespace `TELEGRAM_BOT_TOKEN` built the ops channel (and the
  team channel, and started the link worker) on a garbage token, so every
  Bot API call failed; on the member path the one-shot "TELEGRAM_BOT_TOKEN
  is not set" warning never fired — one failed post per connected chat per
  alert instead. Every Telegram read now goes through `env_setting`: blank
  and whitespace are unset, values are stripped, and the warning fires on
  a half-filled env, which is what the test pins. A correctly configured
  Telegram is built from byte-identical inputs.
- **A half-filled SMTP configuration no longer looks configured** (#100).
  Same defect as the SMS fix, one channel over: a whitespace `SMTP_HOST` or
  `SMTP_SENDER` built an email channel on garbage, every customer email
  failed one log line at a time, and the one-shot "SMTP is not configured"
  warning — and its operator page — never fired. Blank and whitespace now
  mean unset for every `SMTP_*` read (router and ops channel alike), values
  are stripped, and the warning fires on a half-filled env; that is what the
  test pins. The helper is now one shared `env_setting` in `sentinel/config`
  (the SMS one folded in), and the old non-stripping `cli._env` is renamed
  `_env_raw` and says so, so two opposite contracts no longer share a name.
  A correctly configured SMTP is built from byte-identical inputs.
- **A half-filled SMS configuration no longer looks configured.** A
  whitespace-only or space-padded `CLICKSEND_*` / `TWILIO_*` value used to
  read as present: a channel was built with garbage credentials, every
  text failed, and the "no SMS provider is configured" warning never
  fired because a provider apparently was. Blank and whitespace now mean
  unset for all six SMS variables, values are stripped so a stray space
  never reaches the provider, and a blank ClickSend pair falls through to
  a real Twilio one. The warning's firing on a half-filled env is what the
  test pins. SMTP, Telegram and the portal URL are deliberately untouched
  in this change.

## [1.10.0] — 2026-08-25

### Changed detection behaviour

Which alerts *fire* is untouched. Who hears about them changes in four
deliberate ways:

- **Acknowledging one alert now closes its siblings** (#99): an
  unacknowledged alert on the same account carrying the same exchange
  order reference within 24 hours is acknowledged alongside, so its
  escalation ladder stops and the emergency contact is no longer paged
  about a withdrawal someone already handled. Before, the second alert
  of a pair escalated on its own. Alerts with no order reference, and
  other accounts' alerts, are never touched.
- **The Guardian drills itself weekly, on its own account** (#37,
  decision 2). Every week a simulated theft is injected on the dedicated
  Minas drill account and travels the whole path — detection, every
  channel, the account's contacts — labelled DRILL on every surface. No
  customer receives a scheduled drill; a member who presses "Test my
  alerts" receives one on their own phone, once an hour at most,
  whatever their quiet hours say. A drill that reaches nobody pages Minas
  as an outage; so does a schedule with no drill account to run against.
- **A member can choose channels per level and quiet hours** (#24,
  decision 4). Non-critical alerts inside a member's quiet hours are held
  and delivered in their next digest instead of at once; a channel a
  member turned off for a level is skipped for that member. Critical
  alerts are never held and always reach the member by email. Escalations
  and account notices ignore preferences. A member who sets nothing is
  reached exactly as before.
- **A member can stop monitoring on their account, 24 hours after asking**
  (#98): once a requested pause executes, no alerts fire for that account
  until it is resumed. The request is emailed to every contact twice
  before it takes effect, and cancelling is one click.

### Added

- **The dead-man's switch, our side** (#63, decision 7). Set
  `SENTINEL_HEARTBEAT_URL` to an uptime monitor's check-in address and
  the Guardian pushes a heartbeat every five minutes; the monitor alerts
  when the pushes stop, which covers a dead host, a crash loop and a
  wedged process alike. If the monitor cannot be reached the Guardian
  pages HIGH. On restart it reconstructs the window it was not running
  from its last beat, records it as a monitoring gap, pages HIGH, and the
  next proof-of-life digest tells customers their accounts were not being
  checked in that window — in the digest, not as an alert, by decision.
  The vendor is still the owner's choice; until the URL is set nothing is
  pushed and nothing changes.
- **Failed SMS deliveries are shown to operators** (#19). The rows have
  been recorded since #53; the ops dashboard now carries a card with the
  latest twenty refused texts and the provider's reason, so a dead SMS
  account is seen rather than discovered.
- **Delete my data — now, with the gap on record** (#46, decision 6's
  interim). From the portal's security page (password, and the second
  factor when on) or by Minas on a phoned request (`sentinel erase
  <email> --as <who>`), a member's data is hard-deleted immediately: the
  monitoring credentials and every record for the accounts they are the
  primary contact of, their login and settings, their contact details.
  On an account where they are only the second contact they are removed
  as a contact. What remains is the account row as a tombstone carrying
  one final event, `data_erased`, that states the date, the number of
  records removed and that nothing before it can be reconstructed — the
  forensic gap recorded as a gap, never left as silence. The member can
  download their data first (`/export.json`) and receives a written
  confirmation saying what went and what remains. The lawyer's
  crypto-shredding question stays open and did not block this.
- **Fire drills, and the last message that reached you** (#37, decision
  2). Weekly, the Guardian injects a simulated theft on its dedicated
  drill account (`sentinel onboard … --drill`) and records whether it
  arrived and how long detection-to-delivery took; the portal's status
  page shows the last drill, its result and its latency, and offers "Test
  my alerts now" — an on-demand drill to the member's own contacts,
  labelled DRILL, no runbook, never escalated. A failed drill pages Minas
  as an outage. Because a drill proves the machinery and not that a given
  address still accepts mail, the same page shows the last successful
  delivery to the member per channel — email, Telegram, SMS — derived
  from real traffic, with anything older than 30 days or never flagged.
  `sentinel drill [--account]` runs one from the shell; the ops dashboard
  lists the latest ten. On upgrade, onboard the drill account with
  `--drill`: until one exists the weekly schedule records a failed drill
  and pages HIGH, because no drill is no evidence.
- **A portal credential opens exactly what was granted** (#61, decision
  10). Generating a portal password on an account's page now opens that
  account and nothing else; the other accounts the address is a contact
  on are named on the card as not included, grantable from their own
  pages. Each account page carries a grant/remove control for a contact's
  existing login, attributed to the operator and recorded on the account.
  A login nobody has scoped keeps today's behaviour — every account the
  address is a contact on — until an operator first touches its scope, at
  which moment its contact rows become explicit grants before anything
  changes, so revoking never widens. Nothing is required on upgrade:
  existing members keep exactly the access they have.
- **Alert preferences: channels per level, quiet hours, and a floor**
  (#24, decision 4). On the portal's security page a member picks which
  of email, Telegram and SMS reach them for each level, sets their
  timezone, and can enable quiet hours (22:00–07:00 by default, their
  own local time) for everything except critical alerts. Anything held
  during quiet hours is queued — never dropped — and listed in their
  next proof-of-life digest. Email for critical alerts is the one cell
  that cannot be turned off, on the page and in the writer; and because
  email is the floor, an account cannot be onboarded without one and the
  primary email cannot be cleared. Fail-closed on the clock: quiet hours
  cannot be enabled without a timezone the runtime knows, and a stored
  timezone it no longer knows delivers rather than holds — never UTC by
  default. Changes take effect from the next alert and are confirmed on
  the page.
- **The incident page and the alert email now say what to do next, in
  the same words** (#29, decisions 3 and 9). Three steps, in this order:
  revoke any API key you do not recognise (and leave the Guardian's
  read-only key in place — revoking it stops your monitoring); check the
  exchange's own withdrawal history; contact the exchange's support with
  the incident number. Moving funds is not a step — it is something to
  ask the exchange about. The steps instruct only reversible acts, state
  nothing about what the event means, carry no "if this was not you"
  preamble (a summariser strips it), and each sentence is tested to read
  correctly alone. One function feeds both surfaces so they cannot
  drift; the page carries no copy of its own. Wording is the owner's;
  legal reviews later.
- **The ops dashboard is plain, dense and unbranded** (#82, the
  dashboard half). Per the owner's decision on the issue: the member
  portal carries the Minas identity; the internal dashboard now carries
  none of it — no mark, no wordmark, no gradients, none of the brand hues
  — on a light, tabular, utilitarian sheet that says "Ops dashboard ·
  internal · Minas staff only" in its title and header. An ops
  screenshot cannot pass for a customer page, and a mispasted internal
  URL looks visibly wrong. The sign-in, second-factor and enrolment pages
  from #87 land in the same look. Guards: no identity hue, gradient or
  wordmark in the dashboard sheet; every class it emits is styled by its
  own sheet; the two grounds differ. Design note in
  `design/dashboard/DESIGN-SYSTEM.md`.
- **Two-factor is mandatory on the ops dashboard** (#87, decision 19).
  The dashboard — which adds customers, reads every customer's contact
  details and attributes operator actions — had a password-only sign-in
  while the member portal carried TOTP; and with no operator account in
  production it had no sign-in at all. Now a correct password mints no
  session: an enrolled operator must present a code from their
  authenticator or a recovery code (throttled on the same limiter as
  passwords), and an operator with no second factor is walked through
  enrolment at first sign-in and cannot get past it — enrolment is not in
  force until a code verifies, so a mis-scanned secret means "try again",
  never "locked out". `sentinel operator add` enrols at the keyboard and
  prints the recovery codes once. **On upgrade, every operator sets up
  two-factor at their next sign-in.** The only thing that removes an
  operator's second factor is the host break-glass `sentinel operator
  reset-2fa <name> --as <who>` — a shell command on the VM, never a
  dashboard route — recorded in a new append-only `operator_events` table
  with who ran it. Without `SENTINEL_MASTER_KEY` the sign-in is refused,
  not downgraded to password-only.
- **Acknowledging an alert closes the whole customer action** (#99).
  Two detectors seeing one withdrawal used to mean two alerts and one
  orphan: the measured case is a pair sharing one exchange order
  reference, seven seconds apart — one acknowledged, the other left
  open and escalated to the emergency contact about an incident already
  handled. Acknowledging now also acknowledges siblings — unacknowledged
  alerts on the same account carrying the same order reference within 24
  hours — through the same writers, so the first-claim rule, decision
  1's closing note to the emergency contact, and the escalation stop
  apply to each. Deliberately conservative: no order reference claims no
  sameness, other accounts are untouchable, and widening the definition
  is decision 18, the owner's call — a false co-acknowledgement removes
  attention, so leaving an alert open is the safe failure.
- **Alert emails name their incident, and the portal lands on it**
  (#20, decision 16). No links in email, ever — instead the customer
  email's first line now ends with "This is incident #370." as plain
  text, and the portal home pins the newest unacknowledged incident
  above the fold ("Needs your attention — incident #370") with the
  acknowledge path one click away. The number in the email and the
  number on the pinned block are the same row, so a customer reading
  the email and opening the portal directly lands on the thing that
  needs them in one step — the anti-phishing training intact.
- **The mutation gate's cache survives clean trees, fresh clones, and
  CI — and a restored cache must prove itself or die** (#97). The
  gate's expensive artifact (population, test map, per-mutant results)
  now archives to `~/.cache/minas-guardian/mutants/` keyed on the exact
  sources and gate config, and CI's scheduled job restores it via
  actions/cache instead of paying the weekly cold rebuild. The rule that
  makes it safe, enforced twice: a restored archive is kept only if its
  recorded source manifest byte-matches the CURRENT sources — a
  mismatched, manifest-less, or corrupt archive is deleted on the spot
  and the honest rebuild runs, because a durable wrong cache is worse
  than a slow honest one. `--regenerate` bypasses the cache entirely;
  a partial run is never archived. Measured on the real gate: a wiped
  `mutants/` restored, validated, and scored 176/178 in **2m07s**,
  against 1.5–4 hours cold.
- **The customer SMS says almost nothing — deliberately** (#19's copy
  half, #75's shared formatter). The old body shipped the raw detector
  message — amount, destination address — plus the internal rule name
  and a link, on the most spoofable channel there is, straight onto a
  lock screen. The customer body is now fixed copy: severity, account,
  "sign in to the Minas portal", and "we never send links by SMS" — the
  last line doubling as the anti-phishing tell. Operator SMS keeps the
  rule name (a pager needs it) but also drops the raw message: a
  customer's amounts do not belong on an operator's lock screen either.
  And the rule is now structural, not per-channel: a guard discovers
  every alert channel, demands each be classified customer-capable or
  operator-only-with-a-reason, and drives the customer-capable ones with
  a poisoned alert, asserting nothing raw and no URL ever leaves — a
  channel added later that forgets fails before its first send.
- **Members can request a pause — a real 24-hour cancellable countdown,
  never an instant off-switch** (#98, decision 15). An immediate quiet
  pause is the button an attacker with a stolen session presses before
  moving funds, so the member path is request → countdown → automatic
  execution: the pending pause lives in the database with its target
  time, and the executor resumes from the table on every scheduler tick —
  **a deadline that passed while the process was down executes on the
  next startup, never skipped and never lost** (deploys happen, and the
  restart timer can bounce the container on its own; both are probe- and
  test-pinned). Cancellable any time by the member from the portal or by
  Minas (`sentinel cancel-pause`, attributed). Every contact is emailed
  at the request and again near expiry — linkless, saying contact Minas,
  which still works for an owner locked out of the portal — and both
  ENDINGS close the loop for the same people: cancellation says who
  cancelled (the member, or Minas) and that monitoring continues
  unchanged; execution says monitoring has stopped and how to resume. While
  pending, the pause is impossible to miss: a banner on the portal home
  ("Monitoring is about to stop"), a warning block on the account page,
  and a line in the proof-of-life digest. Re-requesting restarts a full
  24 hours; the clock never shortens. And the fast path stays human: a
  phone call to Minas gets an immediate, attributed pause — the
  anonymous path is slow and loud, the immediate path requires a person
  who knows the customer.
- **Design-guard probes run in parallel** (#96). The 144 hand-written
  probes were 75% of a full health check, strictly in series on an idle
  multi-core machine. `scripts/mutation-probe.py --workers auto` fans
  them across one private copy of the tree per core (from `git
  ls-files`; the working tree is never written), and each worker proves
  `import sentinel` resolves inside its copy before it runs a probe — a
  worker importing the real tree would test unmutated code and report
  every probe survived. A worker that fails that proof fails the run,
  like a probe that did not land; the lock is held in both modes;
  `--workers 1` is the old in-place path unchanged. The health check and
  CI both use `--workers auto`.
- **The mutation gate keeps a ledger of accepted survivors, and fails
  on any other.** Proven on a warm `mutants/` and on a #97 restore alike:
  delete one assertion from a selected test, touch nothing under
  `sentinel/`, and the mutant it killed is re-judged into a survivor —
  and the gate passed, because 98% clears a 96% floor. The ratchet's two
  points of headroom is deliberate; what was missing was a hard stop on
  a survivor nobody has accepted. `scripts/mutation-known-survivors.txt`
  lists the two argued equivalents by file + diff fingerprint (stable
  across regeneration, unlike mutant numbers), and `--check` now fails on
  any survivor missing from it, whatever the score. Not the suspected
  cause: every mutant the gate names is re-run on every run, restore or
  not — so no test manifest was added and no test edit costs a rebuild.
  Against the ledger itself growing: every row must carry its reason on
  the same line (`equivalent:` / `unreachable:` / `deliberate:` and why),
  a row without one fails the gate on every run, and the gate prints the
  ledger's size each run — the weekly scheduled-gates report carries it
  as its own row.
- **Pushes go over HTTPS, permanently.** `git push` over SSH connects
  before the pre-push hook runs, and the ~9-minute suite outlasts
  GitHub's idle window: green hook, then exit 141. The hook installer
  now sets a repo-local `pushInsteadOf`; CONTRIBUTING says why. On
  upgrade, developers re-run `scripts/install-hooks.sh` once so their
  pushes take the HTTPS path. Hard rule 7 — one session per working
  tree, never `claude --continue` beside a background job — records what
  late detection cost.

## [1.9.0] — 2026-08-23

### Changed detection behaviour

**Which alerts fire, when, and to whom is untouched — one alert's WORDING
changed** (#13): the permission-drift alert now states exactly what
changed in plain words ("The key GAINED the 'withdraw' authority") instead
of "permission state changed". The trigger, severity, timing, and
recipients are identical; two new operator pages exist (#42, Minas-only —
checks behind schedule, account unwatchable too long) and a host-side
restart timer (#81); no customer-facing detector logic moved.

### Added

- **Onboarding a customer carries the operator's name from the CLI too**
  (#7). The dashboard's add form already attributed onboarding through
  its operator session; `sentinel onboard` recorded nobody. It now takes
  `--as` on the standard gate — mandatory the moment operator accounts
  exist, honest None before — checked *before* the secret prompts, so a
  refusal never costs a pasted key, and the account's founding
  `customer_added` event carries the name.
- **Sensitivity can be tuned per customer, and each customer can see
  theirs** (#17). Any scalar threshold the schema declares can now be
  overridden for one account — `sentinel set-threshold --account <label>
  detectors.withdrawal_burst.min_count 9`, attributed via `--as`, with
  `--clear` returning to the defaults — while every other account keeps
  the global configuration byte for byte (the overlay answers through
  the same read API, so detector call sites cannot tell which layer
  answered, and a registration test fails any poller read that bypasses
  it). Every actual change lands on the account's activity page as
  "Alert sensitivity changed" with old and new values, because how
  easily the alerts fire is a setting whose silent change is an attack.
  A corrupted stored override falls back to the global value, never to
  no threshold. And the portal's Limits page grew "Your alert
  sensitivity": the numbers each account is actually judged against, in
  plain terms, read through the same override layer — with "set for
  this account" marked against anything tuned, and "standard" said
  otherwise. Defaults are unchanged: until an operator sets an override,
  detection behaves identically.
- **The permission-change alert says exactly what changed** (#13). The
  drift detector's alert used to say "permission state changed" and bury
  the diff in evidence; it now leads with the change itself, in the key's
  own terms — "The key GAINED the 'withdraw' authority; the key lost the
  'trade' authority; the key GAINED the '203.0.113.9' IP-allowlist
  entry" — gains first, because a key gaining power is the attack the
  detector exists to catch. List and comma-string shapes diff the same; a
  reordered list reads as "rewritten without changing its entries", never
  as a gain; and a shape that cannot be itemised is admitted with its
  before/after rather than silently treated as empty. The proxy
  disclaimer (Bitget exposes no key inventory for independent accounts)
  stays.
- **The operator dashboard surfaces trouble first** (#39). The accounts
  table now sorts by problem state by default — trouble, then watching,
  then paused (deliberate stillness last) — and filters to one bucket
  with counted links, all server-side per the no-framework decision:
  query parameters and re-render, no table JavaScript. The state is a
  grouping of what each row already says (the status pill and the
  watch-cell's counter threshold), never a third judgement, and an
  unrecognised filter value selects nothing rather than everything.
- **Minas gets paged when checks fall behind — one page, not one per
  account** (#42). The last two criteria of technical health monitoring,
  built on #53's operator route: when no account has completed a check
  inside the window (default 10 minutes), one page says the engine as a
  whole is behind; when a single account has been unwatchable too long
  (default 30 minutes) *while others check normally*, a page names that
  account with its failure counters — the contrast is what makes it a
  fact about the account rather than a symptom of the global case, and
  the global case deliberately suppresses the per-account storm. Never
  paged: disabled accounts (an operator chose that stillness) and
  freshly-onboarded ones (never-checked ages from onboarding, so a
  two-minute-old account is not "behind"). Honest limit, stated in the
  code: these run on the scheduler thread, so a fully dead process
  cannot raise them — that remains #63's dead-man's-switch territory.
- **Plain-English key-creation steps for each exchange, and rejections
  that name the exact permission** (#8, decision 14). The replace-key
  page now walks through creating a read-only key on that account's own
  exchange — Bitget, Bybit, OKX, or Binance — in text only, by the
  owner's decision: written steps that stay accurate beat screenshots
  that quietly stop matching the customer's screen. Every set is dated
  and names the interface it was written against, visibly, on the page
  ("Written against the Bitget web app — API Management page, 2026-08"),
  so staleness is disclosed rather than discovered. A rejected key now
  tells the customer exactly what the exchange reported — the offending
  permission or flag by name — followed by the one-line fix for their
  exchange and the drift question that keeps the text current: "did the
  steps on this page match what your exchange showed you?"
- **An unhealthy container gets restarted — visibly, and never in a
  silent loop** (#81, decision 12). The healthcheck verdict that went
  unconsumed for six weeks (#79: `restart: unless-stopped` acts on exit,
  not health) now has a consumer: a host-side systemd timer that checks
  every two minutes and restarts the service on `unhealthy` — at most
  once per 30 minutes. A second unhealthy verdict inside that window
  means the restart didn't cure, and the timer goes loud exactly once
  instead of reviving a flapping container forever: journald at err, a
  timestamped line with the triggering health state in
  `/var/log/minas-guardian-restarts.log`, and a marker in `/etc/motd`
  the next SSH session cannot miss. Recovery resets the ladder; the motd
  evidence stays until a human removes it. Deliberately a host timer and
  not a docker.sock sidecar — that socket is root on the host, and no
  third-party image belongs next to a credential-holding product for
  fifteen lines of shell. **On upgrade: install the timer once on the
  host, following the deployment docs' "Acting on an unhealthy
  container" section — the deploy itself works without it, but until the
  units are enabled this protection is not running.**

## [1.8.0] — 2026-08-23

### Changed detection behaviour

**One narrow change, for the better** (#22): two checks of the same
condition racing each other could each slip a duplicate alert through the
suppression window; that window is closed, so the duplicate page no longer
happens. No detector, threshold, or routing rule moved — every alert that
fired before still fires, minus the accidental double.

### Fixed

- **One ongoing problem is one alert — now airtight, and never silent**
  (#22). Two checks of the same condition running at the same instant
  could both slip through the duplicate-suppression window and both page
  (the check and the write happened in separate locked blocks with the
  network sends between). The check and the claim now share one locked
  block, so simultaneous detections serialize: one row, one delivery —
  proven by an eight-thread race test. And a repeat the cooldown
  suppresses is withheld from the channels, never from the record: it is
  counted on the alert it repeated, and the portal's home and history
  show "still firing — repeated N×, last <time>", so suppression cannot
  become concealment. The alert row is also claimed before any delivery
  attempt, so a crash mid-send leaves "detected, reached nobody" rather
  than nothing.

### Added

- **The portal home answers "am I covered" per account, at a glance**
  (#34, part of #10). Every account card now carries one of three words a
  customer can act on — **Watching** (checked recently, nothing failing),
  **Paused** (deliberately disabled — shown as a choice, not an error), or
  **Trouble** (stale, rejected key, or tamper-flagged), with what's wrong
  stated in the card itself. The state is `member_status`'s own judgement,
  never recomputed, so the home screen and the "Am I being watched?" page
  cannot disagree. And when ANY account is not being watched — paused ones
  included — a prominent banner says so above everything else, with the
  count and a link to what each account needs; a customer who stops at the
  first screen still learns it.
- **The account page shows that the read-only guarantee held** (#9). The
  refusals were always enforced — a key that can trade or withdraw is
  rejected, never accepted with a warning — but the customer could not
  see that it worked. The security-posture panel now leads with the plain
  summary: "This key can read balances and history. It cannot trade or
  withdraw," stated as the verification's outcome, with the permission
  re-check timestamp drawn from the snapshots the poll loop actually
  takes — and "not re-checked since connection yet" said plainly when
  that is the truth. The "any change raises an alert" sentence is pinned
  to the existence of the permission-drift detector by test.
- **A proof-of-life digest, on the member's own schedule** (#21). For a
  product whose success looks like silence, the digest is how a customer
  tells good silence from a dead poller — so unlike the ops digest, it
  sends precisely when all is quiet: "Issues found: none — all quiet.
  That silence is real: the checks above ran and found nothing to tell
  you." Three plain statements: accounts watched (the same judgement as
  the portal's status page, so the two cannot disagree, with paused and
  troubled accounts named as such), checks performed (a measured counter
  incremented on every successful check — the delta since the last
  digest, never an estimate from the polling cadence), and issues found
  (counts in severity words; never rule names, evidence, or balances).
  Each primary contact chooses daily, weekly (Mondays), or off on the
  portal's Security page — a corrupted stored choice reads as daily
  rather than silently off, a cron re-run on the same day is a no-op
  (both probe-pinned), the change is recorded on /activity, and secondary
  contacts get nothing, per decision 1: they hear escalations, never
  routine traffic. A member whose every account is dark still receives
  the digest — a customer whose monitoring is broken is exactly who must
  keep hearing so — and a paused account is reported motive-neutrally
  ("paused — not being checked; ask us if you didn't expect this"): the
  product holds no record of who wanted a pause, so the digest does not
  claim to know.
- **Members connect Telegram themselves, guided, with a test message
  first** (#18). The portal's Security page grew a Telegram section: one
  button issues a one-time code (15-minute expiry, single-use, superseded
  by any newer code), the guided steps deep-link to the Minas bot, and
  the first message the chat ever receives is the test message — the
  member sees delivery work before relying on it. A worker watching the
  bot's updates completes the handshake; connections and disconnections
  are recorded on the account activity page, and disconnecting also sends
  the email tripwire, because silencing a channel is what an attacker
  does before acting. Delivery is a second copy of what the contact's
  email already gets — customer wording, never rule names or evidence
  (probe-pinned) — and email continues regardless: a Telegram outage
  cannot cost an alert, proven by test. Operators need to do nothing;
  the feature lights up wherever the bot token is configured.
- **The account page says when the monitoring key was last used, and where
  the real kill switch is** (#45). The credentials panel now shows the
  key's last use (it moves with every account check — "never" said
  plainly when no check has completed), and a new posture item states the
  truth about revocation: the customer revokes the key in their exchange
  app, at any moment, and Minas cannot stop or slow that — a read-only
  key cannot delete itself, so a Minas-side "revoke" button would be
  theatre in front of the switch the customer already holds. Revoking
  ends monitoring until a new key is connected; the replace-key path sits
  directly below.
- **Contact addresses are stored one way, not however they were typed**
  (#90). Production carried one person's address as `azdatoo@gmail.com` on
  four accounts and `AZDATOO@GMAIL.COM` on a fifth; every read behaved
  only because each comparison site remembered to lowercase. Emails are
  now normalized at the write boundary (onboarding and `set-contact` — a
  registration test fails any future writer that skips it), a blank is
  stored as NULL rather than `''` (the absent-as-value trap), a one-time
  idempotent migration canonicalizes the rows already written, re-typing
  a stored address in a different case is recognised as no change (no
  manufactured audit rows, no duplicate welcome notes), and alert
  delivery drops duplicate recipients case-insensitively so a person
  listed twice in different casings is mailed once. All read-side
  lowercased comparisons are kept as belt-and-braces.
- **A new emergency contact is told they are one** (#89). The complaint
  behind decision 1 came from a secondary contact who did not know what
  her role meant — nobody should depend on the person who added her
  remembering to say so. Whenever an emergency-contact email is newly
  listed — at onboarding or via `set-contact` — that address receives a
  single note saying why they are receiving it and what the role means in
  decision 1's terms: normally they hear nothing; a high-severity alert
  only after 60 unresponsive minutes; a critical one after 15; one closing
  line when a critical alert is acknowledged. The note carries no
  balances, amounts, addresses, or links; re-running the same values sends
  no second note; and a dead mail server cannot fail the onboarding or
  edit that triggered it. Both writer paths are pinned by design-guard
  probes.
- **An emergency contact can now be changed or removed** (#91). The
  secondary contact was written once at onboarding and never again, so
  when a real person asked to be removed there was no supported path — the
  request was honoured by direct SQL against production, unattributed by
  necessity. `sentinel set-contact` now takes
  `--alert-email-secondary` / `--alert-phone-secondary` /
  `--clear-secondary`, attributed via `--as` the moment operator accounts
  exist (the whole command now carries that gate), recorded on the
  account's activity page as "Emergency contact changed" with the old and
  new values — removal is exactly the change a customer reads /activity to
  find — and no-op-safe: re-running the same values manufactures no
  forensic rows. A secondary-only change cannot disturb where the alerts
  themselves go, and the old footgun where `set-contact` with no flags
  cleared the primary destination is gone: no flags, no change.

- **Every version tag now has a GitHub Release, and the next gap is caught
  by a machine** (#94). Thirteen tags (v1.1.0 through v1.6.2) had no
  Release object — invisible on the repository's Releases page, and the
  discovery was the owner noticing a screenshot. All thirteen are
  backfilled with bodies composed to the release-notes contract
  (docs/RELEASE-NOTES.md, captured from the owner's hand-written v1.7.0
  body): plain-language opening, "What you need to do" only when there is
  something to do, detection behaviour always stated — "not recorded" said
  plainly where a version's changelog predates that rule, never guessed —
  the change entries with their bold plain-language leads, the exact
  deploy command, and the compare link. v1.5.0's body states it was tagged
  and CI-green but never deployed. `scripts/release.sh` now pushes the tag
  and publishes its Release inside the cut, composing the body the same
  way, refusing up front — before anything is written — when gh is missing
  or unauthenticated rather than pushing a tag it cannot publish; and a
  new `release objects` health-check gate fails whenever any version tag
  lacks a Release object, so this drift cannot return silently. The
  publisher (`scripts/github-release.sh`) never creates or moves tags and
  treats an existing Release as done, so a re-run cut cannot error on its
  own earlier success.

## [1.7.0] — 2026-08-21

### Changed detection behaviour

**None.** Which alerts fire, when, and to whom is untouched — this changes
who can be locked out of the portal and how long a session lives.

### Fixed

- **Someone else guessing your email can no longer lock you out** (#31).
  The sign-in throttle counted failures against the bare email address, so
  any stranger who knew it could post wrong passwords and lock the real
  customer out of the portal — the page they most need mid-incident, and a
  denial-of-service an attacker would pair with the theft it hides.
  Identity throttling is now keyed to the (source address, identity) pair
  across sign-in, two-factor, and password-reset; a single source is still
  capped across every email it tries. The recorded residue: a distributed
  guesser is throttled per source only, because the alternative hands
  every stranger a lockout button.
- **Portal sessions now expire after two hours of inactivity** (#31).
  Activity slides the window (the cookie is silently re-issued as you use
  the portal); nothing extends a session past the absolute 12-hour cap set
  at sign-in. **One-time effect on upgrade: every existing portal session
  is signed out** — the token format changed — and everyone signs in once.

## [1.6.2] — 2026-08-21

### Changed detection behaviour

**None.** Which alerts fire, when, and to whom is untouched — this changes
how a release is verified, not what the product does.

### Added

- **Releases stop re-running the slow gates the scheduled CI already ran**
  (#93). `release.sh` now consults `scripts/scheduled-gates-cover.sh`:
  when the most recent scheduled CI run sits on an ancestor of the release
  commit, no gate-relevant path (the package, tests, probe definitions,
  gate scripts, dependency spec) changed since, and that run was green,
  the release runs the fast gates only — saving up to ~50 minutes, cold
  exactly when a release is worth cutting. The predicate fails CLOSED:
  gh missing or unauthenticated, GitHub unreachable, no covering run, or
  any gate-relevant change since all fall back to the full local
  health-check, exactly as before — and a covering run that is RED
  refuses the release outright rather than re-rolling the dice on a
  laptop. Eleven predicate tests prove the fallbacks and the refusal;
  four dispatch tests prove release.sh honours the contract, including
  degrading to the old behaviour when the predicate script itself is
  missing.

## [1.6.1] — 2026-08-21

### Changed detection behaviour

**None.** Which alerts fire, when, and to whom is untouched — this fixes
who can *sign in*, not who is told.

### Fixed

- **Browser sign-in works** (#92). The portal's own hardening broke it:
  `Referrer-Policy: no-referrer` makes a browser serialise the Origin of
  our own form posts as `null`, and the CSRF gate rejected null — so every
  browser POST (sign-in, password reset, key replacement) returned 403
  "cross-origin POST rejected", at every address, always — **the
  customer-facing sign-in was broken from the start**. Neither the suite
  nor the design-guard probes caught it, and the reason is worth keeping:
  both drive the server with clients that send no Origin header at all
  (curl does not apply referrer policy; the test client sends no fetch
  metadata), so every gate was only ever tested from clients that do not
  behave like the browsers the gate exists to referee. The
  gate now reads `Sec-Fetch-Site` first (browser-set and unforgeable;
  cross-site refused even with a plausible Origin), keeps the Origin
  comparison as fallback, never accepts bare `Origin: null`, and both
  surfaces send `Referrer-Policy: same-origin` — which still keeps
  reset-token URLs out of cross-site Referers. Found while proving the
  refusals: the ops dashboard's `/login` POST was outside its CSRF gates
  entirely; every dashboard POST now passes one chokepoint. **Not a proxy
  problem** — an earlier attribution to Tailscale Serve is corrected on
  the issue.

## [1.6.0] — 2026-08-20

### Changed detection behaviour

- **Every secondary contact currently receiving every alert stops
  receiving LOW and MEDIUM entirely, and receives HIGH and CRITICAL only
  when the primary has not acknowledged within the configured window**
  (decision 1, #27 — an emergency contact, not a copy-to). The windows:
  HIGH after 60 unacknowledged minutes, CRITICAL after 15 — both in
  `rules.yaml` (`alerting.escalation_unacknowledged_minutes` /
  `…_high`), hot-reloadable. What a secondary now receives says *why* they are receiving
  it — "the primary contact for <account> has not responded to a critical
  alert in 15 minutes" — and never carries amounts, addresses, or one-tap
  links. New: when a CRITICAL alert is acknowledged, the secondary gets one
  closing line ("a critical alert on <account> was acknowledged at
  <time>"), so acknowledging cannot silently silence the emergency contact
  — at most two messages per incident, usually one, often none. The
  primary contact's alerts are unchanged. **Every secondary contact
  currently receiving everything will stop receiving most of it; that is
  the intent.**

### Added

- **The "Member portal" lockup is back, everywhere the surface identifies
  itself**: a gold micro-caps qualifier under the wordmark in the top bar
  and on the sign-in card, "— Member portal" in every browser-tab title,
  and on the printed status report's masthead — a document handed to an
  exchange or an insurer says on its face which surface produced it. The
  lockup is a guarded distinguishing mark: the ops dashboard may talk
  about the member portal in its copy, but can never wear it.

### Fixed

- **The alert page no longer shows unreviewed advice.** The design
  mockups' "What to do now" steps shipped briefly during the restyle and
  were pulled the same day: that wording is decision 9, one error was
  already found in it, and unread advice on the page a frightened
  customer acts from is not a gap to paper over. The full draft awaits
  the owner's review on #29; the page returns to its pre-restyle state.

- **The portal wears the owner's design** (#82). The member portal was
  restyled end to end from the owner's external design pass (the reference
  mockups and system notes now live in `design/portal/`): dark flat
  surfaces with no gradients and no motion, 16px body type, a 44rem
  reading column, the stroked infinity mark, a scrollable gold-underline
  nav, and one gold primary action per screen with a 48px touch floor.
  Tables are gone entirely — every fact list is stacked key/value rows
  that become two-column on wider screens, chosen over horizontal scroll
  because it reads best on a phone at 3am and prints cleanly. The status
  page is now the one-sentence answer ("You're being watched." / "Your
  accounts are NOT being watched.") over per-account cards whose problem
  callouts say explicitly whether the customer can fix it; printed, it
  becomes a monochrome serif report with a masthead. The alert page gains
  a fixed acknowledge bar on phones. (The mockup's "What to do now" steps
  are NOT shipped: that wording is decision 9, and the draft sits on #29
  for the owner's review.) Behaviour is unchanged: same forms, same routes, same escaping,
  same data. Guards enforce the system (no shared stylesheet with the ops
  dashboard, every emitted class styled, gold reserved for the primary,
  touch floors, mobile-first, no tables, no motion); the full contract is
  docs/portal-design.md.

- **The slow gates now run in scheduled CI, with failure titles that tell a
  regression from a broken gate** (#86). The weekly mutation job gained the
  design-guard probes; both run provably cold (the job asserts a fresh
  runner has no caches, so an 8-second cached pass indistinguishable from a
  real 2951-second run cannot happen there). On failure it files issues
  through `scripts/scheduled-gates-report.sh`, whose classification is
  execution-tested: "Mutation score dropped below the floor" only when
  mutants ran and scored low; "Mutation gate produced no result" /
  "Design-guard probes produced no result" when the question went
  unanswered — the old step filed the floor title on any non-zero exit,
  sending someone hunting a lost test while the gate itself was broken. The
  local health check gained an eighth gate asserting the schedule's own
  pulse (a completed scheduled run within 8 days), because a cron that
  silently stops firing reads as permanently green. `health-check.sh` keeps
  every gate for local and release use; `release.sh` still demands the full
  set before a tag exists.

## [1.5.2] — 2026-08-18

### Changed detection behaviour

- **Every instalment of a drain now alerts, when the exchange strips both
  the reference and the timestamp** (#67). Before: two identical
  withdrawals — same coin, same amount, same address, no id, no txid, no
  timestamp — arriving in one poll produced ONE alert; the second was
  silently dropped as already seen. That shape is precisely an attacker
  draining an account in equal instalments to one destination. After
  upgrading, identical unidentifiable withdrawals visible together are
  told apart and each raises its own alert, and re-polling the same
  records while they settle does not re-alert. The residual that no
  identity scheme can close — a record aging out of the exchange's window
  replaced later by an identical newcomer — still collides, and the
  existing HIGH operator page (which fires on every poll carrying such
  records) now says exactly that instead of "only the first is alerted".
  Chosen against production data: zero unidentifiable withdrawals in the
  measured window, so the condition is format-breakage-rare and loud.

Nothing else in this release changes which alerts fire, when, or to whom:
#83 changes only what the customer /status page *says* about an unwatched
account, and #76 only a startup log line.

### Added

- **A guard that every `accounts.status` value has a writer or a recorded
  reason not to** (#84). Both `disabled` (until #80) and `tamper_suspected`
  (still) shipped as declarations only — in the schema's CHECK constraint,
  styled, worded, and unwritable. The new guard parses the constraint out
  of schema.sql (never retyped), asserts both containment directions, and
  proves each claimed writer in two halves: the value round-trips through
  `set_account_status`, and the named product caller's source carries the
  literal. `tamper_suspected` sits on the explicit unreachable list with
  its reason — a recorded decision that fails loudly when someone later
  assumes the status works, instead of silence that reads as coverage.

### Fixed

- **The "dashboard bound to 0.0.0.0" warning no longer fires on every
  correctly-secured deployment** (#76). It read the container's internal
  bind and could not see the `127.0.0.1:` publish in front of it, so every
  correct Docker deployment logged a security warning — and a warning that
  always fires is how a real one gets scrolled past. The deployment now
  declares who holds the boundary (`SENTINEL_BIND_GATE`, set in
  docker-compose.yml next to the publish it describes; a test holds the
  two together and asserts the loopback publish is really there). Startup
  distinguishes three states: loopback needs no disclosure; a wide bind
  with a declared gate logs INFO naming the gate and the one host-side
  command that verifies it — stated as unverifiable from inside, because
  it is; a wide bind with no declared gate keeps the warning at full
  volume, its text updated to the truth that sign-in is required once
  operator accounts exist. Precision, not deletion.

- **/status no longer tells the customer of a disabled or tamper-flagged
  account to replace their key** (#83). Only `active` accounts are polled,
  so any non-active status freezes the auth-failure counter at whatever it
  held when polling stopped — and the reason ordering checked that frozen
  counter first. An account disabled mid-key-failure (a normal
  disable-pending-resolution move) permanently showed "replace it and
  monitoring restarts", which is false while disabled: replacing the key
  restarts nothing. Worse for `tamper_suspected`, whose detector *is* that
  counter crossing a threshold, so the tamper wording was dead copy in
  every realistic path. The rule, now stated in the code: **current state
  beats stale counters** — status is a fact about now; a counter frozen by
  that very status is a fact about whenever we last looked. The
  replace-key link follows the reason, so it also stops being offered
  where replacing the key would do nothing.

- **Backups have a named home outside the checkout** (#85). The runbook
  never said where a database backup goes, and the one written precedent
  put it inside `/opt/minas-guardian` — where, being an untracked file, it
  tripped `deploy.sh`'s dirty-tree refusal on the check's first production
  run and blocked the next deploy. docs/deployment.md now names
  `/var/backups/minas-guardian/` in both places a backup is instructed,
  with the rule that keeps this from recurring: a git working tree is not
  a storage location, and no instruction in the docs may create an
  untracked file in the tree. The refusal itself deliberately still covers
  untracked files — `COPY sentinel/ sentinel/` sweeps them into the image,
  so they are not harmless here (reasoning recorded in the script and on
  #85) — but its message now tells the untracked case where the file
  belongs instead of offering "revert", which only fits edited tracked
  files.

## [1.5.1] — 2026-08-18

### Changed detection behaviour

**None.** Which alerts fire, when, and to whom is untouched. Everything in
this release is deploy tooling on the server's side of the fence: nothing
the running service executes is modified — no engine code, no schema
change, no config change. PATCH for the same reason.

### Fixed

- **`scripts/deploy.sh` deploys a release tag, never main's tip.** The old
  script pulled `main` and handed HEAD to the deploy gate — but the
  workflow commits a log handoff after cutting every tag, so main's tip
  was routinely one commit past the release and the gate (correctly)
  refused it. This was the normal path, not an edge case: it blocked the
  v1.4.1 and v1.5.0 deploys, and v1.5.0 had to be completed by hand —
  exactly the error-prone sequence the script exists to prevent.
  *(Corrected 2026-08-18: the "completed by hand" claim was wrong —
  v1.5.0's refused deploy was abandoned, production stayed on 1.4.2, and
  v1.5.0 first reached production inside this release's own deploy,
  1.4.2 → 1.5.1 in one step. See docs/DEPLOYMENTS.md.)* The gate
  is unchanged and still has no `--force`; what changed is that the script
  now offers it the right commit: `git fetch --tags`, then check out the
  requested tag (`deploy.sh v1.5.0`) or the latest release by version
  order (`deploy.sh` with no argument). The server stays detached at the
  deployed tag, so `git describe` answers "what is deployed" with exactly
  one name; `git checkout main` puts it back on a branch if ever needed.
- **The deploy refuses more, and asserts instead of reporting.** A dirty
  working tree is refused with the files named (fix it in git, never on
  the server). A tag older than the running version requires "downgrade"
  typed at the terminal, so an accidental downgrade of a monitoring
  product is not one typo away — and is refused outright when there is no
  terminal to ask. After the switchover, the script compares the running
  container's `sentinel version` against the tag it was asked to deploy
  and exits non-zero on mismatch: a deploy that ran the wrong image now
  fails instead of printing what happened. The comparison reads a new
  machine form, `sentinel version --machine` (key=value lines, pinned by
  tests on the CLI side — the side a reformat would happen on), so
  changing the human output can never fail a deploy with a false "wrong
  image is serving"; images predating the flag (rollback targets) are
  parsed by their human output, which is safe precisely because a built
  image's format is frozen. A rollback typed from your machine needs
  `ssh -t` — the downgrade confirmation is read from the TTY, and a plain
  `ssh host '…'` one-liner allocates none; the working one-liner is in
  docs/deployment.md next to the downgrade paragraph. Any failure before "Switching
  over" still leaves the previous version running untouched, and the
  healthy-wait still requires the real compose healthcheck, not
  "started". Eight new tests exercise the refusals against throwaway
  clones — the dirty tree, the missing tag, the tipless resolution, the
  unconfirmed downgrade, the wrong-image assert — not just the happy path.
- **`scripts/release.sh` now prints the deploy command for the tag it just
  cut**, so the release hands the operator the next step instead of
  leaving them to reconstruct it.

## [1.5.0] — 2026-08-18

### Changed detection behaviour

- **Alerts keep arriving however long the process has been up.** Before this
  release, the dashboard's descriptor leak (#79, details under Fixed) starved
  the process of sockets roughly ninety minutes after every restart — and
  alert delivery shares those sockets, so email, Telegram and exchange
  polling all failed from that point until the next restart. An alert raised
  in hour two used to go nowhere; now it arrives.
- **A failing retention prune pages the operator at HIGH** (#73, details
  under Fixed). It used to be one ERROR log line an hour, reaching nobody.
- **A disabled account stops alerting entirely** (#80). `sentinel
  disable-account` removes the account from the polling queue, so nothing
  fires for it — including the poll-failure warnings a dead key generates —
  until `enable-account` restores it, baselines intact.

### Added

- **`sentinel disable-account <label>` / `enable-account <label>`** (#80).
  G-50's "offboarding is disable, never delete" finally has its mechanism:
  the schema declared the status, the dashboard styled it, the poll loops
  filtered on it, and nothing could write it. The verb writes it, records
  an `account_disabled`/`account_enabled` event naming the operator (the
  `--as` gate is `ack`'s: mandatory once operator accounts exist), and the
  account's baselines survive so re-enabling resumes monitoring without
  the false CRITICALs an empty baseline produces. Pinned at the level the
  defect lived: a disabled account disappears from the scheduler's queue,
  not merely from a SELECT.

### Fixed

- **The retention boundary report no longer cries wolf at the boundary**
  (#78). Rows age across their window continuously between hourly prune
  runs, so the oldest surviving row can legitimately sit up to one prune
  interval past its window — the report flagged any negative remainder as
  PAST and rendered minutes of overshoot as "PAST 0d". Overshoot within
  one `prune_interval_minutes` now reads `due (…past; within one prune
  interval)` and exits 0; sub-day amounts print in hours; a real backlog
  still flags and exits 1. Behaviour at exactly the boundary is pinned by
  tests in both directions.
- **`scripts/retention-boundary.py` now ships in the Docker image** (#77),
  so it can run where `/data/sentinel.db` lives. This also corrects
  v1.4.1's release note, which promised the production retention boundary
  reading could be taken "after this release is deployed" — it could not:
  `.dockerignore` excluded `scripts/` wholesale and the Dockerfile copied
  none of it, so the reading had to be taken by mounting the script by
  hand. The copy is deliberately per-file (dev tooling has no business in
  a production image holding customer credentials), and paired tests keep
  the Dockerfile COPY list and the `.dockerignore` re-include list
  agreeing in both directions.
- **A failing retention prune is no longer silent** (#73). It used to be one
  ERROR line an hour: the failure escaped to the scheduler's blanket except,
  froze the table-rows gauge at its last good value (switching off the one
  signal that would have shown the resulting growth), and skipped the
  untimestamped-records alarm behind it on every failing cycle. A failure
  now pages the operator (HIGH), drops a new `sentinel_retention_ok` gauge
  to 0 until a pass succeeds, and the table-rows gauge and the alarm run
  regardless. The hourly retry pacing is unchanged — what was removed is
  the silence, not the interval. The "Enforcement assurance" caveat in
  docs/data-retention.md is replaced by the assurance itself.
- **Every dashboard and portal request leaked a file descriptor** (#79).
  ThreadingHTTPServer runs each connection in a fresh thread; `Database`
  gave each thread its own SQLite connection and kept a strong reference so
  `close()` could find it — which meant a dead request thread's connection
  was never freed. In production the process hit its 1024-descriptor
  ceiling roughly ninety minutes after every restart, after which it could
  not open **any** socket: the dashboard's, and also SMTP, Telegram and the
  exchange API. The healthcheck correctly reported unhealthy, and nothing
  acts on unhealthy, so it ran that way for six weeks of restarts. Request
  handlers now close their thread's connection when the thread finishes
  (`Database.release_thread_connection`), pinned by socket-level tests that
  hammer the real dashboard root and a real signed-in portal session and
  require the process's descriptor count to stay flat.
- **Defence in depth for the next descriptor bug:** the container now runs
  with `nofile` raised to 8192 (docker-compose), so a future leak degrades
  the dashboard long before it starves alert delivery, which shares the
  budget.

## [1.4.2] — 2026-08-18

### Changed detection behaviour

**None.** Which alerts fire, when, and to whom is untouched. This release
is operator tooling and documentation only: the one new script runs in the
server's shell, not inside the service — no engine code, no schema change,
no config change. PATCH for the same reason.

### Added

- **`scripts/deploy.sh` — the whole deploy as one command on the server.**
  Pull (`--ff-only`), deploy gate, build stamped with `GIT_SHA`/`GIT_TAG`,
  `validate-config` against the new image, `up -d`, wait for the
  healthcheck, report `sentinel version`. Self-elevates via sudo. It
  automates the sequence already documented in docs/deployment.md
  "Deploying code changes" and decides nothing new — the gate's tag-only
  rule and missing `--force` apply exactly as when typed by hand — and any
  failure before the switchover leaves the previous version running
  untouched, because the build and config check happen off to the side.
  After the switchover it accepts only a container reporting **healthy**
  (the compose healthcheck, not merely "started"), and it ends by asking
  the running container `sentinel version` so the operator confirms what
  is live rather than assuming it.

### Fixed

- **This entry's own location.** `4db4134` recorded the deploy script
  under the released `[1.1.0] — 2026-08-14` heading, retroactively
  claiming a shipped release contained it — production runs 1.1.0 and has
  no `deploy.sh`. Moved here, where it is true.

## [1.4.1] — 2026-08-17

### Changed detection behaviour

**None.** Which alerts fire, when, and to whom is untouched. Everything in
this release is tooling and documentation: no code the engine runs is
modified, no schema change, no config change. PATCH rather than MINOR for
the same reason — an operator upgrades at will and the running product
behaves identically; the one new script is run by hand, not by the service.

### Added

- **`scripts/retention-boundary.py`** — read-only report of every table
  `prune_all` governs: the configured window, the oldest row, and how many
  days until that row crosses its retention boundary. Table-driven from
  `sentinel.retention`'s own `PRUNABLE`, `KEEP_LATEST_PER_ACCOUNT` and
  `ALERT_CHILDREN`, with windows read through `RulesConfig`'s fallback
  chain, so a table added later appears automatically and the report cannot
  disagree with the deletion job. Opens the database with SQLite `mode=ro`,
  so it cannot write, and reports a table it cannot answer as unanswered
  (exit 2), never as a pass. **Operational note:** because it imports those
  constants from `sentinel.retention`, it only runs against a deployment
  that already carries them — on an older build it fails with
  `ImportError`. The production retention boundary reading the v1.4.0
  handoff owes can therefore only be taken after this release is deployed.
- **`CLAUDE.md`** — the owner's standing operating constraints for
  autonomous sessions (never deploy, never touch credentials beyond an
  issue's needs, a red CI is a stop condition, …), which previously existed
  nowhere in the repository. It deliberately references CONTRIBUTING.md
  rather than duplicating its hard rules, so the two copies cannot drift.

### Documentation

- The v1.4.0 session handoff in docs/AUTONOMOUS-LOG.md, and the three
  process patterns it paid for written into CONTRIBUTING.md: a process
  check that matches itself always reports busy; a killed check is not a
  passed check; registration guards need execution guards.

## [1.4.0] — 2026-08-17

### Changed detection behaviour

**None.** Which alerts fire, when, and to whom is untouched. This release
adds the operator triage queue (deliberately invisible to the escalation
ladder) and the retention disclosure, and repairs the deletion job — none of
which touches detection or delivery. Stated rather than inferred from an
absent heading.

### Added
- **What we keep is now published where a customer can read it** (#47, G-43).
  The `/limits` page gains *What we keep, and for how long*, derived from the
  same `retention:` config `prune_all` enforces hourly — via the same
  fallback chain, so the page and the deletion job cannot disagree. Stated in
  customer terms (activity records, snapshots, dedup records, keys,
  baselines), with the honesty attached: **these are current practice, not
  yet reviewed policy** — publishing the numbers without saying so would
  convert engineering defaults into an implied promise, the overstatement
  this page family exists to avoid (#48). A pairs-style test asserts every
  setting the pruner enforces sits behind a disclosure line, so a table added
  to `PRUNABLE` without a disclosure decision fails CI.

- **An operator can work the alert queue across every customer** (#41, G-37).
  New `/triage` on the ops dashboard: every unacknowledged alert fleet-wide,
  CRITICAL first and oldest first within a severity, with an inline note and a
  handled/reopen marker per alert. Alerts older than the escalation threshold
  are highlighted as overdue — the same number the ladder acts on, derived
  from config, because a queue that calls an alert overdue on a different
  clock teaches the operator a deadline the product does not keep. A second
  table shows who is muting what (live snoozes, SMS opt-outs) — the early
  warning for a noise problem. Flags surface `delivered to nobody` (#57) and
  `escalated` where they apply.
  - **"Handled" is Minas-side bookkeeping and is invisible to escalation.**
    `Escalator` keys on `acknowledged_at IS NULL` — the customer's state. If
    marking an alert handled touched that column, an operator tidying their
    queue would silently stop the escalation ladder for a customer who has
    still not responded. Triage lives in its own `alert_triage` table; a
    handled alert still escalates, still counts as overdue, and still appears
    to the escalator's query. Pinned by tests and a design-guard probe.
  - Every triage action writes an `alert_triaged` event naming the operator
    (G-50); the triage row is current state, the event log is the history.
  - `alert_triage` is pruned on the alerts window (`alerts_days`) — caught by
    the retention pairs-test, which exists for exactly this. That test asks
    whether a decision was *recorded*, and it could not ask whether the
    decision *ran*; see the pruning fix under **Fixed** below, which is the
    half it could not see.
- **Every module imports, and every lazy import resolves**
  (`tests/test_every_module_imports.py`). Written after this feature's module
  was nearly shipped over the top of `sentinel/triage.py` — the AI incident
  narrative behind `sentinel triage --label`. The full suite stayed green with
  `TriageEngine` deleted, because `cmd_triage` imports it lazily and nothing
  ever executed that import; the command would have crashed in production
  instead. The new guard resolves every function-level `from sentinel.X
  import Y` against the real module, so a name deleted from a module that
  still imports cleanly is caught by CI rather than by the next person to run
  the command. The queue module now lives at `sentinel/alert_triage.py`.

### Fixed
- **The retention policy document described a system that no longer exists**
  (#47). `docs/data-retention.md` still said "no retention pruning
  implemented — data accumulates" — untrue since pruning began running
  hourly — plus Alibaba Cloud hosting (it is AWS Tokyo) and contact details
  in env config (they are in the database). A policy document that
  understates what the system deletes misleads the lawyer it is written for:
  the erasure-vs-forensics question reads very differently against "we keep
  everything" than against "we already delete on a schedule engineering
  chose". The factual half is rewritten to match the running system; the
  legal questions and the NOT-REVIEWED flag are unchanged, because that
  criterion needs a lawyer, not a commit.

- **Retention would have stopped dead at the first alert anyone had annotated,
  and taken every table after it down too.** `Database` opens every connection
  with `PRAGMA foreign_keys = ON`, no table in the schema declares `ON DELETE
  CASCADE`, and three tables carry an `alert_id` referencing `alerts` —
  `sms_deliveries` and `expected_activity` (both shipped in 1.1.0) and now
  `alert_triage`. `prune_all` walked its table list parent-first, so SQLite
  refused the `DELETE FROM alerts` and the `IntegrityError` left `prune_all`
  altogether: dedup state, config changes, password resets and the `seen_*`
  tables all stopped being pruned as well. The pruner now deletes an alert's
  annotations in the same transaction as the alert, keyed on the alert's own
  cutoff, so a child cannot outlive its parent — and each child keeps its own
  window, so a *shorter* one still applies. `ON DELETE CASCADE` would be the
  tidier schema and is not available: SQLite cannot add a constraint without
  rebuilding the table, on a live database holding customer credentials.

  **Nothing was lost.** The defect needed an alert older than `alerts_days`
  (730 days) carrying an annotation, and the earliest one possible is
  2028-07-09 — deletion has been running correctly throughout and would have
  kept working for another 692 days. Found by inspection, not by damage.

  The guard is the general lesson: **a registration test needs a paired
  execution test.** `test_every_prunable_table_actually_prunes` puts a row past
  its own window in every prunable table, runs `prune_all`, and asserts all of
  them are gone — table-driven from `PRUNABLE`, with a second test so a table
  added later cannot be silently absent from the population it claims to
  cover. Written up in CONTRIBUTING.md; verified negatively against the parent
  commit, where it fails with the exact foreign-key error.

- **The retention policy document now states the limit of its own assurance**
  (#73). A failure of the hourly prune is written to the log and nowhere else
  — no page, no metric, and the growth gauge that would show the consequence
  is published inside the same call, so it freezes with it. Every window in
  `docs/data-retention.md` is a claim about what we delete, so the reviewer is
  told what stands behind those claims. Deliberately **not** on the customer's
  `/limits` page: an internal monitoring gap is not customer disclosure, and
  the customer's question — what we keep and for how long — is unchanged and
  true. The note is referenced to #73 so it clears when the alarm ships; an
  unreferenced caveat outlives the condition it describes.

## [1.3.0] — 2026-08-17

### Changed detection behaviour

**None.** This release changes no alerting: which alerts fire, when, and to
whom is untouched. It adds two read-only pages to the member portal and makes
an ops control describe itself more accurately. Stated rather than left to be
inferred from an absent heading — "nothing changed" and "nobody checked" look
identical otherwise, which is the distinction this product exists to make.

### Added
- **The product says what it cannot do, and a guard keeps it that way**
  (#48, G-44). New `/limits` in the member portal — *What the Guardian does*,
  *What it cannot do*, *Why it is built this way*, *Timing, honestly* — linked
  from every signed-in page.

  The important sentence is that **it cannot stop an attack**. It has no power
  to freeze, cancel, block or reverse anything on an exchange. A customer who
  believes otherwise will not act on an alert with the urgency that decides
  the outcome — they will assume something is already being done — so a
  prevention claim does not merely oversell the product, it disarms it.

  The read-only key is stated with **both halves**: it cannot trade or
  withdraw, so a compromise of Minas cannot cost anyone money — and the price
  of that guarantee is that the same key cannot be used to rescue funds
  either. Stating only the first half would be marketing. Timing comes from
  the running config (`fast_loop_seconds`, `slow_loop_seconds`) rather than a
  number typed into copy, and is stated as measured from when the *exchange*
  reports something, since we cannot be faster than the source we read.

  **`tests/test_no_prevention_claims.py` enforces the fourth criterion.** The
  claim was already absent — it held because whoever wrote the copy happened
  to be careful, which is the same as not holding. The scan walks the AST of
  the customer-facing modules and reads only string literals that can reach a
  page, so comments and docstrings explaining the rule do not trip it.

  A verb alone is not a claim, which the first two attempts got wrong. Both
  false positives were sentences the product must keep saying: *"It cannot
  stop an attack"* on the limits page, and *"If you did not authorise this,
  freeze the account in the Bitget app"* in the alert runbook — advice to the
  customer, which is the honest model stated out loud. A claim now requires
  the product as subject, the verb, and no negation, all in the same clause.
  A guard that fires on the honest page gets suppressed, and a suppressed
  guard protects nothing.

- **A customer can see whether they are actually being watched** (#38, G-34).
  New `/status` in the member portal. The product's claim is that no news is
  good news, and that claim is worth nothing unless silence can be told apart
  from a dead poller — which the customer previously had no way to do. This is
  the failure this codebase keeps closing on its own behalf, pointed at the
  person paying for it.
  - One headline — *monitoring is running normally* / *some accounts are not
    being watched* / *your accounts are NOT being watched* — plus every
    account's last successful check and, where something is wrong, what is
    wrong in a sentence.
  - **A partially-dark member reads as `degraded`, never as healthy.** One
    freshly-polled account must not let the page say everything is fine while
    another has been dark for hours.
  - **The staleness window is derived from the heartbeat detector's own
    threshold and poll interval**, not typed in. If the page and the alert
    disagree about whether an account is watched, the page is the one the
    customer believes. (The account detail page still uses a literal 300s for
    its own freshness pill — noted, not changed here.)
  - **Reasons are ordered by what the customer can do.** A rejected key is
    theirs to replace and links straight to the replacement door; an
    unreachable exchange is not, and telling someone to re-issue a working key
    is wrong advice (G-6). A rejected key counts as unwatched even when the
    poll timestamp is fresh — the poll ran, it just could not see anything.
  - **`tamper_suspected` is stated in words, not printed as a column value.**
    Only `active` accounts are polled, so flagging an account for suspected
    interference *stops it being checked*: the account we most suspect is
    being interfered with is the one we stop looking at. The customer is now
    told that plainly.
  - **The page states what it cannot prove.** It is served by the Guardian, so
    if it loaded the Guardian is running — but it can never report the
    opposite, because a dead host looks exactly like a closed laptop. It is
    evidence that monitoring works, not the alarm that would say it stopped;
    that alarm is #63 and does not exist yet.

### Changed
- **The member portal's navigation is built in one place.** It was duplicated
  across pages, which is how a new page ends up reachable from some and not
  others — and the reader who most needs "what are the limits of this thing"
  is the one who arrived somewhere that does not link to it.

- **A portal credential now says what it opens, on the button and in the
  record** (#61). The "Generate portal password" control sits on one account's
  page and reads as an action on that account. It is not: portal identity is
  the email, so the credential opens every account that address is a contact
  on. 1.1.0 listed those accounts above the button; this finishes the
  affordance half of the issue.
  - **The button itself now carries the scope** — *"Generate portal password —
    opens 3 accounts"*. The paragraph above it is read once; the button is what
    gets clicked, sometimes months later by somebody who scrolled past the
    paragraph. Unchanged when the credential really does open only this
    account, because a caution shown every time is a caution nobody reads.
  - **The confirmation names them too.** The operator is about to send that
    password to somebody, and it is the last moment a surprise is still
    recoverable.
  - **The forensic record says what was granted**, as it was at the moment of
    issue — `granted_account_ids` and `granted_account_labels` on the
    `member_portal_password_generated` event. The email alone could not answer
    "what did this password reach", because contacts get added and removed:
    replaying the lookup next month returns a different set than the one
    actually handed over. A test pins that changing a contact afterwards does
    not rewrite the record.

  Per-account credentials — the model fix rather than the affordance fix —
  remain the owner's call and are unchanged here, as the issue recommends.

## [1.2.0] — 2026-08-15

### Changed detection behaviour

All of this comes from #69. The other entries below change no alerting.

- **Two new HIGH operator pages**, both from the self-service key-replacement
  door:
  - `monitoring_key_replacement_wrong_account` — a submitted key is valid and
    read-only but reports a different `exchange_uid` than the account it is
    being attached to. Refused and paged, because the failure it prevents is
    silent: the Guardian would poll somebody else's quiet account, report
    "watching", and the real one would be drained unobserved.
  - `monitoring_key_replacement_needs_an_operator` — the customer tried and we
    could not verify them, so a human has to finish it. This one pages
    **because a customer is waiting**, which is a queue nobody was watching
    before.
- **Accounts that went dark can come back without us.** A customer who
  revoked our read-only key during a panic could only be restored by an
  operator being awake; monitoring now resumes on the next poll after they
  submit a replacement. Expect alerting to restart on accounts that have been
  silent — including, at the moment monitoring resumes, whatever accumulated
  while they were dark.

**One transitional effect worth planning for.** The check depends on
`accounts.exchange_uid`, which is learned at onboarding and refreshed by every
slow loop. Until an existing account has been through a slow loop after this
upgrade it has no recorded id, so **every account onboarded before this
release takes the operator path rather than the self-service one**, and pages
`..._needs_an_operator` when a customer tries. That clears itself as the slow
loops run. It does **not** clear for **Binance**, whose API carries no account
identifier at all — those accounts stay on the operator path permanently, by
design. Refusing where we cannot verify is the correct trade, but it means the
first day or so after this deploy is the period when the new door pages most.

### Added
- **A customer can replace a revoked monitoring key themselves** (#69, G-3).
  The runbook on every CRITICAL and HIGH alert tells a frightened person to
  revoke API keys, and names ours as the one to leave alone — it is read-only,
  so revoking it takes nothing from an attacker and switches the monitoring
  off. Panic being what it is, some revoke everything anyway, and until now the
  only way back was an operator being awake. A signed-in member can now submit
  a new read-only key from their account page, and monitoring resumes on the
  next poll.
  - **A replacement must be for the same exchange account.** A key can be
    genuinely read-only, pass every check, and belong to somebody else's
    account — at which point the Guardian polls a quiet account, reports
    "watching", and the real one is drained unobserved. `accounts.exchange_uid`
    records the exchange's own account id (Bitget `userId`, OKX `uid`, …),
    learned at onboarding and refreshed by every slow loop, and a replacement
    that reports a different one is refused and paged.
  - **Where we cannot check, we refuse.** An account with no recorded id — any
    account onboarded before this release, until its next slow loop, and every
    Binance account, whose API carries no account identifier — gets the
    operator path instead, and an operator is paged that the customer is
    waiting.
  - The read-only gate (G-4) applies to this door exactly as it does to
    onboarding: a key that can trade or withdraw is refused, not stored with a
    warning. The change is recorded against the member on their own activity
    page (G-32), and an operator is told.
  - **The alert runbook now says a customer can do this themselves, and still
    does not link to the page.** "Minas emails you a link, you paste your API
    key" is a phishing template, and the alert email is the one message where
    we can teach the opposite habit: sign in, then look.

### Fixed
- **README described a pipeline that had moved on.** It claimed "169 tests
  across 24 files" against an actual 933, and said CI "validates that `docker
  compose config` parses" — true until CI started building the image and
  checking it can name its own commit. Counts are now stated as bounds
  (`900+`/`70+`) so they degrade gracefully instead of drifting five-fold
  before anyone notices, and the network refusal that keeps the suite off the
  wire (#68) is stated where the no-live-exchange claim is made.
- **A half-configured SMS provider can no longer shadow a working one** (#70).
  `build_sms_channel` prefers ClickSend when its credentials are present and
  falls back to Twilio. Nothing in the suite ever configured a provider *half
  way*, so nothing held the "both credentials" check in place — and if it ever
  degraded to "either credential", a stray `CLICKSEND_USERNAME` with no API key
  would select ClickSend, shadow a fully working Twilio fallback, and fail at
  delivery time. SMS is CRITICAL-only in the default routing, so what would not
  arrive is the page that matters most, and the only trace would be a delivery
  failure after the fact. The behaviour was already correct; there is now
  something keeping it that way, for ClickSend and for all three Twilio
  credentials.
- **The untimestamped-record warning is pinned to naming the record** (#70).
  The metric an operator is alarmed on says an exchange stopped stamping times;
  this log line is where whoever was paged looks next, and one that cannot say
  *which* record lost its timestamp gives them nothing to work with. Decision
  recorded rather than left open, and the mutation score moved 90% → 98% as a
  result — the only two survivors left are the two documented equivalent
  mutants, which stay documented rather than chased. The floor ratchets 88 → 96.
- **A test run can no longer reach the network** (#68). The suite made real
  network calls with whatever credentials the machine had: `load_dotenv()` was
  the first line of `sentinel.cli.main`, two tests call `main()`, and
  `load_dotenv` writes the operator's real `.env` into `os.environ` for the
  rest of the process with no teardown. A live SMTP server answered
  `535 Missing password` in the middle of a password-reset test — one correct
  password away from mailing real customers, and the SMS paths cost money per
  message. CI never saw it, because CI has no `.env`; it failed only on a
  developer machine with real credentials configured, which is the machine
  where it costs the most.
  - `load_dotenv()` moved out of `main()` into a new `cli_entry()`, which
    nothing but the installed `sentinel` command reaches. `[project.scripts]`
    points there now, and a test pins that it does — pointing it back at `main`
    would leave production reading no `.env` at all.
  - New `sentinel/net.py` is the only module in the package permitted to import
    `requests` or `smtplib`, and it refuses to open a connection when
    `SENTINEL_ENV=test` or pytest is running. The four alert transports and
    every exchange read go through it. A source scan enforces the "only
    module", so the next transport somebody writes is covered on the day it is
    written rather than the day it is remembered.
  - Refusals name the host and never the URL: a Telegram bot token lives in the
    path, and these exceptions are written to logs (G-41).
- **The onboarding tests were calling Bitget for real.** `_client_with` claimed
  in its docstring to replace "the network-touching methods", and named five —
  but `seed_baseline` also calls `fetch_closed_orders` and `fetch_transfers`,
  so every run of `test_onboard_account_persists_encrypted_on_success` made two
  live HTTP requests to Bitget. Both failures were swallowed by the
  best-effort baseline-seeding handlers, which recorded a gap and carried on,
  so the test passed throughout. The fake now covers the whole `fetch_*`
  surface, and the test asserts the account onboarded with **no** baseline gaps
  rather than only that its key was encrypted.

## [1.1.1] — 2026-08-14

### Fixed
- **CI now builds the image and checks it can name its own commit.** Nothing
  built the image before this — `docker compose config` only proves the
  compose file parses — so the `GIT_SHA` build-arg → Dockerfile `ENV` →
  `sentinel version` chain shipped in 1.1.0 was the one path production
  depends on and no test could reach: the unit tests set the environment
  variable directly, and a source checkout takes the git fallback instead. CI
  now builds with a commit and asserts it is reported back, then builds
  *without* one and asserts the image says `unknown` and exits non-zero rather
  than naming something plausible.
- **README stated the deploy guarantee as it was before 1.1.0** — "only pushed
  code can reach production". Pushed is no longer sufficient: only released
  code can, because the deploy gate refuses any commit that is not exactly a
  tag.
- **`sentinel version` claimed a provenance it did not have.** An image built
  with no `GIT_SHA` reported `commit unknown` and exited non-zero — both right
  — while reporting `source build-arg`, because the Dockerfile's
  `ARG GIT_SHA=unknown` default arrives as the literal string and was taken
  for a real answer. Nothing behavioural was wrong; the lie was in the one
  field whose job is to say how we know. Found by the new image gate on its
  first run.
- **A brittle assertion in the deploy-gate tests.** It scanned the script's
  source for `--force` while stripping the prose that explains its absence, so
  rewording a comment failed the suite. The behavioural checks either side of
  it — that `--force`, `-f` and `--yes` are all rejected — are what actually
  pin the rule.

## [1.1.0] — 2026-08-14

### Changed detection behaviour

**Read this section if you read nothing else.** Every entry here changes which
alerts reach you, or who they reach. Each one is detailed further down under
`Added`/`Fixed`; this is the summary an operator needs before upgrading. The
version bump is MINOR, which says nothing about how much your inbox changes —
that is what this heading is for.

**Alerts that now fire where they previously did not.** Expect more alerts, on
accounts that looked quiet because nothing was watching:

- **Withdrawals with no reference ID** — silently dropped before, so a
  withdrawal the exchange did not stamp with an id produced no CRITICAL at all
  (G-11, #15).
- **Withdrawals that cannot be told apart from each other** — two undated,
  unreferenced withdrawals collided on the same dedup key and the second was
  suppressed for a whole CRITICAL cooldown. Now both are reported (#67).
- **Undated fills** — a fill with no exchange timestamp was skipped by
  off-market and fee checking entirely (G-47, #51).
- **Off-market execution on mixed batches** — one unparseable record ended the
  scan, so every later fill in that poll went unexamined (#66).
- **OKX IP-allowlist drift** — the detector was inert against OKX's response
  shape and could never have fired.
- **Loss of access to a customer's account** — raised to HIGH from a
  digest-only path that in practice never sent, and it now repeats daily while
  unresolved. The customer is told plainly that their account is **not being
  watched** (G-6, #58).
- **An account whose baseline never completed** — onboarded, displayed as
  healthy, and never fully checked (#58, group C).
- **A poll that dies partway through** — used to stamp itself a success, which
  kept `account_unreachable` from ever firing while nothing was being examined
  (#59).

**Alerts that stop firing, or arrive differently.** These were false or
misrouted:

- **Binance CRITICAL quantity-drop false positives** — closed-order coverage
  now accounts for the fills that explained the drop.
- **First-poll `transfer_route_never_seen`** — a new account's first poll
  reported every existing route as never-seen.
- **Old activity no longer explains away a fresh theft** — attribution was
  matching trades outside the window, so a real drop could be marked
  accounted-for (G-7). This makes some genuine thefts alert that previously
  did not.
- **An alert whose channel fails is no longer lost** — one failing channel
  used to drop the alert for every channel (#57).
- **Alert routing falls back to channels rather than to nothing** — a missing
  or mistyped routing table meant alerts were stored, deduped, and delivered
  nowhere.

**Who gets told, and how you can quiet it:**

- **Minas is now paged separately from the customer** for our own outages, on
  its own routing table that customer tuning cannot change (G-49, #53).
- **Customers can acknowledge and snooze their own alerts** from the portal,
  capped per severity — CRITICAL 1 hour, HIGH 4 hours, MEDIUM/LOW 24 hours.
  A snooze silences one alert, never a rule, and never forever (G-22 #26,
  G-26 #30).
- **Secondary contacts receive alerts and can sign in** (G-21, #25).
- **Member SMS** via ClickSend, per-account opt-out honoured for non-CRITICAL.
- **A rejected `rules.yaml` no longer stops the service** — it starts on every
  setting that validates, falls back to built-in defaults for the rest, and
  raises a **CRITICAL operator page** saying the thresholds in force are not
  the ones in the file. Previously the process refused to start, which turned
  a bad edit during an upgrade into an outage.

### Added
- **Releases are enforced rather than remembered.** `scripts/release.sh
  <version>` cuts a release and refuses an empty `[Unreleased]`, a dirty tree,
  a duplicate tag, or a failing health-check; `scripts/deploy-gate.sh` refuses
  to deploy any commit that is not exactly a release tag, with no `--force`.
  Together they mean every deployed commit has notes by construction: the only
  path to production runs through a release, and a release cannot be cut
  without writing what changed. `CONTRIBUTING.md` defines what MAJOR, MINOR
  and PATCH mean for this product, and requires alert-affecting changes to
  appear under a `Changed detection behaviour` heading whatever the bump —
  because "we now alert on X where we didn't" is what an operator needs, and
  it is exactly what a version number cannot express.
- **`sentinel version` reports the running version and the commit it was built
  from**, and the ops dashboard shows the same line in its footer. The commit
  is passed into the image as a build argument by the deploy, since the image
  has no `.git`; a build that was not given one reports `unknown` and exits
  non-zero rather than naming a commit it cannot vouch for. Until now the only
  way to know what was deployed was to trust that the deploy worked.
- **`detectors/withdrawal.py` covered by hand (#65).** The CRITICAL
  money-leaving path, and the module the automated mutation check is
  structurally blind to: it has no conditionals, so it produces no
  decision-point mutants and the silence read like coverage.

  Unrestricted mutation score over the module alone, via the new
  `scripts/mutation-module.sh`: **59 mutants, 40 surviving (67.8%) before, 0
  surviving (0.0%) after.** The automated floor did not move and cannot.

  The tests work down the evidence dict field by field, asking what a wrong or
  missing value costs. `order_id` is the one that matters: `_dedup_key` skips
  evidence fields that are `None`, so an empty `order_id` silently *widens* the
  dedup identity rather than failing — two withdrawals become one alert and the
  second is suppressed for a whole CRITICAL cooldown. That is G-11's shape, and
  nothing had pinned that the derived identity introduced to fix G-11 actually
  distinguishes two withdrawals.

### Added
- **Two-step sign-in and a member security page (G-28, #32).** `/security` in
  the portal: turn two-factor on, see the recovery codes once, turn it off
  again with a current code.

  Sign-in is now two steps when 2FA is on. A correct password issues **no
  session cookie** — only a short-lived signed *pending* token proving that
  this browser presented a correct password moments ago. The session comes
  after the code. If a cookie were issued at step one the second factor would
  be decoration.

  The pending token is deliberately not interchangeable with a session: it is
  signed over a different context, expires in minutes rather than hours, and
  the email comes from the token rather than from the form — reading it from a
  form field would let anyone reaching the endpoint name whichever account they
  liked.

  Failed codes count on the same throttle as failed passwords. Six digits is a
  million guesses, and an endpoint that accepts them as fast as they arrive is
  not a second factor.

- **Two-factor authentication (G-28, #32).** RFC 6238 TOTP on `hmac` and
  `hashlib` — **no new dependency**. This is the module that decides whether
  somebody gets into the thing protecting a customer's exchange accounts, and
  a supply-chain dependency inside the authentication path of a security
  product is a worse trade than forty lines of HMAC.

  Built against a written list of attacks, each with a probe that breaks the
  guard and confirms a test dies:

  - **Replay.** A code is valid for up to ninety seconds — long enough for
    somebody who read it over a shoulder or found it in a proxy log.
    `verify_code` returns the *step* it matched, the caller stores it, and
    every step at or below it is then refused. Blocking only the exact code
    would leave the skew tolerance as a replay window.
  - **A widened window.** ±1 step, and `SKEW_STEPS` is a constant rather than
    a parameter — a call site that could widen it eventually will.
  - **Reading the secret.** Encrypted at rest with the same cipher as exchange
    API keys.
  - **Turning it off with a stolen session.** Disabling requires a current
    code — proof of possession, not proof of a cookie — and emails the owner.
    2FA that whoever holds the session can remove protects nothing an attacker
    cannot undo in one click.
  - **Re-enrolling over live 2FA**, which would be a reset with no proof of
    possession. Refused while a confirmed enrolment exists.
  - **Locking the customer out during enrolment.** The secret is stored so it
    can be scanned, but 2FA is not *in force* until a code has verified.
  - **Recovery codes**, which bypass the second factor and are therefore worth
    what the password is: 48 bits, hashed at rest, single-use, and retired
    when the secret they were printed against is.

  **Operator 2FA is not optional.** Both halves: an enrolled operator cannot
  disable it, and `operator_login_requires_code` returns True regardless of
  enrolment, so an *unenrolled* operator cannot complete a sign-in either.
  Without the second half, "operators must have 2FA" would be satisfied by
  nobody ever enrolling.

### Added
- **Customers can reset a forgotten password (G-29, #33).** `/forgot` and
  `/reset` in the member portal, linked from the sign-in page.

  A reset flow is an authentication bypass by design — it hands access to
  someone who cannot prove who they are — so it is built against a written
  list of attacks rather than against the four criteria:

  - **Customer enumeration.** The response is one sentence, identical for a
    known and an unknown address, and **both paths are padded to a common
    timing floor**. Issuing a token writes twice and sends mail; an unknown
    address does neither, and without equalisation a stopwatch answers the
    question the wording refuses to. A Minas customer is by definition
    somebody holding crypto worth monitoring.
  - **Token theft from a database read, a backup or a log.** Only the SHA-256
    of the token is stored. The plaintext exists once, in one email.
  - **Reuse.** Single-use, one hour maximum, and the TTL is *capped* rather
    than trusted — a caller passing a longer one cannot mint a link that
    outlives the promise. Requesting a new link retires the old one, so a
    mailbox does not accumulate standing bearer credentials.
  - **Keeping a session obtained before the compromise was noticed.** New
    `session_epoch` on `portal_members`, baked into every cookie and checked
    on every request. Bumping it on a password change retires every cookie
    ever issued to that member — including on the ops dashboard's reset, which
    is the path used when a customer phones in compromised.
  - **A silent password change.** Every change emails the owner. Not a
    receipt — a tripwire. It is the only signal they will get that somebody
    else has their mailbox, and it is guarded so a dead SMTP server cannot
    leave a locked-out customer locked out.

  The reset page renders identically for a valid and an invalid token:
  validating on GET would burn a single-use token just to draw a form, and
  would be an oracle for testing guesses without spending an attempt.

  No session is issued on a successful reset. Setting a password proves
  control of a mailbox, not knowledge of the password, and signing them
  straight in would make the link a login.

### Changed
- **The ops dashboard now says what a portal credential will open (#61).**
  The "Generate portal password" button sits on one account's page and read as
  an action on that account. It is not — portal identity is the email, so the
  credential opens every account that address is a contact on, including ones
  the operator was not looking at.

  The card now lists those accounts **by name, before the button**. Not a
  generic caution: a warning shown every time is a warning nobody reads, so it
  appears only when there is genuinely more than this account in scope.

  The affordance is fixed; the *model* is unchanged and is logged as decision
  10. Per-account credentials are a join table, a migration and a question
  about what portal identity is — but nobody can now share three accounts
  while believing they shared one.

### Added
- **Escalation reaches the customer's own contacts, and tells Minas it
  happened (G-23, #27).** Two of that issue's four criteria; the *timings*
  remain the product decision it is blocked on.

  Escalation went to the **global** severity channels — the same ones the
  original alert used. So an unacknowledged CRITICAL re-fired down exactly the
  path that had already failed to get a response, and the customer's own email
  and phone, including the secondary contact G-21 exists to provide, never saw
  it. Escalation that repeats the unanswered channel is not escalation.

  It now also goes through `ContactRouter` to both the primary and secondary
  contacts, guarded on its own so a failing SMTP cannot take the global
  escalation down with it (#57's rule).

  And every escalation pages an operator, not only the ones that fail to
  deliver. `escalation_delivered_to_nobody` is about our machinery breaking;
  `critical_alert_escalated` is about the customer not answering a CRITICAL for
  the full window. Both are worth knowing, and both fire when both are true.

  `Engine` now accepts a `ContactRouter` rather than building one privately —
  a private one could not be given to the escalator, and could not carry the
  operator alerter it needs for #58's missing-provider page.

### Added
- **Every incident has its own page (G-25, #29).** `/incident?id=N` in the
  member portal: what was detected, when, on which account, the balances as
  they were **at the time**, the full evidence, and a
  detection → notification → acknowledgement timeline. Linked from every alert
  row.

  The page is evidence — a customer hands it to an exchange, an insurer or the
  police — so the constraint that shaped it is what it is *allowed to claim*.
  An alert with `delivered_to = 0` was detected and reached nobody (#57), and
  the page says exactly that rather than rendering it as "notified" because a
  row exists. Undoing that distinction in the one document where it matters
  most would be the worst thing this page could do. An unacknowledged incident
  says "not acknowledged" rather than omitting the stage, because an absent
  row reads as an unfinished page rather than as a fact.

  Balances come from the snapshots either side of the alert, never the current
  balance: the reader is reconstructing a past state.

  **Export is the browser's print-to-PDF**, with print styles, not a generated
  file. A PDF library is a new dependency in a security product, server-side
  rendering drifts from what the customer actually saw, and the browser's own
  footer carries the URL and a timestamp — better provenance for evidence than
  a document we composed ourselves.

  **Not built:** the "what to do next" wording. That is advice with
  consequences and the wording is the whole of it — logged as decision 9.

### Added
- **Group A of the logged-and-swallowed sweep now pages (#58).** Three
  conditions, chosen on one test: is this *permanent for the life of the
  process* or *the last resort*, rather than a transient the next cycle
  retries. A page for every entry in that issue would train an operator to
  ignore the channel, which is the same outcome as not paging at all.

  **Escalation reached nobody** — CRITICAL, and the only entry with a real
  claim on it. Escalation is what happens when a CRITICAL goes unacknowledged;
  there is nothing after it. Only on *zero* channels delivering: one dead
  channel alongside a working one is degraded, not uninformed.

  **A channel routed for a severity but never configured** — HIGH, at startup.
  Every alert of that severity is under-delivered for the whole run. Also pages
  when routing *falls back to defaults*, which is arguably worse: the alerts go
  somewhere nobody chose.

  **A contact channel set on an account with no provider** — HIGH, once per
  channel per process. Every alert that account should receive through it
  silently never happens.

  Deliberately **not** paged, and commented so the next sweep doesn't
  re-litigate it: per-event delivery failures. One bounced email is noise; a
  rising rate is a broken SMTP config, which is a metric-and-alarm shape.

- **An account onboarded with a partial baseline is now visible (#58, group
  C).** Seeding trading and transfer history is best-effort on purpose — a
  broken exchange endpoint should not block onboarding, and that is unchanged.
  But the consequence lands later and on the customer: an account seeded
  without its trading history false-positives on `never_touched_pair_activity`
  for pairs they have always traded, and nothing distinguished it from an
  account that started complete.

  The gap is now recorded as a `baseline_seed_incomplete` event **on the
  account**, so months later the reason is attached to the thing being
  investigated rather than to whatever log file was open at the time — and
  paged, because it is fixable now by re-seeding. `sentinel onboard` prints it
  to stderr instead of paging: the operator who ran the command is standing in
  front of the output, and paging someone about something they are currently
  reading is how a channel gets muted.

### Fixed
- **A genuinely failed migration no longer looks like a no-op (#58, group D).**
  Every migration ran under `contextlib.suppress(sqlite3.OperationalError)`
  because re-adding an existing column raises — but so does a renamed table, a
  locked database, or a typo in the DDL. Those were indistinguishable, so a
  database that had not actually migrated started the service anyway and every
  query against the new shape failed somewhere far from the cause. Only
  "duplicate column name" is a no-op now; anything else stops the process.

- **A withdrawal we cannot identify no longer disappears quietly (#67).** When
  an exchange sends a withdrawal with neither an `id`/`txid` nor a timestamp,
  its derived identity is a digest of coin, amount, address, tag, network and
  type — so two separate withdrawals of the same amount to the same address
  collide, and every one after the first is dropped as "already seen".

  That is a missed CRITICAL on the money-leaving path, and it happened in
  complete silence: no log, no metric, no page, and a poll that reported
  success. The identity docstring described the limitation and called it
  something an operator should look at directly. Nobody was looking, because
  nothing told them — documentation is not notification.

  The condition now pages the operator at HIGH and increments
  `sentinel_unidentifiable_withdrawals_total`. **The collision itself is not
  fixed**: the options for fixing it trade against the property that one
  settling withdrawal keeps one identity and does not re-page the customer on
  every poll, and none of them should be chosen without knowing how often this
  happens. The counter is what makes that knowable.

  Checked *before* `record_seen_withdrawal`, since that call is what swallows
  the duplicate — after it there is nothing left to notice. Counted per record
  so the frequency is real, paged once per poll because the condition is about
  the exchange's response format rather than any one record.

- **Both mutation scripts aborted on a perfect score.** `grep` exits 1 when it
  matches nothing, and "nothing" here is the survivor list — so under
  `set -o pipefail` the run died at exactly the moment every mutant was killed,
  printing no number. Found by being the first module to reach zero.

### Added
- **Snooze caps are per severity (G-26, #30, decision 8 answered).** CRITICAL
  1 hour, HIGH 4 hours, MEDIUM and LOW 24 hours, in
  `alerting.snooze_cap_hours`.

  Acknowledging already stops escalation and already says "I have seen this"; a
  snooze only suppresses *repeats*. The only legitimate reason to suppress
  repeats of a CRITICAL is that you are dealing with it right now — a short
  window by definition. A drain unfolds over minutes to hours, so at 24 hours a
  CRITICAL snooze was long enough for the rest of one to finish in silence.
  Re-snoozing costs one tap and forces a fresh decision.

  A configured cap of `0` means that severity cannot be snoozed at all. The
  lookup reads it with `is not None` rather than truthiness — a deliberate zero
  read as "not set" would hand back the built-in cap and silently re-enable
  the suppression an owner had turned off. An unrecognised severity gets the
  **shortest** cap, not the longest.

- **Threshold changes are recorded, with a name where there is one (G-12,
  #16).** The last criterion on #16, blocked since March on there being no
  operator identities; #54 unblocked it.

  A detection threshold is the difference between an alert and silence, so an
  unrecorded threshold change is an unrecorded change to *what this product
  notices*. Take `polling.fast_loop_seconds` from 45 to 3600 and the account is
  effectively unwatched while every dashboard still reads healthy.

  Two paths, deliberately distinguishable:

  `sentinel set-threshold --as <operator> <setting> <value>` changes one
  threshold attributably. It validates against the schema **before** writing —
  a bad value on disk means the service falls back to defaults for that whole
  section until somebody notices, which is the G-12 failure with an extra step,
  and this command would have been the thing that caused it.

  An edit made directly to `rules.yaml` is caught by the reload diff and
  recorded with **no actor**. That is not a gap waiting to be filled: nobody
  can say who edited a file on a server, and writing in the process owner or
  `system` would make the log claim a person acted. An unattributed threshold
  change is itself the finding.

  New `config_changes` table rather than rows in `events`, whose `account_id`
  is `NOT NULL` — a threshold is fleet-wide and belongs to no customer, and
  relaxing that column means rebuilding the forensic log.

  The recorder is wrapped so that a failure to write the audit line can never
  stop the reload. Running thresholds older than the file is the exact failure
  G-12 exists to prevent, and reintroducing it through the logging of it would
  be a poor joke.

- **Customers can snooze a specific alert (G-26, #30).** New `snoozes` table,
  a control on every unacknowledged alert row, and a check in `dispatch` that
  withholds the delivery while a snooze is live.

  Three properties keep this from being a way to turn the product off:

  A snooze is keyed on the alert's **dedup key**, not its rule. Snoozing
  "withdrawal to an unrecognised address" because you recognise *this* address
  leaves the next one — to a different address, which is the case the rule
  exists for — as loud as ever.

  A snooze **always expires**. `expires_at` is NOT NULL and the duration is
  clamped where the row is written rather than in the form, so the table cannot
  represent a permanent mute even if every caller is wrong. A cap enforced only
  in a dropdown is not a cap; the form is a POST that can be retyped.

  A snooze **silences the phone, not the monitoring**. The detection still
  runs, the `alerts` row is still written, and the portal still shows it —
  marked `snoozed_until`, which is deliberately a different column from
  `delivered_to = 0`. That one means we tried to tell someone and reached
  nobody (#57); sharing it would turn every snooze into a false report of a
  broken alert channel.

  Escalation holds while a snooze is live and resumes when it expires — which
  is what snoozes expiring is *for*.

  **Left undecided:** whether a CRITICAL should be snoozable for the same 24
  hours as everything else. The cap is derived from the digest cadence, which
  is honest for a MEDIUM and an assumption for a CRITICAL. Logged as decision
  8 in `docs/DECISIONS-NEEDED.md` with the concrete risk written out, rather
  than picking a shorter number by feel.

- **Customers can acknowledge their own alerts from the portal (G-22, #26).**
  Acknowledgement existed for operators (`sentinel acknowledge --as <name>`,
  #54) but there was no way for the person the alert is *about* to say they
  have seen it. Each unacknowledged alert now carries an Acknowledge button in
  its own row; acknowledged ones show who acknowledged it and how long the
  alert waited.

  The elapsed time is the point of that last part: four minutes means someone
  read the alert, nine hours means someone eventually found it, and until now
  the two timestamps existed but nobody ever subtracted them.

  This is the first member-facing **write** in the portal, and the first thing
  a customer can do that makes the product quieter — acknowledging stops
  escalation. So the account scope lives inside the UPDATE statement itself,
  not in a check before it, and a member acknowledging an alert that isn't
  theirs gets the same not-found page as one that doesn't exist.

  **Not built: acknowledging from a link in the alert email.** "One tap from
  the alert" could mean a tokenised URL that acknowledges without a login.
  That would let anyone holding the email — including whoever compromised the
  mailbox that the account takeover started from — silence the alarm about it.
  The button is one tap from the alert *in the portal*, behind the session.

- **13 more tests pinning thresholds and evidence (#66).** Decision-point
  mutation score **81% → 92%**; combined with the commit below, **60% → 92%**.
  Every threshold in the product is a comparison, and every one of them
  survived being loosened or tightened by one notch. A boundary is the one
  input an attacker can choose — a withdrawal timed to land exactly on the
  window edge, a fee at exactly the multiplier — so these were the values most
  worth checking and the ones nothing checked. Also pinned: alert evidence
  reports the quantity that **executed**, not the quantity ordered. The alert
  still fires with the wrong number otherwise, and the number is what the
  recipient acts on.

  One boundary was a live false-alarm risk: a fee baseline of exactly zero —
  what every account has on its first observation — must seed rather than
  alert. Tightened by one character it becomes `spent > 0`, and every account
  alerts on the first fee it ever pays.

  The eight remaining survivors are documented in `scripts/mutation-check.sh`
  as not gaps: two equivalent mutants with no distinguishing input, and six
  `or` chains that only pick wording for a log line.

- **23 tests pinning the outflow-attribution and skip-vs-stop decisions
  (mutation survivors, #66).** The decision-point mutation score went from
  **60% to 81%** — 43 surviving mutants down to 20 — with no production code
  changed, because in every case the code was already right and nothing was
  holding it there.

  Two clusters, chosen by consequence rather than by count:

  `tests/test_outflow_attribution.py` covers
  `quantity_drop._explained_outflow_for_coin`, the function that decides whether
  a balance drop is *explained*. Mutating `and` to `or` in its side/currency
  checks made a sell of a **different** coin count as an explanation for this
  coin's drop — a real theft, silently marked accounted-for, no alert. That is
  the worst failure this product has, and nothing failed. The tests are grouped
  by which way the error runs: over-crediting (theft looks explained) and
  under-crediting (the customer gets a CRITICAL about their own trade).

  `tests/test_skip_does_not_stop.py` covers the eight `continue` statements that
  survived mutation to `break`. Every detector loop skips records it can't judge
  — no reference price, no timestamp, no currency. Turned into `break`, the
  **first** unjudgeable record ends the scan and every later record in that poll
  goes unexamined. Each test puts a skippable record first and a detectable one
  second. Both are reachable in production: `reference_prices` only holds symbols
  whose ticker fetch succeeded, and the poller's per-symbol fallback is capped at
  ten.

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

## [1.0.0] — 2026-07-05

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

[Unreleased]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.12.0...HEAD
[1.12.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.11.1...v1.12.0
[1.11.1]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.11.0...v1.11.1
[1.11.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.10.0...v1.11.0
[1.10.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.9.0...v1.10.0
[1.9.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.8.0...v1.9.0
[1.8.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.6.2...v1.7.0
[1.6.2]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.6.1...v1.6.2
[1.6.1]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.6.0...v1.6.1
[1.6.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.5.2...v1.6.0
[1.5.2]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.5.1...v1.5.2
[1.5.1]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.4.2...v1.5.0
[1.4.2]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.4.1...v1.4.2
[1.4.1]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.4.0...v1.0.0
[0.4.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/releases/tag/v0.1.0
