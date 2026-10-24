# Contributing

Internal engineering guide for Minas AI Guardian. The repo is private and
proprietary (see [LICENSE](LICENSE)).

## Local setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"     # add ".[dev,triage]" for the AI triage layer
cp .env.example .env                   # generate SENTINEL_MASTER_KEY (command inside)
```

Generate a master key:
```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Running

```bash
.venv/bin/pytest -q                    # full suite — mocked CCXT, never hits a live exchange
.venv/bin/python scripts/soak_test.py  # 100-account synthetic soak
.venv/bin/sentinel run                 # engine + dashboard (:8390) + member portal (:8391) + metrics (:9109)
scripts/health-check.sh                # every gate, one table — "Is everything okay?" below
```

## Hard rules

1. **Read-only is sacred.** Any change touching onboarding, the exchange
   clients, or the read-only gates must keep the fail-closed guarantee and add
   trigger *and* non-trigger tests. A trade/withdraw key must never be
   storable. See [SECURITY.md](SECURITY.md).
2. **Never verify against live exchanges in tests.** Mock CCXT. The suite must
   run offline and deterministically.
3. **`Decimal` for all money/quantity math; UTC for all timestamps.** No
   floats for balances/amounts.
4. **No secrets in code, config, logs, or git.** `.env`, `sentinel_data/`, and
   `*.pem` are git-ignored — keep it that way.
5. **Every detector ships with tests** (one fixture that must fire, one
   adjacent case that must not).
   **And every rule decides its advice**: a new rule identifier - a
   detector's, an operator page's, a notice's - goes into `RULE_ADVICE` in
   `sentinel/alerts/base.py` as `ACCOUNT_ADVICE` or `NO_ADVICE` with the
   reason; `tests/test_incident_advice.py` fails on one in neither. Advice
   is a property of the rule, not of the message format: on 2026-08-31 the
   withdrawal runbook rendered under a liveness page about a missing backup
   directory because it was attached to the format, and every rule got it.
   **And of the diagnosis** (2026-09-23): a rule that fires with an
   ESTABLISHED cause - one the product reached from evidence it holds, such
   as the host-level auth failure - carries the cause on its evidence under
   `CAUSE_KEY`, and `CAUSE_ADVICE` maps the cause to the advice that
   replaces the rule's. Never special-case a cause inline in a renderer:
   the next shared cause (an exchange-wide outage, a route, a certificate)
   is a row in that map and its text beside it, so the email, the operator
   line, the escalation copy and the incident page all change together.
6. **Verify against live docs, never memory.** New exchange endpoints/fields
   get cited from CCXT source or the exchange's docs; anything unverifiable is
   marked UNVERIFIED rather than guessed.
   **And an operations command is verified by running its read-only twin
   against the machine it will be run on.** `status` before `off`,
   `--dry-run` before the write, `SELECT` before `UPDATE`. The vendor's page
   gives you the shape; only the box gives you the arguments. On 2026-09-23
   the Funnel withdraw command in docs/deployment.md was checked against
   Tailscale's CLI reference and was still wrong: the form was right and the
   port was invented - `--https=8391`, a port Funnel cannot listen on -
   because `tailscale serve status` was never run against the box, which
   would have shown the listener on 8443. The doc obeyed the first half of
   this rule and shipped a command that did nothing (corrected 2026-09-25,
   decision 20; Tailscale itself has since left the project, decision 22e,
   and the lesson stands).
7. **One session per working tree.** Before starting any issue, confirm no
   other `claude` process (or person) is working in this directory — and
   NEVER start `claude --continue` while a background job holds this tree,
   or a background job while a session is live, or vice versa. A resumed
   session and the background job it was resumed from are two sessions. The
   design-guard probes patch `sentinel/` in place, so a second session's
   edit inside that window is restored *away*; and two sessions writing the
   same docs do not conflict, they duplicate. Measured 2026-08-25: a
   forked duplicate ran for two and a half hours before the alone-check
   caught it, and in that time it wrote a false claim into
   docs/DECISIONS-NEEDED.md — "opt-in enrolment built" for #87, code that
   does not exist — plus duplicate decision rows and a second section 19.
   Late detection cost: the false claim stood in a document the owner
   decides from, a cleanup commit and its ~9-minute push, and every log
   entry from that window had to be re-read against `git log` before it
   could be believed. Early detection costs one `pgrep` line. The check must
   look for a *peer session's* signature — `git commit`/`git push`/`gh run
   watch` chains under a bash this session did not start — not only for
   gate processes.

## Adding an exchange

1. Confirm its read-only signal from live CCXT source + the exchange's docs.
2. Add a client to `sentinel/exchange.py` (subclass `ExchangeClient`) and wire
   `make_client` + `SUPPORTED_EXCHANGES`.
3. Add a fail-closed gate in `sentinel/onboarding.py` and dispatch it.
4. Widen the schema `CHECK`, the CLI `--exchange` choices, and the form.
5. Tests: read-only accepted, trade/withdraw rejected, ambiguous fails closed.

## Branch & commit conventions

- Work on a feature branch; open a PR into `main`. CI (pytest) must pass.
- Commit subject: imperative, ≤ ~72 chars; body explains the *why*.
- Bump [CHANGELOG.md](CHANGELOG.md) under `[Unreleased]`; releases are cut with
  `scripts/release.sh` — see **Releases and versioning** below.

## Releases and versioning

This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
and [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Both were already
in use informally; what follows is what the levels mean **for this product**,
so the question "is this a minor or a patch" has an answer that does not depend
on who is asking.

The audience for a version number here is an operator deciding whether they can
upgrade during a quiet afternoon or need to schedule a window. Everything below
is written to answer that question and no other.

### MAJOR — the operator must do something before upgrading

Reserved for upgrades that are not `git pull` + rebuild. Specifically:

- **Config schema changes that reject an existing `rules.yaml`.** A file that
  the running version accepts and the new version refuses.
- **Database migrations that are not reversible.** Dropping a table or column,
  rewriting stored values, anything that means the previous image can no longer
  read `/data/sentinel.db`. Additive migrations (`CREATE TABLE IF NOT EXISTS`,
  `ALTER TABLE … ADD COLUMN`) are *not* MAJOR: the old image ignores what it
  does not know about, so a rollback works.
- **Removed settings.** A key an operator may have in their `rules.yaml` that
  now does nothing, or is rejected.

If a release is MAJOR, the notes must open with what the operator has to do,
before anything else. That paragraph is the reason the number changed.

### MINOR — new capability, upgrade at will

New detectors, new features, new alert channels, new CLI commands, new pages.
Additive database migrations live here. An operator can upgrade without reading
anything, and the product does more than it did.

### PATCH — bug fixes, including detector fixes

Fixes to existing behaviour, including detector fixes. No new capability.

### The awkward case: a detector fix that changes what gets alerted

**A detector fix is technically a PATCH, and it can still change behaviour
customers rely on.** "We now alert on X where we didn't" is not a bigger version
number — but it *is* the single thing an operator most needs to read, and it is
exactly what a version number cannot express.

So it goes in the notes under its own heading, **`### Changed detection
behaviour`**, whatever the version bump. Not under `Fixed`, where it reads as
housekeeping and gets skimmed.

Anything belongs under that heading if, after upgrading:

- an alert fires that would not have fired before (a fixed blind spot, a
  detector that was inert, a severity raised);
- an alert stops firing that used to (a fixed false positive, a widened
  tolerance, a new suppression);
- the same condition reaches a different person, or reaches them differently
  (routing, escalation, digest-vs-page, snooze and cooldown changes).

Write each one as the operator experiences it — *what changes about the alerts
they see* — not as the code change that caused it. "Withdrawals with no
reference ID now alert again; they were silently dropped since 0.4.0" is the
sentence. "Fixed `_dedup_key` null handling" is not.

The test of a good entry: an operator who reads only that heading can predict
what their inbox looks like tomorrow.

### Cutting a release

```bash
scripts/release.sh 1.2.0     # refuses on an empty [Unreleased], a dirty tree,
                             # a duplicate tag, or any health-check failure
