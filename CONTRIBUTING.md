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
.venv/bin/sentinel run                 # engine + dashboard (:8390) + metrics (:9109)
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
6. **Verify against live docs, never memory.** New exchange endpoints/fields
   get cited from CCXT source or the exchange's docs; anything unverifiable is
   marked UNVERIFIED rather than guessed.

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
deploy is a pull on the server — nothing is shipped from a laptop:

```bash
ssh ubuntu@minas-guardian     # Tailscale SSH
cd /opt/minas-guardian
sudo git pull                 # root's key: /root/.ssh/id_ed25519
scripts/deploy-gate.sh        # refuses unless HEAD is exactly a release tag
eval "$(scripts/deploy-gate.sh --export)"   # GIT_SHA/GIT_TAG for the build
sudo -E docker compose build sentinel
sudo docker compose run --rm sentinel sentinel validate-config /app/config/rules.yaml
sudo docker compose up -d
sudo docker compose exec -T sentinel sentinel version   # confirm what is running
```

Push first: the server pulls from GitHub `main`, so unpushed work cannot
deploy — GitHub and production stay in lockstep by construction. Don't edit
tracked files on the server (it blocks the next pull); tune `rules.yaml`
through git, it hot-reloads on arrival.

Full procedure, region notes, and the DB-migration recipe:
[docs/deployment.md](docs/deployment.md).


## Is everything okay?

```bash
scripts/health-check.sh          # every gate (~20 min, mutation dominates)
scripts/health-check.sh --fast   # skips the two multi-minute gates (~4 min)
```

One command, seven gates, one summary table: ruff, the suite, the coverage
floor, `pip-audit`, the design-guard probes, the mutation floor, and the
issue-status audit. Non-zero if any of them fail.

```
GATE                         RESULT     TIME  NOTES
---------------------------- -------- ------  -----
ruff                         pass         0s
tests                        pass       179s
coverage floor (78%)         pass         0s
pip-audit                    pass        39s
issue-status audit           pass         3s
design-guard probes          skipped       -  --fast
mutation floor (90%)         skipped       -  --fast
```

Each gate is documented in its own section below; this only runs them. Three
things it is deliberate about:

- **It does not stop at the first failure.** The point of the table is knowing
  all seven answers. Failed gates get their last 30 lines printed inline.
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

Bypass with `--no-verify` when you genuinely need to. It is possible on
purpose — a hook that blocks an urgent fix gets uninstalled — and it is never
silent.

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

## Mutation testing

```bash
scripts/mutation-check.sh            # run; print survivors and the score
scripts/mutation-check.sh --check    # fail below the floor, raise it on improvement
```

Restricted to **decision points** — comparison operators, boolean operators,
`continue`/`break`/`return` — over `sentinel/detectors/` and `sentinel/alerts/`.

An unrestricted run produced **588 survivors from 1,322 mutants** and would take
~36 hours for the whole package; roughly 60% were cosmetic (mutated log text,
SQL keyword casing, string literals). Chasing that list is a project that
stalls. The restricted run is **110 mutants**, and every survivor is a decision
a test could reasonably be expected to pin.

The score is a floor in `scripts/mutation-floor.txt`, ratcheted the same way as
the coverage floor: set from measured reality, never lowered, raised whenever it
improves.

**Known blind spot:** a module with no conditionals produces no decision-point
mutants at all, and the silence reads like coverage. `detectors/withdrawal.py`
is the case — a straight-line `Detection` builder on the CRITICAL money-leaving
path. Its mutations are evidence-key and message changes, which the restricted
run discards as cosmetic and which are nothing of the kind: `dispatch._dedup_key`
reads `evidence["order_id"]` to decide whether two withdrawals are one.

Covered by hand under **#65**, and measured with `scripts/mutation-module.sh`,
which runs an *unrestricted* mutation score over a single module:

```bash
scripts/mutation-module.sh sentinel/detectors/withdrawal.py \
    tests/test_detectors_withdrawal.py tests/test_withdrawal_evidence.py \
    tests/test_dispatch.py tests/test_detector_wiring.py
```

59 mutants: **40 survived (67.8%) before, 0 survived (0.0%) after**. The
decision-point floor did not move and cannot — it counts mutants this module
does not produce. Use this script when auditing one file deeply; it is manual
by design and not in CI.

The mutmut invocation quirks (whole-package `source_paths`, newline-separated
test selection, the cached `mutants/` copy, unstable mutant numbering) are
documented at the top of the script so nobody rediscovers them.

### Hand-written probes, and why they need a tool

```bash
scripts/mutation-probe.py scripts/probes/design-guards.json
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
