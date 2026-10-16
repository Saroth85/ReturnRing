#!/usr/bin/env bash
# Release guide, section 3, plan B of the droplet: the driver of a session on an AMD host WITHOUT
# Docker (AMD AI Notebooks, notebooks.amd.com: JupyterLab, a terminal, a ROCm GPU; or a Hot Aisle
# VM with one MI300X). It does on the host, with plain Python, what 06-gpu-session.sh does with
# the make targets of infra/spikes/README.md: vLLM serving Gemma 4 with the flags of the vllm
# service of infra/docker-compose.yml, the spikes that need no trainer image, the batch of drafts
# and the demo package, the benchmarks B1, B3 and B4, and the live stack for the video. One
# checkpoint per target, with a log, the wall-clock time and a marker, so a rerun resumes where the
# last run stopped. From the repository root, inside tmux when the host has it:
#
#   bash scripts/release/07-host-session.sh <phase> [--from <target>] [--only <target>]
#                                           [--skip <t1,t2,...>] [--force] [--dry-run]
#
# Phases (their targets, in order):
#   probe      probe                       the machine facts (release-logs/host/probe.txt); never fails
#   env        venv vllm-python venv-amd   the venv with requirements/dev.txt; where vLLM is; the
#                                          optional venv with amd_xgboost from the AMD index (its
#                                          failure is recorded as the SPK-G2 plan B, not a stop)
#   vllm       weights weights-wait template vllm-serve vllm-wait first-request
#                                          the Gemma weights at GEMMA_REVISION (unless cached), the
#                                          Gemma 4 chat template, vllm serve in the background
#                                          (release-logs/host/vllm.log, vllm.pid), /health within
#                                          60 min, the first guided-JSON request
#   vllm-stop  vllm-stop                   stops that vLLM (SIGTERM to its process group)
#   spikes     spike-g2 spike-g7 spike-g1 spike-g1-bench spike-g5 spike-g6 spike-g4
#                                          the plain-Python spikes (runbook steps 6, 9-13); G3 is
#                                          not here: DGL needs the rocm/dgl image (plan B of spec 13:
#                                          GraphSAGE stays CPU-trained, docs/results/us-202-graphsage.md)
#   export     reset batch-generate demo-export tarball
#                                          the reset on SQLite (or HOST_DATABASE_URL), the drafts of
#                                          every open return through vLLM (DEVICE=gpu, LLM_MODE=vllm),
#                                          the package labelled with the probed GPU, demo-cache.tar.gz
#                                          and its sha256
#   bench      vllm-stop bench-b1 bench-b2 bench-vllm bench-b4 bench-b3 bench-merge
#                                          python -m bench run --device gpu, parts B1, B4, B3 (B2 is
#                                          skipped with its reason), one BENCH_RUN for the session,
#                                          merge and report
#   live       live-reset api-up readyz telemetry walk tunnel-help
#                                          uvicorn on 127.0.0.1:API_PORT with LLM_MODE=vllm DEVICE=gpu,
#                                          /readyz, the amd-smi sampler, the demo walk, then the
#                                          tunnel instructions (printed: this script starts no tunnel)
#   down       api-stop telemetry-stop fake-stop vllm-stop disk-usage, then the reminder: copy back,
#                                          destroy the paid VM. This script never deletes a file of
#                                          the project, a cache or a database; it only stops what it
#                                          started.
#   all        probe env vllm spikes bench export, in this order (bench before export: the
#                                          package packs bench/results/latest-gpu.json, which bench-merge
#                                          writes; bench ends with vLLM healthy, which export's reset
#                                          and batch need)
#
# Checkpoints: release-logs/host/<phase>/<target>.done is written when a target exits 0. A rerun
# skips the done targets (and those skipped with --skip), so after a failure the next run resumes
# from the failed target; bench reuses BENCH_RUN (release-logs/host/BENCH_RUN). Exception, the
# targets whose effect does not outlive the day or that are cheap and idempotent (ALWAYS_RUN
# below): probe, vllm-python (env: a cheap record), the whole vllm phase but first-request,
# vllm-stop, bench-vllm and every target of live and down run every time, marker or not. vllm-serve keeps a vLLM that is already healthy:
# after a change of .env or VLLM_EXTRA_ARGS (a plan B of runbook step 9), vllm-stop first.
# --from, --only, --skip and --force as in 06-gpu-session.sh.
# A bench rerun WITHOUT --from stops and restarts vLLM (vllm-stop and bench-vllm are ALWAYS_RUN) and
# remeasures the cold start of B3, overwriting vllm-start.txt, vllm-healthy.txt and first-request.json
# of the run: after a failure in bench-b4, bench-b3 or bench-merge, resume with --from <target> (the
# resume line printed on a failure says so).
# Logs: release-logs/host/<phase>/<target>.log; times: release-logs/host/times.tsv with a cost
# column at PRICE_USD_PER_HOUR (2.99, Hot Aisle's price per GPU hour; 0 when HOST_PROVIDER names a
# Notebook, the free host, and then no cost is printed): an
# estimate, and a lower bound of the bill, which runs while the VM exists. HOST_CREATED=<UTC> on
# any run stores the creation time and prints the age and the bill it implies.
# --dry-run prints what each target would run, under release-logs/host-dry-run/ (the real markers
# are untouched), and skips the checks that need the GPU or a running vLLM, with a note.
# On a failure the script prints the plan B for that target, packs the logs into
# release-logs-<stamp>.tgz (the .env secrets and the password of HOST_DATABASE_URL replaced in every
# log: vLLM prints its arguments, --api-key included; the reset and uvicorn lines print DATABASE_URL)
# and exits 1 (2: usage).
#
# Environment: ENV_FILE (.env; VLLM_API_KEY not change-me, GEMMA_MODEL, GEMMA_REVISION, HF_TOKEN
# optional, AMD_PYPI_INDEX_URL for venv-amd), HOST_RUN (the folder under release-logs/, host),
# HOST_PROVIDER ("Hot Aisle", "AMD AI Notebooks"; in the package label), VENV (.venv, the one of
# make demo-walk and docs/amd-host-runbook.md 3.2), VENV_AMD (.venv-bench, amd-host-runbook 3.3),
# VLLM_BIN (the vllm command), VLLM_PYTHON (the python that imports vllm),
# VLLM_CHAT_TEMPLATE (a template file already at hand), VLLM_EXTRA_ARGS ("--enforce-eager"),
# VLLM_HOST/VLLM_PORT (127.0.0.1, 8001), HF_HOME (the weights cache, ~/.cache/huggingface),
# HOST_DATABASE_URL (PostgreSQL if present; else SQLite under release-logs/host/), SERVING_DIR
# (serving), DEMO_DIR (demo-cache), DEMO_GPU (default: the probed GPU name and HOST_PROVIDER),
# API_PORT (8000), BENCH_RUN, BENCH_ARGS, IMAGE (artifacts/spikes/garment.jpg), DURATION and POLL
# (SPK-G4: 300 s, 45 s), PRICE_USD_PER_HOUR, HOST_CREATED, ALLOW_NO_TMUX=1.
# Rehearsal on a CPU (never the MI300X package): REHEARSAL=1 with LLM_MODE=mock DEVICE=cpu, and
# FAKE_VLLM=1 to run the batch against bench/fake_vllm.py (the drafts are recorded under
# fake/gemma-dry-run); demo-export then packs with --rehearsal, labelled as one. On an AMD GPU
# that is not an MI300X, Gemma having run on it, the honest package is a rehearsal of Gemma's
# drafts: REHEARSAL=1 LLM_MODE=vllm DEVICE=gpu bash scripts/release/07-host-session.sh export
# (drafts by GEMMA_MODEL with device gpu, packed with --rehearsal and labelled 'Precomputed in a
# rehearsal, not verified on AMD MI300X'); REHEARSAL=1 alone defaults to the mock and makes no draft.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

ENV_FILE="${ENV_FILE:-.env}"
HOST_RUN="${HOST_RUN:-host}"
HOST_PROVIDER="${HOST_PROVIDER:-AMD host without Docker}"
# 2.99 USD per GPU hour is Hot Aisle's price; a notebook (HOST_PROVIDER names it) is free: price 0.
case "$HOST_PROVIDER" in *[Nn]otebook*) PRICE_USD_PER_HOUR="${PRICE_USD_PER_HOUR:-0}" ;; *) PRICE_USD_PER_HOUR="${PRICE_USD_PER_HOUR:-2.99}" ;; esac
VENV="${VENV:-$ROOT/.venv}"
VENV_AMD="${VENV_AMD:-$ROOT/.venv-bench}"
HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"; export HF_HOME
VLLM_HOST="${VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${VLLM_PORT:-8001}"
HOST_VLLM_BASE_URL="${HOST_VLLM_BASE_URL:-http://$VLLM_HOST:$VLLM_PORT/v1}"
VLLM_WAIT_S="${VLLM_WAIT_S:-3600}"
API_PORT="${API_PORT:-8000}"
API_URL="http://127.0.0.1:$API_PORT"
SERVING_DIR="${SERVING_DIR:-serving}"
DEMO_DIR="${DEMO_DIR:-demo-cache}"
IMAGE="${IMAGE:-artifacts/spikes/garment.jpg}"
BENCH_ARGS="${BENCH_ARGS:-}"
REHEARSAL="${REHEARSAL:-0}"
FAKE_VLLM="${FAKE_VLLM:-0}"
DURATION="${DURATION:-300}"
POLL="${POLL:-45}"
VLLM_EXTRA_ARGS="${VLLM_EXTRA_ARGS:-}"
VLLM_BIN="${VLLM_BIN:-}"
VLLM_PYTHON="${VLLM_PYTHON:-}"
VLLM_CHAT_TEMPLATE="${VLLM_CHAT_TEMPLATE:-}"
SPIKES="artifacts/spikes"
TEMPLATE_NAME="tool_chat_template_gemma4.jinja"
FAKE_MODEL="fake/gemma-dry-run"
export PATH="$PATH:/opt/rocm/bin"   # amd-smi, rocm-smi and rocminfo live there, off the PATH
if [ "$REHEARSAL" = 1 ]; then LLM_MODE="${LLM_MODE:-mock}"; DEVICE="${DEVICE:-cpu}"
else LLM_MODE=vllm; DEVICE=gpu; fi

PHASES="probe env vllm vllm-stop spikes export bench live down all"
TARGETS_probe="probe"
TARGETS_env="venv vllm-python venv-amd"
TARGETS_vllm="weights weights-wait template vllm-serve vllm-wait first-request"
TARGETS_vllm_stop="vllm-stop"
TARGETS_spikes="spike-g2 spike-g7 spike-g1 spike-g1-bench spike-g5 spike-g6 spike-g4"
TARGETS_export="reset batch-generate demo-export tarball"
TARGETS_bench="vllm-stop bench-b1 bench-b2 bench-vllm bench-b4 bench-b3 bench-merge"
TARGETS_live="live-reset api-up readyz telemetry walk tunnel-help"
TARGETS_down="api-stop telemetry-stop fake-stop vllm-stop disk-usage"
ALWAYS_RUN="probe vllm-python weights weights-wait template vllm-serve vllm-wait vllm-stop bench-vllm live-reset api-up readyz telemetry walk tunnel-help api-stop telemetry-stop fake-stop disk-usage"

usage() {
  [ $# -gt 0 ] && printf 'error: %s\n\n' "$1" >&2
  printf 'usage: bash scripts/release/07-host-session.sh <phase> [--from <target>] [--only <target>]\n' >&2
  printf '                                                [--skip <t1,t2,...>] [--force] [--dry-run]\n' >&2
  printf '\nphases: %s\n' "$PHASES" >&2
  exit 2
}

phase_targets() {
  case "$1" in
    probe) printf '%s' "$TARGETS_probe" ;;
    env) printf '%s' "$TARGETS_env" ;;
    vllm) printf '%s' "$TARGETS_vllm" ;;
    vllm-stop) printf '%s' "$TARGETS_vllm_stop" ;;
    spikes) printf '%s' "$TARGETS_spikes" ;;
    export) printf '%s' "$TARGETS_export" ;;
    bench) printf '%s' "$TARGETS_bench" ;;
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
  RUN_PHASES="probe env vllm spikes bench export"   # bench before export: the package packs latest-gpu.json
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
if [ "$DRY" = 1 ]; then HOST_LOGS="$LOGS/$HOST_RUN-dry-run"; else HOST_LOGS="$LOGS/$HOST_RUN"; fi
HOST_REL="${HOST_LOGS#"$LOGS"/}"
mkdir -p "$HOST_LOGS"
TIMES="$HOST_LOGS/times.tsv"
[ -f "$TIMES" ] || printf 'phase\ttarget\tstart_utc\tend_utc\tseconds\tstatus\tusd_estimate_at_%s_per_hour\n' "$PRICE_USD_PER_HOUR" > "$TIMES"
DATABASE_URL="${HOST_DATABASE_URL:-sqlite:///$HOST_LOGS/returnring.db}"
# The password of a PostgreSQL HOST_DATABASE_URL (scheme://user:password@host/db): redacted from
# every printed command and scrubbed from the packed logs like the .env secrets (rule 15).
DB_PASSWORD=""
case "$DATABASE_URL" in *://*:*@*) _v="${DATABASE_URL#*://}"; _v="${_v%%@*}"; DB_PASSWORD="${_v#*:}"; unset _v ;; esac
VLLM_PID="$HOST_LOGS/vllm.pid"; VLLM_LOG="$HOST_LOGS/vllm.log"
API_PID="$HOST_LOGS/uvicorn.pid"; API_LOG="$HOST_LOGS/uvicorn.log"
TELEMETRY_PID="$HOST_LOGS/telemetry.pid"; TELEMETRY_LOG="$HOST_LOGS/telemetry.log"
FAKE_PID="$HOST_LOGS/fake-vllm.pid"; FAKE_LOG="$HOST_LOGS/fake-vllm.log"; FAKE_URL="$HOST_LOGS/fake-vllm.url"
GPU_NAME_FILE="$HOST_LOGS/gpu-name.txt"
PY="$VENV/bin/python"
PY_AMD="$VENV_AMD/bin/python"
RUN_START="$(date +%s)"
RUN_SECS=0
CURRENT_TARGET=""; FAILED_TARGET=""; FAILED_WHERE=""; FAILED_PHASE=""; PHASE_DIR=""
BENCH_RUN="${BENCH_RUN:-}"; BENCH_RUN_FROM=""
if [ -n "$BENCH_RUN" ]; then BENCH_RUN_FROM=env
elif [ -f "$HOST_LOGS/BENCH_RUN" ]; then BENCH_RUN="$(cat "$HOST_LOGS/BENCH_RUN")"; BENCH_RUN_FROM=file; fi
AUTH_HEADER_FILE="$(mktemp)"   # the API key for curl, never on a command line (ps, logs)

