# Account graph and clusters

Story US-203 (links between accounts with their evidence), with the dispute support of US-310;
spec section 8.3; tests MOD-03, MOD-08, MOD-11, MOD-12 and FAIR-01 (spec section 12). Code:
`pipeline/graph/`. Measured results: [results/account-graph.md](results/account-graph.md), written
by a command, never by hand.

```bash
# Build features/links and features/clusters at a decision time (default: the extraction instant,
# 00:00 UTC on the day after the store period). Reads <store>/store only, never the ground truth.
python -m pipeline.graph --store data [--out data] [--as-of 2026-10-01T00:00:00Z] \
  [--disputed disputed.json]
python -m pipeline.graph --store data/ood          # OOD store: evaluation only, never training

# Offline evaluation (reads truth/labels, rule 12): rewrites docs/results/account-graph.md and
# artifacts/graph/evaluation.json from the data, for the main and the OOD scenario.
python -m pipeline.graph.evaluate --data data
```

The build prints a JSON summary (links, cluster links, clusters, hubs, provenance) and writes both
files with `contracts.io.write_table`, so they pass contract v2 and carry their provenance: nature
synthetic, an origin that names the method, its parameters, the decision time and the store it
read, and a period from the oldest account to the last day of data at the decision time.

## What the graph reads

`pipeline.graph.snapshot.load_snapshot(root, as_of)` reads seven store tables through
`contracts.io.read_table(..., as_of=T)`, which keeps the rows with `available_at <= T` (rule 7,
MOD-08): customers (creation), identifiers (first use), coupons (welcome redemptions), orders,
order lines, deliveries and returns (to know which lines were delivered and returned). It reads no
price or amount, so customer value never reaches the cluster features (rule 6), and no ground
truth (rule 12). A test builds the graph at T from a whole store and from the same store extracted
at T, and requires identical outputs.

## Links: features/links

The graph is bipartite, accounts and identifier values (salted hashes), projected on pairs of
accounts. A shared event is one value used by both accounts (ADR-13). Its instant is the later of
the two first uses, when the pair came to share it, and its `d` is the number of accounts that use
the value at T.

| Column | Definition |
| --- | --- |
| kinds | Shared kinds, strongest first, each once |
| weight | w_ij = 1 - prod over kinds (1 - w_k); card 1.0, e-mail 0.9, device 0.8, address 0.6, phone 0.6, IP prefix 0.2 |
| weight_decayed | The same product over w_k*, the largest w_k exp(-dt / 180 days) / log2(d) among the pair's values of kind k; dt runs from the instant the pair came to share the value to T; never above weight |
| first_shared_at, last_shared_at | Earliest and latest of those instants |
| n_shared_events | Values shared; max_sharers: the largest d among them |
| disputed | An operator disputed the link (US-310) |

A link between two accounts only keeps its weight (1/log2 2 = 1); a pickup point shared by 200
customers keeps 1/log2(200) = 0.13 of it.

### Hub pruning

A pickup point used by 200 customers would make 19,900 pairs alone, a mobile carrier prefix a few
hundred. A value is a **hub** when more than 20 accounts use it (the cluster size limit: no
admissible cluster is that large) and its kind alone stays below the cluster threshold (address,
phone and IP prefix at 0.8). Then:

- a hub creates no candidate pair by itself; candidate pairs share a value that is not a hub, or
  two hub values;
- every value a candidate pair shares, hubs included, enters its link with its real d, counted
  before pruning, so the down-weighted contribution of a pickup point is exact;
- the pairs left out share one hub value and nothing else, so their weight is that kind's weight,
  below the threshold: no cluster changes;
- card, e-mail and device are never pruned: one shared value already makes a cluster link.

The evaluation compares the result with the brute force (no pruning) on the full store: same
links on every pair kept, same clusters, and every pair left out below 0.8. A random-store test
checks the same against a pure-Python implementation of the formulas.

## Clusters: features/clusters

Clusters are the connected components of the links with `weight >= 0.8` (a 1e-9 tolerance absorbs
floating-point rounding) that are not disputed. A component of more than 20 accounts is split with
Louvain (networkx, seed 42, nodes and edges inserted in sorted order), recursively, into connected
parts. A component Louvain cannot split, such as a clique, stays whole and shows in the largest
clusters of the report. Cluster ids are `CL-` and 12 hex digits of the sha256 of the sorted member
ids: opaque, stable for the same members, and never a ground-truth ring or family id.

Every account known at T has one row. An account without a cluster link has a null `cluster_id`,
`cluster_size` 1, and its features computed on itself with the same definitions.

| Feature | Definition |
| --- | --- |
| cluster_size | Accounts |
| shared_strong_ids | Card, e-mail and device values used by two accounts of the cluster or more |
| strong_id_share | shared_strong_ids over every value used by two accounts of the cluster or more; 0 when none |
| n_distinct_cards | Card values used by its accounts |
| creation_spread_days | Days from the first to the last account creation |
| welcome_coupons, welcome_coupons_per_account | Welcome coupons **redeemed** by its accounts, and per account |
| cluster_return_rate | Delivered lines returned on the withdrawal path, over delivered lines not returned on the guarantee path (a defect is not behaviour, rule 4); null before any such line |

