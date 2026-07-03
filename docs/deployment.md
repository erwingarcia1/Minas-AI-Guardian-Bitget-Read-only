# Sentinel — Deployment Guide

Target: Alibaba Cloud first, AWS second (per launch decision). Both paths use
the same Docker Compose deployment; only the managed-service names differ.

## Prerequisites

- A Linux VM (Alibaba ECS / AWS EC2), 2 vCPU / 4 GB is plenty for the pilot;
  scale workers before scaling the box.
- Docker + Docker Compose plugin.
- Outbound HTTPS to `api.bitget.com`, `api.telegram.org`, Twilio, your SMTP
  host, and (if triage is enabled) `api.anthropic.com`.

## First deployment

```bash
git clone <repo> sentinel && cd sentinel
cp .env.example .env
# 1. Generate SENTINEL_MASTER_KEY (command in .env.example)
# 2. Fill in alert-channel credentials
docker compose up -d --build
```

Onboard accounts from inside the container so credentials never touch the
host shell history:

```bash
docker compose exec sentinel sentinel onboard --label customer-1
```

Daily digest via cron on the host (MEDIUM/LOW alerts route here, per
`alerting.routing` in rules.yaml):

```bash
0 8 * * * cd /opt/sentinel && docker compose exec -T sentinel sentinel digest
```

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

## Alibaba Cloud specifics

- ECS instance + EIP; security group allows nothing inbound except SSH from
  your admin IPs.
- Use KMS (or at minimum an encrypted RAM-role-scoped parameter) to hold
  `SENTINEL_MASTER_KEY`; inject it into `.env` at deploy time rather than
  storing it on disk unencrypted.
- SQLite volume (`sentinel-data`) lives on the instance; snapshot it with
  regular ECS disk snapshots until the Postgres migration.

## AWS specifics

- EC2 + Elastic IP, same security-group posture.
- `SENTINEL_MASTER_KEY` in Secrets Manager or SSM Parameter Store
  (SecureString), injected at boot.
- When moving to Postgres: RDS + `SENTINEL_DB_DSN`, and the compose postgres
  service goes away.

## Scaling path (Phase 2 → beyond)

1. **One box, more workers**: raise `workers.pool_size` in rules.yaml
   (hot-reloads; no restart).
2. **Postgres**: move storage off SQLite when either (a) more than one
   process needs the DB or (b) the event log outgrows instance disk. Schema
   is deliberately plain SQL.
3. **Multiple egress IPs**: shard accounts across compose stacks/VMs. The
   engine's Task/queue shape is designed so a Redis-backed distributed queue
   (already provisioned in compose) replaces the in-process queue without
   changing worker logic.

## Operations quick reference

| Task | Command |
|---|---|
| Tail logs (JSON) | `docker compose logs -f sentinel` |
| Acknowledge a CRITICAL alert | `docker compose exec sentinel sentinel ack <alert_id>` |
| Send digest now | `docker compose exec sentinel sentinel digest` |
| Tune thresholds | edit `config/rules.yaml` on the host — hot-reloads, no restart |
| AI triage narrative for an account | `docker compose exec sentinel sentinel triage --label <label>` |