utc() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
usd() { awk -v s="$1" -v p="$PRICE_USD_PER_HOUR" 'BEGIN { printf "%.2f", s / 3600 * p }'; }
hms() { printf '%dh %02dmin %02ds' $(( $1 / 3600 )) $(( $1 % 3600 / 60 )) $(( $1 % 60 )); }
note() { printf 'note %s\n' "$1"; }
warn() { printf 'WARN %s\n' "$1"; }
record() {  # <phase> <target> <start-epoch> <end-epoch> <status>
  local secs=$(( $4 - $3 ))
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$(utc "$3")" "$(utc "$4")" "$secs" "$5" "$(usd "$secs")" >> "$TIMES"
}
env_value() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -n 1 | tr -d "'\""; }
dry_skip() { if [ "$DRY" = 1 ]; then note "dry-run: $1 not checked here (needs $2)"; return 0; fi; return 1; }
pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
# redact <text>: the secret values replaced, for every printed command line.
redact() {
  local text="$1" k v
  for k in VLLM_API_KEY HF_TOKEN DB_PASSWORD; do
    v="${!k:-}"
    [ "${#v}" -ge 8 ] && text="${text//"$v"/<redacted $k>}"
  done
  printf '%s' "$text"
}
# do_run <argv...>: prints the command (redacted) and runs it; in a dry run prints it only.
do_run() {
  local shown; shown="$(redact "$*")"
  if [ "$DRY" = 1 ]; then printf 'dry-run: would run: %s\n' "$shown"; return 0; fi
  printf '+ %s\n' "$shown"
  "$@"
}
# do_start <pidfile> <logfile> <argv...>: a detached background process (its own session, so an
# SSH or terminal drop does not kill it), stdin from /dev/null, the pid of the command itself.
do_start() {
  local pidfile="$1" logfile="$2"; shift 2
  local shown; shown="$(redact "$*")"
  if [ "$DRY" = 1 ]; then printf 'dry-run: would start in the background (log %s, pid file %s): %s\n' "$logfile" "$pidfile" "$shown"; return 0; fi
  printf 'starting in the background (log %s): %s\n' "$logfile" "$shown"
  setsid nohup bash -c 'echo $$ > "$0"; exec "$@"' "$pidfile" "$@" > "$logfile" 2>&1 < /dev/null &
  sleep 1
  pid_alive "$pidfile" || { fail "the process exited at once: tail -50 $logfile"; tail -50 "$logfile" 2>/dev/null; return 1; }
  ok "pid $(cat "$pidfile")"
}
# do_stop <pidfile> <name>: SIGTERM to the process group, up to 60 s, then SIGKILL; never fails.
do_stop() {
  local pidfile="$1" name="$2" pid t=0
  if [ "$DRY" = 1 ]; then note "dry-run: would stop $name (pid file $pidfile) with SIGTERM to its process group, SIGKILL after 60 s"; return 0; fi
  if ! pid_alive "$pidfile"; then note "$name is not running (no live pid in $pidfile)"; rm -f "$pidfile"; return 0; fi
  pid="$(cat "$pidfile")"
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$t" -ge 60 ]; then kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true; break; fi
    sleep 2; t=$(( t + 2 ))
  done
  rm -f "$pidfile"
  ok "$name stopped (pid $pid, ${t}s)"
}
vllm_healthy() { curl -fsS -m 5 -H @"$AUTH_HEADER_FILE" -o /dev/null "http://$VLLM_HOST:$VLLM_PORT/health" 2>/dev/null; }
fake_healthy() { [ -f "$FAKE_URL" ] && curl -fsS -m 3 -o /dev/null "$(sed 's|/v1$||' "$FAKE_URL")/health" 2>/dev/null; }
gpu_name() { [ -f "$GPU_NAME_FILE" ] && cat "$GPU_NAME_FILE" || printf 'unknown AMD GPU (not probed)'; }
demo_gpu() { if [ -n "${DEMO_GPU:-}" ]; then printf '%s' "$DEMO_GPU"; else printf '%s (%s)' "$(gpu_name)" "$HOST_PROVIDER"; fi; }
amd_smi_present() { command -v amd-smi >/dev/null 2>&1; }

if [ -n "${HOST_CREATED:-}" ]; then
  if date -u -d "$HOST_CREATED" +%s >/dev/null 2>&1; then printf '%s\n' "$HOST_CREATED" > "$HOST_LOGS/HOST_CREATED"
  else usage "HOST_CREATED must be a UTC time such as 2026-10-17T08:00:00Z (got $HOST_CREATED)"; fi
fi

host_age() {
  if [ -f "$HOST_LOGS/HOST_CREATED" ]; then
    local created age
    created="$(date -u -d "$(cat "$HOST_LOGS/HOST_CREATED")" +%s)"
    age=$(( $(date +%s) - created ))
    if [ "$age" -lt 0 ]; then warn "HOST_CREATED $(cat "$HOST_LOGS/HOST_CREATED") is in the future by $(hms $(( -age ))): check the time (UTC) and set it again"; return 0; fi
    if [ "$PRICE_USD_PER_HOUR" = 0 ]; then
      printf 'host created %s: age %s (free host: no bill; a notebook session has its own time limit: check it)\n' "$(cat "$HOST_LOGS/HOST_CREATED")" "$(hms "$age")"
    else
      printf 'host created %s: age %s, estimated bill so far %s USD at %s USD/h (a paid VM bills while it exists)\n' \
        "$(cat "$HOST_LOGS/HOST_CREATED")" "$(hms "$age")" "$(usd "$age")" "$PRICE_USD_PER_HOUR"
    fi
  else
    note "HOST_CREATED=<UTC time of the creation> on any run shows the host's age and the bill it implies"
  fi
}

# ---- .env ---------------------------------------------------------------------------------
check_env() {
  if [ ! -f "$ENV_FILE" ]; then
    fail "$ENV_FILE is missing: cp .env.example .env, then VLLM_API_KEY (sed -i \"s/^VLLM_API_KEY=.*/VLLM_API_KEY=\$(openssl rand -hex 24)/\" .env)"
    return 1
  fi
  local rc=0 v
  v="$(env_value GEMMA_MODEL)"
  [ -n "$v" ] || { fail "$ENV_FILE: GEMMA_MODEL is empty (google/gemma-4-31B-it, or the plan B google/gemma-4-26B-A4B-it with its revision)"; rc=1; }
  v="$(env_value GEMMA_REVISION)"
  printf '%s' "$v" | grep -Eq '^[0-9a-f]{40}$' || { fail "$ENV_FILE: GEMMA_REVISION must be the 40-character Hugging Face commit of .env.example; got '${v:-<empty>}'"; rc=1; }
  v="$(env_value VLLM_API_KEY)"
  case "$v" in ""|change-me) fail "$ENV_FILE: VLLM_API_KEY is empty or change-me: sed -i \"s/^VLLM_API_KEY=.*/VLLM_API_KEY=\$(openssl rand -hex 24)/\" $ENV_FILE"; rc=1 ;; esac
  v="$(env_value AMD_PYPI_INDEX_URL)"
  case "$v" in https://pypi.amd.com/*) ;; *) warn "$ENV_FILE: AMD_PYPI_INDEX_URL is not the AMD index of .env.example (venv-amd, the optional amd_xgboost, needs it); got '${v:-<empty>}'" ;; esac
  v="$(env_value HF_TOKEN)"
  [ -z "$v" ] || note "$ENV_FILE: HF_TOKEN has a value: used by the weights download only (both Gemma 4 models are not gated: usually not needed); empty it before leaving the host"
  if grep -Ev '^(#|$|ADMIN_PASSWORD_HASH=)' "$ENV_FILE" | grep -Eq '^[A-Za-z_]+=.*[[:space:]"]'; then
    warn "$ENV_FILE has a value with a space or a quote (only ADMIN_PASSWORD_HASH goes between single quotes)"
  fi
  [ "$rc" = 0 ] && ok "$ENV_FILE: GEMMA_MODEL=$(env_value GEMMA_MODEL), GEMMA_REVISION=$(env_value GEMMA_REVISION), VLLM_API_KEY set"
  return "$rc"
}

load_env() {  # the values the commands need, exported in memory, never on a command line
  VLLM_API_KEY="$(env_value VLLM_API_KEY)"; export VLLM_API_KEY
  GEMMA_MODEL="$(env_value GEMMA_MODEL)"; export GEMMA_MODEL
  GEMMA_REVISION="$(env_value GEMMA_REVISION)"; export GEMMA_REVISION
  HF_TOKEN="$(env_value HF_TOKEN)"   # exported only around the download
  SEED="$(env_value SEED)"; SEED="${SEED:-42}"; export SEED
  AMD_PYPI_INDEX_URL="$(env_value AMD_PYPI_INDEX_URL)"
  local v
  v="$(env_value LLM_CONCURRENCY)"; [ -z "$v" ] || export LLM_CONCURRENCY="$v"
  v="$(env_value VLLM_TIMEOUT_S)"; [ -z "$v" ] || export VLLM_TIMEOUT_S="$v"
  printf 'Authorization: Bearer %s\n' "$VLLM_API_KEY" > "$AUTH_HEADER_FILE"
  export BENCH_GIT_COMMIT="${BENCH_GIT_COMMIT:-$(git rev-parse HEAD 2>/dev/null || true)}"
  export BENCH_GIT_DIRTY="${BENCH_GIT_DIRTY:-$(git status --porcelain 2>/dev/null | grep -q . && echo 1 || echo 0)}"
}

# LLM_ENV: the LLM variables of a reset, a batch or the API: the real vLLM, or the fake server of
# a rehearsal (FAKE_VLLM=1: LLM_MODE=vllm against bench/fake_vllm.py, which serves fake/gemma-dry-run).
llm_env() {
  if [ "$FAKE_VLLM" = 1 ]; then
    LLM_ENV=(LLM_MODE=vllm "VLLM_BASE_URL=$(cat "$FAKE_URL" 2>/dev/null || printf 'http://127.0.0.1:0/v1')" "GEMMA_MODEL=$FAKE_MODEL")
  else
    LLM_ENV=("LLM_MODE=$LLM_MODE" "VLLM_BASE_URL=$HOST_VLLM_BASE_URL" "GEMMA_MODEL=$GEMMA_MODEL")
  fi
}

# ---- preconditions -----------------------------------------------------------------------
check_common() {
  local rc=0 tool
  if [ "$(id -u)" = 0 ]; then ok "running as root (bench-vllm can drop the page cache)"
  else note "not root: bench-vllm does not drop the page cache (vllm-start-conditions.txt says so); nothing else needs root"; fi
  if [ -n "${TMUX:-}" ]; then ok "inside tmux"
  elif command -v tmux >/dev/null 2>&1 && [ "${ALLOW_NO_TMUX:-0}" != 1 ] && [ "$DRY" = 0 ]; then
    fail "not inside tmux: tmux new -s host, then run again (reattach: tmux attach -t host); ALLOW_NO_TMUX=1 overrides. vLLM and uvicorn are detached (setsid) and survive a drop, this driver does not: a rerun resumes from the markers"; rc=1
  else warn "no tmux session: vLLM and uvicorn are detached and survive a terminal drop, this driver does not (a rerun resumes from the markers)"; fi
  for tool in python3 curl tar git awk sed; do
    command -v "$tool" >/dev/null 2>&1 || { fail "$tool is missing on this host"; rc=1; }
  done
  [ -f Makefile ] && [ -f infra/spikes/README.md ] || { fail "not the repository root: $ROOT"; rc=1; }
  check_env || rc=1
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    note "a Docker daemon answers on this host: the runbook's 06-gpu-session.sh (make targets, images, G3 included) is the better driver; this one uses none of it"
  fi
  local remote f
  remote="$(git remote get-url origin 2>/dev/null || true)"
  case "$remote" in https://*@*) warn "the git remote URL of origin carries credentials: remove them (the repository is public)" ;; esac
  for f in "$HOME/.git-credentials" "$HOME/.netrc" "$HOME/.config/gh/hosts.yml"; do
    [ -f "$f" ] && warn "$f exists: a token on a shared or persistent host disk; remove it before leaving"
  done
  return "$rc"
}

need_venv() {  # <what>
  [ -x "$PY" ] && return 0
  if [ "$DRY" = 1 ]; then note "dry-run: $VENV does not exist here (the env phase creates it); $1 would need it"; return 0; fi
  fail "$VENV does not exist, and $1 needs it: bash scripts/release/07-host-session.sh env first (VENV=<path> names another one)"
  return 1
}

need_venv_amd() {  # <what> <plan B>
  if [ -x "$PY_AMD" ] && [ ! -f "$HOST_LOGS/env/venv-amd.planb" ]; then ok "$VENV_AMD present (amd_xgboost)"; return 0; fi
  if [ "$DRY" = 1 ]; then note "dry-run: $VENV_AMD not usable here; on the host $1 would need it (plan B: $2)"; return 0; fi
  fail "$VENV_AMD is missing or its amd_xgboost did not load (release-logs/$HOST_REL/env/venv-amd.planb), and $1 needs it: plan B of SPK-G2 (spec 13), $2"
  return 1
}

need_vllm_up() {  # <target>
  if [ "$FAKE_VLLM" = 1 ]; then note "FAKE_VLLM=1: $1 uses the fake server (a rehearsal)"; return 0; fi
  if [ "$REHEARSAL" = 1 ] && [ "$LLM_MODE" != vllm ]; then note "REHEARSAL=1 with LLM_MODE=$LLM_MODE: $1 does not use vLLM"; return 0; fi
  if dry_skip "vLLM healthy at $HOST_VLLM_BASE_URL (needed by $1)" "a running vLLM"; then return 0; fi
  if vllm_healthy; then ok "vLLM answers /health at $HOST_VLLM_BASE_URL"; return 0; fi
  fail "vLLM does not answer at http://$VLLM_HOST:$VLLM_PORT/health, and $1 needs it: bash scripts/release/07-host-session.sh vllm (this script starts it only through the vllm phase or bench-vllm)"
  return 1
}

need_vllm_down() {  # <target>
  if dry_skip "vLLM stopped ($1 runs alone on the CPU and the GPU)" "a running vLLM"; then return 0; fi
  if pid_alive "$VLLM_PID" || vllm_healthy; then
    fail "vLLM is running: $1 runs alone on the CPU and the GPU (the Makefile's VLLM_DOWN guard). Stop it: bash scripts/release/07-host-session.sh vllm-stop; this script does not stop it by itself here"
    return 1
  fi
  ok "vLLM is down"
}

