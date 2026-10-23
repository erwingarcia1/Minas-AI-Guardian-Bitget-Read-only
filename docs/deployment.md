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
4. Security group: inbound **SSH (22) from your IP only** - this rule is
   the only way onto the box, so keep it current ("Reaching the box"
   below) - and **TCP 80 and 443 from anywhere** for the public address
   (decision 22b; Caddy holds them - "The public address" below). Nothing
   else: 8390, 8391 and 9109 stay on 127.0.0.1. The dashboard is reached
   through Caddy at `/admin`, behind its own sign-in; the metrics endpoint
   is never published.
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

**Reaching the dashboard** (from your laptop):
`https://guardian.minastrade.com/admin/`. Caddy forwards `/admin` and
`/admin/*` to the dashboard, and the dashboard asks for an operator sign-in
with its mandatory second factor ("Operator accounts and two-factor"
below). There is no network gate in front of it: the sign-in is the gate.
The SSH tunnel is the break-glass path, since it needs nothing but the EC2
key pair and the security group's SSH rule:

```bash
ssh -i Minas-AI-Guardian-Key-Tokyo.pem -L 8390:127.0.0.1:8390 -L 9109:127.0.0.1:9109 ubuntu@57.181.51.14
#   production's key pair and Elastic IP; a fresh instance: its own key and address
# then open http://localhost:8390/admin/ - by that name: the cookie exemption below names localhost.
# The dashboard serves under /admin inside the container too (compose pins
# SENTINEL_DASHBOARD_BASE_PATH=/admin), so the path is the same through the
# tunnel; a bare http://localhost:8390/ redirects there.
```

The dashboard's session cookie is marked `Secure` (decision 22c item 2),
so a browser returns it over HTTPS only - with one exception that keeps
the tunnel working: per MDN's `Set-Cookie` page, the `https:` requirement
"is ignored when the `Secure` attribute is set by localhost". Open the
tunnel as `http://localhost:8390/admin/`. Not checked in every browser
here: if one signs you in and then shows the sign-in page again, it
refused the cookie - use another browser, or the public address. One
more, once: a browser that opened the tunnel before this release may
still hold an `ops_session` cookie with `Path=/`. It is still a valid
session for up to 12 hours, and `/admin/logout` cannot clear it (it
clears `Path=/admin` only), so after the upgrade clear the browser's
cookies for `localhost` once, or you stay signed in after signing out
until the old token expires. And a browser or extension that trims
same-origin referrers to the bare origin is refused on every dashboard
POST, the sign-in included, with "cross-origin POST rejected: Referer
outside /admin" (the service log says the same, with your address):
the dashboard requires a browser's POST to name a page under `/admin`,
which is what tells its own forms from one planted on a portal page.
Turn the trimming off for this site.

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

## Reaching the box: SSH with the EC2 key pair

Every login is SSH on port 22 with the instance's key pair, by the Elastic
IP, from an address the security group's SSH rule admits. There is no VPN
and no overlay network (decision 22e; the teardown is step 4 of "The public
address" below - until it has run on the box, the old daemon is still
installed there, and nothing in this guide uses it). The rule is the gate,
so read it in the AWS console (EC2 → Security groups → the instance's
group → Inbound rules → SSH) rather than trusting this page, and add your
current address as a `/32` before you need it.

```bash
chmod 400 Minas-AI-Guardian-Key-Tokyo.pem
ssh -i Minas-AI-Guardian-Key-Tokyo.pem ubuntu@57.181.51.14
cd /opt/minas-guardian && sudo docker compose ps -a   # a read-only command proves the login; the stack's state is the answer
```

A timeout at the `ssh` means the rule does not admit your address (or the
instance is down - the console says which); `Permission denied (publickey)`
means the wrong key file.

