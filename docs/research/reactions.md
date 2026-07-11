# Kakera reaction research

## Source

- Mudae Wiki, "Kakera Reactions", accessed 2026-07-11.

## Scope of the first dataset

This dataset stores stable, baseline facts about the eleven Kakera reaction
types: value ranges, baseline expected values where documented, and whether a
reaction consumes no power, standard power, or variable power.

It intentionally does not calculate final payout or reaction-power cost. Those
depend on player and server state, including Gold badges, Ruby III, Kakera Tower
Floor 8, character keys, $starwish bonuses, boosts, Premium, slash commands,
and Perks. A later reaction calculator will combine these sources explicitly.

## Important modeling notes

- Purple is free and fixed at 100 Kakera.
- Sapphire IV and Silver-or-higher keys can transform some spawned colors; they
  are state-dependent transformations and do not alter the baseline dataset.
- Light Kakera's stored average is its documented baseline with zero Kakera
  Tower Floor 10 upgrades.
- Dark and Chaos results are intentionally represented without a numeric
  average until their outcome tables are independently modeled.
