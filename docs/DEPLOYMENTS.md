# Deployment log

What is actually running in production, with the evidence it was checked.

This file exists because **"production runs the July build" was carried
through internal documents for six weeks without anyone logging in to
check — and it was wrong.** Production was 1.1.0, two commits past the
tag, built from a non-release commit. The version in prose drifted from
the version on disk because nothing required a command output next to the
claim. So the rule here: **every entry carries evidence** — measured, not
recalled. Future deploys append an entry.

The measurement commands, for every entry:

```bash
sudo docker compose exec -T sentinel sentinel version   # what the container reports
sudo git -C /opt/minas-guardian describe --tags         # what is on disk
```

Newest entry first.

---

## 2026-08-18 · v1.5.1 (from 1.4.2)

Deployed **2026-08-18 ~15:5x UTC** by the owner via
`sudo /opt/minas-guardian/scripts/deploy.sh v1.5.1` — the first run of the
tag-checkout deploy that this release itself ships, and the first deploy
whose final step **asserted** the version instead of reporting it.

### Before (measured by the deploy itself)

| What | Evidence |
|---|---|
| Container | deploy.sh "Currently running": **1.4.2** — commit `3419ffb97f6fa17264890150c985bbf13ce185b8`, tag `v1.4.2` |

### Deploy

| What | Evidence |
|---|---|
| Refusal first | The first attempt was **refused by the new dirty-tree check**: a DB backup sat inside the checkout. Filed and fixed as [#85] — backups now have a named home, `/var/backups/minas-guardian/`, and the refusal tells the untracked case where files belong |
| Method | `deploy.sh v1.5.1`: clean tree → `fetch --tags` → checkout `v1.5.1` (detached — the server now stays detached at the deployed tag by design) → gate → build → `validate-config` → switchover → healthy wait → version assert |

### After (asserted by the deploy, not read afterwards)

| What | Evidence |
|---|---|
| Container | `sentinel version`: **1.5.1**, commit `3cc4aee06448e5ae4ca93ce9c0174b463062ebb5`, tag `v1.5.1`, source **build-arg** — compared against the requested tag by the script itself, which exits non-zero on mismatch |

### Verified after deploy

- **The descriptor leak is closed in production** (#79 — the six-week
  outage this release shipped the fix for). Descriptor counts on the
  running container, measured by the owner (times UTC): **35 at 15:49**
  (immediately post-deploy), **30 at 16:05**, **30 at 20:46**. Five hours
  flat. At the pre-fix rate of ~11/min that window would have leaked
  ~3,300 descriptors and the container would have hit its 1024 ceiling and
  gone dark around 17:20 — as it had after every restart since early July.
  This is the measurement that closes the outage, not merely the fix that
  should have.

### v1.5.0: tagged, CI-green, never deployed

Its deploy was refused by the gate — HEAD was the post-tag log-handoff
commit, which is exactly the defect v1.5.1 fixes — and the refusal was
abandoned rather than worked around: no hand deploy happened. (A session
note claiming one is corrected inline in docs/AUTONOMOUS-LOG.md; the claim
had no entry in this file, which is the tell this file exists to provide.)
v1.5.0's changes — the #79 descriptor-leak fix among them — first reached
production inside this deploy, 1.4.2 → 1.5.1 in one step.

[#85]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/85

## 2026-08-18 · v1.4.2 (from 1.1.0)

Deployed **2026-08-18 ~01:06 UTC** by the owner via
`sudo /opt/minas-guardian/scripts/deploy.sh` (the script's first production
run). One step, four releases: 1.1.0 → 1.4.2.

### Before (measured on the server)

| What | Evidence |
|---|---|
| Container | `sentinel version`: **1.1.0**; commit, tag and source all **"unknown"** — built before version stamping existed |
| Disk | `git describe --tags`: **v1.1.0-2-g7561627** — two commits past the v1.1.0 tag, i.e. a non-release commit was live |

That row is this file's reason to exist: the container could not say what
it was, the disk was off the last release, and the docs said "the July
build" — three answers, none checked against each other for six weeks.

### Deploy

| What | Evidence |
|---|---|
| Backup, taken first | `/opt/minas-guardian/sentinel_data.backup-2026-08-18` (**349 MB**), copied from the Docker volume `minas-guardian_sentinel-data` |
| Method | `scripts/deploy.sh`: pull → gate (accepted `3419ffb`, exactly tag `v1.4.2`) → build → `validate-config` → switchover → healthcheck wait |

### After (measured on the server)

| What | Evidence |
|---|---|
| Container | `sentinel version`: **1.4.2**, commit `3419ffb97f6fa17264890150c985bbf13ce185b8`, tag `v1.4.2`, source **build-arg** |
| Health | **healthy** (the healthcheck state, not merely "started") |

### Verified after deploy

- **The pruner works in production.** First retention run deleted **190
  rows** (`balance_snapshots`); the next cycle deleted **95 more**. This is
  the repaired deletion job (1.4.0, `232528d`) doing its first real work.
- **The retention boundary reading, owed since the v1.4.0 handoff, is
  taken.** `retention-boundary.py` (run mounted — see [#77]): oldest data
  anywhere is **2026-07-02**. Events and alerts have **684 days** of
  headroom against their 730-day windows; the dedup tables **319 days**
  against 365. This **confirms by measurement** the previously derived
  claim that the foreign-key pruning defect destroyed nothing — the
  earliest possible boundary crossing was 2028, and the oldest surviving
  row is from the product's first week.
- **Email is configured and proven end to end.** Resend SMTP: a raw SMTP
  test was accepted, then `sentinel digest --hours 24` delivered a real
  digest with live account data to support@minastech.ca and to Telegram.
  The old Resend API key was rotated and deleted.

### What the deploy surfaced (filed, not fixed — deploy night is for recording)

| Issue | Finding |
|---|---|
| [#74](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/74) | `paula-garavillas`: **5,668 consecutive OKX poll failures**, "API key doesn't exist". *Corrected 2026-08-18: a **test account**, not a customer — reclassified p1 → p3; the fix is to disable it (offboarding is disable, never delete, G-50). It still matters because a warning every 45 seconds forever trains operators to ignore poll-failure warnings* |
| [#75](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/75) | SMS unconfigured (ClickSend/Twilio) while accounts carry phone contacts — CRITICAL now reaches Telegram **and email**; the SMS leg is still routed and absent |
| [#76](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/76) | Ops dashboard bound to **0.0.0.0 with no login of its own**; the tailnet carries ten devices across five people — establish who can reach port 8390 |
| [#77](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/77) | `scripts/` is not in the Docker image, so `retention-boundary.py` runs only mounted — contradicting v1.4.1's own release note; fix the Dockerfile or correct the note |
| [#78](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/78) | `balance_snapshots` reported **PAST 0d** against its 7-day window minutes after a successful prune — probably day-granularity rounding at the boundary, not a backlog. Re-run and check whether the oldest date advances before deciding; if it is a false alarm, fix the report — a tool built to be trusted crying wolf on its first production run is the mutation-gate problem again |

[#77]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/77