```

The slow gates (probes, mutation floor) are skipped locally only when
scheduled CI provably ran them on this exact tree and passed — see
"Where the slow gates canonically run" below, under "Is everything
okay?"; anything unprovable runs
them locally, and a red scheduled run on this code refuses the release.

It moves `[Unreleased]` under a dated heading, bumps `pyproject.toml`, adds the
compare link, commits, and creates the annotated tag `v1.2.0`. Push with
`git push && git push --tags`.

**There is no release without notes.** The script refuses an empty
`[Unreleased]`, which is the whole point: the note is not paperwork that
follows the release, it is the thing being released.

**Deploys refuse any commit that is not exactly a tag** (see
[docs/deployment.md](docs/deployment.md)), so the only way to ship is to cut a
release, and cutting a release produces the note as a side effect. There is no
`--force`. If something is urgent, cut a PATCH — it takes thirty seconds.

## Deploying

The production host holds a **read-only deploy key** for this repo, so a
deploy is a fetch on the server — nothing is shipped from a laptop. After
cutting and pushing a release (above), it is one command:

```bash
# EC2 key pair (chmod 400 the .pem; your IP must be in the security group's
# port-22 rule - that rule is the only way onto the box):
ssh -i "Minas-AI-Guardian-Key-Tokyo.pem" ubuntu@57.181.51.14

