#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/root/DurianORM"
CHATWOOT_DIR="$REPO_DIR/chatwoot"
ZOHO_DIR="$REPO_DIR/zoho-bridge"
RBENV_BUNDLE="/root/.rbenv/versions/3.4.4/bin/bundle"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ── Environment / .env ────────────────────────────────────────────────────────
# Runtime config lives in $CHATWOOT_DIR/.env (git-ignored, VM-local). This
# script does NOT create or modify it, and `git pull` never touches it — so
# values set there SURVIVE a redeploy. The risk is a FRESH VM (or a recreated
# .env): re-add any non-default vars below, or the app silently falls back to
# its in-code defaults.
#
# Non-default env vars set on this box (keep this list in sync when adding more):
#   WHATSAPP_CAMPAIGN_MAX_RECIPIENTS=50000
#       Per-campaign audience cap (code default 10000, see
#       app/services/whatsapp/campaign_audience_service.rb). Raised 2026-10-17
#       so the ~49.7k furniture label could publish. Read by web (preview) and
#       worker (send), so a change needs both restarted. NOT a Meta limit —
#       Meta enforces its own per-24h tier separately.

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

# ── 4. Asset precompile — BEFORE stopping services ────────────────────────────
# The build is the step that fails: a Vue compile error (e.g. `??` in a template)
# or an OOM on this 4 GB box. Previously we stopped web+worker FIRST to free RAM,
# so a failed build left production DOWN on a half-built tree. Build while the
# services are still UP instead: a failure aborts the deploy here with prod
# untouched. A running Puma serves the asset manifest it loaded at boot, so
# writing new assets underneath it does not disturb live traffic, and swap (see
# /etc/fstab) absorbs the build's peak so it need not fight web/worker for RAM.
# Vite now needs slightly more than a 3 GB JS heap while rendering Chatwoot's
# production chunks. Refuse to start the build unless RAM + free swap provides
# enough headroom for Node, Rails, and the still-running production services.
available_build_kb="$(awk '/MemAvailable:|SwapFree:/ { total += $2 } END { print total + 0 }' /proc/meminfo)"
minimum_build_kb=$((5 * 1024 * 1024))
if [ "$available_build_kb" -lt "$minimum_build_kb" ]; then
  log "ERROR: asset build needs at least 5 GB of available RAM + free swap."
  log "Current available total: $((available_build_kb / 1024)) MB. Production was not changed."
  log "Check: free -h && swapon --show"
  exit 1
fi

log "Precompiling assets (services still up; abort here if the build fails)..."
PRECOMPILE_LOG="$(mktemp)"
precompile_rc=0
NODE_OPTIONS="--max-old-space-size=4096" RAILS_ENV=production \
  $RBENV_BUNDLE exec rails assets:precompile > "$PRECOMPILE_LOG" 2>&1 || precompile_rc=$?
grep -v "DEPRECATION WARNING\|legacy-js-api\|v-deep\|More info:" "$PRECOMPILE_LOG" | tail -20
rm -f "$PRECOMPILE_LOG"
if [ "$precompile_rc" -ne 0 ]; then
  log "ERROR: asset precompile failed (rc=$precompile_rc) — production left running on the OLD build. Nothing was stopped."
  exit 1
fi

# Build is known-good from here, so the only downtime is the restart itself
# (seconds), not the whole build.
# ── 5. DB migrations ──────────────────────────────────────────────────────────
log "Running DB migrations..."
RAILS_ENV=production $RBENV_BUNDLE exec rails db:migrate

# ── 6. Restart services (picks up the new code + freshly built assets) ────────
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