PHOTO_PROBLEM=""
photo_check() {
  PHOTO_PROBLEM=""
  [ -f "$IMAGE" ] || { PHOTO_PROBLEM="SPK-G5 photo missing: $IMAGE (JPEG or PNG under 2 MB, no person, no personal data on labels; copy it from the laptop: scp garment.jpg <user>@<host>:ReturnRing/$IMAGE, or the JupyterLab upload button; IMAGE=<path> names another file)"; return 1; }
  local size; size="$(stat -c %s "$IMAGE")"
  [ "$size" -le 2000000 ] || { PHOTO_PROBLEM="SPK-G5 photo $IMAGE is $size bytes: the runbook asks for under 2 MB"; return 1; }
  python3 - "$IMAGE" <<'EOF' || { PHOTO_PROBLEM="SPK-G5 photo $IMAGE is neither JPEG nor PNG (by its first bytes)"; return 1; }
import sys
head = open(sys.argv[1], "rb").read(8)
sys.exit(0 if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n") else 1)
EOF
  ok "SPK-G5 photo: $IMAGE ($size bytes)"
}

serving_check() {
  [ -f "$SERVING_DIR/manifest.json" ] || { fail "$SERVING_DIR/manifest.json missing: the snapshot is built once on the laptop (make demo-data or make pipeline) and copied: rsync -a --delete serving/ <user>@<host>:ReturnRing/serving/ (Hot Aisle), or uploaded as a tarball through JupyterLab (notebook); never rebuilt on one side only: the cache is keyed by the case"; return 1; }
  python3 - "$SERVING_DIR/manifest.json" <<'EOF'
import json, sys
m = json.load(open(sys.argv[1]))
print("OK   serving manifest:", {k: m.get(k) for k in ("as_of", "seed", "contract_version", "format")})
EOF
}

check_vllm_phase() {
  local rc=0 free
  if [ -z "$(vllm_cmd_line)" ]; then
    if [ "$DRY" = 1 ]; then note "dry-run: no vllm found here (VLLM_BIN, PATH, VLLM_PYTHON); on the host the env phase (vllm-python) records where it is"
    else fail "no vllm on this host: VLLM_BIN=<path to vllm> or VLLM_PYTHON=<python that imports vllm>; bash scripts/release/07-host-session.sh env --only vllm-python prints what was tried and the order of things to try"; rc=1; fi
  else ok "vllm command: $(vllm_cmd_line)"; fi
  if dry_skip "/dev/kfd" "the GPU"; then :; elif [ -e /dev/kfd ]; then ok "/dev/kfd present"; else fail "/dev/kfd missing: no AMD GPU driver on this host"; rc=1; fi
  free="$(df -BG --output=avail "$HF_HOME" 2>/dev/null | tail -1 | tr -dc '0-9' || true)"
  [ -d "$HF_HOME" ] || free="$(df -BG --output=avail "$(dirname "$HF_HOME")" 2>/dev/null | tail -1 | tr -dc '0-9' || true)"
  if [ -n "$free" ] && [ "$free" -lt 70 ] && [ ! -d "$HF_HOME/hub/models--${GEMMA_MODEL//\//--}/snapshots/$GEMMA_REVISION" ]; then
    warn "$free GB free under $HF_HOME: the 31B weights are 62.6 GB (the 26B-A4B 51.6 GB); HF_HOME=<folder on a bigger disk> moves the cache"
  fi
  return "$rc"
}

check_spikes() {
  mkdir -p "$SPIKES/logs"
  if ! photo_check; then
    if [ "$DRY" = 1 ]; then note "$PHOTO_PROBLEM"; note "dry-run: on the host the run would stop at spike-g5 without the photo"
    else warn "$PHOTO_PROBLEM"; warn "copy the photo before spike-g5, or --skip spike-g5 (plan B of spec 13: inspection out of scope)"; fi
  fi
  need_venv "the spikes (g1_guided_json.py and g6_tool_call.py are standard library, but they run with the venv python)" || return 1
  return 0
}

check_export() {
  local rc=0 gpu
  serving_check || rc=1
  need_venv "the reset and the batch" || rc=1
  gpu="$(demo_gpu)"
  if [ "$REHEARSAL" = 1 ]; then
    note "REHEARSAL=1: the package is packed with --rehearsal and labelled 'Precomputed in a rehearsal, not verified on AMD MI300X' (LLM_MODE=$LLM_MODE, DEVICE=$DEVICE, FAKE_VLLM=$FAKE_VLLM)"
    if [ "$LLM_MODE" != vllm ] && [ "$FAKE_VLLM" != 1 ]; then
      note "LLM_MODE=$LLM_MODE: batch-generate makes no draft (python -m apps.api.batch_generate refuses it); the package holds only what $SERVING_DIR/llm_cache already has (nothing, unless an earlier batch wrote it). For Gemma's drafts on an AMD GPU that is not an MI300X: REHEARSAL=1 LLM_MODE=vllm DEVICE=gpu bash scripts/release/07-host-session.sh export"
    fi
  else
    # The package names the platform (rule 11): the default HOST_PROVIDER is not one.
    if [ "$HOST_PROVIDER" = "AMD host without Docker" ]; then
      if [ "$DRY" = 1 ]; then note "dry-run: HOST_PROVIDER is the default '$HOST_PROVIDER'; on the host the export phase needs HOST_PROVIDER='Hot Aisle' or 'AMD AI Notebooks': the package names the platform (rule 11)"
      else fail "HOST_PROVIDER='Hot Aisle' or 'AMD AI Notebooks' (or the platform's name): the package names the platform (rule 11), not '$HOST_PROVIDER'"; rc=1; fi
    fi
    case "$gpu" in
      *MI300X*)
        # The MI300X in the label must be the probe's word (amd-smi static), never typed by hand.
        case "$(gpu_name)" in
          *MI300X*) ok "DEMO_GPU='$gpu' names the MI300X, as the probe did: the package earns the 'Precomputed on AMD MI300X' label if every draft is GEMMA_MODEL's from a batch with DEVICE=gpu" ;;
          *)
            if [ "$DRY" = 1 ] && [ ! -f "$GPU_NAME_FILE" ]; then note "dry-run: DEMO_GPU='$gpu' names the MI300X and no GPU is probed here; on the host the name must be the probe's (amd-smi static), never typed by hand (rule 13)"
            else fail "DEMO_GPU/HOST_PROVIDER say MI300X but the probe named '$(gpu_name)': the MI300X label comes from amd-smi, never by hand (rule 13). On another AMD GPU: REHEARSAL=1 LLM_MODE=vllm DEVICE=gpu bash scripts/release/07-host-session.sh export (a rehearsal of Gemma's drafts, labelled as one)"; rc=1; fi ;;
        esac ;;
      *)
        if [ "$DRY" = 1 ] && [ -z "${DEMO_GPU:-}" ] && [ ! -f "$GPU_NAME_FILE" ]; then
          note "dry-run: no GPU name probed here; on the host DEMO_GPU defaults to the name of probe.txt plus HOST_PROVIDER, and must contain MI300X for the MI300X label (else REHEARSAL=1 LLM_MODE=vllm DEVICE=gpu: a rehearsal of Gemma's drafts)"
        else
          fail "DEMO_GPU='$gpu' does not name the MI300X: apps/api/services/demo_package.py labels a package MI300X only when generated_on contains 'MI300X'. On another AMD GPU the honest package is a rehearsal of Gemma's drafts: REHEARSAL=1 LLM_MODE=vllm DEVICE=gpu bash scripts/release/07-host-session.sh export (drafts by GEMMA_MODEL with device gpu, packed with --rehearsal and labelled 'Precomputed in a rehearsal, not verified on AMD MI300X'); a third label naming that GPU needs a schema change (CachedLabel in apps/api/schemas.py, the contract and the UI), not a session"; rc=1
        fi ;;
    esac
  fi
  if [ -f bench/results/latest-gpu.json ]; then ok "bench/results/latest-gpu.json names $(python3 -c 'import json; print(json.load(open("bench/results/latest-gpu.json")).get("run_id"))'): the package carries that GPU run"
  else warn "no bench/results/latest-gpu.json: run the bench phase (bench-merge) before demo-export, or the package carries no GPU benchmark (a CPU run is packed and labelled, never served as latest)"; fi
  return "$rc"
}

check_bench() {
  local rc=0 f head old
  for f in data/raw/asos_customers.parquet data/raw/asos_events.parquet data/raw/asos_variants.parquet; do
    [ -f "$f" ] || { fail "$f missing (B1 reads the ASOS tables): on the laptop make asos, then copy data/raw/asos_*.parquet to ReturnRing/data/raw/ on this host (about 69 MB)"; rc=1; }
  done
  [ -d data/truth ] && warn "data/truth exists on this host: rule 12 says it never leaves the laptop; never copy it; remove it by hand before leaving (this script deletes nothing)"
  need_venv "the benchmarks" || rc=1
  if [ "$BENCH_RUN_FROM" = file ]; then note "BENCH_RUN=$BENCH_RUN reused from $HOST_LOGS/BENCH_RUN (the parts already in artifacts/bench/$BENCH_RUN stay; BENCH_RUN=<new id> starts another run)"
  elif [ "$BENCH_RUN_FROM" = env ]; then note "BENCH_RUN=$BENCH_RUN from the environment, stored in $HOST_LOGS/BENCH_RUN"
  else BENCH_RUN="$(date -u +%Y%m%dT%H%M%SZ)-gpu"; BENCH_RUN_FROM=new; note "BENCH_RUN=$BENCH_RUN created, stored in $HOST_LOGS/BENCH_RUN"; fi
  printf '%s\n' "$BENCH_RUN" > "$HOST_LOGS/BENCH_RUN"
  head="$(git rev-parse HEAD 2>/dev/null || true)"
  if [ -f "$HOST_LOGS/BENCH_RUN.commit" ] && [ "$(cut -d' ' -f1 "$HOST_LOGS/BENCH_RUN.commit")" = "$BENCH_RUN" ]; then
    old="$(cut -d' ' -f2 "$HOST_LOGS/BENCH_RUN.commit")"
    if [ -n "$head" ] && [ "$old" != "$head" ]; then
      warn "BENCH_RUN=$BENCH_RUN was started on commit ${old:0:12}; HEAD is now ${head:0:12}: the merged run file would name a commit the parts did not run on (rule 13). Check out ${old:0:12} for the merge, or BENCH_RUN=<new id> and measure again"
    fi
  else printf '%s %s\n' "$BENCH_RUN" "$head" > "$HOST_LOGS/BENCH_RUN.commit"; fi
  export BENCH_RUN
  note "BENCH_ARGS='${BENCH_ARGS}' (e.g. \"--b1-repeats 1\" for a short session)"
  if amd_smi_present; then ok "amd-smi on the host: bench-b4 samples it (bench.amdsmi, every 5 s)"
  else warn "no amd-smi on this host (PATH includes /opt/rocm/bin): bench-b4 runs with --b4-sampler inprocess, which calls amd-smi too; every sample is then recorded as an error and B4 reports no GPU readings (rule 13: say so in the results)"; fi
  return "$rc"
}

check_live() {
  local rc=0
  serving_check || rc=1
  need_venv "uvicorn and the walk" || rc=1
  [ -x "$VENV/bin/uvicorn" ] || [ "$DRY" = 1 ] || { fail "$VENV/bin/uvicorn missing: the env phase installs requirements/dev.txt (uvicorn is in requirements/base.txt)"; rc=1; }
  if [ "$DRY" = 0 ] && ! pid_alive "$API_PID" && ss -ltn 2>/dev/null | grep -q ":$API_PORT "; then
    fail "port $API_PORT is taken by a process this script did not start (ss -ltnp); API_PORT=<other port> or stop it by hand"; rc=1
  fi
  note "DATABASE_URL=$(redact "$DATABASE_URL"); LLM_MODE=$LLM_MODE DEVICE=$DEVICE DEMO_MODE=live PUBLIC_READ_ONLY=off (the walk and the video write)"
  return "$rc"
}

check_down() {
  local v f
  v="$(env_value HF_TOKEN)"
  [ -z "$v" ] || warn "$ENV_FILE has a value in HF_TOKEN: empty it before leaving the host (sed -i 's/^HF_TOKEN=.*/HF_TOKEN=/' $ENV_FILE); a notebook's home folder persists between sessions"
  [ -d data/truth ] && warn "data/truth exists on this host (rule 12: it never leaves the laptop): rm -rf data/truth by hand; this script deletes nothing"
  for f in "$HOME/.git-credentials" "$HOME/.netrc" "$HOME/.config/gh/hosts.yml"; do
    [ -f "$f" ] && warn "$f exists: remove it before leaving the host"
  done
  return 0
}

check_phase() {
  local rc=0
  check_common || rc=1
  case "$1" in
    env) need_python312 || rc=1 ;;
    vllm) check_vllm_phase || rc=1 ;;
    spikes) check_spikes || rc=1 ;;
    export) check_export || rc=1 ;;
    bench) check_bench || rc=1 ;;
    live) check_live || rc=1 ;;
    down) check_down || rc=1 ;;
  esac
  return "$rc"
}

check_target() {  # <phase> <target>
  local rc=0
  case "$2" in
    spike-g7|spike-g1|spike-g1-bench|spike-g6) need_vllm_up "$2" || rc=1 ;;
    spike-g5)
      need_vllm_up "$2" || rc=1
      if ! photo_check; then
        if [ "$DRY" = 1 ]; then note "$PHOTO_PROBLEM"
        else fail "$PHOTO_PROBLEM"; fail "spike-g5 cannot run without the photo: copy it and run again, or --skip spike-g5 (plan B of spec 13: inspection out of scope)"; rc=1; fi
      fi ;;
    spike-g2) need_venv_amd "$2" "XGBoost on the CPU: --skip spike-g2,spike-g4 and record it" || rc=1 ;;
    spike-g4)
      need_vllm_up "$2" || rc=1
      need_venv_amd "$2 (its XGBoost job)" "--skip spike-g4 and record it (no job to run next to vLLM without amd_xgboost)" || rc=1 ;;
    reset|live-reset) need_vllm_up "$2 (every open return is drafted through vLLM, LLM_CONCURRENCY calls at once)" || rc=1 ;;
    batch-generate)
      if [ "$REHEARSAL" = 1 ] && [ "$FAKE_VLLM" != 1 ] && [ "$LLM_MODE" != vllm ]; then
        note "REHEARSAL=1 without FAKE_VLLM=1: python -m apps.api.batch_generate refuses LLM_MODE=$LLM_MODE (exit 2, by design: the cache holds vLLM outputs); the target records that and the package is packed without drafts (--allow-empty-cache)"
      else need_vllm_up "$2" || rc=1; fi ;;
    demo-export)
      local n; n="$(find "$SERVING_DIR/llm_cache" -name '*.json' -type f 2>/dev/null | wc -l)"
      if [ "$n" -gt 0 ]; then ok "$SERVING_DIR/llm_cache: $n drafts"
      elif [ "$REHEARSAL" = 1 ]; then note "$SERVING_DIR/llm_cache is empty: a rehearsal package without drafts (--allow-empty-cache)"
      elif [ "$DRY" = 1 ]; then note "dry-run: $SERVING_DIR/llm_cache is empty here; on the host the run would stop before demo-export unless batch-generate wrote the drafts"
      else fail "$SERVING_DIR/llm_cache is empty: batch-generate wrote no draft, and demo-export refuses an empty cache (REHEARSAL=1 is for a rehearsal, never for the MI300X package)"; rc=1; fi ;;
    bench-b1)
      need_vllm_down "$2" || rc=1
      need_venv_amd "$2 (amd_xgboost on the GPU)" "--skip bench-b1 and record B1 as not measured" || rc=1 ;;
    bench-b4) need_vllm_up "$2" || rc=1 ;;
    bench-b3)
      need_vllm_up "$2" || rc=1
      if [ -f "$PHASE_DIR/bench-b4.skipped" ]; then
        fail "bench-b4 was skipped: B3 reads its cases from the store with scores that B4 generates in bench-work/$BENCH_RUN (bench/__main__.py falls back to data/, which needs store/ and scores/). Run bench-b4 first (--only bench-b4), or --skip bench-b3 too and record both as not measured (rule 13)"; rc=1
      fi ;;
    api-up) if [ "$DRY" = 0 ] && pid_alive "$API_PID"; then note "uvicorn is running (pid $(cat "$API_PID")): stopped and started again, so the environment of this run applies"; fi ;;
    walk) if [ "$DRY" = 0 ] && ! curl -sf "$API_URL/healthz" >/dev/null 2>&1; then fail "the API does not answer on $API_URL (api-up, readyz first)"; rc=1; fi ;;
  esac
  return "$rc"
}

need_python312() {
  local cand
  for cand in python3.12 python3.13 python3; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      PY312="$(command -v "$cand")"; ok "python for the venv: $PY312 ($("$cand" --version 2>&1))"; return 0
    fi
  done
  if [ "$DRY" = 1 ]; then note "dry-run: no Python 3.12 or later here"; PY312=python3; return 0; fi
  fail "Python 3.12 or later is required (apps/api, requirements pinned on 3.12) and none of python3.12, python3.13, python3 is: on a notebook, conda create -n rr python=3.12 (if conda is there) or uv python install 3.12; else plan B: vLLM here, the API on the laptop through ssh -L 8001:127.0.0.1:8001 (VLLM_BASE_URL=http://127.0.0.1:8001/v1), recorded as such"
  return 1
}