sudo /opt/minas-guardian/scripts/deploy.sh            # latest release tag
sudo /opt/minas-guardian/scripts/deploy.sh vX.Y.Z     # or a specific one
```

`deploy.sh` **deploys a release tag, never main's tip**, and refuses a
dirty tree. The sequence it runs, its refusals and the reasoning behind
each step: [docs/deployment.md](docs/deployment.md) "Deploying code
changes".

Push first: the server fetches from GitHub, so unpushed work cannot
deploy — GitHub and production stay in lockstep by construction. Don't
edit tracked files on the server (the deploy refuses a dirty tree); tune
`rules.yaml` through git and ship it in a release — it hot-reloads on
arrival, no restart.

Full procedure, region notes, and the DB-migration recipe:
[docs/deployment.md](docs/deployment.md).


## Is everything okay?

```bash
scripts/health-check.sh          # every gate (~20 min, mutation dominates)
scripts/health-check.sh --fast   # skips the two multi-minute gates (~4 min)
```

One command, nine gates, one summary table: ruff, the suite, the coverage
floor, `pip-audit`, the issue-status audit, a freshness check on the
scheduled CI gates, the release-objects check (#94: every version tag has a
GitHub Release), the design-guard probes and the mutation floor. Non-zero if
any of them fail.

**Where the slow gates canonically run (#86): scheduled CI, not the inner
loop.** The probes (~13 min) and the mutation floor (~20 min cold on a CI
runner, ~50 locally) are too slow for feedback anyone acts on per commit.
The division of labour:

- **pre-push hook + push CI** — ruff plus the full suite, on every commit.
- **scheduled CI** (the `scheduled-gates` job, weekly + `workflow_dispatch`)
  — probes and mutation floor, provably cold (a fresh runner has no caches,
  and the job asserts it). On failure it files an issue whose **title**
  distinguishes a real regression from a gate that produced no result;
  `scripts/scheduled-gates-report.sh` holds that classification and is
  execution-tested in `tests/test_scheduled_gates.py`.
- **this script** — everything, locally, when a change touches probe-covered
  code or detection behaviour; `--fast` otherwise.
- **release.sh** — demands the slow gates be green for the exact tree
  being tagged, but no longer insists on running them itself (#93): when
  the most recent scheduled CI run sits on an ancestor of HEAD with no
  gate-relevant path changed since and is green, the release runs the fast
  gates only. Every unprovable case — gh missing or unauthenticated,
  GitHub down, no covering run, gate-relevant changes since — falls back
  to the full local run; a covering run that is RED refuses the release
  outright. The predicate (`scripts/scheduled-gates-cover.sh`) may only
  make a release cheaper, never weaker.

  **#93 alone is NOT sufficient for a safe release — #93 plus the survivor
  ledger is.** The predicate proves the slow gates *ran* on this code and
  were green; it does not prove the ratchet *noticed* anything. A scheduled
  run is green when the score merely clears the floor, and the floor has two
  points of headroom by design — so a decision can stop being pinned, the
  weekly run stays green, and #93 covers the release. What closes that is
  `scripts/mutation-known-survivors.txt`: a survivor not in the ledger fails
  the gate whatever the score, so the scheduled run that #93 trusts is red
  for exactly the case the floor lets through. Remove the ledger and #93
  becomes a proof that the gates ran, not that they held. Do not remove it
  thinking it duplicates the floor; it covers what the floor cannot.

The freshness gate closes the quiet failure of that arrangement: a cron that
silently stops firing reads as permanently green, so
`scripts/scheduled-gates-freshness.sh` asserts a scheduled (or dispatched)
run completed within 8 days — "did it run", not "did it pass"; a red run
already files its own issues.

```
GATE                         RESULT     TIME  NOTES
---------------------------- -------- ------  -----
ruff                         pass         0s
tests                        pass       179s
coverage floor (78%)         pass         0s
pip-audit                    pass        39s
issue-status audit           pass         3s
scheduled gates ran (8d)     pass         2s
release objects              pass         3s
design-guard probes          skipped       -  --fast
mutation floor (96%)         skipped       -  --fast
```

Each gate is documented in its own section below; this only runs them. Three
things it is deliberate about:

- **It does not stop at the first failure.** The point of the table is knowing
  all nine answers. Failed gates get their last 30 lines printed inline.
- **It keeps the logs when everything passes.** Every run writes to
  `.health-check-logs/<utc-stamp>-<sha>/`, named at the top of the run and kept
  for 14 days whatever the result. Deleting them on success sounds tidy and is
  backwards: a pass is the result most worth being able to audit. The mutation
  gate can report 108/119 in eight seconds off mutmut's per-mutant cache, which
  is a real pass and looks exactly like a hollow one — the difference is only in
  the log, and re-running to see it costs twenty minutes.
- **A gate it could not run is `skipped`, never `pass`, and the run exits 2.**
  No `gh`, no authentication, no `mutmut`, or another probe run already holding
  the lock — the question went unanswered, and an unanswered question reported
  in green is the failure mode most of this file exists to prevent. `--fast` is
  the one exception: you asked for the skip, so it still exits 0, and the notes
  column says which gates you gave up.
- **It does not know the floors.** The coverage floor is read out of
  `.github/workflows/ci.yml` and the mutation floor out of
  `scripts/mutation-floor.txt`. A number retyped into this script would go on
  printing "pass" after CI started failing.

Two smaller traps it closes. `.coverage` from an earlier run sits in the working
tree, and `coverage report` will read it without complaint — so the floor gate
refuses to run unless pytest just rewrote the file, rather than scoring a
month-old run. And the floor and the suite are checked separately instead of via
`--cov-fail-under`, because otherwise a coverage shortfall and a failing test
are the same red and the first question is always which one it was.

**A killed check is not a passed check.** The health check takes about twenty
minutes and the design-guard probes alone take thirteen, so wrapping it in a
timeout shorter than that produces exit **143** — SIGTERM — with a truncated log
and no summary table. 143 is not 0. It is not a failure either: it is *no
result*, and it must be reported as no result and re-run, never summarised as
"it didn't fail". Same for any gate you interrupt.

```bash
{ timeout 3600 scripts/health-check.sh; echo "EXIT=$?"; } > run.log 2>&1
```

Capture the code explicitly rather than reading the tail of the output. A run
killed part-way through still leaves a plausible-looking log of the gates that
did finish, and the gates that never ran leave nothing at all — which reads
identically to a clean run of a shorter suite. This cost a re-run on 2026-08-17,
after a `timeout 1200` on a gate this file documents as ~20 minutes.

Note that `--check` on the mutation gate **ratchets**: it raises
`scripts/mutation-floor.txt` to `max(floor, score - 2)`, never lowers it, and
that file is tracked — so a completely clean health check can leave you a change
to commit, and the run says so when it does. The two-point buffer is
deliberate. A floor dragged up to meet the score exactly leaves zero slack, and
a gate sitting on its own floor fires on the next honest change to anything it
measures; a tripwire that goes off during legitimate work is one people learn to
route around with `--no-verify`, and then it guards nothing.

If the mutation gate fails with **"no mutants ran"**, it measured nothing and
exits non-zero. Almost always the baseline suite is failing inside `mutants/`:
the runner uses `-x`, so one failure aborts stats collection before a single
mutant is tested. Reproduce it directly, which is what the error message tells
you to do:

```bash
(cd mutants && ../.venv/bin/python -m pytest -x -q tests/)
```

Historically the cause was mutmut's cache, and it is worth knowing why, because
the shape of it is easy to walk back into. `copy_src_dir()` copies everything
under `source_paths` with `if target_path.exists(): continue`. Python files are
refreshed separately by the mutation pass, which compares mtimes — so *code*
stays current. Everything else is copied once and never again. Meanwhile
`also_copy` — `tests/`, `setup.cfg`, `pyproject.toml` — *is* rewritten every
run. That combination is what made it invisible: the tests inside `mutants/`
were always current, so a stale copy in there looked impossible.

`sentinel/schema.sql` is our only non-Python source file, and it is the schema
every test's database is built from. When it lagged behind a newly added table,
the ~80 tests predating that table passed and the ones touching it failed with
`no such table`, taking the whole gate down. `--regenerate` did not help,
because it deleted nothing.

Both halves are now closed in `scripts/mutation-check.sh`: it compares each
non-Python file under `sentinel/` against its copy and rebuilds on any
difference, and every generation starts with `rm -rf mutants/`. Deleting first
also clears `mutants/mutmut-stats.json`, the cached test-to-function map — if
that map is ever built by a run in which the tests cannot see
`MUTANT_UNDER_TEST` (see the allowlist note in `tests/conftest.py`), every
function looks untested and the total comes out zero. Diagnose that one by
reading the map: `tests_by_mangled_function_name` with a handful of entries
rather than hundreds is this.

Compare contents, never mtimes: mutmut copies with `copy2`, so a stale copy
carries the *source's* old mtime and looks freshly written.

CI never hits any of it — a fresh checkout has no `mutants/` at all, which is
why this only ever bit locally.

This does not replace `gh run list --limit 1` after pushing. It runs what CI
runs; it does not run it on CI's hardware, which is where the `time.monotonic()`
defect below was found and could only have been found.

## Before pushing

Install the hooks once, per clone:

```bash
scripts/install-hooks.sh
```

- **pre-commit** — `ruff check sentinel tests`. Milliseconds. It lints the
  *working tree*, and says so when you have unstaged changes, because a hook
  that quietly checked something other than what you are committing would be
  worse than no hook.
- **pre-push** — ruff plus the full suite. Over two minutes, which is why it is
  not on every commit: a hook people disable protects nothing. Pre-push is the
  last moment before a commit can cost anyone else anything, which is exactly
  where a slow check belongs.

  The suite is now ~9 minutes, and that is longer than GitHub keeps an idle
  SSH connection open. `git push` over SSH connects *before* it runs the
  hook, so the hook passes and then the push dies with exit 141 —
  "Connection to github.com closed by remote host" — and a retry does the
  same thing nine minutes later. That was misread as a transient network
  failure for a month. Over HTTPS the upload is a fresh request after the
  hook, so the installer sets a repo-local `url.https://github.com/.pushInsteadOf
  git@github.com:` (push only; fetch is untouched) and `gh` supplies the
  credential. If you see exit 141 after "All checks passed", you are pushing
  over SSH: run `scripts/install-hooks.sh` again. Never answer it with
  `--no-verify`.

`--no-verify` is possible on purpose (a hook that cannot be bypassed gets
uninstalled) and it is never silent — and the standing rule in CLAUDE.md is
that it is never used here. A hook that blocks you is a defect to fix, not a
gate to step round.

The hooks are deliberately not the `pre-commit` tool. That would buy hook
management at the price of a dev dependency managing its own tool environments,
for two hooks running two commands that are already dependencies. What actually
mattered from that suggestion — that a clone does not silently have no hooks —
is covered by the installer and this section. What a hook manager would *not*
have given us is `tests/test_git_hooks.py`, which fails if the hooks and CI ever
check different things: a hook that drifts narrower than CI goes on passing
while it stops protecting anything, and teaches people that `--no-verify` is
the normal way to commit.


## After pushing

Check the run. A red CI is a stop condition — don't start the next piece of
work until it's green:

```bash
gh run list --limit 1
```

**Read the weekly run too.** `gh run list --workflow ci.yml --event schedule
--limit 3`. Its failures are meant to file an issue, but from 2026-09-14 to
2026-10-22 the job's token could not write issues, and five red weeks
reached nobody. A notification path is itself a thing that can fail
silently.

Green locally is not evidence CI is green. The tests run on a freshly booted
runner with fewer cores, and at least one real defect has been found only there:
`time.monotonic()` is seconds since **boot**, so any `now - last.get(k, 0) >= interval`
check is really asking "has this machine been up that long?" — which is true on
any long-lived host and false on a runner. Use `None` for "never happened", not
`0`.


## Status labels

The `status:` label describes the **current state of the feature** — not the
state it was in when the backlog was written.

| Label | Means |
|---|---|
| `status: built` | The core exists and works; criteria may remain |
| `status: partial` | Exists but materially incomplete |
| `status: broken` | An **active defect** exists in it right now |
| `status: not-built` | Doesn't exist yet |

### Why `built` is deliberately loose about completeness

An **open** issue already means work remains. So `built` vs `partial` on an open
issue carries no decision — it's a distinction with nothing hanging on it.

The distinction that *does* matter is **`broken`**: something is wrong right
now. That's the label to filter on, and it only stays useful while the others
stay quiet. Tightening `built` to mean "every criterion ticked" would reclassify
most of the open backlog to `partial`, making `partial` the default and
destroying its signal along with `broken`'s.

### The one rule worth enforcing

**A closed issue must be `built`.** Anything else on a closed issue is drift.

The common cause is mechanical: closing an issue with a `Closes #N` commit
trailer closes it **without touching its labels**, so an issue whose defect was
fixed by a commit keeps sitting at `broken` — which, on a closed issue, is a
contradiction.

