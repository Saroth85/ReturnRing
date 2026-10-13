"""Navigli Fashion synthetic store generator (US-102..US-105, docs/synthetic-store.md).

python -m pipeline.generator --calibration <json> --out data --seed 42 [--scale 1.0]
[--scenario main|ood]

Deterministic (DATA-04): the same seed and calibration give byte-identical files. The store goes to
<out>/store, the ground truth to <out>/truth (rule 12: running services never read it), and the
out-of-distribution scenario to <out>/ood/store and <out>/truth/ood (never used in training,
US-105).
"""
