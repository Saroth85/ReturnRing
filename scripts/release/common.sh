# Shared helpers for the release scripts (docs/release-guide.md). Sourced, never run.
# Every script logs each step into release-logs/<step>.log and packs the folder at the end, so
# the whole run can be sent back as one file.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
LOGS="$ROOT/release-logs"
mkdir -p "$LOGS"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
API_URL="${API_URL:-http://127.0.0.1:8000}"
UI_URL="${UI_URL:-http://localhost:8080}"
COMPOSE_DEV="docker compose -f infra/docker-compose.yml --env-file .env --profile dev"
COMPOSE_DEMO="docker compose -f infra/docker-compose.yml -f infra/compose.demo.yml --env-file .env --profile demo"
# Loopback calls must not go through a proxy the shell may have configured.
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"

step() { printf '\n==== %s  [%s]\n' "$1" "$(date +%H:%M:%S)"; }
ok()   { printf 'OK   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; }

# run <log-name> <command...>: runs the command, tees its output into release-logs/<log-name>.log
# and keeps the command's exit code.
run() {
  local name="$1"; shift
  step "$name: $*"
  "$@" 2>&1 | tee "$LOGS/$name.log"
  return "${PIPESTATUS[0]}"
}

# wait_ready <base-url> <seconds>: polls <base-url>/readyz until it answers 200.
wait_ready() {
  local url="$1" limit="${2:-600}" t=0
  printf 'waiting for %s/readyz (up to %ss) ' "$url" "$limit"
  until curl -sf "$url/readyz" >/dev/null 2>&1; do
    if (( t >= limit )); then printf '\n'; fail "$url/readyz did not answer within ${limit}s"; return 1; fi
    sleep 5; t=$((t + 5)); printf '.'
  done
  printf '\n'; ok "$url/readyz answers after ${t}s"
}

# save_readyz <file-name>: stores /readyz, pretty-printed, and shows the fields that matter.
save_readyz() {
  curl -s "$API_URL/readyz" | python3 -m json.tool > "$LOGS/$1"
  python3 - "$LOGS/$1" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1]))
keys = ["status", "mode", "llm_mode", "device", "label", "contract_version", "public_read_only"]
print("readyz:", {k: r.get(k) for k in keys})
print("clock:", r.get("clock"))
for c in r.get("checks", []):
    print("  check", c.get("component"), c.get("status"), c.get("detail") or "")
EOF
}

# ensure_venv: a Python 3.12 virtualenv with requirements/dev.txt on the PATH (make demo-walk).
ensure_venv() {
  local py=""
  for cand in python3.12 python3.13 python3; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
      py="$cand"; break
    fi
  done
  if [ -z "$py" ]; then fail "Python 3.12 or later is required (python3.12 not found)"; return 1; fi
  if [ ! -x "$ROOT/.venv/bin/python" ]; then
    step "creating .venv with $py"
    "$py" -m venv "$ROOT/.venv"
  fi
  step "installing requirements/dev.txt into .venv"
  "$ROOT/.venv/bin/pip" install -q --disable-pip-version-check -r requirements/dev.txt
  export PATH="$ROOT/.venv/bin:$PATH"
  ok "python: $(python --version)"
}

# collect <name> <compose command>: the containers and their last lines, for the report.
collect() {
  local name="$1" compose="$2"
  $compose ps > "$LOGS/$name-ps.txt" 2>&1 || true
  $compose logs --tail 300 > "$LOGS/$name-logs.txt" 2>&1 || true
}

# walk_summary <report.json>: the steps of the demo walk (scripts/demo_walk.py: passed, failed,
# latency per step).
walk_summary() {
  python3 - "$1" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1]))
passed, failed, latency = r.get("passed", []), r.get("failed", {}), r.get("latency", {})
for name in passed:
    lat = latency.get(name, {})
    print(f"  ok   {name:<13} p50 {lat.get('p50_s', '?')} s, max {lat.get('max_s', '?')} s")
for name, why in failed.items():
    print(f"  FAIL {name:<13} {why}")
print(f"demo walk: {len(passed)} ok, {len(failed)} failed")
EOF
}

# pack: one archive with every log of this run.
pack() {
  tar -czf "$ROOT/release-logs-$STAMP.tgz" -C "$ROOT" release-logs
  printf '\nLogs: %s\nSend this file: %s\n' "$LOGS" "$ROOT/release-logs-$STAMP.tgz"
}
