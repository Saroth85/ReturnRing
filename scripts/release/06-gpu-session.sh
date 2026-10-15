#!/usr/bin/env bash
# Release guide, section 3: the driver of the MI300X session. It runs the make targets of the
# runbook (infra/spikes/README.md: "Steps" 3-13, "The GPU session" 3-14, "The demo export" 3-5,
# then make gpu-up) in the documented order, one checkpoint per target, with a log, the
# wall-clock time and a marker for each, so that a rerun resumes where the last run stopped.
# On the droplet, as root, from the repository root, inside tmux (tmux new -s spikes, as the
# runbook's step 2 names it):
#
#   bash scripts/release/06-gpu-session.sh <phase> [--from <target>] [--only <target>]
#                                          [--skip <t1,t2,...>] [--force] [--dry-run]
#
# Phases (their targets, in the runbook's order):
#   spikes  spike-preflight spike-prep spike-weights spike-gpu-check spike-g2 spike-g2-load
#           spike-g3 spike-weights-wait spike-g2-cpu spike-g3-cpu spike-vllm spike-wait spike-g7
#           spike-g1 spike-g1-bench spike-g5 spike-g6 spike-g4            (runbook steps 3-13)
#   bench   spike-down bench-build gnn-image gnn-test-gpu bench-b1 bench-b2 embeddings-gpu
#           bench-vllm bench-b4 bench-b3 bench-merge  ("The GPU session" steps 3-14; one
#           BENCH_RUN for the whole session, created once and kept in release-logs/gpu/BENCH_RUN)
#   export  spike-vllm spike-wait batch-generate demo-export            ("The demo export" 3-5)
#   live    gpu-up gpu-reset https-check   (the live stack, then /readyz over HTTPS on PUBLIC_HOST)
#   down    spike-down disk-usage, then the reminder: copy back, shutdown, snapshot, destroy.
#           Printed only: this script never snapshots, destroys, prunes or removes a volume.
#   all     spikes, bench, export, in this order
#
# Checkpoints: release-logs/gpu/<phase>/<target>.done is written when a target exits 0. A rerun
# skips the done targets (and those skipped with --skip), so after a failure the next run resumes
# from the failed target; the bench targets reuse BENCH_RUN, so the parts already written in
# artifacts/bench/$BENCH_RUN stay (runbook). Exception, the targets whose effect does not outlive
# the day: spike-vllm, spike-down and every target of live and down (gpu-up, gpu-reset,
# https-check, disk-usage) run every time, marker or not (ALWAYS_RUN below). So a plan B that
# changes .env or infra/docker-compose.yml (--enforce-eager, vLLM 0.30.0, the 26B-A4B model)
# takes effect on a plain rerun: spike-vllm's `up -d` recreates the container and spike-wait
# polls the new one, instead of the old one for up to 60 min; and on a later day, on a droplet
# created from the snapshot (release-logs/ is on its disk, Friday's markers included), live
# still starts the stack and down still stops it before the shutdown (the containers restart
# unless stopped, so a snapshot taken with them defined brings them up on the next boot).
# --from <target> starts there (the targets before it are not run, done or not; done targets
# from it on, <target> itself included, are still skipped: --force --from <target> runs them),
# --only <target> runs that one whatever its marker, --force ignores every marker, --skip marks
# targets as skipped by the operator (a plan B of the runbook) and goes on; an earlier --skip
# holds until --force or --only, except for the always-run targets, which --skip spares only on
# the command line it is given.
# Logs: release-logs/gpu/<phase>/<target>.log. Times: release-logs/gpu/times.tsv holds the start,
# the end and the seconds of every target and of every run. Droplet time is money (1.99 USD an
# hour, spec section 11): the end of each phase prints the targets' time and its cost, an
# estimate, and a lower bound of the bill, which runs from the creation to the destruction of the
# droplet, powered off or not. DROPLET_CREATED=<UTC, e.g. 2026-10-16T08:00:00Z> on any run stores
# the creation time; the phase ends then also print the droplet's age and the bill it implies.
# --dry-run prints `make -n <target>` for every target instead of running it, under
# release-logs/gpu-dry-run/ (the real markers are untouched), and skips the checks that need
# Docker or the GPU, with a note. On a failure the script prints the runbook's plan B for that
# target, packs the logs into release-logs-<stamp>.tgz and exits 1 (2: usage). The archive is
# scrubbed of the .env secrets before it is packed: the values of VLLM_API_KEY,
# POSTGRES_PASSWORD, HF_TOKEN and ADMIN_PASSWORD_HASH are replaced in every log, because vLLM
# prints its arguments at start-up, --api-key included, and make spike-wait prints the last 100
# lines of its log on a failure. The down phase refuses to go on while HF_TOKEN has a value,
# data/truth exists or a git credential file is on the disk: all three would end up in the
# snapshot (runbook step 14; amd-runbook 13.3).
#
# Environment: ENV_FILE (.env), BENCH_RUN (else created once and stored), BENCH_ARGS (passed
# through, e.g. "--b2-samples 1"), DEMO_GPU ("AMD Instinct MI300X (AMD Developer Cloud)"),
# IMAGE (the SPK-G5 photo, artifacts/spikes/garment.jpg), DROPLET_CREATED, ALLOW_NO_TMUX=1
# (not advised: an SSH drop then kills the run).
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

PRICE_USD_PER_HOUR=1.99
ENV_FILE="${ENV_FILE:-.env}"
DEMO_GPU="${DEMO_GPU:-AMD Instinct MI300X (AMD Developer Cloud)}"
BENCH_ARGS="${BENCH_ARGS:-}"
IMAGE_DEFAULT="artifacts/spikes/garment.jpg"
IMAGE="${IMAGE:-$IMAGE_DEFAULT}"
export BENCH_ARGS
GPU_COMPOSE="docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml --env-file $ENV_FILE --profile gpu"
export PATH="$PATH:/opt/rocm/bin"   # amd-smi on the host, as the Makefile and the spikes do

PHASES="spikes bench export live down all"
TARGETS_spikes="spike-preflight spike-prep spike-weights spike-gpu-check spike-g2 spike-g2-load spike-g3 spike-weights-wait spike-g2-cpu spike-g3-cpu spike-vllm spike-wait spike-g7 spike-g1 spike-g1-bench spike-g5 spike-g6 spike-g4"
TARGETS_bench="spike-down bench-build gnn-image gnn-test-gpu bench-b1 bench-b2 embeddings-gpu bench-vllm bench-b4 bench-b3 bench-merge"
TARGETS_export="spike-vllm spike-wait batch-generate demo-export"
TARGETS_live="gpu-up gpu-reset https-check"
TARGETS_down="spike-down disk-usage"
# Cheap, idempotent, and their effect does not outlive the day: never skipped by a marker.
ALWAYS_RUN="spike-vllm spike-down gpu-up gpu-reset https-check disk-usage"

usage() {
  [ $# -gt 0 ] && printf 'error: %s\n\n' "$1" >&2
  sed -n '9,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  printf '\nphases: %s\n' "$PHASES" >&2
  exit 2
}

phase_targets() {
  case "$1" in
    spikes) printf '%s' "$TARGETS_spikes" ;;
    bench) printf '%s' "$TARGETS_bench" ;;
    export) printf '%s' "$TARGETS_export" ;;
    live) printf '%s' "$TARGETS_live" ;;
    down) printf '%s' "$TARGETS_down" ;;
  esac
}

in_words() { case " $2 " in *" $1 "*) return 0 ;; esac; return 1; }
in_list() { case ",$2," in *",$1,"*) return 0 ;; esac; return 1; }

# ---- arguments ---------------------------------------------------------------------------
PHASE=""; FROM=""; ONLY=""; SKIP=""; FORCE=0; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) [ $# -ge 2 ] || usage "--from needs a target"; FROM="$2"; shift 2 ;;
    --only) [ $# -ge 2 ] || usage "--only needs a target"; ONLY="$2"; shift 2 ;;
    --skip) [ $# -ge 2 ] || usage "--skip needs a list of targets"; SKIP="${SKIP:+$SKIP,}$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage ;;
    -*) usage "unknown option $1" ;;
    *) [ -z "$PHASE" ] || usage "one phase only (got $PHASE and $1)"; PHASE="$1"; shift ;;
  esac
done
[ -n "$PHASE" ] || usage "a phase is required"
in_words "$PHASE" "$PHASES" || usage "unknown phase $PHASE"
if [ -n "$FROM" ] && [ -n "$ONLY" ]; then usage "--from and --only exclude each other"; fi
if [ "$PHASE" = all ]; then
  [ -z "$FROM$ONLY" ] || usage "--from and --only need a single phase, not all"
  RUN_PHASES="spikes bench export"
else
  RUN_PHASES="$PHASE"
  for t in $FROM $ONLY; do in_words "$t" "$(phase_targets "$PHASE")" || usage "$t is not a target of the phase $PHASE: $(phase_targets "$PHASE")"; done
