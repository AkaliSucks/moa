# Kakera Tower research

## Scope

This note records verified first-tower facts. Optimization priorities belong in
the optimization layer, not this document.

## Source

- Mudae Wiki, "Kakera Tower", accessed 2026-07-11.

## Core rules

- A tower has 12 unique floor types, buildable in any order.
- Each completed floor increases the next floor's cost by 5,000 Kakera.
- `$destroy` refunds tower investment and has a 20-hour cooldown.
- After the first 12 floors, the same floor types can be built again with
  reduced or replacement effects.

## MOA modeling decision

`tower.json` contains immutable floor definitions and their first-tower effect.
Progression formulas and account-specific ROI belong in later simulation and
optimization modules.

The service layer accesses these definitions through `TowerRepository`. The
current repository reads JSON; a future SQLite repository can replace it
without changing `TowerService` or its callers.
