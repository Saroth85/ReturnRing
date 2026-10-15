#!/usr/bin/env bash
# Release guide, section 1.1: the judge's path on the laptop with Docker (V8, DEP-01), then the
# demo walk over HTTP. Every step's output goes to release-logs/ and is packed at the end.
#
#   bash scripts/release/02-laptop-docker-check.sh
#
# Expected: make demo-data (about 10 min plus the image build), make dev-up, /readyz ready in live
# mode with the mock LLM, the UI on http://localhost:8080, then make reset and make demo-walk with
# 12 steps passed. Send back release-logs-<stamp>.tgz, whatever the outcome.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

finish() {
  local code=$?
  step "collecting the containers' state"
  collect dev "$COMPOSE_DEV"
  pack
  if [ "$code" = 0 ]; then ok "02-laptop-docker-check: every step passed"; else fail "02-laptop-docker-check: a step failed (exit $code); the logs say which"; fi
}
trap finish EXIT

step "0. .env"
if [ -f .env ]; then ok ".env already there, kept"; else cp .env.example .env; ok ".env copied from .env.example"; fi

run 01-demo-data make demo-data
test -f serving/manifest.json && ok "serving/manifest.json written"

run 02-dev-up make dev-up
wait_ready "$API_URL" 900
save_readyz readyz-dev.json
if curl -sf "$UI_URL/" | grep -qi '<html'; then ok "UI answers on $UI_URL"; else fail "UI does not answer on $UI_URL"; exit 1; fi

ensure_venv
run 03-reset make reset
run 04-demo-walk make demo-walk
cp artifacts/demo-walk.json "$LOGS/demo-walk-dev.json"
step "demo walk summary"
walk_summary "$LOGS/demo-walk-dev.json"
python3 -c 'import json,sys; sys.exit(1 if json.load(open(sys.argv[1]))["failed"] else 0)' "$LOGS/demo-walk-dev.json"