fi
for t in ${SKIP//,/ }; do
  found=0
  for p in $RUN_PHASES; do if in_words "$t" "$(phase_targets "$p")"; then found=1; fi; done
  [ "$found" = 1 ] || usage "--skip $t: not a target of $RUN_PHASES"
done

# ---- folders, times ----------------------------------------------------------------------
if [ "$DRY" = 1 ]; then GPU_LOGS="$LOGS/gpu-dry-run"; else GPU_LOGS="$LOGS/gpu"; fi
GPU_REL="${GPU_LOGS#"$LOGS"/}"
mkdir -p "$GPU_LOGS"
TIMES="$GPU_LOGS/times.tsv"
[ -f "$TIMES" ] || printf 'phase\ttarget\tstart_utc\tend_utc\tseconds\tstatus\tusd_estimate_at_%s_per_hour\n' "$PRICE_USD_PER_HOUR" > "$TIMES"
MAKE_COMMON=("ENV_FILE=$ENV_FILE")
RUN_START="$(date +%s)"
RUN_SECS=0
CURRENT_TARGET=""; FAILED_TARGET=""; FAILED_WHERE=""; PHASE_DIR=""
# One run id for the whole session: the environment wins, then the stored one; the bench phase
# creates and stores a new one otherwise (the down reminder and the collected files use it).
BENCH_RUN="${BENCH_RUN:-}"; BENCH_RUN_FROM=""
if [ -n "$BENCH_RUN" ]; then BENCH_RUN_FROM=env
elif [ -f "$GPU_LOGS/BENCH_RUN" ]; then BENCH_RUN="$(cat "$GPU_LOGS/BENCH_RUN")"; BENCH_RUN_FROM=file; fi

utc() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
usd() { awk -v s="$1" -v p="$PRICE_USD_PER_HOUR" 'BEGIN { printf "%.2f", s / 3600 * p }'; }
hms() { printf '%dh %02dmin %02ds' $(( $1 / 3600 )) $(( $1 % 3600 / 60 )) $(( $1 % 60 )); }
note() { printf 'note %s\n' "$1"; }
warn() { printf 'WARN %s\n' "$1"; }
# record <phase> <target> <start-epoch> <end-epoch> <status>: one line of times.tsv.
record() {
  local secs=$(( $4 - $3 ))
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$(utc "$3")" "$(utc "$4")" "$secs" "$5" "$(usd "$secs")" >> "$TIMES"
}
env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | tr -d "'\""; }
# dry_skip <what> <needs>: in a dry run, says the check is skipped; returns 0 to skip it.
dry_skip() { if [ "$DRY" = 1 ]; then note "dry-run: $1 not checked here (needs $2)"; return 0; fi; return 1; }
vllm_running() { [ -n "$($GPU_COMPOSE ps -q --status running vllm 2>/dev/null)" ]; }

if [ -n "${DROPLET_CREATED:-}" ]; then
  if date -u -d "$DROPLET_CREATED" +%s >/dev/null 2>&1; then
    printf '%s\n' "$DROPLET_CREATED" > "$GPU_LOGS/DROPLET_CREATED"
  else
    usage "DROPLET_CREATED must be a UTC time such as 2026-10-16T08:00:00Z (got $DROPLET_CREATED)"
  fi
fi

droplet_age() {
  if [ -f "$GPU_LOGS/DROPLET_CREATED" ]; then
    local created age booted boot=""
    created="$(date -u -d "$(cat "$GPU_LOGS/DROPLET_CREATED")" +%s)"
    age=$(( $(date +%s) - created ))
    if [ "$age" -lt 0 ]; then warn "DROPLET_CREATED $(cat "$GPU_LOGS/DROPLET_CREATED") is in the future by $(hms $(( -age ))): check the time (UTC) and set it again"; return 0; fi
    # The file travels with the disk into the snapshot: a droplet boots within minutes of its
    # creation, a droplet created from the snapshot boots hours or days after the stored time.
    booted="$(uptime -s 2>/dev/null || true)"
    [ -z "$booted" ] || boot="$(date -d "$booted" +%s 2>/dev/null || true)"
    if [ -n "$boot" ] && [ "$created" -lt $(( boot - 1800 )) ]; then
      warn "this host booted at $(utc "$boot"), more than 30 min after the stored creation $(cat "$GPU_LOGS/DROPLET_CREATED"): a droplet created from the snapshot (the file came with its disk)? Set DROPLET_CREATED=<this droplet's creation, UTC> on the next run; the age below is the destroyed droplet's"
    fi
    printf 'droplet created %s: age %s, estimated bill so far %s USD at %s USD/h (the bill stops at the destruction, not at the shutdown)\n' \
      "$(cat "$GPU_LOGS/DROPLET_CREATED")" "$(hms "$age")" "$(usd "$age")" "$PRICE_USD_PER_HOUR"
  else
    note "DROPLET_CREATED=<UTC time of the creation> on any run shows the droplet's age and the bill it implies"
  fi
}

# ---- preconditions -----------------------------------------------------------------------
check_env() {
  if [ ! -f "$ENV_FILE" ]; then
    fail "$ENV_FILE is missing: cp .env.example .env, then VLLM_API_KEY (sed -i \"s/^VLLM_API_KEY=.*/VLLM_API_KEY=\$(openssl rand -hex 24)/\" .env) and, for the live stack, PUBLIC_HOST and ADMIN_PASSWORD_HASH (runbook step 2)"
    return 1
  fi
  local rc=0 k v
  for k in VLLM_IMAGE DGL_IMAGE ROCM_BASE_IMAGE; do
    v="$(env_value "$k")"
    case "$v" in
      *@sha256:*) ;;
      *) fail "$ENV_FILE: $k must be tag@sha256:digest as in .env.example (compose.gpu.yml refuses to start without it); got '${v:-<empty>}'"; rc=1 ;;
    esac
  done
  v="$(env_value AMD_PYPI_INDEX_URL)"
  case "$v" in https://pypi.amd.com/*) ;; *) fail "$ENV_FILE: AMD_PYPI_INDEX_URL must be the AMD index of .env.example (trainer-gbm and bench builds); got '${v:-<empty>}'"; rc=1 ;; esac
  v="$(env_value GEMMA_MODEL)"
  [ -n "$v" ] || { fail "$ENV_FILE: GEMMA_MODEL is empty (google/gemma-4-31B-it, or the plan B google/gemma-4-26B-A4B-it with its revision)"; rc=1; }
  v="$(env_value GEMMA_REVISION)"
  printf '%s' "$v" | grep -Eq '^[0-9a-f]{40}$' || { fail "$ENV_FILE: GEMMA_REVISION must be the 40-character Hugging Face commit of .env.example; got '${v:-<empty>}'"; rc=1; }
  v="$(env_value VLLM_API_KEY)"
  case "$v" in ""|change-me) fail "$ENV_FILE: VLLM_API_KEY is empty or change-me: sed -i \"s/^VLLM_API_KEY=.*/VLLM_API_KEY=\$(openssl rand -hex 24)/\" $ENV_FILE"; rc=1 ;; esac
  v="$(env_value VLLM_BASE_URL)"
  [ -n "$v" ] || { fail "$ENV_FILE: VLLM_BASE_URL is empty (http://vllm:8001/v1 in .env.example)"; rc=1; }
  if grep -Ev '^(#|$|ADMIN_PASSWORD_HASH=)' "$ENV_FILE" | grep -Eq '^[A-Za-z_]+=.*[[:space:]"]'; then
    warn "$ENV_FILE has a value with a space or a quote: the Makefile sources it with sh (runbook step 2); only ADMIN_PASSWORD_HASH goes between single quotes"
  fi
  [ "$rc" = 0 ] && ok "$ENV_FILE: the variables compose.gpu.yml requires are set (the pinned digests are checked by spike-preflight)"
  return "$rc"
}

# A GitHub token on the droplet disk ends up in the snapshot (runbook, "Before the session";
# release guide): warn, print no value.
tokens_on_disk_check() {
  local remote f
  remote="$(git remote get-url origin 2>/dev/null || true)"
  case "$remote" in https://*@*) warn "the git remote URL of origin carries credentials: use ssh -A with the laptop's key instead (a token on the droplet ends up in the snapshot)" ;; esac
  for f in "$HOME/.git-credentials" "$HOME/.netrc" "$HOME/.config/gh/hosts.yml"; do
    [ -f "$f" ] && warn "$f exists: a token on the droplet ends up in the snapshot; remove it before the snapshot (the down phase refuses to go on while it is there)"
  done
  if [ -f "$ENV_FILE" ] && grep -Eq 'ghp_|github_pat_|gho_' "$ENV_FILE"; then warn "$ENV_FILE holds what looks like a GitHub token: never (runbook, Before the session)"; fi
  return 0
}