- **The dashboard** is `https://guardian.minastrade.com/admin/`, behind
  operator sign-in with a mandatory second factor. New team members get an
  operator account (`sentinel operator add` - "Operator accounts and
  two-factor" below), never a network invite; `sentinel operator disable`
  ends their access at once.
- **Break-glass:** the SSH tunnel in "Reaching the dashboard" above. It
  needs only the key pair and the SSH rule - the same two things as this
  login.
- **The compose file's `127.0.0.1:` publishes stay as they are.** Caddy
  reaches the portal and the dashboard over the `edge` network; the tunnel
  is the only other way to the raw ports, and the only way at all to the
  metrics endpoint (9109).
- **A locked-out box is the failure to design against:** before you change
  the SSH rule or the key pair, prove the new path from a fresh terminal,
  and only then remove the old one.

## The public address: https://guardian.minastrade.com and /admin (decision 22)

Decision 22b (answered 2026-10-23): **Caddy on the instance, as one more
compose service.** It holds ports 80 and 443, the only public listeners
(22c item 4); it gets and renews the certificate from Let's Encrypt by
itself; it forwards `/admin` and `/admin/*` to the ops dashboard (8390)
and everything else to the member portal (8391) - its pages and the apps'
JSON API under `/api/v1`. The metrics endpoint (9109) is never published
(22e). There is no IP allowlist on `/admin` (22c item 5, answered
2026-10-23: no): the operator sign-in with its mandatory second factor and
the login throttle are the protection. This section is also 22e's teardown
of the old way in (step 4), because Caddy cannot have 443 until it is gone.

What the compose file carries for it:

- **`caddy`**: the image pinned to a release and its digest, config in
  `deploy/caddy/Caddyfile` (mounted read-only), certificates in the
  `caddy-data` volume. Keep that volume: re-issuing is rate-limited per
  hostname. Hardened like `sentinel`: every capability dropped except
  `NET_BIND_SERVICE`, without which the image's binary does not start at
  all. The `/admin` route is a named matcher (`@admin path /admin
  /admin/*`) under `handle`, never `handle_path`: the path reaches the
  dashboard unchanged, and the dashboard serves under it.
- **`edge`**: the network between Caddy and `sentinel` (the portal and the
  dashboard), `10.231.0.0/24`, with Caddy fixed at `10.231.0.2`, outside
  the range Docker hands out.
- **`SENTINEL_TRUSTED_PROXIES=10.231.0.2`** in sentinel's environment (22c
  item 6). Behind Caddy every connection comes from Caddy, and a login
  throttle keyed on that address is one bucket for every visitor: five
  wrong passwords from anyone would block every member's sign-in, the
  app's included. So the portal takes the client address from
  `X-Forwarded-For`, and believes it from `10.231.0.2` only. The dashboard
  has its own setting, **`SENTINEL_DASHBOARD_TRUSTED_PROXIES=10.231.0.2`**,
  also in compose: behind Caddy its operator-login throttle and its logs
  name the visitor, not the proxy. (The code default for both stays blank
  - trust no proxy - for any deployment that is not this compose file.)
  Caddy discards whatever a client puts in that header and writes the
  address it saw.
