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
- Bump [CHANGELOG.md](CHANGELOG.md) under `[Unreleased]`; tag releases
  `vMAJOR.MINOR.PATCH` (SemVer) and cut a GitHub Release.

## Deploying

The production host has no GitHub credentials (private repo), so deploys ship
the committed tree over SSH:

```bash
git archive --format=tar.gz -o /tmp/minas-deploy.tar.gz HEAD
scp -i <key.pem> /tmp/minas-deploy.tar.gz <host>:/tmp/
ssh -i <key.pem> <host> "sudo tar xzf /tmp/minas-deploy.tar.gz -C /opt/minas-guardian \
  && cd /opt/minas-guardian && sudo docker compose up -d --build"
```

Full procedure, region notes, and the DB-migration recipe:
[docs/deployment.md](docs/deployment.md). Deploy only committed code so
GitHub and production stay in lockstep.


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
