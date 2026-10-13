# ADR-11: Age is a forbidden feature

Status: accepted, 13 Oct 2026. Extends CLAUDE.md rule 6 and NFR-04.

## Context

The ASOS customer table has `yearOfBirth`. The spec excluded gender and country from every score but
said nothing about age, which is a protected ground (Charter of Fundamental Rights of the EU, art. 21).
The column is also dirty: 11,973 train rows hold the placeholder 1900 and some values are after the data
period (origin: ASOS GraphReturns; nature: real data; measured 12 Oct 2026).

## Decision

Age, in any form (year of birth, age, age band), never enters a score, a model or a policy rule. Like
gender and country, it may appear only in descriptive statistics.

## Consequences

- CLAUDE.md rule 6 lists age.
- The forbidden-feature check (FAIR-01, `contracts/parquet.py`) gains the ASOS column names
  (`yearOfBirth`, `isMale`, `shippingCountry`, `Country_*`) in the US-101 session, which writes the
  ASOS contracts.
- The synthetic store has no age column, so the change affects only the ASOS models.