check_docker() {
  if dry_skip "the Docker daemon and compose v2" "Docker"; then return 0; fi
  command -v docker >/dev/null 2>&1 || { fail "docker is missing on this host"; return 1; }
  docker info >/dev/null 2>&1 || { fail "the Docker daemon does not answer (docker info)"; return 1; }
  docker compose version >/dev/null 2>&1 || { fail "docker compose v2 is missing: apt-get update -q && apt-get install -y docker-compose-plugin if dpkg -l docker-ce lists Docker's packages, else apt-get update -q && apt-get install -y --no-install-recommends docker-compose-v2 (runbook step 3)"; return 1; }
  ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null), compose $(docker compose version --short 2>/dev/null)"
}

check_gpu_device() {
  if dry_skip "/dev/kfd" "the GPU"; then return 0; fi
  [ -e /dev/kfd ] || { fail "/dev/kfd missing: no AMD GPU driver on this host (wrong image or size: the runbook says destroy the droplet and start again, by hand)"; return 1; }
  ok "/dev/kfd present"
}

check_common() {
  local rc=0 tool
  if [ "$(id -u)" = 0 ]; then ok "running as root"
  elif [ "$DRY" = 1 ]; then note "not root here; on the droplet run as root (bench-vllm writes /proc/sys/vm/drop_caches, the containers write artifacts/ as root)"
  else fail "run as root on the droplet (bench-vllm writes /proc/sys/vm/drop_caches; artifacts/ is root's)"; rc=1; fi
  if [ -n "${TMUX:-}" ]; then ok "inside tmux"
  elif [ "${ALLOW_NO_TMUX:-0}" = 1 ]; then warn "not inside tmux (ALLOW_NO_TMUX=1): an SSH drop kills the run"
  elif [ "$DRY" = 1 ]; then note "not inside tmux: on the droplet, tmux new -s spikes first (the session of runbook step 2; reattach with tmux attach -t spikes), so an SSH drop does not kill the run"
  else fail "not inside tmux: tmux new -s spikes (runbook step 2), then run again (reattach: tmux attach -t spikes); ALLOW_NO_TMUX=1 overrides"; rc=1; fi
  for tool in make python3 curl tar; do
    command -v "$tool" >/dev/null 2>&1 || { fail "$tool is missing (apt-get update -q && apt-get install -y --no-install-recommends $tool)"; rc=1; }
  done
  [ -f Makefile ] && [ -f infra/spikes/README.md ] || { fail "not the repository root: $ROOT"; rc=1; }
  check_env || rc=1
  tokens_on_disk_check
  check_docker || rc=1
  check_gpu_device || rc=1
  return "$rc"
}

# photo_check: the SPK-G5 photo (runbook: JPEG or PNG under 2 MB, no person, no personal data).
# Returns 1 with the problem in PHOTO_PROBLEM; the caller decides whether it is a warning or a stop.
PHOTO_PROBLEM=""
photo_check() {
  PHOTO_PROBLEM=""
  if [ ! -f "$IMAGE" ]; then
    PHOTO_PROBLEM="SPK-G5 photo missing: $IMAGE. From the laptop: scp garment.jpg root@<droplet-ip>:ReturnRing/$IMAGE_DEFAULT (JPEG or PNG under 2 MB, no person, no personal data on labels); IMAGE=<path> names another file"
    return 1
  fi
  case "$IMAGE" in
    artifacts/*) ;;
    *) PHOTO_PROBLEM="SPK-G5 photo $IMAGE must be under artifacts/ (the only folder spike-client mounts): copy it to $IMAGE_DEFAULT"; return 1 ;;
  esac
  local size; size="$(stat -c %s "$IMAGE")"
  [ "$size" -le 2000000 ] || { PHOTO_PROBLEM="SPK-G5 photo $IMAGE is $size bytes: the runbook asks for under 2 MB"; return 1; }
  python3 - "$IMAGE" <<'EOF' || { PHOTO_PROBLEM="SPK-G5 photo $IMAGE is neither JPEG nor PNG (by its first bytes)"; return 1; }
import sys
head = open(sys.argv[1], "rb").read(8)
sys.exit(0 if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n") else 1)
EOF
  ok "SPK-G5 photo: $IMAGE ($size bytes)"
}

check_spikes() {
  mkdir -p artifacts/spikes/logs
  if ! photo_check; then
    if [ "$DRY" = 1 ]; then note "$PHOTO_PROBLEM"; note "dry-run: on the droplet the run would stop at spike-g5 (step 11) without the photo"
    else warn "$PHOTO_PROBLEM"; warn "copy the photo before spike-g5 (step 11), or --skip spike-g5 (plan B of spec 13: inspection out of scope)"; fi
  fi
  return 0
}

check_bench() {
  local rc=0 f
  for f in data/raw/asos_customers.parquet data/raw/asos_events.parquet data/raw/asos_variants.parquet; do
    [ -f "$f" ] || { fail "$f missing (B1 and B2 read the ASOS tables): on the laptop, make asos, then scp data/raw/asos_*.parquet root@<droplet-ip>:ReturnRing/data/raw/ (runbook, The GPU session, step 2)"; rc=1; }
  done
  for f in data/store data/features data/ood/store data/ood/features; do
    if [ -d "$f" ] && [ -n "$(ls -A "$f" 2>/dev/null)" ]; then :; else
      fail "$f missing or empty (embeddings-gpu reads the store and its features): on the laptop, rsync -a data/store data/features data/ood root@<droplet-ip>:ReturnRing/data/ (runbook, The GPU session, step 2)"; rc=1; fi
  done
  [ -d data/truth ] && warn "data/truth exists on this host: rule 12 says it never leaves the laptop; never copy it, and remove it by hand before the snapshot (rm -rf data/truth; the down phase refuses to go on while it exists; this script deletes nothing)"
  if [ "$rc" = 0 ]; then ok "data/raw/asos_*.parquet, data/store, data/features, data/ood present"; fi
  local head old
  head="$(git rev-parse HEAD 2>/dev/null || true)"
  if [ "$BENCH_RUN_FROM" = file ]; then note "BENCH_RUN=$BENCH_RUN reused from $GPU_LOGS/BENCH_RUN (the parts already in artifacts/bench/$BENCH_RUN stay; BENCH_RUN=<new id> starts another run; in a shell of your own, before a make by hand: export BENCH_RUN=\$(cat release-logs/$GPU_REL/BENCH_RUN))"
  elif [ "$BENCH_RUN_FROM" = env ]; then note "BENCH_RUN=$BENCH_RUN from the environment, stored in $GPU_LOGS/BENCH_RUN"
  else BENCH_RUN="$(date -u +%Y%m%dT%H%M%SZ)-gpu"; BENCH_RUN_FROM=new; note "BENCH_RUN=$BENCH_RUN created, stored in $GPU_LOGS/BENCH_RUN"; fi
  printf '%s\n' "$BENCH_RUN" > "$GPU_LOGS/BENCH_RUN"
  # bench-merge stamps the run file with the commit checked out when it runs (bench/__main__.py,
  # env.git_info()), not the one the parts ran on: the commit of the first run of this id is kept.
  if [ -f "$GPU_LOGS/BENCH_RUN.commit" ] && [ "$(cut -d' ' -f1 "$GPU_LOGS/BENCH_RUN.commit")" = "$BENCH_RUN" ]; then
    old="$(cut -d' ' -f2 "$GPU_LOGS/BENCH_RUN.commit")"
    if [ -n "$head" ] && [ "$old" != "$head" ]; then
      warn "BENCH_RUN=$BENCH_RUN was started on commit ${old:0:12}; HEAD is now ${head:0:12}: the merged run file would name a commit the parts did not run on (rule 13). Check out ${old:0:12} for the merge (git checkout $old), or BENCH_RUN=<new id> and measure again; no git pull between a part and the merge"
    fi
  else
    printf '%s %s\n' "$BENCH_RUN" "$head" > "$GPU_LOGS/BENCH_RUN.commit"
  fi
  export BENCH_RUN
  note "BENCH_ARGS='${BENCH_ARGS}' (e.g. \"--b2-samples 1\" skips the 25% and 50% sizes of B2 if the session is short)"
  if command -v amd-smi >/dev/null 2>&1; then ok "amd-smi on the host: bench-b4 samples it there"; else note "no amd-smi on the host (PATH includes /opt/rocm/bin): bench-b4 samples inside the container, as the runbook allows"; fi
  return "$rc"
}

serving_check() {
  [ -f serving/manifest.json ] || { fail "serving/manifest.json missing: the snapshot is built once on the laptop (make demo-data or make pipeline) and copied: rsync -a --delete serving/ root@<droplet-ip>:ReturnRing/serving/ (never rebuilt on one side only: the cache is keyed by the case)"; return 1; }
  python3 - <<'EOF'
import json
m = json.load(open("serving/manifest.json"))
print("OK   serving/manifest.json:", {k: m.get(k) for k in ("as_of", "seed", "contract_version", "format")})
EOF
}

check_export() {
  local rc=0
  serving_check || rc=1
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || { fail "host python3 must be 3.8 or later (make demo-export runs with it): $(python3 --version 2>&1)"; rc=1; }
  [ -n "$DEMO_GPU" ] || { fail "DEMO_GPU is empty: make demo-export has no default; it must name the MI300X"; rc=1; }
  note "DEMO_GPU='$DEMO_GPU'; GEMMA_MODEL=$(env_value GEMMA_MODEL): every draft must be this model's, from a batch run with DEVICE=gpu"
  if [ -f bench/results/latest-gpu.json ]; then
    ok "bench/results/latest-gpu.json names $(python3 -c 'import json; print(json.load(open("bench/results/latest-gpu.json")).get("run_id"))'): the package carries the MI300X run"
  else
    warn "no bench/results/latest-gpu.json: run the bench phase (bench-merge) before demo-export, or the package carries no MI300X benchmark (a CPU run is packed and labelled, never served as latest)"
  fi
  return "$rc"
}

check_live() {
  local rc=0 v
  serving_check || rc=1
  v="$(env_value PUBLIC_HOST)"
  case "$v" in
    "") fail "$ENV_FILE: PUBLIC_HOST is empty: the sslip.io name of the droplet's IP, e.g. 203-0-113-10.sslip.io for 203.0.113.10 (compose.public.yml refuses to start without it)"; rc=1 ;;
    *://*|*/*) fail "$ENV_FILE: PUBLIC_HOST must be a host name, not a URL: got '$v'"; rc=1 ;;
    *) ok "PUBLIC_HOST=$v" ;;
  esac
  v="$(env_value ADMIN_PASSWORD_HASH)"
  if [ -z "$v" ]; then
    fail "$ENV_FILE: ADMIN_PASSWORD_HASH is empty: make admin-password prints the bcrypt hash (cost 10); write it with the python line of the runbook (amd-runbook 3.3), between single quotes"; rc=1
  elif ! printf '%s' "$v" | grep -Eq '^\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$'; then
    fail "$ENV_FILE: ADMIN_PASSWORD_HASH is not a complete bcrypt hash (\$2a\$10\$ and 53 characters, 60 in all; got ${#v}): a \$ mangled by the shell or a hash cut short gives 401 even with the right password. Write it again with the python line of the runbook (amd-runbook 3.3), never with sed or echo"; rc=1
  elif ! grep -q "^ADMIN_PASSWORD_HASH='" "$ENV_FILE"; then
    fail "$ENV_FILE: ADMIN_PASSWORD_HASH is not between single quotes: the Makefile sources $ENV_FILE with sh and compose interpolates \$, so the hash the proxy gets differs (401 even with the right password)"; rc=1
  else ok "ADMIN_PASSWORD_HASH set: a complete bcrypt hash between single quotes"; fi
  if [ -f "$GPU_LOGS/bench/gnn-image.skipped" ]; then
    warn "gnn-image was skipped: make gpu-up (up -d --build) builds every service of the gpu profile with a build section, trainer-graphsage included, and stops if that build fails. Then start the live stack by name, without the trainers (vLLM is already healthy after spike-vllm and spike-wait): $GPU_COMPOSE -f infra/compose.public.yml up -d --build postgres reset api ui proxy telemetry telemetry-sampler, then bash scripts/release/06-gpu-session.sh live --skip gpu-up (derived from the compose files, not in the runbook: record it)"
  fi
  v="$(env_value POSTGRES_PASSWORD)"
  case "$v" in ""|change-me) warn "$ENV_FILE: POSTGRES_PASSWORD is still '$v' (PostgreSQL is not published, but set it with DATABASE_URL before a public stack stays up)" ;; esac
  [ -n "$(env_value DATABASE_URL)" ] || { fail "$ENV_FILE: DATABASE_URL is empty"; rc=1; }
  note "the cloud firewall must allow inbound 80 and 443 (Docker-published ports bypass ufw); Caddy takes the certificate for PUBLIC_HOST itself"
  return "$rc"
}

