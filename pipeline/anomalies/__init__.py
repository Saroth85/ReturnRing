"""Product anomalies separated from customer behaviour (US-208, US-606; spec 8.9; MOD-10, E2E-04).

- detect: observed and expected returns per variant, reason and sales week against the category
  baseline, z of spec 8.9 and the flag rule (thresholds versioned, D-15);
- build and __main__: features/product_anomalies at each decision time (rule 7, MOD-08);
- catalogue: the catalogue task of a flagged anomaly and the before/after weekly return series of
  its variant (US-606);
- evaluate: the offline evaluation against truth/variants (rule 12), which rewrites
  docs/results/us-208-product-anomalies.md.

No output here names a customer: an anomaly opens a catalogue task and never a customer hypothesis.
Design and definitions: docs/product-anomalies.md.
"""