A cluster says that accounts are linked, never that they are the same person (rule 11), and it is
not a judgement: the family Rossi is a cluster, and these features (old accounts, distinct cards,
no repeated coupons, normal returns) keep it apart from a ring. Whether a cluster ends in Q3 or Q4
is the risk model's and the engine's decision (US-204, FAIR-03), checked there. No feature name is
forbidden (`contracts.parquet.forbidden_features`, FAIR-01), and a test multiplies every price by
37 and finds identical features.

## Disputes (US-310)

`pipeline.graph.build.dispute_link(result, snapshot, a, b)` marks the link disputed and recomputes
the connected component of cluster links that held the two accounts, so the result equals a full
rebuild that knows the dispute (a Louvain split depends on the whole component). It returns a
`DisputeOutcome`: the new links and clusters, the accounts recomputed, and their rows before and
after, for the before-and-after view. It has no side effect: the API writes the audit log entry
(who, when, why) and keeps the disputed pairs, which a rebuild takes back with
`build_graph(snapshot, disputed=...)` or `--disputed` (a JSON list of `[a, b]` pairs). Disputing a
disputed link changes nothing; a missing link raises `KeyError`.

## Decisions taken on spec 8.3

1. **Clusters use w_ij, without decay**, as the spec defines the threshold. The decayed weight is
   evidence: how recent and how specific the sharing is, for the case card and the risk model.
   With first-use rows, dt is the time since the pair came to share a value, so a family sharing
   a flat since 2021 and a ring opened in March both fall below 0.8 by October; the report has a
   variant that clusters on `weight_decayed`, measured, and it finds almost no cluster. The time
   constant stays a hypothesis (open point D-15); an identifier observation log
   (`store/identifier_uses`, open point of ADR-13) would let dt mean the last shared use.
2. **Pickup points stay out of clusters through the kind weights.** An address weighs 0.6 and an
   IP prefix 0.2, so a pickup point, even with a shared carrier prefix (0.68), never reaches 0.8;
   the log2(d) factor lowers its weight in `weight_decayed`. The threshold-0.6 variant of the
   report shows what happens otherwise.
3. **One value per kind.** Two shared values of one kind count once in w_ij; in the decayed weight
   the kind takes its strongest value.
4. **Hub pruning** as above: lossless for clusters, exact for every link written.
5. **Welcome coupons are redeemed ones.** Every new account is issued one; a ring redeems them.
6. **Return rate on the withdrawal path only** (rule 4).

## Evaluation (offline: MOD-03, MOD-11, MOD-12)

`pipeline/graph/evaluate.py` builds the graph exactly as in production, then reads
`truth/labels` (an offline evaluation job, rule 12 as amended by ADR-13). Nothing is fitted, so
there is no training split; the out-of-distribution store is evaluated on rows of its own and
never used to choose a parameter. Definitions, on the accounts known at T:

- **Ring pairs (MOD-03).** A pair is predicted when its accounts share a cluster and at least one
  is a ring member; it is a true positive when both are in the same ring. Precision = true
  positives / predicted; recall = true positives / pairs of the same ring. Pairs of family
  accounts are not counted against precision, because families are clustered on purpose; whether
  they mix with anyone is the MOD-11 check. Target: precision >= 0.90, recall >= 0.80 (main).
- **Ring clusters.** Member precision = members of the dominant ring / members, over clusters
  holding a ring member; member recall = members in the best cluster of their ring / ring members;
  a ring is exact when one cluster holds exactly its accounts, whole when one cluster holds all of
  them.
- **Unrelated pairs (MOD-11).** Pairs in one cluster that are not of the same ring or family. A
  common address is an address value used by two groups or more (pickup points and collective
  addresses, which no truth table marks); the report counts the unrelated pairs joined among their
  users, lists the most used ones without their values, and the largest clusters by composition.
  F-ROSSI and F-ROSSI-CARD must be clusters of exactly their accounts.
- **Diagnostics.** Same-ring pairs by the weight of their direct link; cluster features by cluster
  type; the pruning check; variants (decayed weight, thresholds 0.6, 0.7 and 0.9).

## Limits

- **Circularity.** The generator plants the sharing the graph looks for, so the main scenario
  measures consistency with the generator. The OOD scenario, where rings share weakly and miss
  identifiers, is the honest stress test, and its recall is far below the target (see the
  results). Most of its same-ring pairs have no direct link, and many linked ones weigh less than
  0.8 (an address, an IP prefix): the same property that keeps pickup points out. A lower threshold mixes unrelated accounts (the
  threshold-0.6 variant). More recall needs other evidence, such as the GraphSAGE embeddings of
  spec 8.3 (A4), measured against the cluster features on the same splits, never a threshold tuned
  on the OOD store.
- **Strong hubs.** A card or device used by many accounts (a shop terminal) would form one large
  clique that Louvain cannot split; it is kept whole and shows in the report.
- **Decay.** See decision 1.
