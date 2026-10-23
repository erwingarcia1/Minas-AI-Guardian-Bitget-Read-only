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

## 2026-10-23 · The box read before the public address: v1.12.0 on disk, the container stopped since 2026-09-28, Tailscale torn down (22e steps 4, 6 and 7)

No deploy. These are the readings that decided the order of
docs/deployment.md "The public address", and the teardown that step 4 of
it records. Every command was typed by the owner over SSH with the EC2
key pair at `57.181.51.14` (22e step 4: the key-pair login proven from the
network the rest was run from) and its output read by the session; the
address answered on the first try, which settles the stale
`ec2-54-168-212-44…` hostname question - the Elastic IP is current.

**Disk and container** (21:38 UTC): `git describe --tags` → `v1.12.0`,
checkout clean apart from `?? held/`. `docker compose ps` → no running
service; `docker compose ps -a` → `minas-guardian-sentinel-1 … Exited
(137) 3 weeks ago`; `docker inspect` → `exit=137 oom=false
finished=2026-09-28T20:48:42Z started=2026-09-22T12:26:51Z
restart=unless-stopped`; the Docker journal for that minute: `Container
failed to exit within 10s of signal 15 - using the force` - a `docker
stop`, not a crash, so the restart policy rightly left it down. Host up
109 days, no reboot. One SSH login that evening: `ubuntu` from
`105.112.176.210`, 2026-09-28 20:44-23:06 UTC. The container's last log
lines (20:46-20:48 UTC) are `breaker open, skipping account N` for nine
accounts. `held/README-2026-09-28.txt`, verbatim: "2026-09-28 20:50 UTC -
ALL ALERTING STOPPED (owner, beta, credit cost). Disabled:
minas-liveness.timer, minas-restart-on-unhealthy.timer (disable --now,
symlinks removed). Stopped: minas-guardian-sentinel-1 (Exited 137; PRAGMA
integrity_check = ok). Commented: root cron 'sentinel digest' 08:00 UTC.
Backup: root-cron-2026-09-28.bak in this folder. State at stop: 9
accounts dark 169.6h; 391 tamper alerts in prior 24h. RESTORE ORDER: 1)
nine exchange allowlists get 57.181.51.14 2) cut+deploy 1.13.0 3) then
start." Root's crontab carries the digest line commented out with a
"PAUSED 2026-09-28" note. The owner confirmed the allowlists (restore
step 1) done on 2026-10-23. This file had not recorded v1.12.0 or the
pause; the deploys between v1.7.0 and v1.12.0 are still unrecorded here.

**Why Caddy could not start on v1.13.0:** `ss -tlnp '( sport = :80 or
sport = :443 )'` → `tailscaled` LISTEN on `100.76.169.10:443` and the
Tailscale IPv6 address; `python3 -c "…socket().bind(('0.0.0.0',443))"`
with `SO_REUSEADDR` → `OSError: [Errno 98] Address already in use`.
v1.13.0 was cut (2026-10-23 14:40) and never deployed. Primary private
IPv4 `172.31.2.189/20` on `ens5`; `ip -4 route | grep '^10\.231\.'` →
nothing; volumes: `minas-guardian_sentinel-data` only.

**Teardown** (later that evening UTC; 22e steps 6 and 7, run before Caddy
for the reason above - nothing was serving, so nothing was lost):
`tailscale serve status` before → the four listeners of the 2026-09-25
reading (`:8443` Funnel → 8391; `:8391`, `/` → 8390, `:9109` tailnet-only).
`tailscale funnel reset; tailscale serve reset` → `serve status`: `No
serve config`. `tailscale down` → `tailscale status`: `Tailscale is
stopped.` `apt-get remove -y tailscale` → `Removing tailscale (1.102.2)`;
`apt-get purge -y tailscale` → `Purging configuration files`;
`rm -rf /var/lib/tailscale` (the node key: there is no coming back to the
old name). After, in a fresh session: `/usr/bin/tailscale` and
`/usr/sbin/tailscaled` → `No such file or directory`; `systemctl
is-active tailscaled` → `inactive`; `ss -tlnp | grep -c tailscaled` → `0`;
`ip -4 addr show tailscale0` → `Device "tailscale0" does not exist.`
(In the teardown shell `command -v tailscale` still printed the path: the
shell's command hash from the `tailscale status` run a moment earlier,
not the file.) Still to do in the Tailscale admin console: Machines →
minas-guardian → Remove.

**Step 5 of the runbook, read afterwards:** `held/` moved to
`/var/backups/minas-guardian/held-2026-09-28/` (both files listed there);
`ss` on 80/443 → no listener; `ip -4 route | grep '^10\.231\.'` → none;
`git status --short` in `/opt/minas-guardian` → nothing. The box is ready
for step 6, the deploy of the release that carries `/admin`.

---

## 2026-08-22 · v1.7.0 (current), and the four deploys this file missed

Discovered 2026-08-22 during an autonomous-run verification pass: the file's
newest entry was v1.5.1 while production ran v1.7.0. **Five deploys went
unrecorded** — the exact drift this file exists to prevent, caught this time
by measuring instead of trusting. Evidence, measured over read-only SSH:

The running container (`sentinel version --machine`, 2026-08-22):

```
version=1.7.0
commit=408ebde4a
tag=v1.7.0
source=build-arg
```

Disk agrees: `git describe --tags` → `v1.7.0`, HEAD `408ebde`.

The deploy history, from the production checkout's reflog (deploy.sh checks
the tag out itself, so each line is a deploy run):

| When (UTC, reflog) | Deployed | From |
|---|---|---|
| 2026-08-22 00:15:59 | **v1.7.0** | v1.6.2 |
| 2026-08-21 04:51:45 | v1.6.2 | v1.6.1 |
| 2026-08-21 03:27:13 | v1.6.1 | v1.6.0 |
| 2026-08-20 12:35:17 | v1.6.0 | v1.5.2 |
| 2026-08-19 01:22:09 | v1.5.2 | v1.5.1 |

Honest limits of this reconstruction: a reflog checkout proves deploy.sh
ran, not that its post-switchover version assertion passed — that
container-level evidence exists only for the version running now (v1.7.0,
above). v1.6.1's deploy is independently corroborated: the owner verified
it live (the first human portal sign-in, 2026-08-21). All deploys were the
owner's, per the standing rule.

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
