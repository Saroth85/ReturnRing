# Main review — 17 October 2026

Reviewed base: `789af50579a1ee4a8bedb7abb2e8f7dc702fa058` (`main`).
This maintenance change is independent of the AMD integration and online-demo work in PR #15.

## Findings fixed

| Priority | Trigger and previous behavior | Correction |
| --- | --- | --- |
| P1 | An export destination equal to, inside or above a source tree could delete input models during cleanup. | Resolve and reject output overlap with both data and artifacts before any mutation. |
| P1 | Two catalogue requests could both pass the active-task check before waiting for the shared ID counter. | Acquire the existing counter lock before checking for an active task. |
| P1 | Confirmations of distinct checkout snapshots could both see the same coupon as unused before waiting for an order ID. | Check coupon redemption and current terms after acquiring the existing order counter lock. |
| P2 | Existing graph files from another store were exported with current-store provenance. | Require graph lineage to derive from the current store before reuse; missing graphs still rebuild. |
| P2 | A malformed local LLM authority failed late or exposed raw URL fragments in an error. | Validate host/port during configuration and return sanitized errors. |
| P2 | Boolean or negative completion-token metadata could reach persistence; vision also discarded a measured zero. | Share optional token-count validation between text and vision, preserving valid zero and summing valid retries. |
| P2 | The KPI abstention link opened the default queue instead of insufficient-evidence cases. | Navigate to the existing insufficient-evidence tab. |
| P2 | Reviews and warehouse inspection showed only the first 25 matching returns. | Add bounded URL pagination, preserve search and reset it to page one after edits; keep earlier pages reachable after a page becomes empty. |
| P3 | A case opened with the mouse kept its New badge when returning to the queue. | Use the same seen callback for row, link and keyboard navigation. |

## Cleanup and comments

- Replace the operator-only assumption parser and renderer with the shared economics component.
  Remove the obsolete comment promising that component in the future; retain fixture invariants.
- Rename the shared parser to `parseAssumptionLabel.ts` so Windows can distinguish it from
  `AssumptionLabel.tsx`.
- Use `fileURLToPath` for test fixture paths and normalize bundle-relative paths to `/`.
  Pin executable modules and generated TypeScript files to LF for reproducible Windows checks.
- Explain why mutable checks follow the existing counters and why exports must validate paths
  and lineage before reusing or replacing files.

No dependency, schema, policy threshold or legal-text change is needed.

## Validation and limits

- UI: 1,159 tests pass; TypeScript, ESLint, production build and bundle isolation checks pass.
- API: both counter interleavings reproduced before the fixes; the complete catalogue and
  checkout test modules pass (33 tests).
- LLM/vision: 112 focused tests pass; the client has 100% line and branch coverage.
- Export: overlap, normalized paths, graph lineage, missing graphs and deterministic full-export
  behavior tested. The output-symlink case requires privileges unavailable on this Windows host.
- Ruff, formatting and mypy targeting Linux pass.

The new concurrency regressions use two real SQLite transactions with a competing commit at
the allocator boundary. They reproduce the PostgreSQL READ COMMITTED visibility window, but do
not replace a real PostgreSQL concurrency run; no PostgreSQL test URL is configured locally.
The whole CPU suite cannot collect on Windows because `pipeline/ingest/cli.py` imports the Linux
`resource` module. Linux CI remains the complete-suite gate. Existing React act warnings in
unrelated UI tests remain visible.