- **`SENTINEL_DASHBOARD_BASE_PATH=/admin`** in sentinel's environment
  (22a), pinned in compose - nothing to set in `.env`. Every link, form
  action and redirect the dashboard emits carries the prefix, its session
  cookie is `Path=/admin`, and a POST whose `Referer` path lies outside
  `/admin` is refused (the CSRF gate's extra check). Unset - the demo
  server, bare `sentinel run` - the dashboard serves at `/` exactly as
  before. The container's healthcheck still probes the bare
  `http://127.0.0.1:8390/healthz`: that path answers before any routing,
  and `/admin/healthz` is not a health route - it meets the sign-in gate
  like any other page.

Bring it up in this order. Each step says how to tell it worked; do not
start the next one until it has. The order departs from 22e's, which put
the teardown after members had moved: step 4 says why it cannot wait that
long. From step 4 until step 7's certificate the portal is unreachable
from the internet (step 8 confirms it) - do those steps in one sitting,
and email members the new address beforehand (22d; there is no overlap
period, the old address dies at step 4).

1. **Prove SSH with the EC2 key pair** (22e step 4; rule 6's read-only
   twin). From a fresh terminal, by the public address, from the network
   you will work from in steps 4 to 9:

   ```bash
   ssh -i Minas-AI-Guardian-Key-Tokyo.pem ubuntu@57.181.51.14
   cd /opt/minas-guardian && sudo git describe --tags && sudo docker compose ps -a
   ```

   Expect the deployed tag (one name, no drift suffix) and the container
   list: `sentinel` shows `Exited (137)` while the 2026-09-28 pause holds
   ("Restoring after the 2026-09-28 pause" below), `Up (healthy)`
   otherwise. When it is up, `sudo docker compose exec -T sentinel
   sentinel version` is 22e's own wording of the same proof. Both read and
   change nothing. Do not go on until this works: step 4 removes the other
   way in, and a box nobody can reach gets no deploys and no last-operator
   recovery.

2. **DNS**, at the registrar: an `A` record for `guardian.minastrade.com`
   pointing at `57.181.51.14`, the Elastic IP. No `AAAA` record: the edge
   is IPv4 only. From your laptop:

   ```bash
   dig +short guardian.minastrade.com A            # expect: 57.181.51.14
   dig +short guardian.minastrade.com A @1.1.1.1   # the same answer from a public resolver
   ```

   Wait for both before step 6. Caddy asks for the certificate the moment
   it starts, and Let's Encrypt validates from several places on the
   internet. (Production's record answered correctly on 2026-10-23; the
   `dig` costs nothing, run it anyway.)

3. **Security group**, in the AWS console (EC2 → Security groups → the
   instance's group → Edit inbound rules): add **TCP 80** and **TCP 443**
   from `0.0.0.0/0`. Leave the SSH rule as it is - it is the only way onto
   the box from step 4 on; read it and confirm the address you used in
   step 1 is in it. From your laptop:

   ```bash
   nc -vz -w 5 57.181.51.14 443
   ```

   Before step 6, expect `Connection refused`: the group lets the
   connection through and nothing listens yet. A timeout means the group
   still drops it.

4. **Tear Tailscale down** (22e steps 6 and 7). On the box, in the
   EC2-key SSH session from step 1 - never in a Tailscale SSH session,
   which `tailscale down` ends. From `funnel reset` on, the old address is
   dead and members have no way in until Caddy holds a certificate (step
   7): go straight on. This step comes before Caddy, not after members
   have moved as 22e lists it, because tailscaled's Serve listener holds
   443 (`/` → 8390 in the reading below) and step 5 requires no listener
   there before Caddy may start.

   ```bash
   sudo tailscale serve status        # expect the four listeners read on 2026-09-25:
   #   # Funnel on: https://minas-guardian.tail0fdd4a.ts.net:8443
   #   :8443 (Funnel on)    -> 127.0.0.1:8391   portal, PUBLIC
   #   :8391 (tailnet only) -> 127.0.0.1:8391   portal, tailnet
   #   /     (tailnet only) -> 127.0.0.1:8390   dashboard
   #   :9109 (tailnet only) -> 127.0.0.1:9109   metrics
   sudo tailscale funnel reset
   sudo tailscale serve reset
   sudo tailscale serve status        # expect: no listeners (the CLI's "No serve config" line, or nothing)
   sudo tailscale down
   tailscale status                   # expect: Tailscale is stopped.
   sudo apt-get remove -y tailscale
   sudo rm -f /etc/apt/sources.list.d/tailscale.list
   command -v tailscale               # expect: nothing
   sudo ss -tlnp | grep -c tailscaled # expect: 0
   ip -4 addr show tailscale0         # expect: Device "tailscale0" does not exist.
   ```

   Then in the Tailscale admin console: Machines → minas-guardian →
   Remove. `apt-get remove` leaves `/var/lib/tailscale/tailscaled.state`
   (the node key) on disk; `sudo apt-get purge -y tailscale` or `sudo rm
   -rf /var/lib/tailscale` clears it - the owner's call. The old warning
   against `tailscale funnel reset` (it might take Serve down with Funnel)
   is withdrawn on purpose: Serve goes too. The expected wordings above
   are the CLI's as best known - verify each command's output on the box
   at the time (rule 6). There is no coming back to this address
   afterwards: a reinstall is a new node with a new name.

5. **Read the box** (rule 6's read-only twin). Nothing may hold 80 or 443,
   the `edge` subnet must be free, and the checkout must be clean:

   ```bash
   sudo ss -tlnp '( sport = :80 or sport = :443 )'   # expect: no listener
   sudo ss -tlnp | grep -c tailscaled                 # expect: 0
   ip -4 route | grep '^10\.231\.'                    # expect: nothing
   cd /opt/minas-guardian && sudo git status --short  # expect: nothing
   ```

   A listener on 80 or 443 means step 4 is not finished. A route there
   means something on the host uses that range: stop, and pick another
   subnet for `edge` in docker-compose.yml (caddy's address,
   `SENTINEL_TRUSTED_PROXIES` and `SENTINEL_DASHBOARD_TRUSTED_PROXIES` move
   with it; a test holds them together). A `??` line is an untracked file,
   and `deploy.sh` refuses to run with one in the tree: on production that
   is `held/` from the 2026-09-28 pause, and "Restoring after the
   2026-09-28 pause" below says where it goes.

6. **Deploy the release that carries `/admin`.** The dashboard's base path
   (`SENTINEL_DASHBOARD_BASE_PATH=/admin`, the `Path=/admin` cookie, the
   `Referer` check), the Caddyfile's `/admin` route, compose's
   `SENTINEL_DASHBOARD_TRUSTED_PROXIES` and this guide arrive in one tag;
   it sits in `[Unreleased]` until the release is cut:

   ```bash
   sudo /opt/minas-guardian/scripts/deploy.sh vX.Y.Z
   ```

   Its `docker compose up -d` creates the `edge` network, recreates
   `sentinel` on it - starting it, if the 2026-09-28 pause still holds -
   and starts `caddy`. Expect `deploy: OK — vX.Y.Z is live and verified`.

7. **`docker compose up -d`, then read what runs:**

   ```bash
   cd /opt/minas-guardian
   sudo docker compose up -d     # after deploy.sh, a no-op that confirms nothing is left down
   sudo docker compose ps        # expect: caddy Up; sentinel Up (healthy)
   sudo ss -tlnp | grep -E ':(80|443|8390|8391|9109)\b'
       # expect: 80 and 443 on 0.0.0.0 (and [::]); 8390, 8391 and 9109 on 127.0.0.1 only;
       #         nothing on a 100. address
   sudo docker compose logs sentinel | grep -E 'X-Forwarded-For is believed|no trusted proxy'
       # expect two lines and no "no trusted proxy" line:
       #   "portal: X-Forwarded-For is believed from 10.231.0.2 only"
       #   "dashboard: X-Forwarded-For is believed from 10.231.0.2 only"
   sudo docker compose logs caddy | grep -E 'certificate obtained successfully|"level":"error"'
       # expect: certificate obtained successfully ... "identifier":"guardian.minastrade.com"
   ```

   No certificate after a few minutes: check DNS from outside (step 2) and
   the security group (step 3), then `sudo docker compose restart caddy` to
   try again at once. After a failure Caddy backs off, up to a day between
   attempts, and retries against Let's Encrypt's staging service so the
   failures do not use up the real rate limit.

8. **Check the certificate and the answers that matter**, from your laptop.
   The portal first:

   ```bash
   echo | openssl s_client -connect guardian.minastrade.com:443 -servername guardian.minastrade.com 2>/dev/null \
     | openssl x509 -noout -subject -issuer -dates
       # expect: subject CN=guardian.minastrade.com; a Let's Encrypt issuer; notAfter about 90 days out
   curl -sS -o /dev/null -D - https://guardian.minastrade.com/login    # a GET: the portal answers HEAD with 501
       # expect: HTTP/2 200; strict-transport-security: max-age=31536000;
       #         x-frame-options: DENY; content-security-policy: frame-ancestors 'none'
   curl -sS -i https://guardian.minastrade.com/api/v1/limits
       # expect: HTTP/2 401; www-authenticate: Bearer realm="minas-guardian";
       #         {"error": "unauthorized", "message": "Sign in again."}
   curl -sS -o /dev/null -D - http://guardian.minastrade.com/login
       # expect: HTTP/1.1 308; Location: https://guardian.minastrade.com/login (header case as the server sends it)
   curl -sS https://guardian.minastrade.com/healthz | grep -c 'name="password"'
       # expect: 1 - the portal's sign-in page; the dashboard answers only under /admin
   ```

   Then try a wrong password once at `https://guardian.minastrade.com/login`
   and read the log. It must name YOUR public address, never `10.231.0.2`:

   ```bash
   sudo docker compose logs --since 10m sentinel | grep 'portal login failed'
   ```

   Then the dashboard under `/admin`:

   ```bash
   curl -sS -o /dev/null -D - https://guardian.minastrade.com/admin/login
       # expect: HTTP/2 200; strict-transport-security, x-frame-options and content-security-policy
       #         as on the portal's /login, plus cache-control: no-store, x-content-type-options: nosniff
       #         and x-robots-tag: noindex, nofollow (the dashboard's pages are never cached or indexed)
   curl -sS -o /dev/null -w '%{http_code}\n' https://guardian.minastrade.com/admin
   curl -sS -o /dev/null -w '%{http_code}\n' https://guardian.minastrade.com/admin/
       # expect: 401 and 401 - the sign-in gate, with no session; both spellings reach
       #         the dashboard (one named matcher at Caddy, no redirect between them)
   curl -sS -o /dev/null -w '%{http_code}\n' https://guardian.minastrade.com/admin/healthz
       # expect: 401 - the health route answers the bare /healthz inside the container only
   curl -sS https://guardian.minastrade.com/admin/login | grep -c '<h1>Ops dashboard</h1>'   # expect: 1 - the unbranded ops view (#82)
   curl -sS https://guardian.minastrade.com/login | grep -c '<h1>Ops dashboard</h1>'         # expect: 0 - the portal is untouched
   curl -sS https://guardian.minastrade.com/admin/login | grep -o 'action="[^"]*"'           # expect: action="/admin/login"
   ```

   Measure enrolment before anyone signs in (22c item 1), and treat it as
   a gate while `/admin` is public: `sudo docker compose exec -T sentinel
   sentinel operator list` - every active row's 2FA column `on`. A row at
   `NOT YET` is a password-only account on a public URL: whoever presents
   its password first enrols their own authenticator and owns the
   account, and `LAST LOGIN` then reads as that person's sign-in. So no
   `NOT YET` row may exist while the address is public - enrol at
   `sentinel operator add` on the box (the default, interactive path) or
   over the SSH tunnel before the URL is shared, and never send a password
   by email ("Operator accounts and two-factor" below). Then try a
   wrong password once at `https://guardian.minastrade.com/admin/login`
   and read the log - the dashboard's throttle keys on this address, so it
   too must name YOUR public address, never `10.231.0.2`:

   ```bash
   sudo docker compose logs --since 10m sentinel | grep 'failed operator login'
   ```

   Then sign in properly. The cookie the browser stores is `ops_session`
   with `Path=/admin; HttpOnly; Secure; SameSite=Lax` (the browser's cookie
   inspector shows the attributes), every link and form on the pages - the
   Metrics page's included - starts with `/admin/`, and `/admin/logout`
   clears the cookie with the same path and returns you to `/admin/login`.

9. **`SENTINEL_PORTAL_URL`** (22e step 2). In `/opt/minas-guardian/.env`
   set `SENTINEL_PORTAL_URL=https://guardian.minastrade.com`, then:

   ```bash
   sudo docker compose up -d     # up -d, not restart: a container's environment is fixed when it is created
   sudo docker compose exec -T sentinel printenv SENTINEL_PORTAL_URL   # expect: https://guardian.minastrade.com
   ```

   From then on the address in alert SMS and emails, and in password-reset
   links, is the new one, and the sign-in page names it - the release from
   step 6 carries that copy (CHANGELOG, "The sign-in page names the one
   address"):

   ```bash
   curl -s https://guardian.minastrade.com/login | grep -c 'Our only address'       # expect: 1
   curl -s https://guardian.minastrade.com/login | grep -c 'Our address has changed' # expect: 0 - the host is recognised
   ```

   Telling members the address (22d) is the owner's email, sent before
   step 4; after that step there is no old address left to keep up, so the
   "Our address has changed" notice has nobody to show to.

**Rolling back:** `sudo docker compose stop caddy` takes the public
listener down at once - and with it the portal and the dashboard, for
everyone. You still reach both through the SSH tunnel; members have no way
in until Caddy is back. Deploying an older tag does NOT stop Caddy: a tag
from before Caddy has no `caddy` service in its compose file, so compose
leaves the container running as an orphan, forwarding to a portal it can
no longer reach. Stop it first. A tag from before `/admin` leaves Caddy
forwarding `/admin` to a dashboard that serves at `/`: its sign-in page
still appears there, but its forms post to bare `/login`, which Caddy hands
to the portal. Use the tunnel at `http://localhost:8390/` until you roll
forward. There is no rolling back step 4: the old address does not come
back.

**Restoring after the 2026-09-28 pause.** Alerting was stopped by hand on
2026-09-28 20:50 UTC (the owner's decision: beta, credit cost):
`minas-liveness.timer` and `minas-restart-on-unhealthy.timer` disabled, the
sentinel container stopped (exit 137 after the 10-second grace; the
database's `PRAGMA integrity_check` was ok), and root's `sentinel digest`
cron line commented out. The note recording it,
`held/README-2026-09-28.txt`, was left in the checkout with the cron
backup; both belong in `/var/backups/minas-guardian/held-2026-09-28/` (an
untracked file in the checkout stops `deploy.sh`, which is what step 5's
`git status --short` catches:
`sudo mkdir -p /var/backups/minas-guardian && sudo mv /opt/minas-guardian/held /var/backups/minas-guardian/held-2026-09-28`).
Its restore order, which the steps above sit inside: (1) every exchange
key's allowlist carries `57.181.51.14` again (done 2026-10-23) - at the
stop nine accounts had been dark for a week and were raising 391 tamper
alerts a day, which is exactly the cost a restart before this step brings
back; (2) the deploy (step 6), whose `up -d` starts the container; (3)
then `sudo systemctl enable --now minas-liveness.timer
minas-restart-on-unhealthy.timer` and the cron line uncommented (`sudo
crontab -e`). Read `sudo systemctl list-timers --all | grep minas` and
`sudo crontab -l` afterwards: both timers listed with a next run, the
digest line live.

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

# On the server (EC2 key pair; your address must be in the security group's
# SSH rule - "Reaching the box" above):
ssh -i "Minas-AI-Guardian-Key-Tokyo.pem" ubuntu@57.181.51.14
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
ssh -t -i "Minas-AI-Guardian-Key-Tokyo.pem" ubuntu@57.181.51.14 'sudo /opt/minas-guardian/scripts/deploy.sh v1.4.2'
#   ^ -t allocates the TTY the downgrade confirmation is typed into
```

The script's one property to internalize: **any failure before its "Switching over" step
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
- **From decision 22 the address is also the way in.** The A record for
  `guardian.minastrade.com` points at it, and Caddy answers there on 80
  and 443 for the portal and, under `/admin`, the dashboard ("The public
  address" above); SSH arrives there too ("Reaching the box"). Moving it
  breaks the exchanges' allowlists AND the public address, until DNS
  follows. (Until 2026-10-23 this bullet said the address was not the
  portal's ingress; that was true while the portal was published through
  the overlay network's relay, which step 4 of "The public address"
  removes.)
- **To move the address on purpose:** allocate the new EIP, add it to every
  affected key's allowlist while the old address still works (both entries
  at once - the exchanges allow more than one), then re-associate, then
  change the A record for `guardian.minastrade.com` to the new address,
  then watch `sentinel` for `host_auth_failure` and the dashboard for auth
  failures before removing the old entry. Never the other order. The
  certificate names the host, not the address, so it survives the move.

Daily digest via cron on the host (MEDIUM/LOW alerts route here, per
`alerting.routing` in rules.yaml). **Use root's crontab** (`sudo crontab -e`)
— the default `ubuntu` user is not in the docker group, so an unprivileged
crontab fails silently every day and the digest never sends:

```bash
0 8 * * * cd /opt/minas-guardian && docker compose exec -T sentinel sentinel digest
```

The `postgres` and `redis` services are Phase 2 scale-out targets behind a
compose profile — the default `up` runs the monitor and the `caddy` proxy.
Enable them later with `docker compose --profile scale up -d`.

## Ports

| Port | What | Exposure rule |
|---|---|---|
| 80, 443 | Caddy, the public address (decision 22b) | **The only public listeners** (22c item 4). TLS for `guardian.minastrade.com`; `/admin` and `/admin/*` to the dashboard, everything else to the portal; 80 answers the certificate challenge and redirects to HTTPS. TCP only: HTTP/3 is off. |
| 8391 | Member portal | Published on 127.0.0.1 only. The public reaches it through Caddy over the compose `edge` network. |
| 8390 | Ops dashboard | Published on 127.0.0.1 only. Reached at `https://guardian.minastrade.com/admin/` through Caddy, behind operator sign-in with a mandatory second factor (decision 19); the SSH tunnel is break-glass (`http://localhost:8390/admin/`). |
| 9109 | Prometheus metrics | **Never published** (22e). 127.0.0.1 only; no login of its own. The dashboard's Metrics page (`/admin/metrics`) is the operators' view; for a scrape by hand, the tunnel's `-L 9109`. |

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
  your admin IPs, plus 80 and 443 for the proxy.
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

The ops dashboard requires a sign-in on every page but the container's
own health probe - with no operator account at all it admits nobody and
says so - and that sign-in is password **plus** a second factor —
mandatory, not opt-in. At `https://guardian.minastrade.com/admin` that
sign-in is the gate; there is no network in front of it.
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
next sign-in. Host access - SSH with the EC2 key pair, from an address the
security group admits ("Reaching the box" above) - is the root of trust
here, as it is for deploys. `sentinel operator disable` still ends every
session immediately.

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
| SSH to the box | `ssh -i Minas-AI-Guardian-Key-Tokyo.pem ubuntu@57.181.51.14` — your address must be in the security group's SSH rule ("Reaching the box" above) |
| Open the dashboard | `https://guardian.minastrade.com/admin/` — operator sign-in plus second factor; break-glass: the SSH tunnel ("Reaching the dashboard" above), then `http://localhost:8390/admin/` |
| Tail logs (JSON) | `sudo docker compose logs -f sentinel` |
| Container health | `sudo docker compose ps` (the healthcheck probes the dashboard's bare `/healthz` every 30s) |
| Acknowledge a CRITICAL alert | `sudo docker compose exec sentinel sentinel ack <alert_id>` |
| Send digest now | `sudo docker compose exec -T sentinel sentinel digest` |
| Tune thresholds | edit `config/rules.yaml` on the host — hot-reloads, no restart |
| AI triage narrative for an account | `sudo docker compose exec sentinel sentinel triage --label <label>` |
| Update to latest code | `sudo /opt/minas-guardian/scripts/deploy.sh` — the full documented sequence, one command ("Deploying code changes" above) |
| What is running right now | `sudo docker compose exec -T sentinel sentinel version` (also in the dashboard footer) |
| Stop the stack | `sudo docker compose down` — **never with `-v`**: that flag also deletes the volumes, i.e. the customer database (see the warning section above) |