# ---- the vllm command --------------------------------------------------------------------
# vllm_cmd_line: the vllm command of this host as one line, from VLLM_BIN, the PATH, the stored
# record of the env phase or VLLM_PYTHON (python -m vllm.entrypoints.cli.main); empty if none.
vllm_cmd_line() {
  if [ -n "$VLLM_BIN" ]; then printf '%s' "$VLLM_BIN"; return 0; fi
  if [ -f "$HOST_LOGS/vllm-python.txt" ]; then
    local bin; bin="$(sed -n 's/^bin=//p' "$HOST_LOGS/vllm-python.txt")"
    if [ -n "$bin" ]; then printf '%s' "$bin"; return 0; fi
    local py; py="$(sed -n 's/^python=//p' "$HOST_LOGS/vllm-python.txt")"
    if [ -n "$py" ]; then printf '%s -m vllm.entrypoints.cli.main' "$py"; return 0; fi
  fi
  if command -v vllm >/dev/null 2>&1; then command -v vllm; return 0; fi
  if [ -n "$VLLM_PYTHON" ] && "$VLLM_PYTHON" -c 'import vllm' >/dev/null 2>&1; then printf '%s -m vllm.entrypoints.cli.main' "$VLLM_PYTHON"; return 0; fi
  printf ''
}
vllm_python() {  # the python that imports vllm (for the version and huggingface_hub), or empty
  if [ -n "$VLLM_PYTHON" ]; then printf '%s' "$VLLM_PYTHON"; return 0; fi
  if [ -f "$HOST_LOGS/vllm-python.txt" ]; then sed -n 's/^python=//p' "$HOST_LOGS/vllm-python.txt"; return 0; fi
  printf ''
}
hub_python() {  # the first python that imports huggingface_hub: vllm's, the venv's, python3
  local cand
  for cand in "$(vllm_python)" "$PY" python3; do
    [ -n "$cand" ] || continue
    if "$cand" -c 'import huggingface_hub' >/dev/null 2>&1; then printf '%s' "$cand"; return 0; fi
  done
  printf ''
}
template_path() {  # the chat template file to serve with: an absolute path (compose comment, runbook 4.2)
  if [ -n "$VLLM_CHAT_TEMPLATE" ]; then
    case "$VLLM_CHAT_TEMPLATE" in /*) printf '%s' "$VLLM_CHAT_TEMPLATE" ;; *) printf '%s/%s' "$ROOT" "$VLLM_CHAT_TEMPLATE" ;; esac
  else printf '%s/%s/%s' "$ROOT" "$SPIKES" "$TEMPLATE_NAME"; fi
}
# vllm_serve_argv: the arguments of the vllm service of infra/docker-compose.yml, in its order.
# One addition: --host=VLLM_HOST (loopback), the host equivalent of "no published port" (NFR-11).
vllm_serve_argv() {
  VLLM_ARGV=(serve "$GEMMA_MODEL"
    "--revision=$GEMMA_REVISION" "--tokenizer-revision=$GEMMA_REVISION"
    "--port=$VLLM_PORT" --gpu-memory-utilization=0.55 --max-model-len=16384
    "--api-key=$VLLM_API_KEY" --enable-auto-tool-choice --tool-call-parser=gemma4
    "--chat-template=$(template_path)" '--limit-mm-per-prompt={"image": 1, "audio": 0}'
    "--host=$VLLM_HOST")
  # shellcheck disable=SC2206 - VLLM_EXTRA_ARGS is a list of plain flags, split on purpose
  [ -z "$VLLM_EXTRA_ARGS" ] || VLLM_ARGV+=($VLLM_EXTRA_ARGS)
}
start_vllm() {  # <start-file...>: writes the start time to each file, starts vllm serve
  local f cmd
  cmd="$(vllm_cmd_line)"
  if [ -z "$cmd" ]; then
    if [ "$DRY" = 1 ]; then note "dry-run: no vllm here; 'vllm' stands for the command the env phase records (VLLM_BIN or VLLM_PYTHON -m vllm.entrypoints.cli.main)"; cmd=vllm
    else fail "no vllm command (VLLM_BIN, VLLM_PYTHON, or the env phase)"; return 1; fi
  fi
  mkdir -p "$SPIKES/logs"
  for f in "$@"; do date -u +%Y-%m-%dT%H:%M:%SZ > "$f"; done
  rm -f "$SPIKES/logs/vllm-healthy.txt"
  vllm_serve_argv
  # shellcheck disable=SC2086 - cmd may be "python -m vllm.entrypoints.cli.main": split on purpose
  HF_HUB_OFFLINE=1 do_start "$VLLM_PID" "$VLLM_LOG" $cmd "${VLLM_ARGV[@]}"
}
wait_vllm() {  # polls /health every 15 s, up to VLLM_WAIT_S; writes vllm-healthy.txt
  local t=0 started healthy
  if [ "$DRY" = 1 ]; then note "dry-run: would poll http://$VLLM_HOST:$VLLM_PORT/health every 15 s for up to $VLLM_WAIT_S s (the API key in the Authorization header), write $SPIKES/logs/vllm-healthy.txt and the start-to-healthy seconds; on a dead process, the last 100 lines of $VLLM_LOG"; return 0; fi
  printf 'waiting for http://%s:%s/health (up to %ss) ' "$VLLM_HOST" "$VLLM_PORT" "$VLLM_WAIT_S"
  until vllm_healthy; do
    if ! pid_alive "$VLLM_PID"; then printf '\n'; fail "vllm is not running: the last 100 lines of $VLLM_LOG"; tail -100 "$VLLM_LOG"; return 1; fi
    if [ "$t" -ge "$VLLM_WAIT_S" ]; then printf '\n'; fail "vllm not healthy after $VLLM_WAIT_S s: the last 100 lines of $VLLM_LOG"; tail -100 "$VLLM_LOG"; return 1; fi
    sleep 15; t=$(( t + 15 )); printf '.'
  done
  printf '\n'
  date -u +%Y-%m-%dT%H:%M:%SZ > "$SPIKES/logs/vllm-healthy.txt"
  started="$(cat "$SPIKES/logs/vllm-start.txt" 2>/dev/null || true)"
  healthy="$(cat "$SPIKES/logs/vllm-healthy.txt")"
  if [ -n "$started" ]; then
    printf 'vllm healthy: started %s, healthy %s, %s s from start to healthy (measured on this host; the weights were %s)\n' \
      "$started" "$healthy" "$(( $(date -u -d "$healthy" +%s) - $(date -u -d "$started" +%s) ))" "$(grep -q cached "$SPIKES/logs/weights-status.txt" 2>/dev/null && printf 'already in HF_HOME' || printf 'downloaded in this session')"
  else printf 'vllm healthy: %s (no start time recorded: started outside this script)\n' "$healthy"; fi
}

# ---- the targets -------------------------------------------------------------------------
probe_cmd() {  # <label> <argv...>: one line in probe.txt, never fails
  local label="$1"; shift
  if [ "$DRY" = 1 ]; then printf 'dry-run: would run (%s): %s\n' "$label" "$*"; return 0; fi
  printf '\n## %s: %s\n' "$label" "$*"
  if ! command -v "$1" >/dev/null 2>&1; then printf 'not found: %s\n' "$1"; return 0; fi
  timeout 30 "$@" 2>&1 | head -60 || true
  return 0
}
probe_url() {  # <label> <url>
  if [ "$DRY" = 1 ]; then printf 'dry-run: would fetch (%s): HEAD %s\n' "$1" "$2"; return 0; fi
  printf '\n## %s: %s -> HTTP %s\n' "$1" "$2" "$(curl -sS -m 15 -o /dev/null -w '%{http_code}' "$2" 2>&1 | tail -1)"
}
gpu_name_probe() {  # the market name of GPU 0 from amd-smi, rocm-smi or rocminfo; empty if none
  local name=""
  if amd_smi_present; then
    name="$(timeout 30 amd-smi static --json 2>/dev/null | python3 -c '
import json, sys
text = sys.stdin.read()
starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
if starts:
    data = json.loads(text[min(starts):])
    gpus = data.get("gpu_data", data) if isinstance(data, dict) else data
    gpus = [gpus] if isinstance(gpus, dict) else gpus
    for g in gpus:
        asic = g.get("asic") if isinstance(g, dict) else None
        if isinstance(asic, dict) and asic.get("market_name"):
            print(str(asic["market_name"]).strip()); break
' 2>/dev/null || true)"
  fi
  if [ -z "$name" ] && command -v rocm-smi >/dev/null 2>&1; then
    name="$(timeout 30 rocm-smi --showproductname 2>/dev/null | grep -i -m1 'Card series' | sed 's/.*:[[:space:]]*//' || true)"
  fi
  if [ -z "$name" ] && command -v rocminfo >/dev/null 2>&1; then
    name="$(timeout 30 rocminfo 2>/dev/null | awk '/Marketing Name/ { sub(/.*Marketing Name:[ \t]*/, ""); n = $0 } /Device Type:.*GPU/ { print n; exit }' || true)"
  fi
  printf '%s' "$name"
}
probe() {
  local out="$HOST_LOGS/probe.txt" name
  if [ "$DRY" = 1 ]; then
    note "dry-run: the probe is read-only (safe to run for real at any time); it would write $out and $GPU_NAME_FILE from:"
    out=/dev/null
  fi
  {
    printf '# ReturnRing host probe, %s, %s @ %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" "$(git rev-parse --short HEAD 2>/dev/null)"
    probe_cmd hostname hostname
    probe_cmd kernel uname -r
    probe_cmd os sh -c '. /etc/os-release && echo "$PRETTY_NAME"'
    probe_cmd user id
    probe_cmd nproc nproc
    probe_cmd memory free -g
    probe_cmd "disk (repository)" df -h "$ROOT"
    probe_cmd "disk (HF_HOME=$HF_HOME)" df -h "$(if [ -d "$HF_HOME" ]; then printf '%s' "$HF_HOME"; else dirname "$HF_HOME"; fi)"
    probe_cmd "GPU devices" ls -l /dev/kfd /dev/dri
    probe_cmd "amdgpu module" cat /sys/module/amdgpu/version
    probe_cmd "ROCm version" cat /opt/rocm/.info/version
    probe_cmd "amd-smi version" amd-smi version
    probe_cmd "amd-smi static (name, VRAM)" amd-smi static --json
    probe_cmd "rocm-smi product name" rocm-smi --showproductname
    probe_cmd "rocminfo (gfx target)" sh -c 'rocminfo | grep -m1 -o "gfx[0-9a-f]*"'
    probe_cmd "GPU visibility env" sh -c 'env | grep -E "^(HIP|ROCR|CUDA)_VISIBLE_DEVICES=" || echo "none set"'
    probe_cmd python3 python3 --version
    probe_cmd "python3 path" sh -c 'command -v python3'
    probe_cmd pip python3 -m pip --version
    probe_cmd "python3.12" python3.12 --version
    probe_cmd "conda" conda --version
    probe_cmd "vllm on PATH" sh -c 'command -v vllm && vllm --version'
    probe_cmd "vllm importable" python3 -c 'import vllm; print("vllm", vllm.__version__, vllm.__file__)'
    probe_cmd "torch (ROCm build?)" python3 -c 'import torch; print("torch", torch.__version__, "hip", torch.version.hip, "gpu", torch.cuda.is_available())'
    probe_cmd "huggingface_hub importable" python3 -c 'import huggingface_hub; print("huggingface_hub", huggingface_hub.__version__)'
    probe_cmd "weights cached?" sh -c "d='$HF_HOME/hub/models--${GEMMA_MODEL//\//--}/snapshots/$GEMMA_REVISION'; if [ -d \"\$d\" ]; then ls \"\$d\" | head -5; du -sh \"\$d/\"; else echo 'not in HF_HOME'; fi"
    probe_cmd "proxy env" sh -c 'env | grep -iE "^(https?_proxy|no_proxy)=" | sed "s#//.*@#//<redacted>@#" || echo "none"'
    probe_url "huggingface.co" "https://huggingface.co/api/models/$GEMMA_MODEL"
    probe_url "pypi.org" "https://pypi.org/simple/"
    probe_url "pypi.amd.com" "${AMD_PYPI_INDEX_URL:-https://pypi.amd.com/rocm-7.2.3/simple}/"
    probe_url "github (chat template)" "https://raw.githubusercontent.com/vllm-project/vllm/main/examples/$TEMPLATE_NAME"
    probe_cmd "docker binary" sh -c 'command -v docker'
    probe_cmd "docker daemon" docker info --format '{{.ServerVersion}}'
    probe_cmd "sshd" sh -c 'pgrep -x sshd >/dev/null && echo "sshd running" || echo "no sshd process"; ss -ltn 2>/dev/null | grep ":22 " || echo "nothing listens on 22"'
    probe_cmd "this shell over SSH?" sh -c '[ -n "$SSH_CONNECTION" ] && echo "yes (SSH_CONNECTION set)" || echo "no (a notebook terminal?)"'
    probe_cmd "tmux" sh -c 'command -v tmux && tmux -V'
    probe_cmd "cloudflared" sh -c 'command -v cloudflared && cloudflared --version'
    probe_cmd "listeners $VLLM_PORT/$API_PORT" sh -c "ss -ltnp 2>/dev/null | grep -E ':($VLLM_PORT|$API_PORT) ' || echo 'none'"
    probe_cmd "uptime" uptime
  } | tee "$out"
  if [ "$DRY" = 1 ]; then return 0; fi
  name="$(gpu_name_probe)"
  if [ -n "$name" ]; then printf '%s\n' "$name" > "$GPU_NAME_FILE"; ok "GPU: $name (stored in $GPU_NAME_FILE; DEMO_GPU defaults to '$(demo_gpu)')"
  else warn "no GPU name from amd-smi, rocm-smi or rocminfo: the export phase cannot earn the MI300X label (it needs the probed name, never a DEMO_GPU typed by hand: rule 13); REHEARSAL=1 LLM_MODE=vllm DEVICE=gpu packs a rehearsal of Gemma's drafts; the bench results name no GPU (write the name in docs/results by hand, rule 13)"; fi
  ok "probe written to $out (send it with the logs)"
  return 0
}

venv() {
  need_python312 || return 1
  if [ ! -x "$PY" ]; then do_run "$PY312" -m venv "$VENV" || return 1; fi
  do_run "$VENV/bin/pip" install -q --disable-pip-version-check -r requirements/dev.txt || return 1
  if [ "$DRY" = 1 ]; then return 0; fi
  ok "$VENV: $("$PY" --version 2>&1), $("$VENV/bin/pip" list --disable-pip-version-check 2>/dev/null | grep -ciE '^(fastapi|uvicorn|xgboost-cpu|httpx|jsonschema) ') of the 5 packages the session needs"
  "$PY" -c 'import apps.api.main, bench, scripts.demo_walk, scripts.demo_export; print("imports ok: apps.api.main, bench, scripts.demo_walk, scripts.demo_export")' || return 1
}

vllm_python_target() {
  local bin="" py="" tried=""
  if [ -n "$VLLM_BIN" ]; then bin="$VLLM_BIN"; tried="VLLM_BIN=$VLLM_BIN"
  elif command -v vllm >/dev/null 2>&1; then bin="$(command -v vllm)"; tried="vllm on the PATH"
  else tried="no vllm on the PATH"; fi
  if [ -n "$VLLM_PYTHON" ]; then py="$VLLM_PYTHON"
  elif [ -n "$bin" ] && head -1 "$bin" 2>/dev/null | grep -q '^#!'; then py="$(head -1 "$bin" | sed 's/^#![[:space:]]*//' | awk '{print $1}')"; fi
  if [ -z "$py" ]; then
    local cand
    for cand in python3 "$PY" /opt/venv/bin/python /opt/conda/bin/python; do
      [ -x "$cand" ] || command -v "$cand" >/dev/null 2>&1 || continue
      if "$cand" -c 'import vllm' >/dev/null 2>&1; then py="$cand"; break; fi
    done
  fi
  if [ "$DRY" = 1 ]; then
    note "dry-run: would record bin=${bin:-<none>} python=${py:-<none>} in $HOST_LOGS/vllm-python.txt ($tried)"
    return 0
  fi
  if [ -n "$py" ] && "$py" -c 'import vllm' >/dev/null 2>&1; then
    printf 'bin=%s\npython=%s\nversion=%s\n' "$bin" "$py" "$("$py" -c 'import vllm; print(vllm.__version__)' 2>/dev/null)" > "$HOST_LOGS/vllm-python.txt"
    ok "vllm: $(tr '\n' ' ' < "$HOST_LOGS/vllm-python.txt")"
    "$py" -c 'import torch; print("torch", torch.__version__, "hip", torch.version.hip, "gpu visible", torch.cuda.is_available())' 2>&1 | tail -1 || true
    return 0
  fi
  printf 'bin=%s\npython=\nversion=\n' "$bin" > "$HOST_LOGS/vllm-python.txt"
  warn "vllm is not importable here ($tried; VLLM_PYTHON=${VLLM_PYTHON:-unset}). Things to try, in order (each unverified on this host):"
  cat <<'EOF'
  1. The image's own vLLM: ls /opt/venv/bin /opt/conda/envs 2>/dev/null; which -a vllm python3; a kernel of
     the notebook named vllm or rocm; then VLLM_BIN=<that vllm> or VLLM_PYTHON=<that python> on the next run.
  2. Do NOT pip install vllm from pypi.org: that wheel is the CUDA build and does not run on ROCm.
  3. A ROCm build of vLLM: AMD documents the rocm/vllm Docker images (not usable here) and a build from
     source (hours of paid time: not on the VM); pypi.amd.com/rocm-7.2.3/simple/vllm/ answered 403 on
     16 Oct 2026 (no vllm project visible there). Ask the provider which image ships vLLM for ROCm.
  4. Plan B: a host that ships vLLM (the notebook image with vLLM, the droplet with Docker).
EOF
  return 0
}

