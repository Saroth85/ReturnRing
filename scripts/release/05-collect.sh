#!/usr/bin/env bash
# Collects what to send back after a failed or a manual run: /readyz, the containers of both
# profiles with their last lines, the demo walk report, .env with the secrets blanked.
#
#   bash scripts/release/05-collect.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

step "readyz"
if curl -sf "$API_URL/readyz" >/dev/null 2>&1; then save_readyz readyz-now.json; else printf 'API not answering on %s\n' "$API_URL"; fi
step "containers"
collect dev "$COMPOSE_DEV"
collect demo "$COMPOSE_DEMO"
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' > "$LOGS/docker-ps.txt" 2>&1 || true
docker images --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' > "$LOGS/docker-images.txt" 2>&1 || true
step "files"
[ -f artifacts/demo-walk.json ] && cp artifacts/demo-walk.json "$LOGS/demo-walk-last.json" && walk_summary "$LOGS/demo-walk-last.json"
[ -f serving/manifest.json ] && cp serving/manifest.json "$LOGS/serving-manifest.json"
[ -f demo-cache/MANIFEST.json ] && cp demo-cache/MANIFEST.json "$LOGS/demo-cache-MANIFEST.json"
# .env without its secrets: the keys matter, the values do not.
[ -f .env ] && sed -E 's/^(POSTGRES_PASSWORD|DATABASE_URL|VLLM_API_KEY|HF_TOKEN|ADMIN_PASSWORD_HASH)=.*/\1=<redacted>/' .env > "$LOGS/env-redacted.txt"
{ uname -a; docker --version; docker compose version; make --version | head -n 1; python3 --version; df -h .; } > "$LOGS/system.txt" 2>&1 || true
pack