Acceptance criteria are **not** part of this. Under the meanings above, unticked
criteria don't contradict `built`, so counting them produces noise rather than
findings.

### Auditing it

```bash
scripts/audit-issue-status.sh            # report; changes nothing; exit 1 on drift
scripts/audit-issue-status.sh --apply    # also apply the evidenced recommendations
```

It looks only at closed issues. Drift is reported whether or not it can be acted
on; the evidence checks — a commit on `main` references it, CI passed on that
commit, nothing it names as a blocker is still open — decide only whether
`--apply` may fix it unattended. Rows without a `*` need a person.

A `not_planned` closure never becomes `built`; marking a wontfix as built is the
most likely way a script like this does real damage.

It cannot check whether the code does what the criteria claim. Nothing
mechanical can, so it recommends rather than decides.

## Falsy sentinels

**Never use a falsy value to mean "absent".** Use `None`, and handle it
explicitly.

```python
ts = f.get("timestamp") or 0          # NO  - 0 is a valid timestamp
ts = f.get("timestamp")               # yes - then decide what None means
if ts is None: ...
```

Five shipped defects in this project were one mistake, and every one was silent:

- `(f.get("timestamp") or 0) >= last_run_ms` — an undated fill sorted before
  every window and was **never price-checked** (G-47).