venv_amd() {
  local planb="$HOST_LOGS/env/venv-amd.planb" lib
  rm -f "$planb"
  if [ -z "$AMD_PYPI_INDEX_URL" ]; then
    warn "AMD_PYPI_INDEX_URL empty in $ENV_FILE: amd_xgboost cannot be installed; SPK-G2 plan B (XGBoost on the CPU) recorded"
    [ "$DRY" = 1 ] || printf 'AMD_PYPI_INDEX_URL empty\n' > "$planb"; return 0
  fi
  if [ ! -x "$PY_AMD" ]; then need_python312 || return 1; do_run "$PY312" -m venv "$VENV_AMD" || return 1; fi
  # The two steps of infra/docker/trainer-gbm.Dockerfile and bench.Dockerfile: amd_xgboost alone,
  # AMD index only, wheel hash pinned, no dependencies; then everything else from PyPI, pinned,
  # --no-deps, pip check. Never xgboost or xgboost-cpu here: the same module, it would overwrite
  # the ROCm build (which is why this is a second venv, apart from requirements/dev.txt).
  if ! do_run "$VENV_AMD/bin/pip" install -q --disable-pip-version-check --no-cache-dir --no-deps --require-hashes --index-url "$AMD_PYPI_INDEX_URL" -r infra/docker/requirements-gbm-amd.txt; then
    warn "amd_xgboost did not install from $AMD_PYPI_INDEX_URL (unreachable, 403, hash mismatch): SPK-G2 plan B (XGBoost on the CPU) recorded; B1 and spike-g4 need --skip"
    [ "$DRY" = 1 ] || printf 'pip install of amd_xgboost failed (see env/venv-amd.log)\n' > "$planb"; return 0
  fi
  if ! do_run "$VENV_AMD/bin/pip" install -q --disable-pip-version-check --no-cache-dir --no-deps --index-url https://pypi.org/simple -r infra/docker/requirements-bench.txt; then
    warn "the bench stack did not install from PyPI into $VENV_AMD: SPK-G2 plan B recorded"
    [ "$DRY" = 1 ] || printf 'pip install of requirements-bench.txt failed\n' > "$planb"; return 0
  fi
  do_run "$VENV_AMD/bin/pip" check || warn "pip check reports a conflict in $VENV_AMD (recorded, not a stop)"
  if [ "$DRY" = 1 ]; then return 0; fi
  lib="$("$PY_AMD" -c "import importlib.util as u, pathlib; print(pathlib.Path(u.find_spec('xgboost').origin).parent / 'lib' / 'libxgboost.so')" 2>/dev/null || true)"
  if [ -z "$lib" ] || [ ! -f "$lib" ]; then
    warn "libxgboost.so not found in $VENV_AMD: SPK-G2 plan B recorded"; printf 'libxgboost.so missing\n' > "$planb"; return 0
  fi
  if ldd "$lib" | grep -q 'not found'; then
    warn "libxgboost.so misses ROCm libraries on this host (the wheel is built for ROCm 7.2.3; this host's ROCm: $(cat /opt/rocm/.info/version 2>/dev/null || echo unknown)); the AMD index also publishes rocm-7.1.1 and rocm-7.0.2 builds with other hashes (infra/docker/requirements-gbm-amd.txt: change index and hash together, by hand, and record it). SPK-G2 plan B recorded"
    ldd "$lib" | grep 'not found' | head -10
    printf 'libxgboost.so: ROCm libraries not found (ldd)\n' > "$planb"; return 0
  fi
  if "$PY_AMD" -c "import xgboost as x; assert x.build_info().get('USE_HIP'), x.build_info(); print('xgboost', x.__version__, 'USE_HIP', 'rocm', open('/opt/rocm/.info/version').read().strip() if __import__('os').path.exists('/opt/rocm/.info/version') else None)"; then
    ok "$VENV_AMD: amd_xgboost with USE_HIP, the bench stack installed (pip check above)"
  else
    warn "the xgboost of $VENV_AMD is not the HIP build: SPK-G2 plan B recorded"; printf 'xgboost without USE_HIP\n' > "$planb"
  fi
  return 0
}

weights() {
  local hubpy status="$SPIKES/logs/weights-status.txt" snap="$HF_HOME/hub/models--${GEMMA_MODEL//\//--}/snapshots/$GEMMA_REVISION"
  mkdir -p "$SPIKES/logs"
  hubpy="$(hub_python)"
  if [ -z "$hubpy" ]; then
    if [ "$DRY" = 1 ]; then note "dry-run: no python with huggingface_hub here; on the host it would be installed into $VENV (pip install huggingface_hub, Apache-2.0, a session tool, not a product dependency)"; hubpy="$PY"
    else
      note "no python with huggingface_hub (vllm's, the venv's, python3): installing it into $VENV"
      do_run "$VENV/bin/pip" install -q --disable-pip-version-check huggingface_hub || return 1
      hubpy="$PY"
    fi
  fi
  if [ "$DRY" = 1 ]; then
    note "dry-run: would check the cache with HF_HUB_OFFLINE=1 $hubpy -c 'huggingface_hub.snapshot_download(\"$GEMMA_MODEL\", revision=\"$GEMMA_REVISION\")' (HF_HOME=$HF_HOME; snapshot $snap)"
    note "dry-run: if not cached, would start the download in the background (setsid; HF_HUB_OFFLINE=0, HF_TOKEN from $ENV_FILE if set) with the same call, log $SPIKES/logs/download-weights.log, status $status ('download-weights: exit N, took S s (model@revision)', as infra/spikes/weights.sh writes it)"
    return 0
  fi
  if HF_HUB_OFFLINE=1 "$hubpy" -c 'import sys, huggingface_hub as h; print(h.snapshot_download(sys.argv[1], revision=sys.argv[2]))' "$GEMMA_MODEL" "$GEMMA_REVISION" 2>/dev/null; then
    printf 'download-weights: cached, took 0 s (%s@%s; already in %s)\n' "$GEMMA_MODEL" "$GEMMA_REVISION" "$HF_HOME" | tee "$status"
    return 0
  fi
  if pid_alive "$HOST_LOGS/weights.pid"; then note "a download is already running (pid $(cat "$HOST_LOGS/weights.pid")): weights-wait polls it"; return 0; fi
  rm -f "$status"
  note "weights not cached under $HF_HOME: downloading $GEMMA_MODEL@$GEMMA_REVISION (62.6 GB for the 31B, 51.6 GB for the 26B-A4B; the time is measured into $status)"
  # The download survives this driver (setsid); the token, if any, is in its environment only.
  HF_HUB_OFFLINE=0 HF_TOKEN="$HF_TOKEN" setsid nohup bash -c '
    echo $$ > "$5"
    s=$(date +%s)
    "$1" -c "import sys, huggingface_hub as h; print(h.snapshot_download(sys.argv[1], revision=sys.argv[2]))" "$2" "$3"
    rc=$?
    echo "download-weights: exit $rc, took $(( $(date +%s) - s )) s ($2@$3)" > "$4"
  ' _ "$hubpy" "$GEMMA_MODEL" "$GEMMA_REVISION" "$status" "$HOST_LOGS/weights.pid" > "$SPIKES/logs/download-weights.log" 2>&1 < /dev/null &
  sleep 2
  ok "download started (log $SPIKES/logs/download-weights.log); weights-wait waits for it. Meanwhile the GPU is free: bash scripts/release/07-host-session.sh spikes --only spike-g2 in another terminal records SPK-G2 alone on the GPU"
}

weights_wait() {
  local status="$SPIKES/logs/weights-status.txt" i
  if [ "$DRY" = 1 ]; then note "dry-run: would poll $status every 15 s for up to 90 min and fail unless it says 'exit 0' or 'cached'"; return 0; fi
  for i in $(seq 1 360); do
    if [ -f "$status" ]; then
      cat "$status"
      if grep -Eq 'exit 0,|cached' "$status"; then return 0; fi
      fail "the download failed: tail $SPIKES/logs/download-weights.log"; tail -30 "$SPIKES/logs/download-weights.log"; return 1
    fi
    if ! pid_alive "$HOST_LOGS/weights.pid"; then fail "no download running and no status: --only weights again (tail $SPIKES/logs/download-weights.log)"; return 1; fi
    [ $(( i % 20 )) = 0 ] && printf 'still downloading (%d min): %s\n' $(( i / 4 )) "$(du -sh "$HF_HOME/hub" 2>/dev/null | cut -f1) in HF_HOME/hub"
    sleep 15
  done
  fail "weights download not finished after 90 min: tail $SPIKES/logs/download-weights.log"
  return 1
}

template() {
  local target="$SPIKES/$TEMPLATE_NAME" origin="$HOST_LOGS/vllm/template-origin.txt" py pkg found="" version
  mkdir -p "$SPIKES" "$HOST_LOGS/vllm"
  if [ -n "$VLLM_CHAT_TEMPLATE" ]; then
    [ -f "$VLLM_CHAT_TEMPLATE" ] || { fail "VLLM_CHAT_TEMPLATE=$VLLM_CHAT_TEMPLATE does not exist"; return 1; }
    ok "chat template: $VLLM_CHAT_TEMPLATE (VLLM_CHAT_TEMPLATE)"; printf 'VLLM_CHAT_TEMPLATE=%s\n' "$VLLM_CHAT_TEMPLATE" > "$origin"; return 0
  fi
  if [ -f "$target" ]; then ok "chat template already at $target ($(cat "$origin" 2>/dev/null || echo 'origin not recorded'))"; return 0; fi
  py="$(vllm_python)"
  if [ "$DRY" = 1 ]; then
    note "dry-run: would look for $TEMPLATE_NAME next to the installed vllm package (find \$(dirname vllm.__file__)/.. and the image folders /app/vllm/examples, /vllm-workspace/examples, /workspace/vllm/examples), copy it to $target, else download https://raw.githubusercontent.com/vllm-project/vllm/v<installed version>/examples/$TEMPLATE_NAME (the file exists at v0.31.0 and on main, checked 16 Oct 2026), recording the origin in $origin"
    return 0
  fi
  if [ -n "$py" ]; then
    pkg="$("$py" -c 'import os, vllm; print(os.path.dirname(vllm.__file__))' 2>/dev/null || true)"
    for d in "$pkg" "$pkg/.." "$pkg/../.." /app/vllm/examples /vllm-workspace/examples /workspace/vllm/examples; do
      [ -d "$d" ] || continue
      found="$(find "$d" -maxdepth 4 -name "$TEMPLATE_NAME" -type f 2>/dev/null | head -1)"
      [ -z "$found" ] || break
    done
    version="$("$py" -c 'import vllm; print(vllm.__version__)' 2>/dev/null || true)"
  fi
  if [ -n "$found" ]; then
    cp "$found" "$target"; printf 'copied from %s\n' "$found" > "$origin"; ok "chat template copied from $found to $target"; return 0
  fi
  version="${version:-0.31.0}"
  local url="https://raw.githubusercontent.com/vllm-project/vllm/v${version%%+*}/examples/$TEMPLATE_NAME"
  note "no $TEMPLATE_NAME in the installed package (a pip wheel ships no examples/): downloading it from the vLLM repository at the installed version"
  if do_run curl -fsSL -m 60 -o "$target.tmp" "$url"; then
    mv "$target.tmp" "$target"; printf 'downloaded from %s\n' "$url" > "$origin"; ok "chat template downloaded to $target ($(wc -c < "$target") bytes)"
    head -c 200 "$target" | grep -q . || { fail "the downloaded template is empty"; return 1; }
    return 0
  fi
  rm -f "$target.tmp"
  fail "no chat template: copy examples/$TEMPLATE_NAME of the vLLM repository (tag v$version) from the laptop to $target, or VLLM_CHAT_TEMPLATE=<path>"
  return 1
}

vllm_serve() {
  if [ "$DRY" = 0 ] && vllm_healthy; then
    note "vLLM is already healthy at $HOST_VLLM_BASE_URL: kept as it is (vllm-stop first to apply a change of $ENV_FILE or VLLM_EXTRA_ARGS; the earlier start and healthy times stay in $SPIKES/logs/)"
    return 0
  fi
  if [ "$DRY" = 0 ] && pid_alive "$VLLM_PID"; then
    note "vLLM started at $(cat "$SPIKES/logs/vllm-start.txt" 2>/dev/null || echo '?') is still loading (pid $(cat "$VLLM_PID")): vllm-wait polls it"
    return 0
  fi
  [ "$DRY" = 1 ] || [ -f "$(template_path)" ] || { fail "chat template $(template_path) missing: --only template first"; return 1; }
  start_vllm "$SPIKES/logs/vllm-start.txt"
}

first_request() {
  local out="$SPIKES/first-request-host.json"
  mkdir -p "$SPIKES" "$HOST_LOGS/vllm"
  # g1_guided_json.py refuses --runs below 2 (run 0 is its warm-up): run 0 is the first request
  # after healthy, and its elapsed_s (grammar compile included) is the number recorded here.
  VLLM_BASE_URL="$HOST_VLLM_BASE_URL" do_run "$PY" infra/spikes/g1_guided_json.py --runs 2 --out "$out" || return 1
  [ "$DRY" = 1 ] && return 0
  python3 - "$out" <<'EOF' | tee "$HOST_LOGS/vllm/first-request.txt"
import json, sys
r = json.load(open(sys.argv[1]))["runs"][0]
print(f"first guided-JSON request after healthy: {r['elapsed_s']} s (g1_guided_json.py run 0, grammar compile included; "
      f"finish_reason {r.get('finish_reason')}, {r.get('completion_tokens')} completion tokens)")
EOF
}

vllm_stop() { do_stop "$VLLM_PID" "vLLM"; }

spike_g2() {
  local context
  if [ "$DRY" = 0 ] && vllm_healthy; then context="vLLM serving at --gpu-memory-utilization=0.55 on the same GPU (host session, no Docker)"
  else context="alone on the GPU (host session, no Docker)"; fi
  mkdir -p "$SPIKES"
  do_run "$PY_AMD" infra/spikes/g2_xgboost_gpu.py --device gpu --context "$context" --out "$SPIKES/g2_gpu.json" || return $?
  do_run "$PY_AMD" infra/spikes/g2_xgboost_gpu.py --device cpu --context "alone on the CPU (host session; vLLM $(if [ "$DRY" = 0 ] && vllm_healthy; then echo running; else echo stopped; fi))" --gpu-result "$SPIKES/g2_gpu.json" --out "$SPIKES/g2_cpu.json" || return $?
  # The GPU model loads in xgboost-cpu 3.2.0 (spec 10): the venv of requirements/dev.txt has it.
  do_run "$PY" infra/spikes/g2_load_cpu.py --check "$SPIKES/g2_gpu_check.json" || return $?
}

