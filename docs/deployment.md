# Minas AI Guardian — Deployment Guide

Target: **AWS first** (launch decision 2026-07-03), Alibaba Cloud kept as an
alternative. Both paths use the same Docker Compose deployment; only the
managed-service names differ.

## Prerequisites

- A Linux VM (AWS EC2 / Alibaba ECS). 2 vCPU / 2 GB (t3.small) is
  comfortable for the pilot; t3.micro (1 GB) works with the swap the
  bootstrap script creates. Scale workers before scaling the box.
- Docker + Docker Compose plugin (the bootstrap script installs both).
- Outbound HTTPS to `api.bitget.com`, `api.telegram.org`, Twilio, your SMTP
  host, and (if triage is enabled) `api.anthropic.com`.

## AWS quickstart (pilot)

**Console steps:**

1. EC2 → Launch instance, region **ap-northeast-1 (Tokyo)**. The region
   decision is exchange reachability, not latency: us-east-1 geo-blocks
   Bybit, Binance Global, and OKX Global (production was migrated out of it
   for exactly this — see CHANGELOG 1.0.0); Tokyo reaches all four.
2. AMI: **Ubuntu Server 24.04 LTS**. Instance type: **t3.small** (or
   t3.micro to start). Disk: 20 GB gp3.
3. Key pair: create/download one (this is your SSH login).
4. Security group: inbound **SSH (22) from your IP only**. Nothing else —
   the dashboard is reached over an SSH tunnel, never opened publicly.
5. Advanced details → User data: paste the contents of
   [`scripts/aws-user-data.sh`](../scripts/aws-user-data.sh). It installs
   Docker, adds swap, and enables unattended security updates. It does
   **not** clone the repo — the repo is private, so the clone waits until
   the deploy key exists (first-login steps below; the MOTD repeats them).
   It contains no secrets by design (user-data is readable from instance
   metadata).
6. Launch, then: Elastic IPs → **Allocate** → **Associate** with the
   instance. This is the permanent egress IP you whitelist on Bitget keys.
7. Instance settings → Metadata options: require **IMDSv2** with hop limit
   1 (new launches default to this; verify) — blocks SSRF-style metadata
   theft from anything running on the box.

**First SSH login** (the message of the day repeats these steps):

```bash
ssh -i your-key.pem ubuntu@<elastic-ip>

# One-time: a read-only deploy key so the box can pull the private repo.
# It lives under /root/.ssh because /opt/minas-guardian is root-owned and
# every pull runs under sudo — a key in ubuntu's ~/.ssh is invisible there.
sudo ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N '' -C 'minas-guardian-deploy'
sudo cat /root/.ssh/id_ed25519.pub
#   → GitHub → repo → Settings → Deploy keys → Add deploy key.
#     Leave "Allow write access" UNCHECKED: this box holds decrypted customer
#     credentials, and a read-only key means a compromise of it cannot push
#     poisoned code that our own next deploy would then install.

sudo git clone git@github.com:erwingarcia1/Minas-AI-Guardian-Bitget-Read-only.git /opt/minas-guardian
cd /opt/minas-guardian
sudo cp .env.example .env && sudo nano .env   # master key + Telegram creds
sudo chmod 600 .env                           # secrets: root-only, always
sudo docker compose up -d --build
sudo docker compose exec -it sentinel sentinel onboard --label customer-1
```

> **Migrating an existing DB instead of onboarding fresh?** Put the OLD
> machine's `SENTINEL_MASTER_KEY` into `.env` *before* this first
> `up -d --build`. Container environments are frozen at creation: editing
> `.env` later and doing `stop`/`start` does NOT apply the new key — the
> engine would keep the old one and fail to decrypt the migrated DB. (If
> you already booted with a fresh key, the migration recipe below uses
> `up -d`, which recreates the container and picks up the edited `.env`.)

**Reaching the dashboard** (from your laptop): production access is over
**Tailscale Serve** — `https://minas-guardian.<tailnet>.ts.net`, set up once
per instance (next section). The SSH tunnel remains the break-glass path,
since it needs nothing but the EC2 key pair:

