#!/bin/bash
# EC2 user-data bootstrap for Minas AI Guardian (Ubuntu 24.04 LTS).
# Paste this into "Advanced details -> User data" when launching the instance,
# or run it once over SSH as root.
#
# Deliberately contains NO secrets: user-data is readable from the instance
# metadata service, so the .env (master key, Telegram token) is created by
# hand over SSH afterwards - see docs/deployment.md "AWS quickstart".
#
# It also does NOT clone the repo: the repo is private, and cloning needs the
# read-only deploy key that is created by hand on first login (MOTD below).
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl git

# --- Docker (official convenience script; installs engine + compose plugin) ---
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

# --- 1G swap: lets a t3.micro (1GB RAM) build/run the image comfortably ------
if ! swapon --show | grep -q swapfile; then
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# --- Unattended security updates ---------------------------------------------
apt-get install -y unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

# --- Handoff note for the operator's first SSH login ---------------------------
cat > /etc/motd <<'MOTD'
=====================================================================
 Minas AI Guardian - bootstrap complete. Finish the deploy:

   1. Deploy key (repo is private; key must be root's - pulls run via sudo):
        sudo ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N '' -C minas-deploy
        sudo cat /root/.ssh/id_ed25519.pub
          -> GitHub repo -> Settings -> Deploy keys -> Add deploy key
             leave "Allow write access" UNCHECKED (read-only, always)
   2. sudo git clone git@github.com:erwingarcia1/Minas-AI-Guardian-Bitget-Read-only.git \
        /opt/minas-guardian     # this exact path: compose volume names derive from it
   3. cd /opt/minas-guardian
   4. sudo cp .env.example .env && sudo nano .env
        - SENTINEL_MASTER_KEY  (generate fresh, or - if migrating the old
          machine's database - copy the OLD key here BEFORE step 6)
        - TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
   5. sudo chmod 600 .env
   6. sudo docker compose up -d --build
   7. sudo docker compose exec -it sentinel sentinel onboard --label <name>
   8. Whitelist this instance's Elastic IP on every exchange API key
      (Bitget, Bybit, OKX, Binance) - docs/deployment.md "The Elastic IP is
      load-bearing". A key without it is refused on its first check.
   9. The public address - docs/deployment.md "The public address" has each
      check; do these in order, and go on only when the check passes:
        a. DNS: an A record for guardian.minastrade.com -> this instance's
           Elastic IP, no AAAA record.
             dig +short guardian.minastrade.com A      # expect: the Elastic IP
        b. Security group: TCP 80 and 443 from 0.0.0.0/0; SSH 22 from admin
           IPs only.
             nc -vz -w 5 <elastic-ip> 443
               # expect: succeeded; a timeout means the group still drops it
        c. .env: SENTINEL_PORTAL_URL=https://guardian.minastrade.com, then
             sudo docker compose up -d      # up -d, not restart: a container's
                                            # environment is fixed at creation
        d. "docker compose up -d" (step 6) started caddy, which fetches the
           certificate itself:
             sudo docker compose logs caddy | grep "certificate obtained"
           Nothing after a few minutes: recheck a and b, then
           "sudo docker compose restart caddy" tries again at once.
        e. sudo docker compose exec -it sentinel sentinel operator add <name>
           (password, then two-factor enrolment), then sign in at
           https://guardian.minastrade.com/admin

 Daily host access is what you are using now: SSH on port 22 with the EC2
 key pair, from an address in the security group's port-22 rule. Never
 delete that rule - it is the only way onto the box.

 Updates from now on, one command (docs/deployment.md "Deploying code
 changes" - ships release tags only):
   sudo /opt/minas-guardian/scripts/deploy.sh

 Break-glass dashboard access, when https://guardian.minastrade.com/admin
 is unreachable (only needs the EC2 key pair, from an allowlisted address):
   ssh -i your-key.pem -L 8390:127.0.0.1:8390 -L 9109:127.0.0.1:9109 \
       ubuntu@<elastic-ip>          ->  http://localhost:8390/admin/
=====================================================================
MOTD

echo "minas-guardian bootstrap done" >> /var/log/minas-bootstrap.log