spike_g7() {
  mkdir -p "$SPIKES"
  cat "$SPIKES/logs/weights-status.txt" "$SPIKES/logs/vllm-start.txt" "$SPIKES/logs/vllm-healthy.txt" 2>/dev/null || true
  if [ "$DRY" = 1 ]; then note "dry-run: would grep $VLLM_LOG for 'Time spent downloading weights|Loading weights took|Available KV cache memory|KV cache size|Maximum concurrency' into $SPIKES/g7-vllm-log.txt"; return 0; fi
  [ -f "$VLLM_LOG" ] || { fail "no $VLLM_LOG: vLLM was not started by this script"; return 1; }
  grep -E 'Time spent downloading weights|Loading weights took|Available KV cache memory|KV cache size|Maximum concurrency' "$VLLM_LOG" > "$SPIKES/g7-vllm-log.txt" || true
  cat "$SPIKES/g7-vllm-log.txt"
  [ -s "$SPIKES/g7-vllm-log.txt" ] || { fail "none of the expected lines in $VLLM_LOG (is vLLM healthy? the lines appear once the engine is up)"; return 1; }
  if [ -f "$SPIKES/logs/vllm-start.txt" ] && [ -f "$SPIKES/logs/vllm-healthy.txt" ]; then
    printf 'SPK-G7 on this host: start to healthy %s s; weights: %s\n' "$(( $(date -u -d "$(cat "$SPIKES/logs/vllm-healthy.txt")" +%s) - $(date -u -d "$(cat "$SPIKES/logs/vllm-start.txt")" +%s) ))" "$(cat "$SPIKES/logs/weights-status.txt" 2>/dev/null || echo 'no status')"
  fi
}

spike_g1() { mkdir -p "$SPIKES"; VLLM_BASE_URL="$HOST_VLLM_BASE_URL" do_run "$PY" infra/spikes/g1_guided_json.py --out "$SPIKES/g1.json"; }

spike_g1_bench() {
  local cmd tok c n log rc
  cmd="$(vllm_cmd_line)"
  if [ -z "$cmd" ]; then
    if [ "$DRY" = 1 ]; then note "dry-run: no vllm here; 'vllm' stands for the command the env phase records"; cmd=vllm
    else fail "no vllm command for 'vllm bench serve'"; return 1; fi
  fi
  mkdir -p "$SPIKES/logs"
  tok="$HF_HOME/hub/models--${GEMMA_MODEL//\//--}/snapshots/$GEMMA_REVISION"
  for c in 1 32; do
    if [ "$c" -eq 1 ]; then n=16; else n=64; fi
    log="$SPIKES/logs/g1-bench-c$c.txt"
    # shellcheck disable=SC2086
    rc=0
    OPENAI_API_KEY="$VLLM_API_KEY" do_run $cmd bench serve --backend openai-chat \
      --base-url "http://$VLLM_HOST:$VLLM_PORT" --endpoint /v1/chat/completions --model "$GEMMA_MODEL" \
      --tokenizer "$tok" --dataset-name random --random-input-len "${BENCH_IN:-1500}" --random-output-len "${BENCH_OUT:-400}" \
      --num-prompts "$n" --max-concurrency "$c" --num-warmups 2 --ignore-eos --temperature 0.2 \
      --percentile-metrics ttft,tpot,itl,e2el --metric-percentiles 50,95 > "$log" 2>&1 || rc=$?
    [ "$DRY" = 1 ] && continue
    cat "$log"
    [ "$rc" -eq 0 ] || return "$rc"
    grep -Eq "Successful requests: +$n\b" "$log" || { fail "not all $n requests succeeded (c=$c)"; return 1; }
  done
  [ "$DRY" = 1 ] || note "single-stream tokens/s is about 1000 / median TPOT in ms of g1-bench-c1.txt (D-12: below 15 tokens/s, plan B model)"
}

spike_g5() { mkdir -p "$SPIKES"; VLLM_BASE_URL="$HOST_VLLM_BASE_URL" do_run "$PY" infra/spikes/g1_guided_json.py --image "$IMAGE" --out "$SPIKES/g5.json"; }

spike_g6() {
  mkdir -p "$SPIKES"
  if VLLM_BASE_URL="$HOST_VLLM_BASE_URL" do_run "$PY" infra/spikes/g6_tool_call.py --mode auto --out "$SPIKES/g6-auto.json"; then return 0; fi
  note "spike-g6 in auto mode failed: the runbook's next step is --mode named (the function forced through structured outputs, no gemma4 tool parser)"
  VLLM_BASE_URL="$HOST_VLLM_BASE_URL" do_run "$PY" infra/spikes/g6_tool_call.py --mode named --out "$SPIKES/g6-named.json" || return 1
  note "record in docs/spike-results.md: mode that passed: named (auto failed, log release-logs/$HOST_REL/spikes/spike-g6.log)"
}

# SPK-G4 on the host: vLLM at 0.55 serves guided-JSON requests every POLL s while the XGBoost
# job trains on the same GPU for DURATION s; amd-smi sampled every 5 s when the host has it. The
# DGL job of infra/spikes/g4_coexistence.sh is absent (rocm/dgl needs Docker): recorded in the
# context and in the summary (plan B of spec 13, GraphSAGE as an upgrade).
spike_g4() {
  local sampler="" since_line=0 xgb n=0 llm_fail=0 xgb_rc vllm_rc errors
  mkdir -p "$SPIKES/logs"
  if [ "$DRY" = 1 ]; then
    note "dry-run: would run, with vLLM healthy: amd-smi metric --json + amd-smi process every 5 s into $SPIKES/g4_amdsmi.log (if amd-smi exists); $PY_AMD infra/spikes/g2_xgboost_gpu.py --device gpu --min-seconds $DURATION --context 'SPK-G4 (host, no Docker): vLLM serving; no DGL job' --out $SPIKES/g4_xgb.json in the background; every $POLL s $PY infra/spikes/g1_guided_json.py --runs 2 --out $SPIKES/g4_llm_N.json; then /health, and the error lines of $VLLM_LOG written since the start into $SPIKES/logs/g4_vllm_errors.log"
    return 0
  fi
  since_line="$(wc -l < "$VLLM_LOG" 2>/dev/null || echo 0)"
  if amd_smi_present; then
    (while true; do echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ)"; amd-smi metric --json 2>&1; amd-smi process 2>&1; sleep 5; done) > "$SPIKES/g4_amdsmi.log" 2>&1 < /dev/null &
    sampler=$!
  else note "amd-smi not found on the host: no GPU samples for SPK-G4 (rocm-smi is not parsed; record it)"; fi
  "$PY_AMD" infra/spikes/g2_xgboost_gpu.py --device gpu --min-seconds "$DURATION" \
    --context "SPK-G4 (host session, no Docker): vLLM serving at 0.55; no DGL job (rocm/dgl needs Docker)" \
    --out "$SPIKES/g4_xgb.json" > "$SPIKES/logs/g4_xgb.log" 2>&1 < /dev/null &
  xgb=$!
  while kill -0 "$xgb" 2>/dev/null; do
    sleep "$POLL"
    n=$(( n + 1 ))
    if VLLM_BASE_URL="$HOST_VLLM_BASE_URL" "$PY" infra/spikes/g1_guided_json.py --runs 2 --out "$SPIKES/g4_llm_$n.json" > "$SPIKES/logs/g4_llm_$n.log" 2>&1; then
      echo "guided-JSON request $n during training: pass"
    else echo "guided-JSON request $n during training: FAIL (see $SPIKES/logs/g4_llm_$n.log)"; llm_fail=1; fi
  done
  xgb_rc=0; wait "$xgb" || xgb_rc=$?   # errexit: never "cmd; rc=$?" (the function would stop here)
  [ -n "$sampler" ] && kill "$sampler" 2>/dev/null
  vllm_rc=0; vllm_healthy || vllm_rc=$?
  tail -n +"$(( since_line + 1 ))" "$VLLM_LOG" 2>/dev/null | grep -i -E 'out of memory|hipError|HIP error|Traceback' > "$SPIKES/logs/g4_vllm_errors.log" || true
  errors="$(wc -l < "$SPIKES/logs/g4_vllm_errors.log")"
  echo "XGBoost job exit $xgb_rc, LLM failures $llm_fail, vLLM health $vllm_rc, vLLM error lines since the start $errors; DGL job: not run (no rocm/dgl without Docker)"
  if [ "$xgb_rc" -eq 0 ] && [ "$llm_fail" -eq 0 ] && [ "$vllm_rc" -eq 0 ]; then echo "SPK-G4 (host, XGBoost half only): pass"; return 0; fi
  echo "SPK-G4: FAIL. Plan B (spec 13): train with vLLM stopped."
  return 1
}

fake_up() {
  if [ "$DRY" = 1 ]; then note "dry-run: would start bench/fake_vllm.py (FakeVllm, loopback, the API key of $ENV_FILE, model $FAKE_MODEL) in the background, its URL in $FAKE_URL"; return 0; fi
  if pid_alive "$FAKE_PID" && fake_healthy; then ok "fake vLLM already up at $(cat "$FAKE_URL")"; return 0; fi
  rm -f "$FAKE_URL"
  FAKE_API_KEY="$VLLM_API_KEY" FAKE_URL_FILE="$FAKE_URL" do_start "$FAKE_PID" "$FAKE_LOG" "$PY" -c '
import os, signal
from bench.fake_vllm import FakeVllm
with FakeVllm(api_key=os.environ["FAKE_API_KEY"]) as fake:
    open(os.environ["FAKE_URL_FILE"], "w").write(fake.base_url + "\n")
    signal.pause()
' || return 1
  local t=0
  until fake_healthy; do [ "$t" -ge 20 ] && { fail "the fake server did not answer within 20 s: $FAKE_LOG"; return 1; }; sleep 1; t=$(( t + 1 )); done
  ok "fake vLLM at $(cat "$FAKE_URL") (a rehearsal: its drafts are $FAKE_MODEL's, never Gemma's)"
}

reset_target() {
  if [ "$FAKE_VLLM" = 1 ]; then fake_up || return 1; fi
  llm_env
  do_run env "DATABASE_URL=$DATABASE_URL" "SERVING_DIR=$SERVING_DIR" "DEVICE=$DEVICE" DEMO_MODE=live "${LLM_ENV[@]}" "$PY" -m apps.api.reset
}

batch_generate() {
  if [ "$FAKE_VLLM" = 1 ]; then fake_up || return 1
  elif [ "$LLM_MODE" != vllm ]; then
    note "LLM_MODE=$LLM_MODE: python -m apps.api.batch_generate exits 2 by design (the cache holds vLLM outputs); nothing generated in this rehearsal (FAKE_VLLM=1 runs it against bench/fake_vllm.py)"
    return 0
  fi
  llm_env
  # DEVICE is recorded in every draft (gpu on the host; the configured one in a rehearsal).
  do_run env "SERVING_DIR=$SERVING_DIR" "DEVICE=$DEVICE" DEMO_MODE=live "${LLM_ENV[@]}" "$PY" -m apps.api.batch_generate
}

demo_export() {
  local py3=python3 model n args=()
  [ -x "$PY" ] && py3="$PY"
  llm_env
  model="$(printf '%s\n' "${LLM_ENV[@]}" | sed -n 's/^GEMMA_MODEL=//p')"
  n="$(find "$SERVING_DIR/llm_cache" -name '*.json' -type f 2>/dev/null | wc -l)"
  if [ "$REHEARSAL" = 1 ]; then args+=(--rehearsal); [ "$n" -gt 0 ] || args+=(--allow-empty-cache); fi
  mkdir -p "$(dirname "$DEMO_DIR")"
  do_run "$py3" -m scripts.demo_export build --serving "$SERVING_DIR" --bench bench/results --out "$DEMO_DIR" \
    --generated-on "$(demo_gpu)" --model "$model" "${args[@]}" || return 1
  [ "$DRY" = 1 ] || cp "$DEMO_DIR/MANIFEST.json" "$HOST_LOGS/export/MANIFEST.json" 2>/dev/null || true
}

tarball() {
  local dir base
  dir="$(cd "$(dirname "$DEMO_DIR")" && pwd)"; base="$(basename "$DEMO_DIR")"
  do_run tar -czf "$DEMO_DIR.tar.gz" -C "$dir" "$base" || return 1
  [ "$DRY" = 1 ] && return 0
  mkdir -p "$HOST_LOGS/export"
  { ls -l "$DEMO_DIR.tar.gz"; sha256sum "$DEMO_DIR.tar.gz"; } | tee "$HOST_LOGS/export/demo-cache.tar.gz.txt"
  python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); c=m.get("llm_cache", {}); print("MANIFEST:", {"created_at": m.get("created_at"), "drafts": c.get("entries"), "models": c.get("models"), "devices": c.get("devices"), "generated_on": c.get("generated_on"), "rehearsal": c.get("rehearsal")})' "$DEMO_DIR/MANIFEST.json"
  [ "$base" = demo-cache ] || warn "the archive's top folder is $base: make demo-import expects demo-cache/ (DEMO_DIR=demo-cache for the real package)"
}

bench_b1() {
  do_run "$PY_AMD" -m bench run --device gpu --parts B1 --run-id "$BENCH_RUN" --raw data/raw --no-merge ${BENCH_ARGS:+$BENCH_ARGS}
}

bench_vllm() {
  local run_dir="artifacts/bench/$BENCH_RUN"
  mkdir -p "$run_dir" "$SPIKES/logs"
  if [ "$DRY" = 0 ] && vllm_healthy; then
    # As the Makefile's bench-vllm: the first request is measured only after a start made here (a
    # request to a vLLM that already served is warm, and B3 would report it as the cold start).
    echo "vLLM already running: B3 reports no cold start and no first request"
    rm -f "$run_dir/vllm-start.txt" "$run_dir/first-request.json"
  else
    if [ "$DRY" = 1 ]; then note "dry-run: if vLLM is down, would drop the page cache (root only), write $run_dir/vllm-start-conditions.txt, vllm-start.txt, start vllm serve (the flags of vllm-serve), poll /health (15 s), copy vllm-healthy.txt, then python -m bench first-request --run-id $BENCH_RUN"
    else
      if [ "${BENCH_DROP_CACHES:-1}" = 1 ] && sync && { echo 3 > /proc/sys/vm/drop_caches; } 2>/dev/null; then
        echo "page cache dropped before the start" > "$run_dir/vllm-start-conditions.txt"
      else echo "page cache not dropped (not root, or BENCH_DROP_CACHES=0: a warm restart)" > "$run_dir/vllm-start-conditions.txt"; fi
      cat "$run_dir/vllm-start-conditions.txt"
    fi
    start_vllm "$run_dir/vllm-start.txt" "$SPIKES/logs/vllm-start.txt" || return 1
    wait_vllm || return 1
    [ "$DRY" = 1 ] || cp "$SPIKES/logs/vllm-healthy.txt" "$run_dir/vllm-healthy.txt"
    VLLM_BASE_URL="$HOST_VLLM_BASE_URL" do_run "$PY" -m bench first-request --run-id "$BENCH_RUN"
  fi
}

bench_b4() {
  local run_dir="artifacts/bench/$BENCH_RUN" sampler="" samples rc
  mkdir -p "$run_dir" bench-work
  if amd_smi_present; then
    if [ "$DRY" = 1 ]; then note "dry-run: would run $PY -m bench.amdsmi --out $run_dir/amdsmi.jsonl in the background during B4"
    else "$PY" -m bench.amdsmi --out "$run_dir/amdsmi.jsonl" > "$HOST_LOGS/bench/amdsmi-sampler.log" 2>&1 < /dev/null & sampler=$!; fi
    samples=(--b4-samples "$run_dir/amdsmi.jsonl")
  else
    note "no amd-smi on the host: --b4-sampler inprocess (it calls amd-smi too: every sample is recorded as an error, no GPU readings in B4; rule 13)"
    samples=(--b4-sampler inprocess)
  fi
  rc=0
  VLLM_BASE_URL="$HOST_VLLM_BASE_URL" do_run "$PY" -m bench run --device gpu --parts B4 --run-id "$BENCH_RUN" --no-merge "${samples[@]}" ${BENCH_ARGS:+$BENCH_ARGS} || rc=$?
  [ -n "$sampler" ] && kill "$sampler" 2>/dev/null   # always: a second sampler would write the same amdsmi.jsonl
  return "$rc"
}

