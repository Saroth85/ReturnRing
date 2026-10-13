"""The serving snapshot: everything the API reads from the offline pipeline, and nothing else
(ADR-16; CLAUDE.md rules 7, 11 and 12).

python -m pipeline.serving --data data --artifacts artifacts --out serving

The API container never mounts data/ (rule 12, infra/docker-compose.yml): it mounts serving/
read-only and loads it into memory at startup. The export copies, with contracts.io and their
provenance, the store tables, the feature and score tables and the model files the API runs at
request time, adds a manifest with the sha256 of every file, and refuses any path or column of
the ground truth.
"""
