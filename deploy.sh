#!/bin/bash
# ── GeoPulse Deploy Script ──────────────────────────────
# Run on VPS-2 (YOUR_SERVER_IP) or locally via SSH.
#
# Usage:
#   ssh -i ~/.ssh/graf_vlasti root@YOUR_SERVER_IP 'bash /root/geo-pulse-next/deploy.sh'
#   OR locally: ./deploy.sh  (runs rsync + remote build)

set -euo pipefail

# ── Config ──────────────────────────────────────────────
IMAGE="geo-pulse-next:massaraksh"
CONTAINER="geo-pulse-next"
PORT="3333:3000"
BUILD_ARG='--build-arg API_URL=""'

echo "🐙 GeoPulse Deploy starting..."

# ── If running ON VPS ───────────────────────────────────
if [ -f /root/geo-pulse-next/Dockerfile ]; then
  cd /root/geo-pulse-next

  echo "📥 Pulling latest from GitHub..."
  git pull origin nextjs

  echo "📦 Building Docker image (no-cache)..."
  docker build --no-cache $BUILD_ARG -t "$IMAGE" .

  echo "🔄 Restarting container..."
  docker stop "$CONTAINER" 2>/dev/null || true
  docker rm "$CONTAINER" 2>/dev/null || true
  docker run -d --name "$CONTAINER" --restart unless-stopped -p "$PORT" "$IMAGE"

  sleep 3
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3333)
  if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Deploy OK — HTTP $HTTP_CODE"
  else
    echo "❌ Deploy FAILED — HTTP $HTTP_CODE"
    docker logs "$CONTAINER" --tail 20
    exit 1
  fi

# ── If running LOCALLY ──────────────────────────────────
else
  VPS="root@YOUR_SERVER_IP"
  KEY="$HOME/.ssh/graf_vlasti"

  echo "🏗️ Running remote deploy (git pull + docker build)..."
  ssh -i "$KEY" "$VPS" 'bash /root/geo-pulse-next/deploy.sh'
fi

echo "🎉 Done!"
