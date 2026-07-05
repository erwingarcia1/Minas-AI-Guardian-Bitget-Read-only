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

1. EC2 → Launch instance, region **ca-central-1 (Montreal)**.
2. AMI: **Ubuntu Server 24.04 LTS**. Instance type: **t3.small** (or
   t3.micro to start). Disk: 20 GB gp3.
3. Key pair: create/download one (this is your SSH login).
4. Security group: inbound **SSH (22) from your IP only**. Nothing else —
   the dashboard is reached over an SSH tunnel, never opened publicly.
5. Advanced details → User data: paste the contents of
   [`scripts/aws-user-data.sh`](../scripts/aws-user-data.sh). It installs
   Docker, adds swap, enables unattended security updates, and clones the
   repo to `/opt/minas-guardian`. It contains no secrets by design
   (user-data is readable from instance metadata).
6. Launch, then: Elastic IPs → **Allocate** → **Associate** with the
   instance. This is the permanent egress IP you whitelist on Bitget keys.

**First SSH login** (the message of the day repeats these steps):

```bash
ssh -i your-key.pem ubuntu@<elastic-ip>
cd /opt/minas-guardian
sudo cp .env.example .env && sudo nano .env   # master key + Telegram creds
sudo docker compose up -d --build
sudo docker compose exec -it sentinel sentinel onboard --label customer-1
```

**Reaching the dashboard** (from your laptop):

```bash
ssh -i your-key.pem -L 8390:127.0.0.1:8390 -L 9109:127.0.0.1:9109 ubuntu@<elastic-ip>
# then open http://localhost:8390
```

**Migrating an existing pilot DB instead of re-onboarding:** copy the old
machine's `SENTINEL_MASTER_KEY` into the new `.env` (the DB's credentials
are encrypted with it — a fresh key cannot read them), then:

```bash
scp -i your-key.pem sentinel.db ubuntu@<elastic-ip>:/tmp/
ssh -i your-key.pem ubuntu@<elastic-ip> \
  "sudo docker compose -f /opt/minas-guardian/docker-compose.yml stop sentinel && \
   sudo docker run --rm -v minas-guardian_sentinel-data:/data -v /tmp:/src alpine \
     cp /src/sentinel.db /data/sentinel.db && \
   sudo docker compose -f /opt/minas-guardian/docker-compose.yml start sentinel"
```

**After the instance is live:** add the Elastic IP to each Bitget API key's
IP whitelist (Bitget → API management → edit key). From that moment the key
is useless from any other network — do this for every onboarded account.

**Reboot survival:** `restart: unless-stopped` + Docker's systemd unit mean
the stack comes back on its own after a reboot or crash; no operator action.

## First deployment (any host)

```bash
git clone https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only.git minas-guardian
cd minas-guardian
cp .env.example .env
# 1. Generate SENTINEL_MASTER_KEY (command in .env.example)
# 2. Fill in alert-channel credentials
docker compose up -d --build
```

Onboard accounts from inside the container so credentials never touch the
host shell history:

```bash
docker compose exec -it sentinel sentinel onboard --label customer-1
```

Daily digest via cron on the host (MEDIUM/LOW alerts route here, per
`alerting.routing` in rules.yaml):

```bash
0 8 * * * cd /opt/minas-guardian && docker compose exec -T sentinel sentinel digest
```

The `postgres` and `redis` services are Phase 2 scale-out targets behind a
compose profile — the default `up` runs only the monitor. Enable them later
with `docker compose --profile scale up -d`.

## Ports

| Port | What | Exposure rule |
|---|---|---|
| 8390 | Status dashboard | **Never public.** No auth by design; bind stays on 127.0.0.1, reach it via SSH tunnel or VPN/security-group allowlist. |
| 9109 | Prometheus metrics | Same rule; scrape from a private network only. |

## Rate limits and egress IPs

The capacity math (docs/phase0-capability-audit.md §4) assumes multiple
egress IPs at 1,000+ accounts. On one VM you have one IP — fine for the
pilot; at scale run one compose stack per VM (or per elastic IP) and shard
accounts across them. The `rate_limits.egress_ip_rps` bucket in rules.yaml
protects each IP; keep it at ~60% of Bitget's 100 req/s ceiling.

## Bitget API key IP whitelisting

Once the VM has a stable public IP, re-issue customer read-only keys with
that IP whitelisted on the Bitget side. This is a real security win (a stolen
read-only key becomes useless off our IP) and should be standard onboarding
practice, but note it couples customers to our egress IPs — document the IP
rotation procedure before enforcing it.

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

## Alibaba Cloud specifics (alternative path)

- ECS instance + EIP; security group allows nothing inbound except SSH from
  your admin IPs.
- Use KMS (or at minimum an encrypted RAM-role-scoped parameter) to hold
  `SENTINEL_MASTER_KEY`; inject it into `.env` at deploy time rather than
  storing it on disk unencrypted.
- SQLite volume (`sentinel-data`) lives on the instance; snapshot it with
  regular ECS disk snapshots until the Postgres migration.

## Scaling path (Phase 2 → beyond)

1. **One box, more workers**: raise `workers.pool_size` in rules.yaml
   (hot-reloads; no restart).
2. **Postgres**: move storage off SQLite when either (a) more than one
   process needs the DB or (b) the event log outgrows instance disk. Schema
   is deliberately plain SQL. On AWS this is RDS + `SENTINEL_DB_DSN`, and
   the compose postgres service goes away.
3. **Multiple egress IPs**: shard accounts across compose stacks/VMs. The
   engine's Task/queue shape is designed so a Redis-backed distributed queue
   (already provisioned behind the `scale` profile) replaces the in-process
   queue without changing worker logic.

## Operations quick reference

| Task | Command |
|---|---|
| Tail logs (JSON) | `docker compose logs -f sentinel` |
| Container health | `docker compose ps` (healthcheck hits the dashboard every 30s) |
| Acknowledge a CRITICAL alert | `docker compose exec sentinel sentinel ack <alert_id>` |
| Send digest now | `docker compose exec -T sentinel sentinel digest` |
| Tune thresholds | edit `config/rules.yaml` on the host — hot-reloads, no restart |
| AI triage narrative for an account | `docker compose exec sentinel sentinel triage --label <label>` |
| Update to latest code | `cd /opt/minas-guardian && sudo git pull && sudo docker compose up -d --build` |
