#!/usr/bin/env bash
# Release guide, section 1.2: the cached demo on the laptop with a rehearsal package (no GPU, no
# drafts), the same path the public demo server follows. Run after 02-laptop-docker-check.sh.
#
#   bash scripts/release/03-cached-rehearsal.sh
#
# Expected: demo-cache.tar.gz with "rehearsal": true in its MANIFEST.json; the dev stack stopped;
# make demo-import then make demo-local; /readyz in cached mode, read-only, the rehearsal label
# and a frozen clock; make demo-reset then make demo-walk: the writes of the pitch refused (403),
# the checkout evaluation still answering. Keeps demo-cache.tar.gz: it is what the demo server
# imports tonight (section 2); the MI300X package replaces it on Saturday.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

finish() {
  local code=$?
  step "collecting the containers' state"
  collect demo "$COMPOSE_DEMO"
  pack
  if [ "$code" = 0 ]; then ok "03-cached-rehearsal: every step passed"; else fail "03-cached-rehearsal: a step failed (exit $code); the logs say which"; fi
}
trap finish EXIT

test -f serving/manifest.json || { fail "no serving/ snapshot: run 02-laptop-docker-check.sh first"; exit 1; }

run 05-demo-export make demo-export ALLOW_EMPTY_CACHE=1 DEMO_GPU="CPU rehearsal (laptop)"
cp demo-cache/MANIFEST.json "$LOGS/MANIFEST-rehearsal.json"
python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); c=m.get("llm_cache", {}); print("manifest:", {"created_at": m.get("created_at"), "snapshot_as_of": m.get("serving", {}).get("as_of"), "drafts": c.get("entries"), "generated_on": c.get("generated_on"), "rehearsal": c.get("rehearsal"), "nature": c.get("nature")})' "$LOGS/MANIFEST-rehearsal.json"
ls -l demo-cache.tar.gz

run 06-dev-down make dev-down
run 07-demo-import make demo-import
run 08-demo-local make demo-local
wait_ready "$API_URL" 600
save_readyz readyz-cached.json
if curl -sf "$UI_URL/" | grep -qi '<html'; then ok "UI answers on $UI_URL"; else fail "UI does not answer on $UI_URL"; exit 1; fi

ensure_venv
run 09-demo-reset make demo-reset
run 10-demo-walk make demo-walk
cp artifacts/demo-walk.json "$LOGS/demo-walk-cached.json"
step "demo walk summary (read-only cached demo)"
walk_summary "$LOGS/demo-walk-cached.json"
python3 -c 'import json,sys; sys.exit(1 if json.load(open(sys.argv[1]))["failed"] else 0)' "$LOGS/demo-walk-cached.json"
printf '\nThe cached demo stays up on %s (make demo-down stops it). demo-cache.tar.gz is ready for the demo server.\n' "$UI_URL"
