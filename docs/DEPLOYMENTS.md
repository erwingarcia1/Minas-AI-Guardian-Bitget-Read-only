# Deployment log

What is actually running in production, with the evidence it was checked.

This file exists because **"production runs the July build" was carried
through documents for weeks without anyone checking the server** — the
version in prose drifted from the version on disk, and every reader
repeated the last reader's claim. So the rule here: **every entry carries
evidence** — command output, not recollection. A version stated without the
command that measured it does not belong in this file.

The measurement commands, for every entry:

```bash
sudo docker compose exec -T sentinel sentinel version   # what the container reports
sudo git -C /opt/minas-guardian describe --tags         # what is on disk
```

Newest entry first.

---

## 2026-08-18 · v1.4.2 (from 1.1.0)

Deployed **2026-08-18 ~01:06 UTC** via `sudo /opt/minas-guardian/scripts/deploy.sh`
(the script's first production run). One step, four releases: 1.1.0 → 1.4.2.

### Before (measured on the server)

| What | Evidence |
|---|---|
| Container | `sentinel version`: **1.1.0**, commit/tag/source all **"unknown"** (pre-dates the `GIT_SHA`/`GIT_TAG` build stamping) |
| Disk | `git describe --tags`: **v1.1.0-2-g7561627** — two commits past the v1.1.0 tag |

Note what the "before" row proves about this file's purpose: the running
container could not say what it was, the disk was two commits off the last
release, and the docs said "the July build" — three answers, none checked
against each other until this deploy.

### After (measured on the server)

| What | Evidence |
|---|---|
| Container | `sentinel version`: **1.4.2**, commit `3419ffb97f6fa17264890150c985bbf13ce185b8`, tag `v1.4.2`, source **build-arg** |
| Health | `docker compose ps`: **healthy** (the deploy script waits for the healthcheck state, not merely "started") |
| Backup | `/opt/minas-guardian/sentinel_data.backup-2026-08-18` (**349 MB**), taken before the switchover |
| First retention run | deleted **190 rows** (`balance_snapshots`) — the first successful prune since the pruner repair shipped in 1.4.0 (`232528d`) |

### What the deploy surfaced (filed, not fixed — deploy night is for recording)

| Issue | Finding |
|---|---|
| [#74](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/74) (p1) | `paula-garavillas`: **5,656 consecutive OKX poll failures**, "API key doesn't exist" — account unmonitored; needs a replacement read-only key |
| [#75](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/75) | SMTP and SMS unconfigured while accounts carry email/phone contacts — **CRITICAL reaches Telegram only** |
| [#76](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/76) | Ops dashboard bound to **0.0.0.0 with no login**; the tailnet has ten devices across five people |
| [#77](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/77) | `scripts/` is not copied into the Docker image, so `retention-boundary.py` cannot run as v1.4.1's release note says it should |
| [#78](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/78) | `balance_snapshots` reported **PAST 0d** minutes after a successful prune — likely boundary rounding/steady-state aging; **re-check in a few hours** before deciding |
