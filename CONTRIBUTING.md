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


## Keeping issue status honest

Closing an issue with a `Closes #N` commit trailer closes it **without touching
its labels**, so an issue finished by a commit keeps whatever `status:` it was
filed with. Three had drifted before anyone looked — two sat `status: broken`
long after being fixed.

```bash
python3 scripts/audit_issue_status.py            # report, changes nothing
python3 scripts/audit_issue_status.py --fix      # promote the safe ones
python3 scripts/audit_issue_status.py --check    # exit 1 if anything is stale
```

It promotes to `status: built` only when the issue is closed *as completed*, at
least one commit on `main` references it, and every acceptance-criteria checkbox
is ticked. Everything else is printed for a human with the reason.

It will **not** tick a checkbox for you. A tick is a claim that a criterion is
met, and nothing in a script can verify that — one that ticked boxes to make
itself pass would destroy the signal it exists to raise.
