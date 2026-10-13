# ADR-12: Rule 15 applies to the repository, not to third-party images we pull unmodified

Status: accepted, 13 Oct 2026. Clarifies CLAUDE.md rule 15 and LIC-01.

## Context

The GPU containers run third-party images pulled by digest: `vllm/vllm-openai-rocm`, `rocm/dgl` and
`rocm/dev-ubuntu-24.04` (the trainer-gbm base). They ship ROCm components under NCSA, AMD and
GPL-3.0 licences (for example ROCgdb) and Ubuntu packages under GPL and LGPL. The trainer-gbm image
also installs numpy, whose licence expression is "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND
CC0-1.0". None of these can be replaced within the hackathon week.

## Decision

Rule 15 governs what the repository contains and distributes: our code and the Python and npm
dependencies the product installs (`requirements/`, the UI lock file), checked by
`scripts/check_licenses.py` (LIC-01). Images and packages that are pulled unmodified at build or
run time, from their official sources, pinned by digest or version, and never redistributed by us,
are outside the gate. They are listed with their licence in the README.

## Consequences

- No image, wheel or layer built from these sources is published by this project (no registry
  push, no image export in the demo package).
- `infra/docker/requirements-gbm*.txt` stay outside the licence gate; they are pinned and minimal.
- A dependency that our code imports in the product (the API container, the UI) still needs a
  licence from the rule 15 list.