bench_b3() {
  local run_dir="artifacts/bench/$BENCH_RUN"
  mkdir -p "$run_dir"
  [ "$DRY" = 1 ] || df -BG --output=used / | tail -1 | tr -dc '0-9' > "$run_dir/disk-used-gb.txt"
  VLLM_BASE_URL="$HOST_VLLM_BASE_URL" do_run "$PY" -m bench run --device gpu --parts B3 --run-id "$BENCH_RUN" --no-merge ${BENCH_ARGS:+$BENCH_ARGS}
}

bench_merge() {
  local run_dir="artifacts/bench/$BENCH_RUN"
  do_run "$PY" -m bench merge --run-id "$BENCH_RUN" || return 1
  do_run "$PY" -m bench report --run "bench/results/$BENCH_RUN.json" --out "$run_dir/report.md" || return 1
  [ "$DRY" = 1 ] && return 0
  python3 - "bench/results/$BENCH_RUN.json" <<'EOF'
import json, sys
doc = json.load(open(sys.argv[1]))
print("run", doc.get("run_id"), "device", doc.get("device"), "git", doc.get("git"))
for name, part in sorted((doc.get("benchmarks") or {}).items()):
    hw = (doc.get("hardware") or {}).get(name) or {}
    print(f"  {name}: {part.get('status')}, {len(part.get('measurements') or [])} measurements; gpu named by amd-smi: {bool(hw.get('gpu'))}")
print("natures of the measurements:", sorted({str((m.get('provenance') or {}).get('nature')) for m in doc.get('measurements') or []}))
EOF
  note "what the results say: B1, B3 and B4 'measured' on this host (natures real/simulated as docs/benchmarks.md states), B2 absent; the GPU name comes from amd-smi static (none without amd-smi: write '$(gpu_name)' from probe.txt in docs/results/benchmarks-mi300x.md by hand, rule 13); the report file is $run_dir/report.md; on the laptop: make bench-report BENCH_RUN=$BENCH_RUN DEVICE=gpu"
}

api_up() {
  llm_env
  if [ "$DRY" = 0 ] && pid_alive "$API_PID"; then do_stop "$API_PID" "uvicorn"; fi
  do_start "$API_PID" "$API_LOG" env "DATABASE_URL=$DATABASE_URL" "SERVING_DIR=$SERVING_DIR" "DEVICE=$DEVICE" DEMO_MODE=live \
    "${LLM_ENV[@]}" BENCHMARKS_DIR=bench/results DEMO_CLOCK=on PUBLIC_READ_ONLY=off \
    "$VENV/bin/uvicorn" apps.api.main:app --host 127.0.0.1 --port "$API_PORT"
}

readyz_target() {
  if [ "$DRY" = 1 ]; then note "dry-run: would poll $API_URL/readyz for up to 120 s, save it to $PHASE_DIR/readyz.json and check status != not_ready, mode live, llm_mode $LLM_MODE, device $DEVICE, public_read_only false"; return 0; fi
  wait_ready "$API_URL" 120 || { tail -50 "$API_LOG"; return 1; }
  save_readyz "$HOST_REL/live/readyz.json"
  python3 - "$PHASE_DIR/readyz.json" "$LLM_MODE" "$DEVICE" <<'EOF' || return 1
import json, sys
r = json.load(open(sys.argv[1])); llm, dev = sys.argv[2], sys.argv[3]
bad = []
if r.get("status") == "not_ready": bad.append("status not_ready")
if r.get("mode") != "live": bad.append(f"mode {r.get('mode')!r}, expected live")
if r.get("llm_mode") != llm: bad.append(f"llm_mode {r.get('llm_mode')!r}, expected {llm}")
if r.get("device") != dev: bad.append(f"device {r.get('device')!r}, expected {dev}")
if r.get("public_read_only"): bad.append("public_read_only true: the walk and the video write")
if bad:
    print("FAIL readyz: " + "; ".join(bad)); sys.exit(1)
print(f"OK   readyz: live, llm_mode {llm}, device {dev}, status {r.get('status')} (degraded is expected without the telemetry sampler)")
EOF
}

telemetry_target() {
  if [ "$DEVICE" != gpu ]; then note "DEVICE=$DEVICE: no telemetry sampler (a rehearsal)"; return 0; fi
  if ! amd_smi_present; then note "no amd-smi on this host: no telemetry sampler; the /system page shows no GPU numbers and says why (rule 11)"; return 0; fi
  if [ "$DRY" = 0 ] && pid_alive "$TELEMETRY_PID"; then do_stop "$TELEMETRY_PID" "the telemetry sampler"; fi
  do_start "$TELEMETRY_PID" "$TELEMETRY_LOG" env "DATABASE_URL=$DATABASE_URL" DEVICE=gpu \
    "$PY" -m apps.api.services.other.telemetry --device "$(gpu_name)"
}

walk() {
  mkdir -p "$PHASE_DIR"
  do_run "$PY" -m scripts.demo_walk --base-url "$API_URL" --report "$PHASE_DIR/demo-walk.json" || { [ "$DRY" = 1 ] || walk_summary "$PHASE_DIR/demo-walk.json" 2>/dev/null; return 1; }
  [ "$DRY" = 1 ] && return 0
  walk_summary "$PHASE_DIR/demo-walk.json"
  note "the walk approved cases: before the recording, bash scripts/release/07-host-session.sh live --only live-reset (every open return drafted again by Gemma, golden cases first)"
}

tunnel_help() {
  local host_ip="<host-ip>" user="<user>"
  cat <<EOF
The API listens on 127.0.0.1:$API_PORT of this host only (no public port). For the UI and the walk on the
laptop, one tunnel, started by hand (this script never starts one):

A. Hot Aisle VM, or any host with SSH (the chosen road of amd-runbook 11.3, the SSH tunnel):
     laptop:  ssh -L $API_PORT:127.0.0.1:$API_PORT $user@$host_ip          # keep it open
     laptop:  cd apps/ui && RETURNRING_API_URL=http://127.0.0.1:$API_PORT npm run dev:live   # vite proxies /api to it
     laptop:  python -m scripts.demo_walk --base-url http://127.0.0.1:$API_PORT --report artifacts/demo-walk.json
              (requirements/dev.txt installed; or run the walk here: --only walk)
   Add -L $VLLM_PORT:127.0.0.1:$VLLM_PORT to reach vLLM itself from the laptop (plan B: the API on the laptop).

B. A notebook without SSH and without public ports (notebooks.amd.com): a quick tunnel of cloudflared,
   a single binary (Apache-2.0) downloaded from github.com/cloudflare/cloudflared/releases:
     host:    ~/bin/cloudflared tunnel --url http://127.0.0.1:$API_PORT      # prints https://<random>.trycloudflare.com
     laptop:  export HISTCONTROL=ignorespace    # the shell history is a file: the URL must not end up in it
     laptop:   cd apps/ui && RETURNRING_API_URL=https://<random>.trycloudflare.com npm run dev:live   # leading space: kept out of the history
     laptop:   python -m scripts.demo_walk --base-url https://<random>.trycloudflare.com
   WARNING: that URL exposes this API to the whole Internet WITHOUT any authentication (no Caddy, no
   basic auth, PUBLIC_READ_ONLY=off: anyone with the URL can approve cases). Only while recording;
   Ctrl-C the tunnel as soon as the recording ends, then --only live-reset. The URL goes in no file,
   no commit, no chat, and not in the laptop's shell history (~/.bash_history, ~/.zsh_history: the
   leading space with HISTCONTROL=ignorespace, or unset HISTFILE in that terminal): it is typed on
   the laptop's command line only.

For the video (release-guide 4.2, amd-runbook 11.5): browser at 1440x900 on http://localhost:5173
(the vite dev server of dev:live), a terminal with amd-smi in a corner during the generations:
     host:    watch -n 2 amd-smi monitor            # /opt/rocm/bin is on the PATH of this script
OBS or QuickTime at 1920x1080; at most 5 minutes, under 300 MB (ffmpeg line of the guide); a second
recording of reserve on the cached server; the link checked from an anonymous browser.
Checklist before recording: /readyz above says live, vllm, gpu; --only live-reset done after the walk;
the three questions of the agent answer (US-702); the demo clock runs (DEMO_CLOCK=on: a reset again
two days later, amd-runbook 11.6).
EOF
}

api_stop() { do_stop "$API_PID" "uvicorn"; }
telemetry_stop() { do_stop "$TELEMETRY_PID" "the telemetry sampler"; }
fake_stop() { do_stop "$FAKE_PID" "the fake vLLM"; [ "$DRY" = 1 ] || rm -f "$FAKE_URL"; }

disk_usage() {
  if [ "$DRY" = 1 ]; then note "dry-run: would run df -h $ROOT $HF_HOME and du -sh of the weights, artifacts/, bench-work/, $DEMO_DIR"; return 0; fi
  df -h "$ROOT" "$HF_HOME" 2>/dev/null || df -h "$ROOT"
  du -sh "$HF_HOME/hub" artifacts bench-work "$DEMO_DIR" "$DEMO_DIR.tar.gz" 2>/dev/null || true
  echo "nothing above is deleted by this script"
}

down_reminder() {
  local bench_run="${BENCH_RUN:-<BENCH_RUN>}"
  step "next, by hand (this script never deletes a file, a cache or a VM)"
  cat <<EOF
1. Copy back to the laptop (scp on a host with SSH; on a notebook, zip and download from the JupyterLab
   file browser: tar -czf back.tgz artifacts/spikes artifacts/bench/$bench_run bench/results release-logs-*.tgz demo-cache.tar.gz):
     scp -r <user>@<host-ip>:ReturnRing/artifacts/spikes ./artifacts/
     scp <user>@<host-ip>:"ReturnRing/bench/results/*.json" ./bench/results/
     scp -r <user>@<host-ip>:ReturnRing/artifacts/bench/$bench_run ./artifacts/bench/
     scp <user>@<host-ip>:ReturnRing/demo-cache.tar.gz .          # then make demo-import demo-local; the demo server: make demo-import demo-up
     scp <user>@<host-ip>:"ReturnRing/release-logs-*.tgz" .
   Never: .env, data/truth, the weights.
2. A paid VM (Hot Aisle, 2.99 USD per GPU hour, billed per minute while it exists): destroy it in the
   provider's console now and write the creation and destruction times (UTC) in docs/spike-results.md.
   Nothing on its disk is needed after step 1. A notebook (free): stop the session; its home folder may
   persist: HF_TOKEN emptied in .env, no token file, no data/truth (the warnings above).
3. On the laptop: make bench-report BENCH_RUN=$bench_run DEVICE=gpu, fill docs/spike-results.md from
   artifacts/spikes/ (G3 and B2: not run, plan B), commit bench/results/*.json and the docs only.
EOF
}

bench_b2_skip_reason() {
  printf 'B2 (GraphSAGE on DGL) needs the rocm/dgl image (trainer-graphsage), which needs Docker: not run on this host. GraphSAGE stays CPU-trained as in docs/results/us-202-graphsage.md (plan B of spec 13); no B2 part is written, bench-merge reports B2 absent'
}

exec_target() {  # <target>
  case "$1" in
    probe) probe ;;
    venv) venv ;;
    vllm-python) vllm_python_target ;;
    venv-amd) venv_amd ;;
    weights) weights ;;
    weights-wait) weights_wait ;;
    template) template ;;
    vllm-serve) vllm_serve ;;
    vllm-wait) wait_vllm ;;
    first-request) first_request ;;
    vllm-stop) vllm_stop ;;
    spike-g2) spike_g2 ;;
    spike-g7) spike_g7 ;;
    spike-g1) spike_g1 ;;
    spike-g1-bench) spike_g1_bench ;;
    spike-g5) spike_g5 ;;
    spike-g6) spike_g6 ;;
    spike-g4) spike_g4 ;;
    reset|live-reset) reset_target ;;
    batch-generate) batch_generate ;;
    demo-export) demo_export ;;
    tarball) tarball ;;
    bench-b1) bench_b1 ;;
    bench-vllm) bench_vllm ;;
    bench-b4) bench_b4 ;;
    bench-b3) bench_b3 ;;
    bench-merge) bench_merge ;;
    api-up) api_up ;;
    readyz) readyz_target ;;
    telemetry) telemetry_target ;;
    walk) walk ;;
    tunnel-help) tunnel_help ;;
    api-stop) api_stop ;;
    telemetry-stop) telemetry_stop ;;
    fake-stop) fake_stop ;;
    disk-usage) disk_usage ;;
    *) fail "no such target: $1"; return 1 ;;
  esac
}

run_target() {  # <phase> <target>
  local phase="$1" t="$2" log="$HOST_LOGS/$1/$2.log" marker="$PHASE_DIR/$2.done" skipped="$PHASE_DIR/$2.skipped" s e rc=0 now
  if in_list "$t" "$SKIP" || [ "$t" = bench-b2 ]; then
    now="$(date +%s)"
    if [ "$t" = bench-b2 ] && ! in_list "$t" "$SKIP"; then step "$t: skipped: $(bench_b2_skip_reason)"
    else step "$t: skipped by the operator (--skip)"; fi
    if [ "$DRY" = 0 ]; then utc "$now" > "$skipped"; [ "$t" != bench-b2 ] || bench_b2_skip_reason > "$PHASE_DIR/bench-b2.skipped-reason.txt"; fi
    record "$phase" "$t" "$now" "$now" skipped
    return 0
  fi
  if [ "$FORCE" = 0 ] && [ -z "$ONLY" ]; then
    if in_words "$t" "$ALWAYS_RUN"; then
      [ -f "$marker" ] && note "$t: done at $(cat "$marker"), run again anyway (ALWAYS_RUN: cheap, or its effect does not outlive the day)"
    else
      if [ -f "$marker" ]; then ok "$t: done at $(cat "$marker"), skipped (--force or --only $t runs it again)"; return 0; fi
      if [ -f "$skipped" ]; then ok "$t: skipped by the operator at $(cat "$skipped") (--force or --only $t runs it)"; return 0; fi
    fi
  fi
  step "$t: preconditions"
  check_target "$phase" "$t" || { FAILED_WHERE="a precondition of $t"; FAILED_TARGET="$t"; FAILED_PHASE="$phase"; return 1; }
  CURRENT_TARGET="$t"
  step "$t$( [ "$DRY" = 1 ] && printf ' (dry-run)' )"
  s="$(date +%s)"
  # The target runs in a pipeline (its output tees into its log), so it changes no variable of
  # this shell: everything it must leave behind goes to a file.
  exec_target "$t" 2>&1 | tee "$log" || rc=$?
  e="$(date +%s)"
  RUN_SECS=$(( RUN_SECS + e - s ))
  if [ "$rc" = 0 ]; then
    if [ "$DRY" = 1 ]; then record "$phase" "$t" "$s" "$e" dry-run; ok "$t: printed, not run (dry-run)"
    else record "$phase" "$t" "$s" "$e" ok; utc "$e" > "$marker"; ok "$t: exit 0 in $(hms $(( e - s ))), marker $marker"; fi
  else
    record "$phase" "$t" "$s" "$e" fail
    fail "$t: exit $rc after $(hms $(( e - s ))) (log: release-logs/$HOST_REL/$phase/$t.log)"
    FAILED_TARGET="$t"; FAILED_WHERE="the target $t"; FAILED_PHASE="$phase"
    return 1
  fi
  CURRENT_TARGET=""
}

phase_summary() {  # <phase>
  local phase="$1" this all
  this="$(awk -F'\t' -v p="$phase" -v s="$(utc "$RUN_START")" '$1 == p && $2 != "run" && $6 != "skipped" && $3 >= s { t += $5 } END { print t + 0 }' "$TIMES")"
  all="$(awk -F'\t' -v p="$phase" '$1 == p && $2 != "run" && $6 != "skipped" { t += $5 } END { print t + 0 }' "$TIMES")"
  step "phase $phase: host time"
  if [ "$PRICE_USD_PER_HOUR" = 0 ]; then
    printf 'targets of this run: %s; every run of this phase: %s (free host: no price)\n' "$(hms "$this")" "$(hms "$all")"
    printf 'the sum counts the targets'"'"' wall time only (times: %s)\n' "$TIMES"
  else
    printf 'targets of this run: %s (estimate at %s USD/h: %s USD); every run of this phase: %s (%s USD)\n' \
      "$(hms "$this")" "$PRICE_USD_PER_HOUR" "$(usd "$this")" "$(hms "$all")" "$(usd "$all")"
    printf 'the sum counts the targets'"'"' wall time only: a paid VM bills while it exists, so it is a lower bound (times: %s)\n' "$TIMES"
  fi
  host_age
}

run_phase() {  # <phase>
  local phase="$1" t started=1
  PHASE_DIR="$HOST_LOGS/$phase"; mkdir -p "$PHASE_DIR"
  step "phase $phase: preconditions"
  check_phase "$phase" || { FAILED_WHERE="the preconditions of the phase $phase"; return 1; }
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
      vllm|spikes) copy_small "$HOST_LOGS/spikes/artifacts" "$SPIKES"/*.json "$SPIKES"/logs/*.txt "$SPIKES"/logs/*.log; [ -f "$VLLM_LOG" ] && tail -300 "$VLLM_LOG" > "$HOST_LOGS/vllm-tail.log" ;;
      bench)
        [ -n "$BENCH_RUN" ] && copy_small "$HOST_LOGS/bench/artifacts" artifacts/bench/"$BENCH_RUN"/*.json artifacts/bench/"$BENCH_RUN"/*.txt artifacts/bench/"$BENCH_RUN"/*.md
        copy_small "$HOST_LOGS/bench/results" bench/results/*.json ;;
      export) copy_small "$HOST_LOGS/export/artifacts" "$DEMO_DIR/MANIFEST.json" "$SERVING_DIR/manifest.json" ;;
      live) [ -f "$API_LOG" ] && tail -300 "$API_LOG" > "$HOST_LOGS/uvicorn-tail.log" ;;
    esac
  done
  return 0
}

scrub_secrets() {
  local k v f files
  for k in VLLM_API_KEY HF_TOKEN POSTGRES_PASSWORD ADMIN_PASSWORD_HASH DB_PASSWORD; do
    if [ "$k" = DB_PASSWORD ]; then v="$DB_PASSWORD"; else v="$(env_value "$k")"; fi
    case "$v" in ""|change-me) continue ;; esac
    [ "${#v}" -ge 8 ] || continue
    # Never a .env (a test may keep one under release-logs/), a venv, a snapshot copy or a package.
    files="$(grep -rlF --exclude='.env' --exclude='*.env' --exclude-dir=venv --exclude-dir='.venv*' --exclude-dir=serving --exclude-dir=demo-cache -- "$v" "$LOGS" 2>/dev/null || true)"
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

pack_host() {  # the logs, without a venv, a snapshot copy, a package or a database under release-logs/
  tar -czf "$ROOT/release-logs-$STAMP.tgz" -C "$ROOT" \
    --exclude='release-logs/*/venv' --exclude='release-logs/*/.venv*' --exclude='release-logs/*/serving' \
    --exclude='release-logs/*/demo-cache' --exclude='release-logs/*/demo-cache.tar.gz' --exclude='release-logs/*/*.db' --exclude='release-logs/*/*.db-journal' \
    release-logs
  printf '\nLogs: %s\nSend this file: %s\n' "$HOST_LOGS" "$ROOT/release-logs-$STAMP.tgz"
}