check_phase() {
  local rc=0
  check_common || rc=1
  case "$1" in
    spikes) check_spikes || rc=1 ;;
    bench) check_bench || rc=1 ;;
    export) check_export || rc=1 ;;
    live) check_live || rc=1 ;;
    down) check_down || rc=1 ;;
  esac
  return "$rc"
}

# What must not be on the disk when the snapshot is taken (runbook step 14; amd-runbook 13.3): a
# Hugging Face token in .env, the ground truth (rule 12), a git credential file. In a dry run, on
# the laptop, each is a note.
check_down() {
  local rc=0 v f
  v="$(env_value HF_TOKEN)"
  if [ -n "$v" ]; then
    if [ "$DRY" = 1 ]; then note "dry-run: $ENV_FILE has a value in HF_TOKEN; on the droplet the down phase refuses to go on until it is emptied"
    else fail "$ENV_FILE: HF_TOKEN has a value: empty it before the snapshot (sed -i 's/^HF_TOKEN=.*/HF_TOKEN=/' $ENV_FILE); the weights are already in hf-cache and vLLM runs with HF_HUB_OFFLINE=1"; rc=1; fi
  fi
  if [ -d data/truth ]; then
    if [ "$DRY" = 1 ]; then note "dry-run: data/truth exists here (the laptop keeps it, rule 12); on the droplet the down phase refuses to go on until it is removed"
    else fail "data/truth exists on this droplet (rule 12: it never leaves the laptop): rm -rf data/truth before the snapshot; this script deletes nothing"; rc=1; fi
  fi
  for f in "$HOME/.git-credentials" "$HOME/.netrc" "$HOME/.config/gh/hosts.yml"; do
    if [ -f "$f" ]; then
      if [ "$DRY" = 1 ]; then note "dry-run: $f exists here; on the droplet the down phase refuses to go on until it is removed"
      else fail "$f exists: a token on the droplet ends up in the snapshot; remove it (the repository is public, the push of the results uses ssh -A: no credential belongs on the droplet)"; rc=1; fi
    fi
  done
  [ "$rc" = 0 ] && ok "nothing the snapshot must not hold: HF_TOKEN empty, no data/truth, no git credential file"
  return "$rc"
}

need_vllm_up() {  # <target> <how to start it>
  if dry_skip "vLLM running (needed by $1)" "Docker"; then return 0; fi
  if vllm_running; then ok "vLLM is running"; return 0; fi
  fail "vLLM is not running, and $1 needs it: $2 (this script starts it only through those targets)"
  return 1
}

need_vllm_down() {  # <target>
  local why
  case "$1" in
    gnn-test-gpu) why="this script runs $1 with vLLM down, as the runbook orders the steps (the Makefile itself does not refuse it)" ;;
    *) why="the Makefile refuses $1 while vLLM is up (B1, B2, embeddings-gpu and the GNN jobs run alone on the CPU and the GPU)" ;;
  esac
  if dry_skip "vLLM stopped ($why)" "Docker"; then return 0; fi
  if vllm_running; then
    fail "vLLM is running: $why. Stop it: bash scripts/release/06-gpu-session.sh bench --only spike-down (make spike-down keeps the volumes); this script does not stop it by itself"
    return 1
  fi
  ok "vLLM is down"
}

rocm_check() {
  if dry_skip "a container already running (the Quick Start image: rocm in the AMD guides, but the name is theirs)" "Docker"; then return 0; fi
  local line
  # Before spike-prep nothing of this project runs; its containers are named returnring-<service>-N
  # (infra/docker-compose.yml, name: returnring), so any other running container is foreign.
  line="$(docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}' 2>/dev/null | awk -F'\t' '$1 !~ /^returnring-/')"
  if [ -n "$line" ]; then
    fail "a container that is not this project's is running on this droplet (the Quick Start image? the AMD guides call it rocm, but the name is theirs): $line. Write its name, image and ports in docs/spike-results.md (Quick Start container found), then docker stop <name> && docker update --restart=no <name> (it may hold GPU memory, which vLLM sizes its KV cache from, and publish Jupyter or vLLM on the public IP); this script does not stop it by itself"
    return 1
  fi
  ok "no container of another project is running (the Quick Start container is stopped or absent)"
}

llm_cache_check() {
  local n
  n="$(find serving/llm_cache -type f 2>/dev/null | wc -l)"
  if [ "$n" -gt 0 ]; then ok "serving/llm_cache: $n files"; return 0; fi
  if [ "$DRY" = 1 ]; then note "dry-run: serving/llm_cache is empty here; on the droplet the run would stop before demo-export unless batch-generate wrote the drafts"; return 0; fi
  fail "serving/llm_cache is empty: batch-generate wrote no draft, and demo-export refuses an empty cache (REHEARSAL=1 and ALLOW_EMPTY_CACHE=1 are for a rehearsal, never for the MI300X package)"
  return 1
}

