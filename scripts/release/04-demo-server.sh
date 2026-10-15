#!/usr/bin/env bash
# Release guide, section 2: the public cached demo server (a small CPU droplet, Ubuntu 24.04, run
# as root). Installs Docker, clones the repository, writes .env (PUBLIC_HOST on sslip.io, a random
# PostgreSQL password, the admin password hash), waits for demo-cache.tar.gz, then
# make demo-import demo-up and checks HTTPS from the server itself.
#
#   curl -fsSL https://raw.githubusercontent.com/Saroth85/ReturnRing/main/scripts/release/04-demo-server.sh -o 04-demo-server.sh
#   ADMIN_PASSWORD='...' bash 04-demo-server.sh          # or let it prompt
#
# Environment: ADMIN_PASSWORD (prompted if unset), PUBLIC_IP (detected if unset), REPO_URL,
# BRANCH (main). Before running: the provider's cloud firewall allows inbound 22, 80 and 443
# (Docker-published ports bypass ufw). Re-running is safe: it keeps .env and the package.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Saroth85/ReturnRing.git}"
BRANCH="${BRANCH:-main}"
DIR="${DIR:-$HOME/ReturnRing}"

step() { printf '\n==== %s  [%s]\n' "$1" "$(date +%H:%M:%S)"; }
ok()   { printf 'OK   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; }

step "1. packages"
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q --no-install-recommends docker.io docker-compose-v2 make git curl ca-certificates openssl
  systemctl enable --now docker
fi
ok "$(docker --version); $(docker compose version | head -n 1); $(make --version | head -n 1)"

step "2. repository"
if [ -d "$DIR/.git" ]; then git -C "$DIR" fetch -q origin "$BRANCH" && git -C "$DIR" checkout -q "$BRANCH" && git -C "$DIR" pull -q --ff-only; else git clone -q --branch "$BRANCH" "$REPO_URL" "$DIR"; fi
cd "$DIR"
ok "$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"

step "3. .env"
[ -f .env ] || cp .env.example .env
PUBLIC_IP="${PUBLIC_IP:-$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") print $(i + 1); exit}')}"
[ -n "$PUBLIC_IP" ] || { fail "could not detect the public IP: run with PUBLIC_IP=<ip>"; exit 1; }
PUBLIC_HOST="${PUBLIC_IP//./-}.sslip.io"
if grep -q '^POSTGRES_PASSWORD=change-me$' .env; then PG_PASSWORD="$(openssl rand -hex 16)"; else PG_PASSWORD="$(sed -n 's/^POSTGRES_PASSWORD=//p' .env)"; fi
if grep -q "^ADMIN_PASSWORD_HASH='\?\$2" .env; then
  ADMIN_HASH="$(sed -n "s/^ADMIN_PASSWORD_HASH=//p" .env | tr -d "'")"
  ok "admin password hash kept from .env"
else
  if [ -z "${ADMIN_PASSWORD:-}" ]; then read -r -s -p "admin password for /api/telemetry (not echoed): " ADMIN_PASSWORD; printf '\n'; fi
  [ -n "$ADMIN_PASSWORD" ] || { fail "empty admin password"; exit 1; }
  CADDY_IMAGE="$(sed -n 's/^CADDY_IMAGE = //p' Makefile)"
  ADMIN_HASH="$(docker run --rm "$CADDY_IMAGE" caddy hash-password --algorithm bcrypt --bcrypt-cost 10 --plaintext "$ADMIN_PASSWORD")"
fi
python3 - "$PG_PASSWORD" "$PUBLIC_HOST" "$ADMIN_HASH" <<'EOF'
import re, sys
pg, host, admin = sys.argv[1:4]
text = open(".env").read()
def put(key, value):
    global text
    line = f"{key}={value}"
    text, n = re.subn(rf"(?m)^{key}=.*$", line.replace("\\", r"\\"), text)
    if n == 0:
        text = text.rstrip("\n") + "\n" + line + "\n"
put("POSTGRES_PASSWORD", pg)
put("DATABASE_URL", f"postgresql://returnring:{pg}@postgres:5432/returnring")
put("PUBLIC_HOST", host)
put("ADMIN_PASSWORD_HASH", f"'{admin}'")
open(".env", "w").write(text)
EOF
chmod 600 .env
ok "PUBLIC_HOST=$PUBLIC_HOST, PostgreSQL password and admin hash written to .env (mode 600)"

step "4. the demo package"
if [ ! -f demo-cache.tar.gz ]; then
  printf 'copy it from the laptop, in another terminal:\n  scp demo-cache.tar.gz root@%s:%s/\nwaiting ' "$PUBLIC_IP" "$DIR"
  until [ -f demo-cache.tar.gz ]; do sleep 5; printf '.'; done; printf '\n'; sleep 5
fi
ls -l demo-cache.tar.gz

step "5. make demo-import demo-up"
make demo-import
make demo-up
python3 -c 'import json; m=json.load(open("demo-cache/MANIFEST.json")); c=m.get("llm_cache", {}); print("package:", {"created_at": m.get("created_at"), "snapshot_as_of": m.get("serving", {}).get("as_of"), "drafts": c.get("entries"), "generated_on": c.get("generated_on"), "rehearsal": c.get("rehearsal"), "model": c.get("gemma_model")})'

step "6. checks over HTTPS (the certificate can take up to two minutes)"
t=0
until curl -sf "https://$PUBLIC_HOST/healthz" >/dev/null 2>&1; do
  if (( t >= 240 )); then fail "https://$PUBLIC_HOST/healthz not answering; docker compose logs proxy"; exit 1; fi
  sleep 5; t=$((t + 5)); printf '.'
done
printf '\n'; ok "https://$PUBLIC_HOST/healthz answers after ${t}s"
curl -s "https://$PUBLIC_HOST/readyz" | python3 -c 'import json,sys; r=json.load(sys.stdin); print("readyz:", {k: r.get(k) for k in ("status","mode","llm_mode","label","contract_version","public_read_only")}); print("clock:", r.get("clock"))'
code="$(curl -s -o /dev/null -w '%{http_code}' "https://$PUBLIC_HOST/api/telemetry")"
if [ "$code" = 401 ]; then ok "/api/telemetry asks for the admin password (401)"; else fail "/api/telemetry answered $code, expected 401"; fi

printf '\nPublic demo: https://%s   (admin user for /api/telemetry: admin)\n' "$PUBLIC_HOST"
printf 'Saturday, with the MI300X package in %s: make demo-down && make demo-import demo-up\n' "$DIR"
