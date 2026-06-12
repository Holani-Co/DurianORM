#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/root/DurianORM"
CHATWOOT_DIR="$REPO_DIR/chatwoot"
ZOHO_DIR="$REPO_DIR/zoho-bridge"
RBENV_BUNDLE="/root/.rbenv/versions/3.4.4/bin/bundle"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

cd "$REPO_DIR"

# ── 1. Pull latest code ───────────────────────────────────────────────────────
log "Pulling latest code..."
git pull

# ── 2. Ruby dependencies ──────────────────────────────────────────────────────
log "Installing Ruby gems..."
cd "$CHATWOOT_DIR"
BUNDLE_SILENCE_ROOT_WARNING=1 $RBENV_BUNDLE install --quiet

# ── 3. JS dependencies ────────────────────────────────────────────────────────
log "Installing JS packages..."
pnpm install --frozen-lockfile --silent --ignore-scripts

# ── 4. Free RAM for the build (services restart in step 6) ────────────────────
log "Stopping web/worker to free memory for the build..."
systemctl stop chatwoot-worker chatwoot-web || true

# ── 5. Asset precompile ───────────────────────────────────────────────────────
log "Precompiling assets..."
NODE_OPTIONS="--max-old-space-size=3072" RAILS_ENV=production $RBENV_BUNDLE exec rails assets:precompile 2>&1 | grep -v "DEPRECATION WARNING\|legacy-js-api\|v-deep\|More info:" | tail -10

# ── 6. DB migrations ──────────────────────────────────────────────────────────
log "Running DB migrations..."
RAILS_ENV=production $RBENV_BUNDLE exec rails db:migrate

# ── 7. Restart services ───────────────────────────────────────────────────────
log "Restarting chatwoot-web..."
systemctl restart chatwoot-web

log "Restarting chatwoot-worker..."
systemctl restart chatwoot-worker

# ── 8. Zoho bridge (sync deps + restart if a systemd unit exists) ─────────────
if systemctl list-unit-files zoho-bridge.service --quiet 2>/dev/null | grep -q zoho-bridge; then
  if [ -f "$ZOHO_DIR/requirements.txt" ]; then
    log "Installing zoho-bridge Python deps..."
    "$ZOHO_DIR/venv/bin/pip" install -q -r "$ZOHO_DIR/requirements.txt"
  fi
  log "Restarting zoho-bridge..."
  systemctl restart zoho-bridge
else
  log "zoho-bridge has no systemd unit — skipping"
fi

# ── 9. Health check ───────────────────────────────────────────────────────────
log "Waiting for web server to come up..."
for i in $(seq 1 15); do
  if curl -sf http://localhost:3000/health > /dev/null 2>&1; then
    log "Health check passed."
    break
  fi
  sleep 2
  if [ "$i" -eq 15 ]; then
    log "WARNING: health check did not pass after 30s — check logs:"
    log "  journalctl -u chatwoot-web -n 50"
  fi
done

log "Done."