check_target() {  # <phase> <target>
  local rc=0
  case "$2" in
    spike-prep) rocm_check || rc=1 ;;
    spike-g7|spike-g1|spike-g1-bench|spike-g6|spike-g4) need_vllm_up "$2" "bash scripts/release/06-gpu-session.sh $1 --only spike-vllm, then --only spike-wait" || rc=1 ;;
    spike-g5)
      need_vllm_up "$2" "bash scripts/release/06-gpu-session.sh $1 --only spike-vllm, then --only spike-wait" || rc=1
      if ! photo_check; then
        if [ "$DRY" = 1 ]; then note "$PHOTO_PROBLEM"; note "dry-run: on the droplet the run would stop here (--skip spike-g5 applies the plan B: inspection out of scope)"
        else fail "$PHOTO_PROBLEM"; fail "spike-g5 cannot run without the photo: copy it and run again, or --skip spike-g5 (plan B of spec 13: inspection out of scope)"; rc=1; fi
      fi ;;
    batch-generate) need_vllm_up "$2" "bash scripts/release/06-gpu-session.sh export --only spike-vllm, then --only spike-wait" || rc=1 ;;
    bench-b4) need_vllm_up "$2" "bash scripts/release/06-gpu-session.sh bench --only bench-vllm (it also times the cold start B3 reports)" || rc=1 ;;
    bench-b3)
      need_vllm_up "$2" "bash scripts/release/06-gpu-session.sh bench --only bench-vllm (it also times the cold start B3 reports)" || rc=1
      if [ -f "$PHASE_DIR/bench-b4.skipped" ]; then
        fail "bench-b4 was skipped: B3 reads its cases from the store with scores that B4 generates in bench-work/$BENCH_RUN (bench/__main__.py falls back to data/, which the bench container mounts only as data/raw: no store, no scores). Run bench-b4 first with the same BENCH_RUN (--only bench-b4), or --skip bench-b3 too and record both as not measured (rule 13)"; rc=1
      fi ;;
    gnn-test-gpu|bench-b1|bench-b2|embeddings-gpu) need_vllm_down "$2" || rc=1 ;;
    bench-vllm) [ "$(id -u)" = 0 ] || note "not root: the page cache is not dropped and vllm-start-conditions.txt says so" ;;
    spike-vllm)
      if [ "$DRY" = 0 ] && vllm_running; then
        note "vLLM is already running: up -d --no-build changes nothing, but spike-vllm rewrites artifacts/spikes/logs/vllm-start.txt (the earlier start and healthy times stay in release-logs/$GPU_REL/spikes/spike-g7.log and, for the benchmark, in artifacts/bench/\$BENCH_RUN/)"
      fi ;;
    demo-export) llm_cache_check || rc=1 ;;
  esac
  return "$rc"
}

# ---- the targets -------------------------------------------------------------------------
gpu_half_context() {
  # What ran next to the GPU halves of G2 and G3 (recorded in their JSON, rules 11 and 13): on a
  # rerun after step 9 vLLM holds 55% of the GPU memory, and the record must say so.
  if [ "$DRY" = 0 ] && vllm_running; then printf 'vLLM serving at --gpu-memory-utilization=0.55 on the same GPU (rerun after step 9; the weights download had finished)'
  elif [ -f artifacts/spikes/logs/weights-status.txt ]; then printf 'alone on the GPU: the Gemma weights download had already finished (rerun)'
  elif [ -f artifacts/spikes/logs/download-weights.log ]; then printf 'Gemma weights downloading in the background'
  else printf 'no Gemma weights download running (spike-weights not started)'; fi
}

target_argv() {  # sets ARGV: the make arguments of a target
  case "$1" in
    spike-g2|spike-g3) ARGV=("$1" "CONTEXT=$(gpu_half_context)") ;;
    spike-g5) ARGV=(spike-g5 "IMAGE=$IMAGE") ;;
    bench-*|gnn-*|embeddings-gpu)
      ARGV=("$1" DEVICE=gpu "BENCH_RUN=$BENCH_RUN")
      [ -z "$BENCH_ARGS" ] || ARGV+=("BENCH_ARGS=$BENCH_ARGS") ;;
    demo-export) ARGV=(demo-export "DEMO_GPU=$DEMO_GPU") ;;
    *) ARGV=("$1") ;;
  esac
}

https_check() {
  local host t=0 code
  host="$(env_value PUBLIC_HOST)"
  printf 'waiting for https://%s/healthz (the certificate can take up to two minutes) ' "$host"
  until curl -sf "https://$host/healthz" >/dev/null 2>&1; do
    if [ "$t" -ge 240 ]; then printf '\n'; echo "FAIL https://$host/healthz did not answer within 240 s: $GPU_COMPOSE -f infra/compose.public.yml logs proxy api; is 443 open in the cloud firewall?"; return 1; fi
    sleep 5; t=$(( t + 5 )); printf '.'
  done
  printf '\nhealthz answers after %ss\n' "$t"
  curl -s "https://$host/readyz" > "$PHASE_DIR/readyz-https.json"
  python3 - "$PHASE_DIR/readyz-https.json" <<'EOF' || return 1
import json, sys
r = json.load(open(sys.argv[1]))
keys = ["status", "mode", "llm_mode", "device", "label", "contract_version", "public_read_only"]
print("readyz:", {k: r.get(k) for k in keys})
print("clock:", r.get("clock"))
for c in r.get("checks", []):
    print("  check", c.get("component"), c.get("status"), c.get("detail") or "")
bad = []
if r.get("status") == "not_ready": bad.append("status not_ready")
if r.get("llm_mode") != "vllm": bad.append(f"llm_mode {r.get('llm_mode')!r}, expected vllm")
if r.get("device") != "gpu": bad.append(f"device {r.get('device')!r}, expected gpu")
if bad:
    print("FAIL readyz over HTTPS: " + "; ".join(bad))
    sys.exit(1)
print("OK   readyz over HTTPS: live stack on the GPU with vLLM")
EOF
  code="$(curl -s -o /dev/null -w '%{http_code}' "https://$host/api/telemetry")"
  if [ "$code" = 401 ]; then echo "OK   /api/telemetry asks for the admin password (401)"; else echo "FAIL /api/telemetry answered $code, expected 401 (basic auth of compose.public.yml)"; return 1; fi
  echo "live stack: https://$host (user admin for /api/telemetry and the writes). The pitch walk needs the API on loopback, not the proxy (every write gets 401 there: scripts/demo_walk.py sends no credentials, PUBLIC_READ_ONLY is false on the droplet): from the laptop, ssh -L 8000:127.0.0.1:8000 root@<droplet-ip>, then make gpu-reset on the droplet and make demo-walk API_URL=http://127.0.0.1:8000 on the laptop (requirements/dev.txt installed). The browser passes: it asks for the admin password at the first write"
}

https_check_dry() {
  local host; host="$(env_value PUBLIC_HOST)"
  printf 'dry-run: would poll https://%s/healthz for up to 240 s, save https://%s/readyz to %s/readyz-https.json (status not not_ready, llm_mode vllm, device gpu), and expect 401 from https://%s/api/telemetry\n' "$host" "$host" "$PHASE_DIR" "$host"
}

disk_usage() { df -h /; docker system df; echo "the snapshot is about the disk in use above"; }

exec_target() {  # <log-name> <target>
  local log="$1" t="$2"
  case "$t" in
    https-check) run "$log" https_check ;;
    disk-usage) run "$log" disk_usage ;;
    spike-g6)
      if run "$log" make "${MAKE_COMMON[@]}" spike-g6 G6_MODE=auto; then return 0; fi
      note "spike-g6 in auto mode failed: the runbook's next step is G6_MODE=named (the function forced through structured outputs, no gemma4 tool parser)"
      run "$log-named" make "${MAKE_COMMON[@]}" spike-g6 G6_MODE=named || return 1
      note "record in docs/spike-results.md: mode that passed: named (auto failed, log release-logs/$log.log)" ;;
    *) target_argv "$t"; run "$log" make "${MAKE_COMMON[@]}" "${ARGV[@]}" ;;
  esac
}

dry_target() {  # <log-name> <target>
  local log="$1" t="$2"
  case "$t" in
    https-check) run "$log" https_check_dry ;;
    disk-usage) run "$log" printf 'dry-run: would run %s\n' "df -h /" "docker system df" ;;
    spike-g6)
      run "$log" make -n "${MAKE_COMMON[@]}" spike-g6 G6_MODE=auto || return 1
      note "and, only if auto fails:"
      run "$log-named" make -n "${MAKE_COMMON[@]}" spike-g6 G6_MODE=named ;;
    *) target_argv "$t"; run "$log" make -n "${MAKE_COMMON[@]}" "${ARGV[@]}" ;;
  esac
}