```bash
ssh -i your-key.pem -L 8390:127.0.0.1:8390 -L 9109:127.0.0.1:9109 ubuntu@<elastic-ip>
# then open http://localhost:8390
```

**Migrating an existing pilot DB instead of re-onboarding:**

1. Stop the OLD machine's monitor first (`docker compose down` there) — a
   copy taken mid-write can be inconsistent, and two monitors on one DB
   double-alert.
2. Copy the old machine's `SENTINEL_MASTER_KEY` into the new `.env` (the
   DB's credentials are encrypted with it — a fresh key cannot read them).
3. Ship the DB and recreate the container:

```bash
scp -i your-key.pem sentinel.db ubuntu@<elastic-ip>:/tmp/
ssh -i your-key.pem ubuntu@<elastic-ip> \
  "cd /opt/minas-guardian && \
   sudo docker compose stop sentinel && \
   sudo docker run --rm -v minas-guardian_sentinel-data:/data -v /tmp:/src alpine \
     sh -c 'cp /src/sentinel.db /data/sentinel.db && chown 1000:1000 /data/sentinel.db' && \
   rm /tmp/sentinel.db && \
   sudo docker compose up -d"
```

The `chown 1000:1000` matters: the container runs as the non-root
`sentinel` user, and a root-owned DB file makes every SQLite write fail.
The final `up -d` (not `start`) recreates the container so any `.env`
changes actually apply.

**After the instance is live:** add the Elastic IP to each exchange API
key's IP allowlist (Bitget → API management → edit key; Bybit → API →
bound IP addresses; OKX and Binance likewise). From that moment the key is
useless from any other network — do this for every onboarded account, and
read "The Elastic IP is load-bearing" below before ever touching the
address.

**Reboot survival:** `restart: unless-stopped` + Docker's systemd unit mean
the stack comes back on its own after a reboot or crash; no operator action.

## Remote access: Tailscale (one-time per instance)

Day-to-day access — dashboard and SSH — goes over the tailnet, so no port
ever opens publicly and the dashboard URL works from any admin device:

```bash
# On the server (over the EC2-key SSH, the last time you'll need it daily):
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --hostname minas-guardian   # prints an auth URL — open it in your browser

# Publish the loopback-bound ports to the tailnet (persists across reboots
# and container rebuilds — tailscaled proxies to the host's 127.0.0.1):
sudo tailscale serve --bg 8390                 # dashboard → https://minas-guardian.<tailnet>.ts.net
sudo tailscale serve --bg --https=9109 9109    # Prometheus metrics
# The member portal (8391) is NOT served this way: it is published to the
# internet with `tailscale funnel` (decision 20) - see "Rules of the road".
tailscale serve status                         # verify
```

