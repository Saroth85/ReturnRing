"""Decision-time customer features and per-customer quantities (US-205, US-206, US-209).

Every quantity here is computed from a StoreSnapshot: the store rows whose available_at is at or
before the decision time, read with contracts.io.read_table(as_of=...) (rule 7, NFR-15, MOD-08).

- lines: one row per order line known at the snapshot, with the outcome settled at the close of its
  return window, and as-of history counts per variant, product, category and customer;
- customer: behaviour features of features/customer_features (no customer value, rule 6);
- residual: R_c and z_c of spec 8.2 over the settled lines, from the product/cohort reference
  model of ml/reference (rule 8);
- value: V_c of spec 8.5 over 12 months, reconciled to the cent with the orders;
- evidence: evidence quality (US-209) with the list of missing evidence (US-307);
- requests: the request-level features of a return, for scoring at request time;
- build and __main__: the as-of tables and the command line.

Design and definitions: docs/customer-features.md. Measured numbers: docs/results/.
"""