run_target() {  # <phase> <target>
  local phase="$1" t="$2" log="$GPU_REL/$1/$2" marker="$PHASE_DIR/$2.done" skipped="$PHASE_DIR/$2.skipped" s e rc=0 now
  if in_list "$t" "$SKIP"; then
    now="$(date +%s)"
    step "$t: skipped by the operator (--skip)"
    if [ "$DRY" = 0 ]; then utc "$now" > "$skipped"; fi
    record "$phase" "$t" "$now" "$now" skipped
    return 0
  fi
  if [ "$FORCE" = 0 ] && [ -z "$ONLY" ]; then
    if in_words "$t" "$ALWAYS_RUN"; then
      [ -f "$marker" ] && note "$t: done at $(cat "$marker"), run again anyway (its effect does not outlive the day: ALWAYS_RUN)"
      [ -f "$skipped" ] && note "$t: skipped by the operator at $(cat "$skipped") on an earlier run, run now (an always-run target: --skip $t on this command line spares it)"
    else
      if [ -f "$marker" ]; then ok "$t: done at $(cat "$marker"), skipped (--force or --only $t runs it again)"; return 0; fi
      if [ -f "$skipped" ]; then ok "$t: skipped by the operator at $(cat "$skipped") (--force or --only $t runs it)"; return 0; fi
    fi
  fi
  step "$t: preconditions"
  check_target "$phase" "$t" || { FAILED_WHERE="a precondition of $t"; FAILED_TARGET="$t"; return 1; }
  CURRENT_TARGET="$t"
  s="$(date +%s)"
  if [ "$DRY" = 1 ]; then dry_target "$log" "$t" || rc=$?; else exec_target "$log" "$t" || rc=$?; fi
  e="$(date +%s)"
  RUN_SECS=$(( RUN_SECS + e - s ))
  if [ "$rc" = 0 ]; then
    if [ "$DRY" = 1 ]; then record "$phase" "$t" "$s" "$e" dry-run; ok "$t: printed, not run (dry-run)"
    else record "$phase" "$t" "$s" "$e" ok; utc "$e" > "$marker"; ok "$t: exit 0 in $(hms $(( e - s ))), marker $marker"; fi
  else
    record "$phase" "$t" "$s" "$e" fail
    fail "$t: exit $rc after $(hms $(( e - s ))) (log: release-logs/$log.log)"
    FAILED_TARGET="$t"; FAILED_WHERE="the target $t"
    return 1
  fi
  CURRENT_TARGET=""
}

phase_summary() {  # <phase>
  local phase="$1" this all
  this="$(awk -F'\t' -v p="$phase" -v s="$(utc "$RUN_START")" '$1 == p && $2 != "run" && $6 != "skipped" && $3 >= s { t += $5 } END { print t + 0 }' "$TIMES")"
  all="$(awk -F'\t' -v p="$phase" '$1 == p && $2 != "run" && $6 != "skipped" { t += $5 } END { print t + 0 }' "$TIMES")"
  step "phase $phase: droplet time"
  printf 'targets of this run: %s (estimate at %s USD/h: %s USD); every run of this phase: %s (%s USD)\n' \
    "$(hms "$this")" "$PRICE_USD_PER_HOUR" "$(usd "$this")" "$(hms "$all")" "$(usd "$all")"
  printf 'the sum counts the targets'"'"' wall time only: the bill runs from the creation to the destruction of the droplet, powered off or not, so it is a lower bound (times: %s)\n' "$TIMES"
  droplet_age
}

down_reminder() {
  local bench_run="${BENCH_RUN:-<BENCH_RUN>}"
  step "next, by hand (this script never snapshots, destroys, prunes or removes a volume)"
  cat <<EOF
1. From the laptop, copy back (runbook step 14 and "Files to copy back"):
     scp -r root@<droplet-ip>:ReturnRing/artifacts/spikes ./artifacts/
     scp root@<droplet-ip>:"ReturnRing/bench/results/*.json" ./bench/results/
     scp -r root@<droplet-ip>:ReturnRing/artifacts/bench/$bench_run ./artifacts/bench/
     scp root@<droplet-ip>:"ReturnRing/artifacts/eval/gnn/*.json" ./artifacts/eval/gnn/
     scp root@<droplet-ip>:ReturnRing/data/features/customer_embeddings.parquet ./data/features/
     scp root@<droplet-ip>:ReturnRing/data/ood/features/customer_embeddings.parquet ./data/ood/features/
     scp root@<droplet-ip>:ReturnRing/demo-cache.tar.gz .
     scp root@<droplet-ip>:"ReturnRing/release-logs-*.tgz" .
2. On the droplet: shutdown -h now
3. In the control panel: take a snapshot, wait until it is listed, destroy the droplet, check it is
   gone from the Droplets list. Snapshots cost 0.06 USD per GB per month (expect roughly 170-220 GB,
   about 10-13 USD per month; the disk in use above is the size); the droplet keeps billing at
   1.99 USD/hour until it is destroyed, also while the snapshot runs. Whether snapshots work for AMD
   Developer Cloud GPU droplets, and how long one takes, is unconfirmed: if the snapshot would take
   longer than the download measured in step 8 (artifacts/spikes/logs/weights-status.txt), destroy
   without it and note the decision.
4. Write the creation and destruction times (UTC) in docs/spike-results.md (Session table): the GPU
   hours used, spec section 11.
5. On a later day, on a droplet created from the snapshot: release-logs/gpu/ comes with its disk,
   Friday's markers included. The live and down phases run every target again anyway (gpu-up,
   gpu-reset, https-check; spike-down, disk-usage), so a plain `live` starts the stack and a
   plain `down` stops it before the shutdown; DROPLET_CREATED=<the new droplet's creation, UTC>
   replaces the stored one (the script warns when the stored time precedes the boot).
EOF
}

run_phase() {  # <phase>
  local phase="$1" t started=1
  PHASE_DIR="$GPU_LOGS/$phase"; mkdir -p "$PHASE_DIR"
  step "phase $phase: preconditions"
  check_phase "$phase" || { FAILED_WHERE="the preconditions of the phase $phase"; return 1; }
  if [ "$DRY" = 0 ] && command -v docker >/dev/null 2>&1; then
    docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' > "$PHASE_DIR/docker-ps-$STAMP.txt" 2>&1 || true
  fi
  [ -z "$FROM" ] || started=0
  for t in $(phase_targets "$phase"); do
    if [ -n "$ONLY" ] && [ "$t" != "$ONLY" ]; then continue; fi
    if [ "$started" = 0 ]; then
      if [ "$t" = "$FROM" ]; then started=1; else note "$t: before --from $FROM, not run"; continue; fi
    fi
    run_target "$phase" "$t" || return 1
  done
  phase_summary "$phase"
  [ "$phase" != down ] || down_reminder
}

# copy_small <dest> <files...>: the small result files (JSON, text, Markdown) for the package.
copy_small() {
  local dest="$1" f; shift
  for f in "$@"; do
    if [ -f "$f" ] && [ "$(stat -c %s "$f")" -le 2000000 ]; then mkdir -p "$dest"; cp "$f" "$dest/"; fi
  done
}