Then two settings in the [admin console](https://login.tailscale.com/admin):

1. **Machines → minas-guardian → Disable key expiry.** Node keys expire
   after ~180 days by default; an expired key silently drops the monitoring
   box off the tailnet.
2. **DNS → MagicDNS + HTTPS Certificates enabled** (needed for the
   `https://…ts.net` names).

Rules of the road:

- **`serve` for the dashboard and metrics, never `funnel`.** They look
  alike; `funnel` publishes to the open internet, and the dashboard has no
  customer-facing reason to be there.
- **`funnel` for the member portal (8391), and only the portal** - decision
  20 (docs/DECISIONS-NEEDED.md): customers reach it from their own devices,
  and it carries its own login, TOTP and throttle. Published in August 2026;
  `tailscale funnel status` shows what the box is publishing, and
  `tailscale funnel --https=8391 8391 off` (or `tailscale funnel reset`)
  withdraws it in one command. Nothing else may ever be funnelled: an
  operator capability on a
  public surface is the A1 compromise with the door held open.
- Daily SSH is `ssh ubuntu@minas-guardian` (Tailscale SSH, no key file).
  Keep the security group's port-22-from-admin-IP rule as break-glass —
  and never remove it before Tailscale SSH is verified from a *fresh*
  terminal.
- The compose file needs no change for any of this: the `127.0.0.1:`
  publishes stay exactly as they are, which is the point.
- New team members reach the dashboard by tailnet invite — remember the
  dashboard trusts the network entirely, so an invite *is* dashboard access.

## Before any upgrade: check the config that is already on the box

```bash
sentinel validate-config /path/to/config/rules.yaml   # on the server
```

`0` valid, `1` the schema rejects it, `2` the file could not be read.

Thresholds are validated against a schema (G-12) that gains entries as
detectors are added, so a file an older build ran happily on can be rejected by
a newer one. The service **will still start** in that case — it runs on the
settings that validate, falls back to built-in defaults for the rest, and
raises a CRITICAL operator page — because refusing to start would turn an
upgrade into a monitoring outage. That is a safety net, not a plan: the
thresholds in force would not be the ones in the file.

So check first. It costs a second and it is the difference between fixing a
typo at your desk and finding out from a page.

## Deploying code changes (routine update)

The VM holds a read-only deploy key and `/opt/minas-guardian` is a real
clone, so a deploy is a fetch on the server — nothing is shipped from a
laptop, and only what is committed *and pushed* can reach production. The
whole thing is one command:

```bash
# From your machine — a deploy ships a RELEASE, not a bare push (see the
# gate below), so cut one if there is no tag for what you want to ship:
scripts/release.sh X.Y.Z             # level rules: CONTRIBUTING.md
git push && git push --tags

# On the server:
ssh ubuntu@minas-guardian            # Tailscale SSH (preferred)
#   — or, off the tailnet, the EC2 key pair (IP must be in the SG rule):
# ssh -i "Minas-AI-Guardian-Key-Tokyo.pem" ubuntu@ec2-54-168-212-44.ap-northeast-1.compute.amazonaws.com
sudo /opt/minas-guardian/scripts/deploy.sh            # latest release tag
sudo /opt/minas-guardian/scripts/deploy.sh v1.5.0     # or that specific tag
```

`scripts/deploy.sh` (plain `scripts/deploy.sh` also works — it re-runs
itself under sudo) **deploys a release tag, never a branch tip**: it fetches
tags and checks out the requested tag (or the latest release by version
order, with no argument), and what sits at the end of `main` is irrelevant.
This is deliberate and load-bearing — the workflow commits a log handoff
*after* cutting each tag, so main's tip is routinely one commit past the
release, and the old pull-main flow handed that untagged tip to the gate,
which correctly refused it (it blocked the v1.4.1 and v1.5.0 deploys).

It stops at the first failure and refuses: a dirty working tree (naming the
files — fix it in git, never on the server), a tag that does not exist, and
— without a typed confirmation at the terminal — a tag *older* than the
running version, so an accidental downgrade is not one typo away.

**Rolling back needs a real terminal.** The confirmation is read from the
TTY, and `ssh host 'sudo … deploy.sh v1.4.2'` allocates none — it is
refused outright. Rollback is the emergency path, so the one-liner that
works is worth having ready:

```bash
ssh -t ubuntu@minas-guardian 'sudo /opt/minas-guardian/scripts/deploy.sh v1.4.2'
#   ^ -t allocates the TTY the downgrade confirmation is typed into
``` Its one
property to internalize: **any failure before its "Switching over" step
leaves the previous version running untouched** — the build and the config
check happen off to the side, so a refused gate or a rejected config costs
nothing but the time to fix it. After the switchover it waits for the
container's real healthcheck, then **asserts** the running container's
`sentinel version` names the tag it was asked to deploy — a deploy that ran
the wrong image exits non-zero rather than printing it.

**The server stays detached at the deployed tag.** Deliberate: `git
describe --tags` then answers "what is deployed" with exactly one name — no
`-2-g1234abc` drift suffix — which is the property docs/DEPLOYMENTS.md
exists to protect. `git fetch --tags` works fine from a detached HEAD, so
the next deploy needs nothing done first. To put the clone back on a branch,
should anyone need to: `sudo git -C /opt/minas-guardian checkout main`.

The manual sequence — for understanding what the script does, or for the
day the script itself is what's broken:

```bash
cd /opt/minas-guardian
sudo git fetch --tags
sudo git checkout vX.Y.Z             # the release you are deploying — detached, on purpose
scripts/deploy-gate.sh               # refuses anything that is not exactly a release tag
eval "$(scripts/deploy-gate.sh --export)"
sudo -E docker compose build sentinel
sudo docker compose run --rm sentinel sentinel validate-config /app/config/rules.yaml
sudo docker compose up -d
sudo docker compose ps               # wait for "healthy"
sudo docker compose exec -T sentinel sentinel version   # must name vX.Y.Z — if not, the wrong image is serving
```

Why each step is shaped that way:

- **`scripts/deploy-gate.sh` first, before the build and before the config
  check.** It runs `git describe --exact-match --tags HEAD` and stops the
  deploy if HEAD is not exactly a release tag. An untagged commit is one nobody
  wrote release notes for, and shipping it puts changes into production that
  are described nowhere — which is how you end up reading a diff to find out
  what changed under a customer.

  It is step one because the other steps cost real time: the build takes
  minutes and `validate-config` needs the new image. Finding out *after* both
  that this commit was never meant to ship wastes the window and tempts
  somebody into shipping it anyway.

  **There is no `--force`.** If something is urgent, cut a PATCH release —
  `scripts/release.sh X.Y.Z` — which takes about thirty seconds and produces
  the note as a side effect. An escape hatch used once during an incident is
  an escape hatch used every time after, and the first use always looks
  justified. Full rules in
  [CONTRIBUTING.md](../CONTRIBUTING.md) under "Releases and versioning".

- **`eval "$(… --export)"` then `sudo -E`** — the gate prints `GIT_SHA` and
  `GIT_TAG`; compose forwards them to the image as build args. `-E` is what
  carries them through sudo. Skip this and the build still succeeds, but
  `sentinel version` will report its commit as `unknown` and exit non-zero,
  because a build that misreports what it is would be worse than one that
  admits it does not know.
- **`sudo git fetch --tags`, then check out the tag** — the checkout and the
  deploy key are root's (`/root/.ssh/id_ed25519`), so both run under sudo.
  Never `git pull`: it moves the checkout to main's tip, which is usually
  *past* the release (the post-tag log handoff), and cannot run at all once
  the server is detached at a tag.
- **Build, then `validate-config`, then switch** — the check runs *the new
  image* against the box's live config, which is the order that catches a
  schema tightened since the running build (see the section above). Exit 0
  proceeds; exit 1 means fix the config before `up`.
- **`up -d`, never `restart` or `stop`/`start`** — only recreating the
  container applies the new image and any `.env` edits; `restart` re-runs
  the old one.
- Data survives all of this: the DB lives in the `sentinel-data` named
  volume, which container recreation does not touch. The one flag that
  *does* touch it is `down -v` — see the warning section below.

Two operational consequences of fetch-based deploys:

- **Don't edit tracked files on the server.** `deploy.sh` refuses a dirty
  tree outright (naming the files), so a threshold tweaked directly in the
  box's `config/rules.yaml` blocks the next deploy. Tune thresholds in git
  and deploy them — they still hot-reload on arrival, no restart. Stated
  plainly, because the old pull-based flow blurred it: a config change
  reaches the box the same way code does, inside a release tag. A
  threshold tweak worth shipping is a PATCH release, which takes thirty
  seconds and writes the note that says what was tuned and why.
- **The directory name is load-bearing.** Compose derives volume names from
  it (`minas-guardian_sentinel-data`), so the checkout must stay at
  `/opt/minas-guardian` — a re-clone under another name would come up with
  a fresh, empty database.

## The liveness gate

Every mechanism that needs a human switch-on step is declared in
`sentinel/liveness.py` with the act that switches it on and the signal that
proves it is running (operators > 0; a digest cron line AND a digest sent
in 48 h; a backup newer than 48 h; a drill account; an SMS provider; a
per-account threshold; the heartbeat URL; the restart timer; Telegram and
SMTP). CI proves the declaration is complete
(`tests/test_liveness_gate.py`: a `.env.example` variable, an `enabled:`
flag or a host timer with no entry fails the build). Production proves
liveness:

```
sudo docker compose exec -T sentinel sentinel liveness            # from the container: DB + env facts
sudo /opt/minas-guardian/scripts/host/liveness.sh                  # from the host: adds cron, backup, timer facts
```

Exit 1 on any mechanism with no evidence of running - `unknown` counts. A
deliberate hold is declared in `config/liveness-holds.yaml` with a reason
and reports as `held`, not `DEAD`; an undeclared hold is exactly what the
gate exists to catch.

Install the hourly timer next to #81's:

```
sudo cp scripts/host/minas-liveness.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now minas-liveness.timer
```

The verdict pages an operator (Telegram ops chat + ops email - proven live
by production's escalation pages, and not a path this gate checks; never
the digest, never SMS) **on a change in the failing set, never on the
state**: a mechanism that newly fails pages HIGH once; a set that has not
changed is silent however many hourly runs go by (the set it last paged
about is kept in `portal_meta`, key `liveness:paged_failing`); a mechanism
coming back - or being declared held - is reported once at MEDIUM. Seven
mechanisms that could not change without a human act paged HIGH every
hour from 2026-08-31 to 2026-09-02, each page escalating, until the timer
was stopped by hand; that is what this rule replaces. The host script
applies the same rule to journald + `/etc/motd` with its own state file,
so a dead Telegram cannot hide a dead everything-else.

## Acting on an unhealthy container (#81, decision 12)

`restart: unless-stopped` acts on **exit**, not on health — an
alive-but-broken container stays broken until a human notices, which is
how #79 ran unhealthy for six weeks. A host-side systemd timer closes
that gap: every two minutes it reads the container's health and, on
`unhealthy`, restarts the service — **at most once per 30 minutes**. A
second unhealthy verdict inside that window means the restart didn't
cure: the timer stops restarting and goes loud once (journald at err, a
line in `/var/log/minas-guardian-restarts.log`, and a marker appended to
`/etc/motd` so the next SSH session trips over it). Once #63's external
paging exists, the cap event is precisely what pages.

Install (one-time, on the host — the units ship in the repo):

```bash
sudo cp /opt/minas-guardian/scripts/host/minas-restart-on-unhealthy.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now minas-restart-on-unhealthy.timer
systemctl list-timers minas-restart-on-unhealthy.timer   # confirm it is scheduled
```

The record: every restart appends a timestamped line with the health
state that triggered it to `/var/log/minas-guardian-restarts.log`, and
the same lines reach `journalctl -t minas-restart-on-unhealthy`. A
restart loop reads as a fault in that log, never as uptime. After a cap
event, remove the notice from `/etc/motd` once handled — the script
deliberately never removes it itself.

## Never run `docker compose down -v` on this box

`docker compose down` is safe: it stops and removes **containers**, the
named volumes survive, and the next `up -d` picks them back up. The same
command with `-v` (or `--volumes`) **also deletes the volumes** — and on
this stack `minas-guardian_sentinel-data` is the entire production state:
every onboarded customer's encrypted API key, every baseline (known
addresses, traded pairs, transfer routes, balance and permission
snapshots), the append-only event log, and the alert and SMS delivery
history. One extra flag, no confirmation prompt, gone in a second.

