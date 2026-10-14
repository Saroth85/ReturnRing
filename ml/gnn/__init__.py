"""GraphSAGE on AMD (US-202) and the customer embeddings of A4 (US-204, spec 8.1 and 8.3).

Two jobs, one model family:

- `python -m ml.gnn asos`: GraphSAGE on the ASOS customer-variant purchase graph for the return
  prediction task, under both protocols of ADR-10 (B, decision time, first), on the splits,
  populations and leave-one-out features of ml.propensity, side by side with XGBoost trained in
  the same run and with the paper's numbers. A benchmark of the ASOS task (B2), never evidence
  about rings (spec US-202).
- `python -m ml.gnn embed`: GraphSAGE trained by link prediction on the customer-identifier graph
  of the synthetic store known at a decision time (no label, rule 12), written as
  features/customer_embeddings for the A4 ablation of ml/risk (ADR-13 amendment 6).
- `python -m ml.gnn report`: docs/results/us-202-graphsage.md from the metrics files (no torch).

Modules without torch (importable by every job and test): graph (heterogeneous graph indices),
asos_data (ASOS graph and features per protocol), identity_graph (customer-identifier graph of a
store snapshot), report. Modules with torch: runtime (device, seeds, backend, timers), model
(GraphSAGE: DGL when importable, else the same layers in pure PyTorch), train, asos, embeddings.
DEVICE=cpu|gpu picks the device (gpu is torch.device("cuda"), also on ROCm), SEED the seed.
"""