collect_results() {
  local p
  for p in $RUN_PHASES; do
    case "$p" in
      spikes) copy_small "$GPU_LOGS/spikes/artifacts" artifacts/spikes/*.json artifacts/spikes/logs/*.txt artifacts/spikes/logs/*.log ;;
      bench)
        [ -n "$BENCH_RUN" ] && copy_small "$GPU_LOGS/bench/artifacts" artifacts/bench/"$BENCH_RUN"/*.json artifacts/bench/"$BENCH_RUN"/*.txt artifacts/bench/"$BENCH_RUN"/*.md
        copy_small "$GPU_LOGS/bench/results" bench/results/*.json
        copy_small "$GPU_LOGS/bench/eval-gnn" artifacts/eval/gnn/*.json ;;
      export) copy_small "$GPU_LOGS/export/artifacts" demo-cache/MANIFEST.json serving/manifest.json
        [ -f demo-cache.tar.gz ] && { ls -l demo-cache.tar.gz; sha256sum demo-cache.tar.gz; } > "$GPU_LOGS/export/demo-cache.tar.gz.txt" 2>&1 ;;
    esac
  done
  return 0
}

# scrub_secrets: the values of the .env secrets, replaced in every log before the pack (vLLM prints
# its arguments, --api-key included; make spike-wait prints the last 100 lines of its log on a
# failure; a DATABASE_URL carries the PostgreSQL password). A literal replacement, no regex.
scrub_secrets() {
  local k v f files
  for k in VLLM_API_KEY POSTGRES_PASSWORD HF_TOKEN ADMIN_PASSWORD_HASH; do
    v="$(env_value "$k")"
    case "$v" in ""|change-me) continue ;; esac
    [ "${#v}" -ge 8 ] || continue
    files="$(grep -rlF -- "$v" "$LOGS" 2>/dev/null || true)"   # grep exits 1 with no match: not an error
    [ -n "$files" ] || continue
    while IFS= read -r f; do
      python3 - "$f" "$v" "$k" <<'EOF'
import sys
path, value, key = sys.argv[1:4]
data = open(path, "rb").read()
open(path, "wb").write(data.replace(value.encode(), f"<redacted {key}>".encode()))
EOF
      printf 'scrubbed %s from %s\n' "$k" "$f"
    done <<< "$files"
  done
  return 0
}

plan_b() {  # <target>: the runbook's plan B, infra/spikes/README.md
  case "$1" in
    spike-preflight) cat <<'EOF'
Runbook step 3. An ERROR line names what is wrong:
- a .env value: copy the pinned value from .env.example (never :latest; keep values free of spaces and quotes);
- "docker compose missing: if dpkg -l docker-ce lists Docker's own packages, apt-get install -y docker-compose-plugin; otherwise apt-get install -y --no-install-recommends docker-compose-v2";
- "Less than 250 GB free for Docker, or /dev/kfd missing: wrong image or size. Destroy the droplet and start again" (by hand, in the control panel);
- "Host driver: ... An older driver is a risk for SPK-G1 and SPK-G2: note it and go on."
EOF
      ;;
    spike-prep|bench-build) cat <<'EOF'
Runbook step 4 (bench-build uses the same amd_xgboost wheel and ROCm base as trainer-gbm):
| build-gbm pip error on the AMD index (unreachable, 403, hash mismatch) | Read logs/build-gbm.log. Do not debug on paid time: SPK-G2 plan B (XGBoost on CPU). Alternative, if the 7.2.3 wheel was replaced: switch index, wheel hash and base image together to ROCm 7.1.1 (see infra/docker/requirements-gbm-amd.txt). |
| build-gbm lists librccl.so.1 => not found | ROCM_BASE_IMAGE is not the -complete image: copy it again from .env.example. |
| pull-dgl or pull-vllm with 429 Too Many Requests | docker login with your own Docker Hub account, then re-run. |
If bench-build fails, the bench image does not exist: bench-vllm (its first request), bench-b4, bench-b3
and bench-merge cannot run on the droplet either (they all run in the bench container: Makefile
BENCH_RUN_GPU), not only B1. Do not skip them one by one. Either fix the build (the ROCm 7.1.1 switch
above, or the index back) and rerun, or run only the trainer-graphsage targets: --only gnn-image,
--only gnn-test-gpu, --only bench-b2, --only embeddings-gpu; record B1, B3 and B4 as not measured
(rule 13); merge B2 on the laptop, on the same commit, with the parts copied back:
scp -r root@<droplet-ip>:ReturnRing/artifacts/bench/$BENCH_RUN ./artifacts/bench/ then
python -m bench merge --run-id $BENCH_RUN (the merge needs only the parts). The export phase then
warns that bench/results/latest-gpu.json is missing: the package carries no MI300X benchmark.
EOF
      ;;
    spike-weights|spike-weights-wait) cat <<'EOF'
Runbook step 8: "A 401 or 403 is not expected (both Gemma 4 models are not gated): set HF_TOKEN in .env, then make spike-weights spike-weights-wait" (here: --only spike-weights, then --only spike-weights-wait, then a plain rerun), and empty HF_TOKEN again before the snapshot (sed -i 's/^HF_TOKEN=.*/HF_TOKEN=/' .env): the down phase refuses to go on while it is set. "weights download not finished after 90 min: tail artifacts/spikes/logs/download-weights.log". Plan B of SPK-G7 (spec 13): snapshot of the droplet after the first download.
EOF
      ;;
    spike-gpu-check) cat <<'EOF'
Runbook step 6: "spike-gpu-check fails for one image only: the other image's line still shows whether the host passes the GPU through; the failing image is the problem." Both images failing: the host (driver, /dev/kfd, /dev/dri): artifacts/spikes/host.json.
EOF
      ;;
    spike-g2|spike-g2-load|spike-g2-cpu) cat <<'EOF'
Runbook step 6: "Exit 2 (GPU not used): amd_xgboost falls back to CPU with only a warning. Check make spike-gpu-check." "Plan B (spec 13): XGBoost on CPU for training, GPU only for Gemma and DGL": --skip spike-g2,spike-g2-load,spike-g2-cpu (spike-g4 then also loses its XGBoost job: record it). The CPU half "stops with an error if the GPU file was produced with a different configuration".
EOF
      ;;
    spike-g3|spike-g3-cpu) cat <<'EOF'
Runbook step 7: "Exit 2: GPU not visible in the DGL container: make spike-gpu-check." "Plan B (spec 13): GraphSAGE only as an upgrade; benchmark B2 postponed": --skip spike-g3,spike-g3-cpu (and later, in the bench phase, gnn-image,gnn-test-gpu,bench-b2,embeddings-gpu).
EOF
      ;;
    spike-vllm|spike-wait|bench-vllm|gpu-up) cat <<'EOF'
Runbook step 9 (make spike-logs shows the last 200 lines of vLLM):
| Free memory on device ... is less than desired GPU memory utilization | Something else holds GPU memory (Quick Start container, a trainer still running): stop it, make spike-vllm spike-wait. |
| Chat template not found | The template path in infra/docker-compose.yml must stay absolute. |
| Compile or graph-capture error, or no healthy server after 15 min of compiling | On the droplet only, add - --enforce-eager to the vllm command in infra/docker-compose.yml, then make spike-vllm spike-wait. Record it: eager mode is slower, so G1 numbers are a lower bound. |
| Engine or kernel error in vLLM 0.31.0 | Set VLLM_IMAGE=vllm/vllm-openai-rocm:v0.30.0@sha256:2e7da1ad1c66836802072588adea75f9f4991da5f9545b4318e91d422c22ce6a in .env (another 11 GB pull), then make spike-vllm spike-wait. |
| Gemma 4 31B does not start at all | Plan B model (spec 13, R-02, D-12): in .env set GEMMA_MODEL=google/gemma-4-26B-A4B-it and GEMMA_REVISION=4d7ae4984b7db7de8f8457170b3f1a419ee76d52 (both together), then make spike-weights spike-weights-wait spike-vllm spike-wait (another 51.6 GB download). |
With this script, `make spike-vllm spike-wait` after the change is a plain rerun of the phase: spike-vllm is never skipped by its marker, its `up -d` recreates the container when the image, the command or the model changed, and spike-wait then polls the new container (a done spike-vllm would otherwise leave the old one polled for up to 60 min). In the bench phase, --only spike-down first: bench-vllm starts vLLM only when none is running. For the plan B model: --only spike-weights, --only spike-weights-wait, --only spike-vllm, --only spike-wait, then --force --from spike-g7 (G7, G1 and what follows again on the 26B-A4B; the earlier records are the 31B's: note it).
gpu-up also needs PUBLIC_HOST and ADMIN_PASSWORD_HASH in .env (compose.public.yml), the api healthy after the reset: docker compose ... -f infra/compose.public.yml logs reset api proxy. If gpu-up stops on the build of trainer-graphsage (up -d --build builds every service of the gpu profile with a build section, the trainers included, from the cache when nothing changed; rocm/dgl refusing numpy 2 is the plan B of gnn-image): start the live stack by name, so the trainers are neither built nor started (vLLM is already healthy after spike-vllm and spike-wait, which gpu-up ran first): docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml --env-file .env --profile gpu -f infra/compose.public.yml up -d --build postgres reset api ui proxy telemetry telemetry-sampler, then bash scripts/release/06-gpu-session.sh live --skip gpu-up for the reset and the HTTPS check. Derived from the compose files, not in the runbook: record it.
EOF
      ;;
    spike-g7) cat <<'EOF'
spike-g7 only reads the logs (weights-status.txt, vllm-start.txt, vllm-healthy.txt, the vLLM log lines): if it fails, vLLM is not up: --only spike-vllm, then --only spike-wait, then a plain rerun.
EOF
      ;;
    spike-g1|spike-g1-bench) cat <<'EOF'
Runbook step 10: "finish_reason length: raise --max-tokens (default 1024) in the command from make -n spike-g1." "Plan B (spec 13, R-02, D-12): single-stream below 15 tokens/s (1000 / median TPOT in ms) or a start error. In .env set GEMMA_MODEL=google/gemma-4-26B-A4B-it and GEMMA_REVISION=4d7ae4984b7db7de8f8457170b3f1a419ee76d52 (both together), then make spike-weights spike-weights-wait spike-vllm spike-wait (another 51.6 GB download) and repeat this step" (here: --only for each of those four targets, then --force --from spike-g7, so that G7 too is recorded on the plan B model). spike-g1-bench "fails unless every request succeeds".
If the log ends with "FAIL: request without the API key was not refused with 401": the tokens/s in artifacts/spikes/g1.json are valid, the model is not the problem. Record nfr11_key_enforced=false in docs/spike-results.md (to note, never to ignore), check VLLM_API_KEY in .env against the --api-key of the container (make spike-logs, or docker compose ... ps), and go on with --skip spike-g1 (spike-g1-bench still runs; its tokens/s decide D-12).
EOF
      ;;
    spike-g5) cat <<'EOF'
Runbook step 11: "Plan B (spec 13): inspection out of scope (it was Could)": --skip spike-g5 and record it. Another photo: IMAGE=artifacts/spikes/<file>.
EOF
      ;;
    spike-g6) cat <<'EOF'
