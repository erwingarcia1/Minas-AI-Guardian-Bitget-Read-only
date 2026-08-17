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

### Added
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
    the retention pairs-test, which exists for exactly this.
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

[Unreleased]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.4.0...v1.0.0
[0.4.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/releases/tag/v0.1.0
