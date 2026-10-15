#!/usr/bin/env bash
# Release guide, section 1: what the laptop needs before the Docker check. Read-only: it installs
# nothing and changes nothing. Exit 1 when a required tool is missing.
#
#   bash scripts/release/01-check-prereqs.sh
#
# On Windows run it inside WSL2 (Ubuntu) with Docker Desktop's WSL integration enabled.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

missing=0
need() {  # need <label> <command...>: prints the command's first output line, or MISSING
  local label="$1"; shift
  if out="$("$@" 2>&1 | head -n 1)"; then ok "$label: $out"; else fail "$label: MISSING ($*)"; missing=$((missing + 1)); fi
}
optional() {
  local label="$1"; shift
  if out="$("$@" 2>&1 | head -n 1)"; then ok "$label: $out"; else printf 'note %s: not found (optional)\n' "$label"; fi
}

step "system"
printf 'os: %s %s\n' "$(uname -s)" "$(uname -m)"
case "$(uname -s)" in
  Linux)  if grep -qi microsoft /proc/version 2>/dev/null; then printf 'WSL2 detected\n'; fi
          printf 'memory: %s\n' "$(free -h 2>/dev/null | awk '/^Mem:/ {print $2 " total, " $7 " available"}')" ;;
  Darwin) printf 'memory: %s GB\n' "$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))" ;;
esac
printf 'free disk here: %s\n' "$(df -h . | awk 'NR == 2 {print $4}')"

step "required tools"
need "git" git --version
need "GNU make" make --version
need "curl" curl --version
need "tar" tar --version
need "docker CLI" docker --version
need "docker daemon" docker info --format '{{.ServerVersion}} ({{.OSType}}/{{.Architecture}})'
need "docker compose v2" docker compose version
pyfound=0
for cand in python3.12 python3.13 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    ok "python >= 3.12: $cand ($("$cand" --version))"; pyfound=1; break
  fi
done
if [ "$pyfound" = 0 ]; then fail "python >= 3.12: MISSING (needed by make demo-walk; the containers bring their own)"; missing=$((missing + 1)); fi

step "optional tools"
optional "node 22 (only for the UI live e2e)" node --version
optional "ffmpeg (only to compress the video)" ffmpeg -version

step "network"
if curl -sS -o /dev/null -m 15 -w '%{http_code}\n' https://registry-1.docker.io/v2/ | grep -q '401'; then
  ok "Docker Hub reachable"
else
  fail "Docker Hub not reachable (image pulls will fail)"; missing=$((missing + 1))
fi

step "repository"
printf 'branch: %s @ %s\n' "$(git rev-parse --abbrev-ref HEAD)" "$(git rev-parse --short HEAD)"
if [ -f .env ]; then ok ".env exists (kept)"; else printf 'note .env missing: 02-laptop-docker-check.sh copies .env.example\n'; fi
if [ -d serving ] && [ -f serving/manifest.json ]; then ok "serving/ snapshot present (make dev-up skips make demo-data)"; else printf 'note no serving/ yet: make demo-data builds it (about 10 min plus the image build)\n'; fi

step "result"
if [ "$missing" = 0 ]; then ok "everything required is here: run bash scripts/release/02-laptop-docker-check.sh"; else fail "$missing required item(s) missing"; exit 1; fi
