# ADR-18 — AMD shared inference and GPU training, without hardware comparisons

Date: 16 October 2026. Status: accepted by the project owner.

The submission retains two required implementation goals: Gemma in the working product through
AMD Token Factory, and real AMD GPU training of XGBoost and GraphSAGE. CPU versus MI300X
benchmarks are removed from the delivery plan and from the System screen. Historical B1-B4
code and evidence remain available for research and reproducibility; they are not release gates.
This supersedes the one-MI300X architecture and local-only model rule where they conflict.

## Inference

`LLM_MODE=tokenfactory` selects the fixed AMD HTTPS destination and `Gemma-4-31B-IT` catalog ID.
`RADEON_API_KEY` is read from the process environment, hidden in object representations and never
committed. No arbitrary public endpoint is admitted by the local vLLM allow-list.

Shared Gemma accepts JSON-object output, not strict JSON Schema. Its case-specific schema is
included in the system prompt. The existing validator, regeneration and reviewed template remain
authoritative; a changed decision is rejected. The investigator uses the same client and tool
calling. Shared Gemma is text-only, so warehouse inspections use the manual-review template.

Calls on the process client are serialised and paced below the observed 20 RPM team limit.
Only 429 is retried, at most twice, respecting Retry-After with jitter. Run one API process and
avoid concurrent batch jobs sharing the key: the cap is per team, not per process. A later quota
change must be reviewed. Use synthetic demo cases only; the service logs prompts and outputs.

The API contract becomes 1.9.0: `llm_mode` gains `tokenfactory`, recorded in generation and audit
rows and shown explicitly by the UI. `device` continues to describe the application host; it
does not identify Token Factory hardware. No MI300X utilisation, dedicated GPU or cold-start
claim is made for API inference. Latencies include network, pacing and any provider backoff.

The old cache format and export path retain their original local-vLLM provenance. Token Factory
uses live mode; its outputs are not exported as an MI300X cache. Before using an existing SQL
database with 1.9.0, widen `llm_generations.llm_mode` to VARCHAR(16) and update its CHECK to admit
`tokenfactory`. Existing demo databases may instead be recreated by their normal reset workflow.
Never migrate a database implicitly during startup.

## Training

`python -m scripts.train_amd` runs one existing model job, records environment, revision, command,
data nature, exit status and duration, and preserves the trained models and metrics in a unique
directory. It refuses a non-HIP XGBoost build or a PyTorch environment without an AMD GPU. The
training code checks the effective XGBoost device and refuses a silent CPU fallback.

GraphSAGE uses the existing pure-PyTorch backend, whose layers support ROCm; DGL is optional.
The ASOS route needs the original hash-verified files. If the source is unavailable, training on
the calibrated synthetic store is a separate experiment, labelled synthetic, never a new ASOS
accuracy result. A GPU kernel smoke test alone is not completion of either model-training goal.

No GPU speedup is calculated. Model quality, regression, calibration and held-out evaluation
remain part of the deliverable. Unsupported environments and failed experiments are recorded.

## Sources

- [Event technology requirement and Token Factory FAQ](https://lablab.ai/ai-hackathons/amd-developer-hackathon-act-iii).
- Token Factory manual supplied by the owner, 16 October 2026.
- [AMD XGBoost prerequisites](https://rocm.docs.amd.com/projects/xgboost/en/latest/install/install.html).
- [PyTorch ROCm releases](https://pytorch.org/get-started/previous-versions/).
