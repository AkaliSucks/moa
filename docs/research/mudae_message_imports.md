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

## Intentional limits

The parser only extracts fields that are clear in the observed format. It does
not yet infer ownership, tags, series aliases, disable-list status, or a
character's permanent global identity. Those need separate, explicit modeling
instead of being guessed from one message.
