#!/usr/bin/env bash
# SPK-G7 download: the Gemma weights go into the hf-cache volume in the background, while the GPU runs
# G2 and G3, so paid time is not spent waiting on the network. Run on the droplet host from the repo
# root, after make spike-prep (it needs the vLLM image):
#   make spike-weights        # starts the download and returns at once
#   make spike-weights-wait   # waits for it, prints the exit code and the download time
# The download runs inside the vLLM image (no GPU use), at GEMMA_REVISION, the same revision vllm
# serve is started with.
set -u

COMPOSE=${COMPOSE:-"docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml -f infra/compose.spike.yml --env-file .env --profile gpu"}
ENV_FILE=${ENV_FILE:-.env}
LOGS=artifacts/spikes/logs
STATUS="$LOGS/weights-status.txt"
mkdir -p "$LOGS"

case "${1:-}" in
  start)
    set -a
    # shellcheck disable=SC1090
    . "./$ENV_FILE"
    set +a
    rm -f "$STATUS"
    # setsid: the download survives the end of this script and an SSH drop.
    setsid bash -c '
      s=$(date +%s)
      $0 run --rm --no-deps -T -e HF_HUB_OFFLINE=0 --entrypoint python3 vllm -c \
        "import sys, huggingface_hub as h; print(h.snapshot_download(sys.argv[1], revision=sys.argv[2]))" \
        "$1" "$2"
      rc=$?
      echo "download-weights: exit $rc, took $(( $(date +%s) - s )) s ($1@$2)" > "$3"
    ' "$COMPOSE" "$GEMMA_MODEL" "$GEMMA_REVISION" "$STATUS" > "$LOGS/download-weights.log" 2>&1 < /dev/null &
    echo "weights download started: log $LOGS/download-weights.log; later run make spike-weights-wait"
    ;;
  wait)
    for _ in $(seq 1 360); do  # up to 90 min
      if [ -f "$STATUS" ]; then
        cat "$STATUS"
        grep -q 'exit 0,' "$STATUS"
        exit $?
      fi
      sleep 15
    done
    echo "weights download not finished after 90 min: tail $LOGS/download-weights.log" >&2
    exit 1
    ;;
  *)
    echo "usage: $0 start|wait" >&2
    exit 2
    ;;
esac
