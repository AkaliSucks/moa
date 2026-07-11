# Kakera Badge research

## Source

- Mudae Wiki, "Kakera Badges", accessed 2026-07-11.

## Modeling decision

Badge definitions and their default base values are immutable knowledge.
`$badgevalue` is server configuration, so effective purchase costs are
calculated from the server-provided base value rather than embedded in a badge
definition.

Ruby IV's 25% discount is account state, not a property of every badge. It is
only active after the Ruby IV purchase that unlocks it.

## Confirmed baseline cost rule

For a badge with server base value `x`, level `n` costs `n * x` before Ruby IV
discounts. Server owners may alter each badge's base value with `$badgevalue`.
