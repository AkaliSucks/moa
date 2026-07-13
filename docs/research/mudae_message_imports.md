# Mudae message imports

## Source types supported first

MOA's first parser supports copied text from:

- `$top` ranking pages;
- `$im <character>` responses;
- standard roll cards that show `Claims` and Kakera value.

The CLI accepts a UTF-8 text file or `--clipboard`. Clipboard mode is intended
for the normal workflow: copy a Mudae message in Discord, then immediately run
the matching MOA parse command.

## Provenance rule

These messages are direct Mudae bot output and are the authoritative source for
live ranks and server-specific Kakera values. MOA must preserve raw imported
messages in a later storage layer alongside the parsed observations and capture
time.

## First local catalog import

`moa import top --clipboard` records each parsed character, appends a
timestamped claim-rank snapshot, and archives the exact copied message in the
local SQLite database. The database is intentionally local and ignored by Git:
it is personal account data, not packaged reference knowledge.

`moa import im --server <label> --clipboard` enriches the canonical character
with observed gender and roulette metadata, records the current global ranks,
and stores the displayed Kakera value under that explicit server label. Kakera
value is never treated as a universal character property.

`moa import mm --server <label> --account <label> --clipboard` records one
keyed-harem page from `$mmy=`. `$mmyk=` is also supported: its per-character
Kakera values are stored against that server/account rather than treated as
global values. The harem list does not include series names, so MOA links an
entry only when its character name resolves uniquely in the local catalog.
Other entries remain explicitly unresolved until a matching `$im` import
provides reliable identity metadata.

`moa import auto` also recognizes keyed-harem pages. Pass the same
`--server` and `--account` context, plus `--scan <id>` when importing pages
into a multi-page harem scan.

`moa import mmr --server <label> --account <label> --clipboard` records direct
owned-character evidence from one ranked `$mmr` or `$mmrk` page. The parser
keeps the claim rank and optional displayed Kakera value, and links names to
the catalog only when the name is unique. `moa import auto` recognizes this
format as `ranked_harem` too. Use `moa catalog top --owned-only` to restrict
the imported `$top` list to characters directly observed in those pages.
Missing owned evidence is not proof of unownership: a single `$mm` page is
only a partial observation until every page has been imported.

For an accurate full-harem snapshot, begin a scan before copying pages:

```powershell
uv run moa harem begin --server "Lake Arrowhead 2025" --account "ernieuuu"
uv run moa import mm --scan <id> --server "Lake Arrowhead 2025" --account "ernieuuu" --clipboard
uv run moa harem status <id>
uv run moa harem complete <id>
```

Import every page under the same scan ID. MOA activates that scan only when it
has all pages advertised by Mudae, so a partial page can never become the
current basis for key-farm recommendations.

`moa import bonus --server <label> --account <label> --clipboard` stores a
timestamped `$bonus` snapshot. Every displayed line is retained; the
decision-critical values (roll, wish, Starwish, key, and Kakera-button
modifiers) are additionally extracted as typed fields.

`moa import wishlist --server <label> --account <label> --clipboard` stores a
timestamped `$wl` snapshot. `$wl` is the authoritative source for active
Starwishes because each Starwish is marked with `⭐`; `$sw` itself only displays
help and slot information.

## Intentional limits

The parser only extracts fields that are clear in the observed format. It does
not yet infer unownership, tags, series aliases, disable-list status, or a
character's permanent global identity. Those need separate, explicit modeling
instead of being guessed from one message.