Three properties make this worth its own section rather than a footnote:

- **The muscle memory is real.** On a dev machine `down -v` is the routine
  "give me a clean slate" command. That habit is exactly wrong here, and
  the command that expresses it differs by two characters from the safe
  one you type all the time.
- **The damage doesn't look like damage.** Nothing errors. The next
  `up -d` quietly creates a fresh empty volume, the service starts
  healthy, and the dashboard shows zero accounts — it looks like a fresh
  install, while every customer silently stopped being monitored.
- **Recovery is snapshot-or-nothing.** The DB exists nowhere but that
  volume; without an EBS snapshot or a `tar` backup (see "Secrets
  posture") there is nothing to restore, and every customer would have to
  be re-onboarded — new keys, new baselines, history unrecoverable.

If you genuinely need to destroy the state on a production box, say so
explicitly instead of reaching for a cleanup flag: take a backup (to
`/var/backups/minas-guardian/` — see "Where backups go" below, and never
into the checkout), then `sudo docker volume rm minas-guardian_sentinel-data`
— a command that can only mean the thing it does.

## First deployment (any host)

```bash
git clone git@github.com:erwingarcia1/Minas-AI-Guardian-Bitget-Read-only.git minas-guardian
cd minas-guardian
cp .env.example .env
# 1. Generate SENTINEL_MASTER_KEY (python3 command in .env.example)
# 2. Fill in alert-channel credentials
chmod 600 .env
docker compose up -d --build
```

Note: the repo is **private**, so the clone needs a key GitHub knows — on a
production host that is a **read-only deploy key** (creation steps in the
AWS quickstart above), generated where the pulls will actually run (root's
`~/.ssh` if the checkout is root-owned). An unauthenticated `git clone`/
`curl` fails with a 404.

Onboard accounts from inside the container so credentials never touch the
host shell history:

```bash
docker compose exec -it sentinel sentinel onboard --label customer-1
```

### The Elastic IP is load-bearing: `57.181.51.14`

Production's egress address is the Elastic IP **57.181.51.14**, associated
with instance **i-0faad6c4d361b127d** (ap-northeast-1). Every onboarded
customer's exchange key is (or must be) allowlisted to that address on the
exchange side, so:

- **Every new key's allowlist must carry `57.181.51.14`** before the
  account is onboarded, or its first check is refused. Whoever onboards
  customer ten reads this line; nothing else in the product knows the
  address (the poller keeps the exchange's own words for a refused key in
  `poll_state.last_auth_error`, and a Bitget refusal names the requesting
  address - that is how a wrong allowlist is diagnosed, not how the address
  is chosen).
- **Releasing, re-associating or replacing the address unmonitors every
  allowlisted account at once** - nine accounts as of 2026-09-22 (seven
  Bitget, two Bybit). This is not a hypothetical: on 2026-09-21 at 19:10 UTC
  the host's public address changed, nine keys were refused within the same
  second, and those customers were unwatched for more than 24 hours while
  the allowlists were updated by hand, one exchange login at a time. The
  product now reads that shape as one host-level finding (alerts/fleet.py,
  2026-09-23) and pages the operator once with the address the exchange
  named; it cannot fix an allowlist.
- **The address is also the portal's ingress** (decision 20: the member
  portal is Funnel-published on this host), so changing it breaks the
  customer's bookmark and the exchanges' allowlists together.