- `str(record.get("id") or "")` then `return False` — an id-less withdrawal read
  as *already seen*, so **money left the account with nobody told** (G-11). The
  identical line in `record_seen_transfer` survived that fix and had to be found
  again.
- `evidence["order_id"] or evidence["coin"]` — two distinct withdrawals shared a
  dedup key and the second was suppressed. Later, off-market fills (which carry
  `symbol`, not `coin`) collapsed to `""` and **every one on an account shared a
  key**.
- `self._last_slow.get(account_id, 0)` — `time.monotonic()` is seconds since
  **boot**, so this asked "has the machine been up 10 minutes?". It passed
  trivially on any long-lived host and failed on every fresh CI runner.

The shape: a falsy default is indistinguishable from a real falsy value, and
both are indistinguishable from absent. `0` is a valid timestamp, `""` is a
valid string, `[]` is a valid routing list. The moment one stands in for
"missing", every comparison, dict key and dedup key downstream is reasoning
about a value that was never there.

**Where it matters most:** anything feeding a comparison, a dict key, or a dedup
key. `or ""` for display or string-building is fine.

**The correct shape**, when a derived value genuinely is needed — take the real
one if present, otherwise derive one deliberately, and never silently:

```python
reference = str(record.get("id") or record.get("txid") or "")
if reference:
    return f"id:{reference}"
return "sha256:" + _digest(...)       # explicit, not a fallback value
```

## A process check that matches itself always reports busy

**Never decide "is it still running" from a bare `grep` over a pattern that also
appears in your own command line.** Your shell, the `grep`, and the wrapper that
launched them are all processes, and all of them carry the pattern you are
searching for. The check then finds itself, reports the thing as alive, and
keeps reporting it alive forever — including long after it exited.

Liveness, when you have a PID, is the kernel's answer and nothing else:

```bash
kill -0 "$PID" 2>/dev/null   # exit 0 = alive
test -d "/proc/$PID"
```

Searching by name is the harder case. The `[c]laude` bracket trick used by the
standing rule above works, but **only while the bracketed form is the sole
occurrence of the pattern in your own command line.** Put the plain word
anywhere else in the same command — a `for n in mutmut pytest …` list, an
`echo`, a comment — and the plain word self-matches regardless of the brackets.
That is exactly how a dead PID got reported as "still running, 2h 10m" on
2026-08-17, where the elapsed time was the wrapper shell's own.

When in doubt, snapshot first and search the snapshot, so the pattern does not
exist in any running command line by the time the search runs:

```bash
ps -eo pid,etime,cmd --no-headers > /tmp/snap.txt   # then, separately:
grep -E "mutmut|health-check" /tmp/snap.txt
```

The general shape is the one this file keeps coming back to: a check whose
positive result can be produced by the check itself is not evidence. Compare
`test_the_scan_can_see_the_source_it_claims_to_scan` in
`tests/test_no_live_traffic.py`, which states its population before asserting an
absence, for the same reason.

## Registration guards need execution guards

**A test that a thing is *listed* is not a test that the thing *works*.** When
you add a guard asserting some registry is complete — every table has a
retention decision, every setting has a disclosure line, every module imports —
add its pair: run the machinery the registry feeds and assert the effect.

The defect that named this: `alert_triage` was added to `retention.PRUNABLE`
(#41), and `test_every_growing_table_is_covered` went green because the key was
present. The pruner still could not delete a single alert. `PRAGMA
foreign_keys = ON` with no `ON DELETE CASCADE` meant `DELETE FROM alerts` was
refused while any of the three tables referencing it still had a row, and the
`IntegrityError` propagated out of `prune_all` entirely — so **every table after
`alerts` in the dict stopped being pruned too**. Registration guard green,
retention dead, the growth metric that would have shown it frozen by the same
exception. Two tables (`sms_deliveries`, `expected_activity`) had been in that
state since long before `alert_triage` joined them.

The shape: a registry is a claim about intent, and a guard over it can only
check that somebody wrote the intent down. Whether the intent is *carried out*
is a different assertion against different code, and the gap between them is
invisible precisely because the first guard is green.

**The pair, for `PRUNABLE`** (`tests/test_retention.py`) — the pattern to copy:

- `test_every_growing_table_is_covered` — registration: every table has a decision.
- `test_every_prunable_table_actually_prunes` — execution: one row per table past
  its own window, one `prune_all`, every table empty afterwards.
- `test_every_prunable_table_has_a_row_factory` — the execution guard's own
  registration guard, because a table with no factory would be silently absent
  from the population it claims to cover.

**Prove the pair is load-bearing**: the execution guard must fail against the
tree *without* your fix. One that passes both ways is testing the registry
again, in more lines.

## Retired criteria are amended in the issue, in the same commit

**When a decision retires a criterion, the ISSUE TEXT is amended in the same
commit as the decision** - struck through, with the decision named and
dated, and the box ticked so the audit does not read it as unfinished. An
unamended criterion becomes a defect report against a choice we made on
purpose - as it did twice in audit #103: #45's "revoke it from the portal"
(dropped by decision 15, kill switch at the exchange) and #65's "the message
contains ... destination address" (barred by the no-amounts-no-addresses
rule and decision 16) were both graded as contradictions, because the issue
still promised them.

