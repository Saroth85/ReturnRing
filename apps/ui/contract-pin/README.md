# contract-pin/

A copy of the API contract the UI was built on (ADR-15 contract 1.1.0, repository commit
`5b3c8e9`): `json/api` (the JSON Schemas), `examples/api` (the examples the mock adapter
serves), `vocab.py` and `levers.py` (read by `scripts/gen-contract.mjs`).

The API on this branch serves contract 1.5.0. Until the UI is migrated to it, the UI generates
its types from this copy and its mock mode serves these examples, so `npm run check` is
self-consistent. Against the live API the UI shows its contract-mismatch notice.

Remove this folder when the migration is done: the scripts then read `../../contracts` again
(or `RETURNRING_CONTRACTS_DIR`).

The image already accepts the `VITE_READ_ONLY` build argument that compose passes for the public
read-only demo; the UI starts reading it with the migration.
