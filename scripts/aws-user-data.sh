#!/bin/bash
# EC2 user-data bootstrap for Minas AI Guardian (Ubuntu 24.04 LTS).
# Paste this into "Advanced details -> User data" when launching the instance,
# or run it once over SSH as root.
#
# Deliberately contains NO secrets: user-data is readable from the instance
# metadata service, so the .env (master key, Telegram token) is created by
# hand over SSH afterwards - see docs/deployment.md "AWS quickstart".
set -euo pipefail

REPO_URL="https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only.git"
APP_DIR="/opt/minas-guardian"

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

# --- App checkout --------------------------------------------------------------
if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
fi

# --- Handoff note for the operator's first SSH login ---------------------------
cat > /etc/motd <<'MOTD'
=====================================================================
 Minas AI Guardian - bootstrap complete. Finish the deploy:

   1. cd /opt/minas-guardian
   2. sudo cp .env.example .env && sudo nano .env
        - SENTINEL_MASTER_KEY  (generate fresh, or copy from the old
          machine if migrating its database)
        - TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
   3. sudo docker compose up -d --build
   4. sudo docker compose exec -it sentinel sentinel onboard --label <name>
   5. Whitelist this instance's Elastic IP on the Bitget API key.

 Dashboard (from your laptop, never exposed publicly):
   ssh -L 8390:127.0.0.1:8390 ubuntu@<elastic-ip>   ->  http://localhost:8390
=====================================================================
MOTD

echo "minas-guardian bootstrap done" >> /var/log/minas-bootstrap.log