- **To move the address on purpose:** allocate the new EIP, add it to every
  affected key's allowlist while the old address still works (both entries
  at once - the exchanges allow more than one), then re-associate, then
  watch `sentinel` for `host_auth_failure` and the dashboard for auth
  failures before removing the old entry. Never the other order.

Daily digest via cron on the host (MEDIUM/LOW alerts route here, per
`alerting.routing` in rules.yaml). **Use root's crontab** (`sudo crontab -e`)
— the default `ubuntu` user is not in the docker group, so an unprivileged
crontab fails silently every day and the digest never sends:

```bash
0 8 * * * cd /opt/minas-guardian && docker compose exec -T sentinel sentinel digest
```

The `postgres` and `redis` services are Phase 2 scale-out targets behind a
compose profile — the default `up` runs only the monitor. Enable them later
with `docker compose --profile scale up -d`.

## Ports

| Port | What | Exposure rule |
|---|---|---|
| 8390 | Status dashboard | **Never public.** No auth by design; bind stays on 127.0.0.1, reach it via Tailscale Serve (production) or an SSH tunnel (break-glass). |
| 9109 | Prometheus metrics | Same rule; scrape over the tailnet or a private network only. |

## Rate limits and egress IPs

The capacity math (docs/phase0-capability-audit.md §4) assumes multiple
egress IPs at 1,000+ accounts. On one VM you have one IP — fine for the
pilot; at scale run one compose stack per VM (or per elastic IP) and shard
accounts across them. The `rate_limits.egress_ip_rps` bucket in rules.yaml
protects each IP; keep it at ~60% of Bitget's 100 req/s ceiling.

