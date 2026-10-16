# AMD session: shared Gemma inference and real GPU training

Completed on 16 October 2026. Delivery scope: [ADR-18](adr/ADR-18-amd-api-and-training.md).
Machine-readable evidence: [session results](results/amd-session-2026-10-16.json).

## What ran

| Workload | Actual execution | Result |
| --- | --- | --- |
| Gemma in the product | AMD Token Factory, `Gemma-4-31B-IT`, through the real HTTP regeneration route | Giulia, Marco and Elena returned HTTP 200 with validated model text and unchanged decisions. Giulia passed first call; Marco and Elena passed regeneration. |
| XGBoost risk training | AMD Instinct MI300X VF, HIP-enabled AMD XGBoost 2.1.3 | A1, A2, A3 and the request model trained; job exited 0; all golden checks passed. A3 also loaded in the CPU serving runtime with xgboost-cpu 3.2.0. |
| GraphSAGE training | Same MI300X VF, PyTorch 2.8.0+rocm6.4, pure-PyTorch backend | Customer-identifier encoder trained for 300 epochs; encoder and 77,703 embedding rows over four snapshot instants saved. |

The two ML jobs used the Navigli Fashion **synthetic** store, seed 42, scale 1.0 (20,000
customers), generated from the committed ASOS aggregate calibration. They did not train on the
original ASOS event tables. OSF returned HTTP 503 on both download attempts, and the raw-data
directory remained empty. No alternative ASOS files were fabricated.

XGBoost A3 held-out AUPRC was 0.980861 on the synthetic risk task. GraphSAGE validation AUC was
0.948373 on its self-supervised link-prediction task, **not** return prediction or abuse detection.
Neither result establishes accuracy on real merchant data or a benefit from GraphSAGE in A4.
No out-of-distribution store was generated for this session; earlier OOD limitations still apply.

The XGBoost job took 42.199 seconds including evaluation. The GraphSAGE report records 9.1426
seconds for the training phase, with import, device initialisation, data loading, inference and
writing separate. These are observations from this session, not CPU/GPU speed comparisons.

The Gemma check used a separate small synthetic API fixture store. Its three request durations
were 5.895, 15.729 and 33.191 seconds, including validation and any regeneration. This is an
integration rehearsal, not latency percentiles, a load test or a model-accuracy evaluation.
The first rehearsal exposed an existing validator/API mismatch: SCORE labels on unscored cases
were accepted by the LLM validator but rejected by the API. Regression tests reproduced it;
the validator now regenerates those replies and the case schema excludes SCORE where forbidden.

## Notebook environment and XGBoost build

The AMD One-Click Tutorials notebook exposed one MI300X VF to PyTorch, Ubuntu 22.04.5,
glibc 2.35, ROCm 6.4.3, Python 3.12.11. DGL and Docker were not required.

The pinned XGBoost 3.2.0 recipe targets Ubuntu 24.04 and newer ROCm. Two source-build attempts
on this older notebook failed; their logs are preserved. The successful experiment used the
AMD `release_2.1.3-rocm` branch at commit
`ee5234f2c19a202888e1988ef9929672e56b5184`, in its own environment. This is a tested session build,
not a claim of certification for the current AMD 3.2.0 support matrix. The application CPU
dependency remains pinned to 3.2.0.

```bash
git clone --depth 1 --branch release_2.1.3-rocm --recurse-submodules --shallow-submodules \
  https://github.com/AMD-Ecosystem/xgboost.git /workspace/amd-xgboost-213
git -C /workspace/amd-xgboost-213 rev-parse HEAD
# The revision must match the one above; pin it before building if the branch has advanced.
cd /workspace/amd-xgboost-213
AMDGPU_TARGETS=gfx942 CMAKE_PREFIX_PATH=/opt/rocm/lib/cmake CXX=g++ CC=gcc \
  cmake -DUSE_HIP=1 -DUSE_RCCL=1 -DCMAKE_HIP_ARCHITECTURES=gfx942 -S . -B build
cmake --build build -j 16

cd /workspace/ReturnRing
python3.12 -m venv .venv-xgb
.venv-xgb/bin/python -m pip install -r <(grep -v '^xgboost-cpu' requirements/base.txt)
.venv-xgb/bin/python -m pip install --no-deps /workspace/amd-xgboost-213/python-package
# Packaging initially built a CPU library while the HIP build was still finishing.
# Install the completed HIP library explicitly, matching the Python package version.
cp /workspace/amd-xgboost-213/lib/libxgboost.so \
  .venv-xgb/lib/python3.12/site-packages/xgboost/lib/libxgboost.so
export LD_LIBRARY_PATH=/opt/rocm/lib:${LD_LIBRARY_PATH:-}
.venv-xgb/bin/python -c 'import xgboost; print(xgboost.build_info()); assert xgboost.build_info()["USE_HIP"]'
.venv-xgb/bin/python -m scripts.train_amd risk --data data/gpu-synthetic --nature synthetic
```

The installed HIP library SHA-256 was
`bc4ee417ab4340a79fb1ce4ef300c40562116798a2e5a7b85c201ac54ca19c39`.
The risk-training code checks the effective booster device after every fit, so CPU fallback
cannot be reported as successful GPU training. The job manifest records `USE_HIP=true`.

The separate PyTorch environment retained xgboost-cpu 3.2.0 for serving and tests:

```bash
.venv/bin/python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/rocm6.4
.venv/bin/python -m pipeline.generator --calibration pipeline/calibration/asos_calibration.json \
  --out data/gpu-synthetic --seed 42 --scale 1.0 --scenario main
.venv/bin/python -m pipeline.graph --store data/gpu-synthetic
.venv/bin/python -m pipeline.features --store data/gpu-synthetic --out data/gpu-synthetic
.venv/bin/python -m ml.gnn embed --store data/gpu-synthetic \
  --out artifacts/gpu-session/graphsage --models artifacts/gpu-session \
  --device gpu --backend torch --seed 42
```

## Preserved evidence

`returnring-gpu-results.zip` contains model files, GraphSAGE embeddings, metrics, the training
manifest, the modified risk-training source and logs of both successful and failed builds.
The archive is 8,094,729 bytes; remote and downloaded SHA-256 both equal
`d80daa01f806b3226946cbd34cf8d351b0d895c3c9f8c7879ff44eb61e747d89`; ZIP CRCs were verified.
No API key, raw ASOS dataset or compiled XGBoost library is in the archive.

The public repository keeps the compact results JSON and reproduction recipe; the binary
artifacts remain in the local session handoff. The GPU-trained models have not been deployed
to a public demo or substituted into an existing serving snapshot.