The shape: `~~old words~~ - **retired by decision N (date):** why, in one
line. Amended <date>.` A criterion that a later DESIGN retired rather than an
owner decision (#18's "tries another channel" against #57's try-every-
channel; #73's prune timer) is amended the same way, naming the commit, and
stays open to the owner to overrule.

## A green check that depended on a hole

**Ask what a passing check is resting on.** The container healthcheck
fetched the ops dashboard root unauthenticated, and it passed for weeks -
because `_gate` waived sign-in while the operator table was empty. The
probe was green *because of* a security hole. Closing the hole (#103
item 1) turned the container unhealthy the moment the first operator was
created, and the restart timer had to be stopped by hand: fixing a defect
broke a check that had been resting on it. This is the inverse of "a fix
can silently retire its own guard": here a guard silently depended on the
defect.

The sharper half: that probe also asserted the wrong thing. "An HTTP
server answered" reads healthy through a wedged poller - the dashboard
thread kept answering through #79's six dead weeks. A health check must
assert the thing the service exists to do (the engine completed a cycle:
`/healthz`), not that a socket accepts connections.

When you close a hole, list what currently passes *through* it - probes,
tests, cron jobs, dashboards - and repoint them in the same change, or
the fix ships as an outage.

## A stopped timer is not a disabled timer

**`systemctl stop` on an ENABLED unit is not `disable`.** It survives
nothing - not a daemon-reload, not a reboot, not the next thing that
touches the unit - and in `systemctl list-timers` "stopped" and
"disabled" look the same until something reloads. The verification of a
silenced timer is the page record, not the command's exit code.

The defect that named this: `minas-liveness.timer` was stopped by hand on
2026-09-02 and the owner was told the paging had stopped. It had not. The
page record shows hourly `liveness_failed` pages continuing to 2026-09-03
17:46 UTC, one missed run apart - 25 hours of paging nobody was watching
for, because the report said quiet. What held was `disable` on
2026-09-08.

When you silence a unit, `disable --now` it, read `systemctl is-enabled`
and `is-active` back, and then look at what the unit produces (the
operator_pages table, journald) after its next scheduled moment. A
mitigation that has not been observed to take is not a mitigation - it is
a claim, and the claim is what the owner acts on.

## A measurement that reads one of two counters is not a measurement

**When you measure production, enumerate the columns before you read one.**
poll_state carries two failure counters, `consecutive_failures` and
`consecutive_auth_failures`, and an account is unwatched when either
crosses the threshold. On 2026-09-22 the session's audit script flagged
"unwatched" on the first counter alone and reported nine accounts at 61-78
refused authentications as clean. The product was not blind: the dashboard's
watch cell, the portal's status and the engine all read both counters. The
instrument was, and for an hour the audit's own headline said the opposite
of what the same output's per-account rows showed.

The rule for any read-only audit: a decision the product makes (watched,
delivered, escalated) is re-derived in the query from the same inputs the
product uses - which today means `sentinel.watching.FAILURE_COUNTERS`, not a
column name remembered from a previous session - and the query prints the
inputs beside the verdict so a reader can see a verdict that contradicts its
own row. A number that cannot be checked against its inputs on the same
page is a claim, and the owner acts on claims.

## A test that waits must bound the wait

**A loop that waits for a condition the code under test produces does not
fail when a mutant removes that condition - it runs forever, and it takes
the suite with it.** mutmut reports it as a timeout (⏰), which is neither
killed nor survived; the gate lists it as unjudged and refuses the run,
which is how this was found, and a hung test in the ordinary suite is
worse: no report at all until someone kills the process.

The two that named this (2026-09-22, the Telegram link worker): a test
that ran `worker.run()` with the stop flag already set expected the loop
never to poll; the mutant `while not stop` -> `while stop` polled forever.
Another drove `run()` through one failed poll and stopped it from the first
successful poll; the mutant `offset is not None` -> `offset is None` raised
before every poll, the loop caught each one and went round again, and the
stop was never reached.

The rule: **every wait in a test is bounded by something the test
controls, never by the behaviour under test.** The shapes that work:

- A counter in the stub that ends the loop by force after N calls - by
  clearing the flag, raising an exception the loop does not catch, or
  calling `stop()` - and an assertion that the loop ended for the RIGHT
  reason (N was never reached) rather than because the bound tripped.
- A fake clock or a zeroed backoff, so a retry loop cannot sleep the suite
  (`worker.BACKOFF_BASE_SECONDS = 0`), together with the bound above.
- For anything polling a real resource (a subprocess, a file, a port): a
  deadline from `time.monotonic()` and a failure message that says what was
  waited for, never a bare `while not ready: sleep()`.

Before arming a loop in a test, ask: *if the code under test never
produces the condition, what stops this?* If the answer is "nothing" or
"pytest's global timeout", the test cannot fail - it can only hang, and a
test that cannot fail is the absence-of-evidence shape this file keeps
coming back to. The same applies to a monitor script or a wait loop in a
session: bound it, and make the timeout a reported result, not silence.

## A test that posts to a path that does not exist always passes

**Assert the route is registered before asserting who may use it.** A 404 is
not an authorization result: it is what every path nobody serves answers to
everyone, refused or not.

The defect that named this: #25's closing test proved that a secondary
contact "cannot change settings or remove accounts" by POSTing to `/account`,
`/settings`, `/accounts/1/delete` and `/account/remove` and asserting the
status was 403, 404 or 405. None of those paths ever existed. The test was
green for six weeks while `/key`, `/erase`, `/preferences`, `/pause/request`
and `/snooze` were added underneath it, every one reachable by the emergency
contact. The test could not fail, because nothing it asserted against was
real.

The shape to copy (`tests/test_member_roles.py`):

- a **completeness test** over an explicit registry of routes, each
  classified in exactly one of `PRIMARY_ONLY` / `NON_PRIMARY_ALLOWED`, so a
  new route breaks the build until somebody decides - an allowlist, never a
  denylist, because a denylist reproduces the bug the next time a route is
  added;
- a **registration test** that every classified route answers something
  other than 404, and that a made-up path answers 404;
- only then the **refusal tests**, parametrised over the registry, each
  showing the primary reaches the handler and the non-primary gets the
  refusal on the *same* path with the *same* body, and that state did not
  change.

## Mutation testing

```bash
scripts/mutation-check.sh            # run; print survivors and the score
scripts/mutation-check.sh --check    # fail below the floor, raise it on improvement
```

Restricted to **decision points** — comparison operators, boolean operators,
`continue`/`break`/`return` — over `sentinel/detectors/` and `sentinel/alerts/`.

**The instrument is pinned: mutmut 3.8.0** (`MUTMUT_VERSION` in
`scripts/mutation-check.sh`, the install line in `ci.yml`; the gate refuses
any other version, and `tests/test_scheduled_gates.py` keeps the two equal).
CI used to install whatever was newest. mutmut 3.8.0 was released on
2026-09-12 and generates mutant kinds 3.7.0 does not: the same tree went
from 322 decision points to 400. The weekly run went red on 2026-09-14 over
code that had not changed and stayed red for five weeks, and nobody was told
(see "After pushing"). A floor and a survivor ledger mean something only
against one generator. Upgrade deliberately: the new version, its new
survivors judged (tests or ledger rows), and both pins, in one commit.
That is how 3.7.0 became 3.8.0 (#106, 2026-10-22). Of the 25 survivors
3.8.0 found, 16 are now killed by tests in
`tests/test_conditional_branches.py`, and 9 are ledger rows, each argued.

An unrestricted run produced **588 survivors from 1,322 mutants** and would take
~36 hours for the whole package; roughly 60% were cosmetic (mutated log text,
SQL keyword casing, string literals). Chasing that list is a project that
stalls. The restricted run was **110 mutants** when written, and every survivor
is a decision a test could reasonably be expected to pin.

**The population includes class methods - since 2026-09-22 (#104).** The
classifier matched `^def (x_...)`: column zero, underscore after the x. mutmut
names a method `xǁClassǁmethod__mutmut_N` and indents it inside the class, so
for as long as the gate existed no method was in its population - not
`ContactRouter`, not the escalator, not `WatchAlarms`, not `OperatorAlerter` -
and "341 decision-point mutants" was read as the whole scope. Measured
honest: 538, of which 197 in methods; 30 of those were unpinned and 8 were in
functions no selected test called. `tests/test_classify_mutants.py` is the
execution guard: a generated module with an indented method mutant must be
selected. A population that reads clean because of what it excludes is the
98% story this file keeps retelling.

The score is a floor in `scripts/mutation-floor.txt`, ratcheted the same way as
the coverage floor: set from measured reality, never lowered by the gate,
raised whenever it improves. It was reset by hand ONCE, 97 -> 92 on
2026-09-22, when the population above became honest: the owner's call, in
preference to pinning thirty gaps to protect a number over a population that
was never honest. The ratchet is unchanged; the floor climbs behind each
batch of `unpinned` rows retired from the ledger.

**Known blind spot:** a module with no conditionals produces no decision-point
mutants at all, and the silence reads like coverage. `detectors/withdrawal.py`
is the case — a straight-line `Detection` builder on the CRITICAL money-leaving
path. Its mutations are evidence-key and message changes, which the restricted
run discards as cosmetic and which are nothing of the kind: `dispatch._dedup_key`
reads `evidence["order_id"]` to decide whether two withdrawals are one.

Covered by hand under **#65**. The measurement was taken once, 2026-08-13,
with an *unrestricted* mutation run over that one module: 59 mutants, **40
survivors brought to 0**. The decision-point floor did not move and cannot —
it counts mutants this module does not produce. The one-module script that
produced it has since drifted from the gate and was removed; rebuild it on
`scripts/mutation-check.sh`'s `setup.cfg` block if ever needed.

The mutmut invocation quirks (whole-package `source_paths`, newline-separated
test selection, the cached `mutants/` copy, unstable mutant numbering) are
documented at the top of `scripts/mutation-check.sh` so nobody rediscovers
them.

### Regeneration is incremental, and proven

`mutants/` is expensive to build from scratch — generation of the whole
package is single-threaded and took 2h57m and 6h34m for eighteen changed
lines on 2026-08-26 — and cheap to bring up to date: a changed file
regenerates in ~9 s (portal.py, the largest, is the bound; see the log), a
full re-collection of the test-to-function map in ~27 s, a no-op in 10 s.
So when the source manifest says files changed, the gate regenerates only
those (`scripts/mutant-refresh.py plan` deletes their copies so mutmut must
rebuild them — mutmut's own change detection is by mtime, which a restored
archive or a checkout defeats; that was the 2026-08-23 hollow pass), always
re-collects the test map (a new function with no map entry would score "no
tests"), and then **proves the result**: `mutant-refresh.py validate` reads
every `.meta`'s per-function hashes and demands each equal the hash of the
CURRENT source, computed with mutmut's own function. A missing copy, a
missing `.meta`, a recorded function absent from the source, or a differing
hash fails the proof, and a failed proof — like anything the planner cannot
map (no recorded manifest, an unchanged file with no `.meta`) — goes to the
from-scratch path. The manifest is written only after the proof. Unverifiable
is stale; fail closed.

`--regenerate` still means from scratch. The scope is `sentinel.detectors`,
`sentinel.alerts` and, since `env_setting` moved there, `sentinel.config`
(its `settings_diff` tests are in the selection for that reason: the
completeness guard refuses an unreached module rather than scoring it).

### The survivor ledger

The floor has two points of headroom on purpose (see the ratchet comment in
`scripts/mutation-check.sh`). The price, proven 2026-08-25 on a warm
`mutants/` and on a `#97` restore alike: delete one assertion from a selected
test, touch nothing under `sentinel/`, run the gate — the mutant that
assertion killed is re-judged (every mutant the gate names is, restore or
not) and comes back a survivor, 185/188 instead of 186/188, and the gate
passes. One decision quietly unpinned, inside the headroom.

So `scripts/mutation-known-survivors.txt` lists every survivor the gate
accepts — sixteen rows today, each with its reason — by file plus the `-`/`+` lines of
`mutmut show` plus the mutant's FUNCTION (since 2026-09-23: the same diff in
two functions of one file was one row, and pinning one left the row covering
the other), hashed (`scripts/mutation_survivors.py`); not by mutant number,
which changes on every regeneration. `--check` fails on any survivor not in
the ledger, whatever the score, and prints its diff. If a run shows `NEW`,
either kill it with a test or, if it is a proven equivalent, read the diff
and add the fingerprint with the reason. Adding a line is a claim; the
review is the commit that adds it. A survivor whose diff cannot be read
fails closed, the same rule as a manifest that cannot be checked.

Two things resist the ledger growing, because adding a row is easier than
writing a test and each addition looks legitimate on the day. Every row
carries its reason on the same line — `<fingerprint> <file> <kind>: <why>`,
kind one of `equivalent`, `unreachable`, `deliberate`, `unpinned` — and a row
without one fails the gate on every run, survivors or not. And the gate prints
the ledger's size on every run (`Survivor ledger: N accepted`); the weekly
scheduled-gates report carries that number as its own row. A count that only
ever goes up is the signal that rows are being added instead of tests.

`unpinned` (#104) is the honest kind for a survivor that has been read, is a
real gap, and is not yet pinned - its reason names the batch that will pin
it. It exists so the population could be made honest without calling thirty
gaps "deliberate". Those rows are the number that must come DOWN: retire a
batch by writing its tests, delete the rows, and let the ratchet take the
score. A row that stays is a test nobody wrote; the weekly count says so.

**The ledger is also what makes #93 safe** (see "Is everything okay?"): the
release predicate trusts a green scheduled run, and a run is green on a
dropped score as long as the floor's headroom absorbs it. The ledger is the
part of that run that fails on one lost kill. The two mechanisms depend on
each other; neither is redundant with the floor.

### Hand-written probes, and why they need a tool

```bash
scripts/mutation-probe.py scripts/probes/design-guards.json --workers auto
```

mutmut can only mutate what it can parse an operator out of. It cannot express
"the account scope is inside the `UPDATE`", "a failed audit write must not stop
the reload", or "attribution is required once operators exist" — design guards
with no operator to flip. Those get broken by hand instead.

**Never do that with an ad-hoc `sed`.** A pattern that doesn't match, or a
replacement whose indentation is off by four spaces, changes nothing — and an
unchanged file passes its tests, which is indistinguishable from a test killing
the mutant. That is the same failure this whole section exists to prevent, one
level up: an absence of evidence read as evidence. It has already happened here
once.

`mutation-probe.py` refuses to run tests until it has proved the file on disk
changed, requires the pattern to match exactly once, and **fails the run** if
any probe didn't land. `expect: "survived"` marks a probe you have decided is
an equivalent mutation; it then fails if a test ever starts killing it, which
means the equivalence claim has stopped being true.

`scripts/mutation-check.sh` now also fails when fewer mutants ran than were
selected — a mutant that never ran shrinks the denominator and raises the score
by not existing.

**One probe run at a time.** The probes patch files in the working tree, so two
runs at once corrupt it — and they do it silently. Each run copies a file aside
before mutating it, so if the second run takes its backup while the first has a
mutation applied, that backup *is* the mutant, and the second run faithfully
restores it on the way out. What's left is a real-looking source edit nobody
made, in a tree `git status` says is dirty for a reason you'll assume was you.
Seen for real: an incident-balance query left as `snapshot_at >= ?` where it
should read `<= ?`, found only because the diff was read.

`mutation-probe.py` therefore takes an advisory `flock` on
`.mutation-probe.lock` and **refuses** — exit 2 — rather than queueing behind
another run, since the callers are hooks and health checks that shouldn't block
for however long somebody else's run takes. The kernel drops the lock when the
process dies however it dies, so a killed run can't leave it stuck. The health
check reports a refusal as `skipped`, never `pass`, and still exits non-zero:
the guards went unchecked either way.

**Workers (#96).** The probes were 75% of a full health check — 144 of them
strictly in series on an idle multi-core machine. `--workers N` (or `auto`:
one per core, at most 8) fans them across N private copies of the tree, one
per worker, built from `git ls-files` (tracked plus untracked-not-ignored, so
no `.venv`, no `mutants/`); each worker patches, tests and restores only
inside its copy and the working tree is never written. `xargs -P` over the
script would be wrong by construction — that is the corruption the lock
exists to prevent. The tests run with the copy as cwd and first on
`PYTHONPATH`, and before a worker touches a probe it proves `import sentinel`
resolves *inside its copy*: a worker importing the real tree would run every
probe against unmutated code and report `survived` for all of them — an
absence of evidence, one level up from the one the tool already refuses. A
worker that fails that proof fails the run, like a probe that did not land.
The lock is held in worker mode too (a copy taken mid-mutation of an in-place
run carries the mutant into every worker), and `--workers 1` — the default —
is the original in-place path unchanged.