Runbook step 12: "make spike-g6 G6_MODE=named # only if auto fails" (this script already tried it). "Plan B (spec 13): the agent's code picks the tool, Gemma writes only the answer": --skip spike-g6 and record it.
EOF
      ;;
    spike-g4) cat <<'EOF'
Runbook step 13: "Plan B (spec 13): train with vLLM stopped" (make spike-down, then the jobs; the bench phase already runs B1, B2 and the GNN jobs with vLLM down). artifacts/spikes/logs/g4_*.log and g4_amdsmi.log say which job failed. --skip spike-g4 and record it.
EOF
      ;;
    spike-down) cat <<'EOF'
make spike-down is docker compose ... --profile spike down (the volumes are kept). If it failed: docker ps, then the same compose command by hand; never add -v (hf-cache holds the 62 GB of weights).
EOF
      ;;
    gnn-image|gnn-test-gpu) cat <<'EOF'
Runbook, The GPU session, step 6: "A failure there is the rocm/dgl image refusing numpy 2 (it ships numpy 1.26.4, pinned by amd-dgl): record it, plan B of spec 13 (GraphSAGE as an upgrade, B2 postponed), and go on with step 8": --skip gnn-image,gnn-test-gpu,bench-b2,embeddings-gpu (embeddings-gpu needs the same image; on the laptop make risk then skips the A4 row, which needs data/features/customer_embeddings.parquet).
EOF
      ;;
    bench-b1) cat <<'EOF'
Runbook, The GPU session, step 8: "the log line B1 cuda: cold start ... and no 'GPU not used' error (amd_xgboost falls back to the CPU with only a warning; B1 refuses that run instead of reporting it)": make spike-gpu-check. Plan B of SPK-G2 (XGBoost on CPU): --skip bench-b1 and record it. "After a failure, rerun from the failed step with the same BENCH_RUN: the parts already written in $RUN stay."
EOF
      ;;
    bench-b2) cat <<'EOF'
Runbook, The GPU session, step 9: "Exit 2 from the job means the GPU is not visible in the container (make spike-gpu-check)." A short session: BENCH_ARGS="--b2-samples 1" skips the 25% and 50% sizes. Plan B (spec 13): GraphSAGE as an upgrade, B2 postponed: --skip bench-b2 and record it. The same BENCH_RUN keeps the parts already written.
EOF
      ;;
    embeddings-gpu) cat <<'EOF'
Runbook, The GPU session, step 10: it refuses to start once vLLM is up (make spike-down first) and exits 2 when the GPU is not visible (make spike-gpu-check). Without it, make risk on the laptop skips the A4 row (data/features/customer_embeddings.parquet missing): --skip embeddings-gpu and record it.
EOF
      ;;
    bench-b4) cat <<'EOF'
Runbook, The GPU session, step 12: "amd-smi must exist on the host (/opt/rocm/bin is added to the PATH); otherwise the target says so and samples inside the container. batch_generation exits 1 if vLLM was unavailable for any decision" (make spike-logs, then rerun with the same BENCH_RUN). "Every part says measured, skipped (with the reason) or failed (with the error); a failed part does not stop the next one."
EOF
      ;;
    bench-b3) cat <<'EOF'
Runbook, The GPU session, step 13: "failed must be 0 in every load of B3.json (details); failures are kept out of the latencies and noted. To change the loads: BENCH_ARGS=\"--b3-batches 1,8,32 --b3-concurrency 4,16 --b3-repeats 2\"." vLLM must be up (make spike-logs). Rerun with the same BENCH_RUN.
B3 reads its cases from the store with scores that B4 generated in bench-work/$BENCH_RUN: if B4 failed before its scoring step, or was skipped, rerun bench-b4 first with the same BENCH_RUN (--only bench-b4); without B4, B3 has no cases (data/ in the bench container holds only data/raw: no store, no scores), and the error looks unrelated.
EOF
      ;;
    bench-merge) cat <<'EOF'
bench-merge reads the parts under artifacts/bench/$BENCH_RUN (a failed part is merged as failed, never as a result) and writes bench/results/$BENCH_RUN.json, latest.json and latest-gpu.json. Check that the parts exist; rerun with the same BENCH_RUN.
EOF
      ;;
    batch-generate) cat <<'EOF'
Runbook, The demo export, step 4: it "stops (exit 2) unless vLLM serves GEMMA_MODEL" (the model vLLM started with is in make spike-logs; .env must name the same model and revision) and exits "1 if vLLM failed any (not cached)": rerun; the cached drafts stay, only the missing ones are made again. It needs vLLM healthy (--only spike-vllm, --only spike-wait) and serving/ copied from the laptop.
EOF
      ;;
    demo-export) cat <<'EOF'
Runbook, The demo export, step 5: it "refuses an empty cache and drafts of a model other than GEMMA_MODEL or of a batch run off the GPU". Fix the batch (batch-generate again), never REHEARSAL=1 or ALLOW_EMPTY_CACHE=1 for the MI300X package (they label it a rehearsal). Host python3 must be 3.8 or later.
EOF
      ;;
    gpu-reset) cat <<'EOF'
Runbook, The demo export, step 8: the reset "drafts every open return through vLLM, LLM_CONCURRENCY model calls at once (default 32; VLLM_TIMEOUT_S bounds each, default 20 s)". vLLM must be healthy (make spike-logs); docker compose ... --profile gpu logs reset.
EOF
      ;;
    https-check) cat <<'EOF'
Caddy takes the certificate for PUBLIC_HOST by itself (up to two minutes): the cloud firewall must allow 80 and 443 inbound, PUBLIC_HOST must be the sslip.io name of this droplet's IP. docker compose ... -f infra/compose.public.yml logs proxy api.
EOF
      ;;
    *) echo "no plan B in the runbook for $1: read the log and infra/spikes/README.md" ;;
  esac
}

# ---- run ---------------------------------------------------------------------------------
finish() {
  local code=$? e status=fail
  e="$(date +%s)"
  [ "$code" = 0 ] && status=ok
  [ "$DRY" = 1 ] && status="dry-run-$status"
  record "$PHASE" run "$RUN_START" "$e" "$status"
  if [ "$code" != 0 ] && [ -n "$FAILED_TARGET" ]; then
    step "plan B for $FAILED_TARGET (infra/spikes/README.md)"
    plan_b "$FAILED_TARGET"
  fi
  if [ "$DRY" = 1 ]; then
    note "dry-run: nothing packed; the dry-run logs and times are under $GPU_LOGS"
  else
    collect_results
    scrub_secrets
    pack
  fi
  if [ "$code" = 0 ]; then
    ok "06-gpu-session $PHASE: every step passed (whole run $(hms $(( e - RUN_START ))), targets $(hms "$RUN_SECS"))"
  else
    fail "06-gpu-session $PHASE: stopped at ${FAILED_WHERE:-an unexpected error} (exit $code) after $(hms $(( e - RUN_START )))"
    if [ -n "$FAILED_TARGET" ]; then
      printf 'resume: bash scripts/release/06-gpu-session.sh %s   (starts again at %s; --skip %s applies its plan B and goes on)\n' "$PHASE" "$FAILED_TARGET" "$FAILED_TARGET"
      case "$FAILED_TARGET" in
        spike-wait|gpu-up) printf 'after a change of .env or infra/docker-compose.yml (a plan B of runbook step 9): the same plain rerun; spike-vllm is never skipped, its up -d recreates the container, and spike-wait polls the new one\n' ;;
        bench-vllm) printf 'after a change of .env or infra/docker-compose.yml: bash scripts/release/06-gpu-session.sh bench --only spike-down first (bench-vllm starts vLLM only when none is running), then the same rerun\n' ;;
      esac
    fi
  fi
}
trap finish EXIT

step "06-gpu-session $PHASE$( [ "$DRY" = 1 ] && printf ' (dry-run: make -n, no Docker)' )"
printf 'started %s; repository %s @ %s; ENV_FILE=%s; price %s USD per MI300X hour (billing from creation to destruction, powered off or not)\n' \
  "$(utc "$RUN_START")" "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" "$(git rev-parse --short HEAD 2>/dev/null)" "$ENV_FILE" "$PRICE_USD_PER_HOUR"
printf 'phases: %s%s%s%s%s\n' "$RUN_PHASES" "${FROM:+; from $FROM}" "${ONLY:+; only $ONLY}" "${SKIP:+; skip $SKIP}" "$( [ "$FORCE" = 1 ] && printf '; force (markers ignored)' )"
if [ -f "$ENV_FILE" ]; then
  sed -E 's/^(POSTGRES_PASSWORD|DATABASE_URL|VLLM_API_KEY|HF_TOKEN|ADMIN_PASSWORD_HASH)=.*/\1=<redacted>/' "$ENV_FILE" > "$GPU_LOGS/env-redacted.txt"
fi
droplet_age

for p in $RUN_PHASES; do
  run_phase "$p" || exit 1
done