## Exchange API key IP allowlisting

The VM's stable public IP is the Elastic IP `57.181.51.14` (see "The
Elastic IP is load-bearing" under First deployment). Customer read-only
keys are issued with that address allowlisted on the exchange side - Bitget,
Bybit, OKX and Binance all support it. This is a real security win (a stolen
read-only key becomes useless off our IP) and is standard onboarding
practice; it couples every customer to our egress address, which is why the
rotation procedure above exists and why the address appears in this
document at all.

## Secrets posture

- Pilot: `SENTINEL_MASTER_KEY` lives only in `/opt/minas-guardian/.env`
  (root-readable) on the VM and in your password manager. Never in git,
  never in user-data, never in shell history.
- Hardening step (recommended before customer #2): move it to **AWS Secrets
  Manager** or **SSM Parameter Store (SecureString)** and inject it into the
  environment at boot; give the instance an IAM role scoped to that one
  secret.
- Back up the SQLite volume with EBS snapshots (or a nightly
  `docker run --rm -v minas-guardian_sentinel-data:/data ... tar` job) — the
  DB is useless to a thief without the master key, but you still need it to
  restore service.

### Where backups go: `/var/backups/minas-guardian/`, never the checkout

**A git working tree is not a storage location.** `/opt/minas-guardian` is
a checkout: anything dropped into it is exposed to everything git does — a
re-clone or a cleanup wipes the backup along with the tree — and an
untracked file there trips `deploy.sh`'s dirty-tree refusal, blocking the
next deploy (#85; it happened on the check's first production run). The
refusal is deliberate and stays: `COPY sentinel/ sentinel/` sweeps
untracked files into the image, tracked or not, so a file dropped in the
checkout can ship to production described nowhere. The rule that keeps the
two consistent: **no instruction in these docs may create an untracked
file in the tree.**

```bash
sudo mkdir -p /var/backups/minas-guardian
sudo cp -a /path/to/copy /var/backups/minas-guardian/sentinel_data.backup-$(date -u +%Y-%m-%d)
```

## Email authentication (SPF, DKIM, DMARC)

Alerts are sent through Resend from `minastrade.com`. The DNS side is set up
and verified: DKIM at `resend._domainkey`, SPF on the `send.` subdomain, and a
custom return path so bounces are handled.

**Why this matters more here than for most products.** We train customers to
act on an email that says their funds may be moving. A forged one is close to a
perfect phishing lure against exactly the people we have taught to respond
quickly, and the whole defence is that a forgery cannot authenticate as us.

Two gaps, both open:

**1. DMARC reports go nowhere we read.** The current record is

```
v=DMARC1; p=quarantine; adkim=r; aspf=r; rua=mailto:dmarc_rua@onsecureserver.net;
```

`onsecureserver.net` is the registrar's address, not ours, so we have no
visibility into who is sending as our domain. Change `rua` to a mailbox we
monitor — a dedicated one, since the reports are bulky XML sent daily by every
receiving provider:

```
v=DMARC1; p=quarantine; adkim=r; aspf=r; rua=mailto:dmarc@minastrade.com; fo=1;
```

**2. The policy is `quarantine`, not `reject`.** Quarantine means a forgery
lands in spam, where a customer can still find it and act on it. Reject means
it is refused at the door. The ramp, in order:

1. Point `rua` at a mailbox we read (above).
2. Watch reports for **at least two weeks** — long enough to catch monthly
   senders such as invoicing or a newsletter tool.
3. Confirm every legitimate source passes: today that is Resend (alerts) and
   Microsoft 365 via GoDaddy (`include:secureserver.net`, human mail).
4. Only then move `p=quarantine` → `p=reject`.

Do not skip step 2. Moving to `reject` while a legitimate sender still fails
alignment means that sender's mail is silently refused, and the failure looks
like "the customer never replied" rather than like a DNS problem.

**Note the coupling:** the root domain's SPF (`include:secureserver.net -all`)
does *not* include Resend. That is correct only while Resend sends from the
`send.` subdomain via the custom return path. If that return path is ever
turned off, SPF hard-fails immediately — and under `p=reject` the alerts stop
being delivered at all rather than landing in spam.

## Alibaba Cloud specifics (alternative path)

- ECS instance + EIP; security group allows nothing inbound except SSH from
  your admin IPs.
- Use KMS (or at minimum an encrypted RAM-role-scoped parameter) to hold
  `SENTINEL_MASTER_KEY`; inject it into `.env` at deploy time rather than
  storing it on disk unencrypted.
- SQLite volume (`sentinel-data`) lives on the instance; snapshot it with
  regular ECS disk snapshots until the Postgres migration.

## Scaling path (Phase 2 → beyond)

1. **One box, more workers**: raise `workers.pool_size` in rules.yaml,
   then `docker compose restart sentinel` — the pool is sized once at
   startup (detection thresholds hot-reload; the worker count does not).
2. **Postgres**: move storage off SQLite when either (a) more than one
   process needs the DB or (b) the event log outgrows instance disk. Schema
   is deliberately plain SQL. On AWS this becomes RDS; the migration will
   introduce a `SENTINEL_DB_DSN` setting (not implemented yet — today the
   engine is SQLite-only), and the compose postgres service goes away.
3. **Multiple egress IPs**: shard accounts across compose stacks/VMs. The
   engine's Task/queue shape is designed so a Redis-backed distributed queue
   (already provisioned behind the `scale` profile) replaces the in-process
   queue without changing worker logic.

## Operator accounts and two-factor (#87, decision 19)

The ops dashboard requires a sign-in once an operator account exists, and
that sign-in is password **plus** a second factor — mandatory, not opt-in.
On the host, inside the container's environment (it needs `SENTINEL_MASTER_KEY`):

```bash
sentinel operator add erwin --name "Erwin Garcia"   # prompts for a password,
                                                     # then walks through enrolment:
                                                     # scan the secret, enter a code,
                                                     # save the recovery codes (shown once)
sentinel operator list                               # 2FA column: on / NOT YET
```

An operator created with `--enrol-later` (or from a non-interactive shell)
is walked through enrolment at their first sign-in and cannot get past it.
Two-factor is not in force until a code verifies, so a mis-scanned secret
means "try again", never "locked out".

**Lost device.** A recovery code signs in once. When those are gone too,
the only reset is on the host — there is deliberately no dashboard route
for it:

```bash
sentinel operator reset-2fa erwin --as erwin        # recorded in operator_events
```

The reset is recorded with who ran it; the operator enrols again at their
next sign-in. Host access over the tailnet is the root of trust here, as it
is for deploys. `sentinel operator disable` still ends every session
immediately.

## Dead-man's switch (#63, decision 7)

Nothing inside the Guardian can report the Guardian dying, so it pushes a
heartbeat OUT to an external monitor and that monitor alerts when the pushes
stop. Our side is one setting:

```
SENTINEL_HEARTBEAT_URL=https://<your monitor>/<check-id>   # in .env on the host
```

Pushed every `uptime.heartbeat_interval_seconds` (300 s); set the monitor's
grace to about 15 minutes (three missed beats). A wedged process stops
pushing too - that is the point. If the monitor itself cannot be reached the
Guardian pages HIGH (`heartbeat_unreachable`); on restart, if the last beat is
older than two intervals, it records the window as a monitoring gap, pages
HIGH (`monitoring_gap`), and the next member digest tells customers plainly
that their accounts were not being checked in that window.

The vendor is the owner's choice (decision 7 recommends any dead-man's-switch
service that pages a phone: the destination must not route through the
Guardian). What is NOT covered by code: a drill of the vendor's alert path -
#63's criterion - which needs the vendor to exist first.

## Operations quick reference

All commands below assume the default EC2 posture (the `ubuntu` user is
deliberately NOT in the docker group — membership is root-equivalent), so
every docker command takes `sudo`:

| Task | Command |
|---|---|
| Tail logs (JSON) | `sudo docker compose logs -f sentinel` |
| Container health | `sudo docker compose ps` (healthcheck hits the dashboard every 30s) |
| Acknowledge a CRITICAL alert | `sudo docker compose exec sentinel sentinel ack <alert_id>` |
| Send digest now | `sudo docker compose exec -T sentinel sentinel digest` |
| Tune thresholds | edit `config/rules.yaml` on the host — hot-reloads, no restart |
| AI triage narrative for an account | `sudo docker compose exec sentinel sentinel triage --label <label>` |
| Update to latest code | `sudo /opt/minas-guardian/scripts/deploy.sh` — the full documented sequence, one command ("Deploying code changes" above) |
| What is running right now | `sudo docker compose exec -T sentinel sentinel version` (also in the dashboard footer) |
| Stop the stack | `sudo docker compose down` — **never with `-v`**: that flag also deletes the volumes, i.e. the customer database (see the warning section above) |