plan_b() {  # <target>
  case "$1" in
    venv) cat <<'EOF'
pip could not install requirements/dev.txt: is pypi.org reachable (probe.txt, "pypi.org")? A proxy of the
notebook (HTTPS_PROXY)? Python older than 3.12: need_python312 above names the options. Rerun the phase.
EOF
      ;;
    venv-amd) cat <<'EOF'
venv-amd never stops the run: a failure is recorded in release-logs/host/env/venv-amd.planb (SPK-G2 plan B of
spec 13: XGBoost on the CPU; B1 not measured). If it stopped, the venv itself could not be created.
EOF
      ;;
    weights|weights-wait) cat <<'EOF'
Runbook step 8: a 401 or 403 is not expected (both Gemma 4 models are not gated): set HF_TOKEN in .env,
--only weights, --only weights-wait, then empty HF_TOKEN again. Not finished after 90 min: tail
artifacts/spikes/logs/download-weights.log (the network of the host); --only weights-wait polls again.
No space: HF_HOME=<folder on the big disk> (Hot Aisle: the NVMe; a notebook: check df in probe.txt).
EOF
      ;;
    template) cat <<'EOF'
Copy examples/tool_chat_template_gemma4.jinja of the vLLM repository (tag v<installed version>) to
artifacts/spikes/ from the laptop, or VLLM_CHAT_TEMPLATE=<path>. The path given to --chat-template is
absolute (this script builds it from the repository root, as the compose file and runbook 4.2 ask).
EOF
      ;;
    vllm-serve|vllm-wait|bench-vllm) cat <<'EOF'
Runbook step 9, read release-logs/host/vllm.log (tail -200):
| Free memory on device ... is less than desired GPU memory utilization | Something else holds GPU memory: amd-smi process; a notebook kernel with torch? Stop it, then --only vllm-serve, --only vllm-wait. |
| Chat template not found | --only template; the path printed by vllm-serve must exist. |
| Compile or graph-capture error, or no healthy server after 15 min of compiling | VLLM_EXTRA_ARGS="--enforce-eager" bash scripts/release/07-host-session.sh vllm-stop, then the vllm phase again. Record it: eager mode is slower, G1 numbers are a lower bound. |
| Engine or kernel error | Another vLLM build of the image, if the host has one (VLLM_BIN); there is no image to swap here. |
| Gemma 4 31B does not start at all | Plan B model (spec 13, R-02, D-12): in .env GEMMA_MODEL=google/gemma-4-26B-A4B-it and GEMMA_REVISION=4d7ae4984b7db7de8f8457170b3f1a419ee76d52 (both together), vllm-stop, then the vllm phase again (another 51.6 GB download). |
A vLLM already healthy is kept by vllm-serve: after any change, vllm-stop first. bench-vllm starts vLLM
only when none answers /health.
EOF
      ;;
    first-request|spike-g1|spike-g1-bench) cat <<'EOF'
first-request runs g1_guided_json.py --runs 2 (its minimum: run 0 is the request timed here); a failure is a
request failure (HTTP error, cannot reach, a problem in the JSON), read the log.
Runbook step 10: finish_reason length: raise --max-tokens (g1_guided_json.py --max-tokens, default 1024).
"FAIL: request without the API key was not refused with 401": the tokens/s in artifacts/spikes/g1.json are
valid; record nfr11_key_enforced=false, check --api-key in release-logs/host/vllm.log (redacted in the
pack), go on with --skip spike-g1. Plan B of D-12 (below 15 tokens/s, 1000 / median TPOT of
g1-bench-c1.txt): the 26B-A4B model, see vllm-serve. spike-g1-bench needs `vllm bench serve` of the
installed vLLM (0.31.0 has it); --skip spike-g1-bench records the tokens/s of g1.json only (single
non-streamed requests, a lower bound).
EOF
      ;;
    spike-g2) cat <<'EOF'
Runbook step 6: exit 2 (GPU not used): amd_xgboost falls back to the CPU with only a warning; the venv-amd
log says whether libxgboost.so loaded (ldd) and the host ROCm matches the wheel (7.2.3). Plan B (spec 13):
XGBoost on the CPU: --skip spike-g2,spike-g4,bench-b1 and record it.
EOF
      ;;
    spike-g5) cat <<'EOF'
Runbook step 11: plan B (spec 13): inspection out of scope (it was Could): --skip spike-g5 and record it.
Another photo: IMAGE=<path>.
EOF
      ;;
    spike-g6) cat <<'EOF'
Runbook step 12: --mode named was already tried. Plan B (spec 13): the agent's code picks the tool, Gemma
writes only the answer: --skip spike-g6 and record it.
EOF
      ;;
    spike-g4) cat <<'EOF'
Runbook step 13: plan B (spec 13): train with vLLM stopped (the bench phase already runs B1 with vLLM down).
artifacts/spikes/logs/g4_xgb.log and g4_llm_*.log say which half failed. --skip spike-g4 and record it:
on this host G4 had no DGL job anyway.
EOF
      ;;
    reset|live-reset) cat <<'EOF'
Exit 1: some returns failed or vLLM was unavailable for some drafts (they use the template, labelled):
is vLLM healthy (curl http://127.0.0.1:8001/health)? LLM_CONCURRENCY and VLLM_TIMEOUT_S in .env
(32 and 20 s by default). Exit 2: DATABASE_URL or the snapshot (the reason is printed). Rerun.
EOF
      ;;
    batch-generate) cat <<'EOF'
Runbook, The demo export, step 4: exit 2 unless vLLM serves GEMMA_MODEL (vllm.log says which model it
started with; .env must name the same model and revision); exit 1 if vLLM failed any (not cached): rerun,
the cached drafts stay, only the missing ones are made again.
EOF
      ;;
    demo-export) cat <<'EOF'
Runbook, The demo export, step 5: it refuses an empty cache and drafts of a model other than GEMMA_MODEL or
of a batch run off the GPU. Fix the batch (batch-generate again), never REHEARSAL=1 for the MI300X package.
DEMO_GPU must contain MI300X (check_export says what happens on another GPU).
EOF
      ;;
    bench-b1) cat <<'EOF'
Runbook, The GPU session, step 8: the log line "B1 cuda: cold start ..." and no "GPU not used" error (B1
refuses a CPU fallback instead of reporting it). Plan B of SPK-G2: --skip bench-b1 and record B1 as not
measured. Same BENCH_RUN on the rerun: the parts already written stay.
EOF
      ;;
    bench-b4) cat <<'EOF'
Runbook, The GPU session, step 12: batch_generation exits 1 if vLLM was unavailable for any decision
(vllm.log, then rerun with the same BENCH_RUN). Without amd-smi on the host B4 has no GPU readings: say so.
EOF
      ;;
    bench-b3) cat <<'EOF'
Runbook, The GPU session, step 13: failed must be 0 in every load of B3.json. To change the loads:
BENCH_ARGS="--b3-batches 1,8,32 --b3-concurrency 4,16 --b3-repeats 2". B3 reads B4's store in
bench-work/$BENCH_RUN: run bench-b4 first (--only bench-b4). Rerun with the same BENCH_RUN.
EOF
      ;;
    bench-merge) cat <<'EOF'
bench-merge reads the parts under artifacts/bench/$BENCH_RUN (a failed part is merged as failed) and writes
bench/results/$BENCH_RUN.json, latest.json and latest-gpu.json. Check that the parts exist.
EOF
      ;;
    api-up|readyz) cat <<'EOF'
tail -100 release-logs/host/uvicorn.log: DATABASE_URL (SQLite needs an absolute path: sqlite:////abs/path.db),
SERVING_DIR, VLLM_BASE_URL refused (loopback and private IPv4 only, llm/client.py), the port taken
(API_PORT=<other>). /readyz lists a check per component with its detail.
EOF
      ;;
    walk) cat <<'EOF'
The report names the failed step (release-logs/host/live/demo-walk.json). The walk needs a fresh reset
(--only live-reset) and writes allowed (PUBLIC_READ_ONLY=off, set by api-up). In LLM_MODE=vllm the agent
answers through vLLM: vllm.log on a 503.
EOF
      ;;
    *) echo "no plan B written for $1: read the log, infra/spikes/README.md and docs/amd-runbook.md section 15" ;;
  esac
}

# ---- run ---------------------------------------------------------------------------------
finish() {
  local code=$? e status=fail
  e="$(date +%s)"
  rm -f "$AUTH_HEADER_FILE"
  [ "$code" = 0 ] && status=ok
  [ "$DRY" = 1 ] && status="dry-run-$status"
  record "$PHASE" run "$RUN_START" "$e" "$status"
  if [ "$code" != 0 ] && [ -n "$FAILED_TARGET" ]; then
    step "plan B for $FAILED_TARGET"
    plan_b "$FAILED_TARGET"
  fi
  if [ "$DRY" = 1 ]; then
    note "dry-run: nothing packed; the dry-run logs and times are under $HOST_LOGS"
  else
    collect_results
    scrub_secrets
    pack_host
  fi
  if [ "$code" = 0 ]; then
    ok "07-host-session $PHASE: every step passed (whole run $(hms $(( e - RUN_START ))), targets $(hms "$RUN_SECS"))"
  else
    fail "07-host-session $PHASE: stopped at ${FAILED_WHERE:-an unexpected error} (exit $code) after $(hms $(( e - RUN_START )))"
    if [ -n "$FAILED_TARGET" ] && [ "$FAILED_PHASE" = bench ]; then
      # Without --from, vllm-stop and bench-vllm (ALWAYS_RUN) would stop and restart vLLM and
      # remeasure the cold start of B3, overwriting the run's vllm-start.txt and first-request.json.
      printf 'resume: bash scripts/release/07-host-session.sh bench --from %s   (vllm-stop and bench-vllm are not run again: vLLM and the cold-start files of B3 stay; --from %s --skip %s applies its plan B and goes on)\n' "$FAILED_TARGET" "$FAILED_TARGET" "$FAILED_TARGET"
      [ "$PHASE" != all ] || printf 'then: bash scripts/release/07-host-session.sh export   (the phase after bench in all)\n'
    elif [ -n "$FAILED_TARGET" ]; then
      printf 'resume: bash scripts/release/07-host-session.sh %s   (starts again at %s; --skip %s applies its plan B and goes on)\n' "$PHASE" "$FAILED_TARGET" "$FAILED_TARGET"
    fi
  fi
}
trap finish EXIT

step "07-host-session $PHASE$( [ "$DRY" = 1 ] && printf ' (dry-run: nothing run, no GPU, no vLLM)' )"
printf 'started %s; repository %s @ %s; ENV_FILE=%s; HOST_RUN=%s; price %s USD per GPU hour (%s); LLM_MODE=%s DEVICE=%s%s\n' \
  "$(utc "$RUN_START")" "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" "$(git rev-parse --short HEAD 2>/dev/null)" "$ENV_FILE" "$HOST_RUN" "$PRICE_USD_PER_HOUR" "$HOST_PROVIDER" "$LLM_MODE" "$DEVICE" "$( [ "$REHEARSAL" = 1 ] && printf ' (REHEARSAL, FAKE_VLLM=%s)' "$FAKE_VLLM" )"
printf 'phases: %s%s%s%s%s\n' "$RUN_PHASES" "${FROM:+; from $FROM}" "${ONLY:+; only $ONLY}" "${SKIP:+; skip $SKIP}" "$( [ "$FORCE" = 1 ] && printf '; force (markers ignored)' )"
if [ -f "$ENV_FILE" ]; then
  sed -E 's/^(POSTGRES_PASSWORD|DATABASE_URL|VLLM_API_KEY|HF_TOKEN|ADMIN_PASSWORD_HASH)=.*/\1=<redacted>/' "$ENV_FILE" > "$HOST_LOGS/env-redacted.txt"
  load_env
else
  VLLM_API_KEY=""; GEMMA_MODEL=""; GEMMA_REVISION=""; HF_TOKEN=""; AMD_PYPI_INDEX_URL=""; SEED=42
fi
host_age

for p in $RUN_PHASES; do
  run_phase "$p" || exit 1
done
