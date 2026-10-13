#!/usr/bin/env bash
# SPK-G4: vLLM at 0.55 of the GPU memory serves requests while the XGBoost and DGL jobs train on the
# same MI300X. Run on the droplet host from the repo root, after `make spike-wait`: make spike-g4
# Pass: both jobs exit 0 (no out-of-memory or HIP error), every guided-JSON request during the run
# passes, and vLLM is still healthy at the end. amd-smi is sampled on the host (the in-container
# amd-smi is more tightly coupled to the host driver).
set -u
export PATH="$PATH:/opt/rocm/bin"  # host amd-smi is often installed there, off the PATH

COMPOSE=${COMPOSE:-"docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml -f infra/compose.spike.yml --env-file .env --profile gpu"}
DURATION=${DURATION:-300}
POLL=${POLL:-45}
OUT=artifacts/spikes
mkdir -p "$OUT/logs"

health() { $COMPOSE exec -T vllm curl -fsS -o /dev/null http://127.0.0.1:8001/health; }

if ! health; then
  echo "vLLM is not healthy: run make spike-vllm and make spike-wait first" >&2
  exit 1
fi
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)

sampler=""
if command -v amd-smi >/dev/null; then
  (while true; do
     echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ)"
     amd-smi metric --json 2>&1
     amd-smi process 2>&1
     sleep 5
   done) > "$OUT/g4_amdsmi.log" &
  sampler=$!
else
  echo "amd-smi not found on the host: record memory with rocm-smi or skip" >&2
fi

$COMPOSE run --rm -T trainer-gnn python infra/spikes/g3_graphsage_synthetic.py \
  --device gpu --min-seconds "$DURATION" --context "SPK-G4: vLLM serving, XGBoost training" \
  --out "$OUT/g4_dgl.json" > "$OUT/logs/g4_dgl.log" 2>&1 &
dgl=$!
$COMPOSE run --rm -T trainer-gbm python infra/spikes/g2_xgboost_gpu.py \
  --device gpu --min-seconds "$DURATION" --context "SPK-G4: vLLM serving, DGL training" \
  --out "$OUT/g4_xgb.json" > "$OUT/logs/g4_xgb.log" 2>&1 &
xgb=$!

llm_fail=0
n=0
while kill -0 "$dgl" 2>/dev/null || kill -0 "$xgb" 2>/dev/null; do
  sleep "$POLL"
  n=$((n + 1))
  if $COMPOSE run --rm -T spike-client python infra/spikes/g1_guided_json.py --runs 2 \
       --out "$OUT/g4_llm_$n.json" > "$OUT/logs/g4_llm_$n.log" 2>&1; then
    echo "guided-JSON request $n during training: pass"
  else
    echo "guided-JSON request $n during training: FAIL (see $OUT/logs/g4_llm_$n.log)"
    llm_fail=1
  fi
done

wait "$dgl"; dgl_rc=$?
wait "$xgb"; xgb_rc=$?
[ -n "$sampler" ] && kill "$sampler" 2>/dev/null
health; vllm_rc=$?
$COMPOSE logs --no-color --since "$since" vllm 2>&1 \
  | grep -i -E 'out of memory|hipError|HIP error|Traceback' > "$OUT/logs/g4_vllm_errors.log"

echo "DGL job exit $dgl_rc, XGBoost job exit $xgb_rc, LLM failures $llm_fail, vLLM health $vllm_rc"
echo "vLLM error lines since $since: $(wc -l < "$OUT/logs/g4_vllm_errors.log")"
if [ "$dgl_rc" -eq 0 ] && [ "$xgb_rc" -eq 0 ] && [ "$llm_fail" -eq 0 ] && [ "$vllm_rc" -eq 0 ]; then
  echo "SPK-G4: pass"
  exit 0
fi
echo "SPK-G4: FAIL. Plan B (spec 13): train with vLLM stopped (make spike-down, then the jobs)."
exit 1
