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

`$topo` pages must include the server where the message was observed so owner
claims do not leak between servers. In Discord, run `$topo` and copy the full
response, then run the matching command:

```powershell
moa import top --server "Lake Arrowhead 2025" --clipboard
```

Owner names shown after `=>` are retained as server-scoped claimed-by evidence,
while rows without an owner are retained as `Unclaimed`. Catalog search can
therefore report reasons such as `Unavailable (claimed by xuppii)` only for
that server. A normal `$top` page without owner names can still omit
`--server`.

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
owned-character evidence from one ranked `$mmr`, `$mmrk`, `$mmrt`, or combined
`$mmrkty+` page. The parser keeps the claim rank, optional displayed Kakera
value, `$wa`/`$ha`/`$wg`/`$hg` roll types, and key markers when the combined
command provides them. Gender markers come from `$im` character details and
may contain both `:female:` and `:male:`. Names link to the catalog only when
unique. `moa import auto` recognizes this format as `ranked_harem` too. Use
`moa catalog top --owned-only` to restrict the imported `$top` list to
characters directly observed in those pages.

MOA preserves Mudae's roll-type markers exactly: `$w`/`$wx` and `$m`/`$mx`
cover animanga and games together, `$wa`/`$ma` are animanga-only, `$wg`/`$mg`
are games-only, and the corresponding husbando forms are `$h`/`$hx`, `$ha`,
and `$hg`. The catalog displays these as a `Roll type` field rather than
guessing from a character's gender or general `$im` roulette label.
Missing owned evidence is not proof of unownership: a single `$mm` page is
only a partial observation until every page has been imported.

For a complete ownership snapshot, start an owned scan and import every ranked
harem page. Prefer `$mmrkty+` so one page set supplies ownership, ranks, Kakera,
roll types, and keys:

```powershell
uv run moa harem begin --kind owned --server "Lake Arrowhead 2025" --account "ernieuuu"
uv run moa import mmr --scan <id> --server "Lake Arrowhead 2025" --account "ernieuuu" --clipboard
uv run moa harem complete <id>
```

After completion, `moa catalog top --unowned-only --server <label> --account
<label>` is safe to interpret as absent from that complete imported harem.

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

`$adl` is imported as series-level rollability evidence. Start a complete scan,
then run `$adl` in Discord, copy the full response, and use the exact command
MOA prints for each page:

```powershell
moa adl begin --server "Lake Arrowhead 2025" --account "ernieuuu"
moa import adl --scan 3 --server "Lake Arrowhead 2025" --account "ernieuuu" --clipboard
```

The `3` is the scan ID printed by `moa adl begin`; replace it with your actual
scan ID. `$ad <series>` modifies that list, so MOA matches antidisable state to
the character's catalog series and does not model it as an individual-character
setting.

## Discord listener

The first Discord ingestion path is available through `moa discord listen`.
Create a Discord bot application, enable Message Content Intent, invite it to
the target server with View Channel and Read Message History permissions, and
keep its token outside the repository:

```powershell
$env:MOA_DISCORD_BOT_TOKEN = "your-bot-token"
moa discord listen
```

The listener sets the bot presence to `Watching Mudae progress`. Customize it
with `--status`, for example:

```powershell
moa discord listen --status 'Tracking Mudae data'
```

MOA associates a configured Discord server ID and user ID with the latest
`$`/slash command in each channel, then imports recognized Mudae responses and
message edits through the existing automatic importer. Roll commands such as
`$m`, `$wa`, `$ha`, `$wg`, and `$hg` are captured as roll observations, and raw
reactions from the configured user are tracked so Mudae Kakera receipts are
assigned to the right account. It automatically opens and completes
harem/antidisable scans when it sees page 1 through the final page.
When Mudae responds to a slash interaction, MOA can recover the invoking user
from Discord's interaction metadata instead of relying on message content.
`--mudae-user-id` or `MOA_MUDAE_BOT_ID` can optionally restrict imports to the
real Mudae bot. The bot does not impersonate the user or click Mudae
components; pagination still follows the user's normal Discord interaction.
Cached message edits are handled locally when possible, with a debounced REST
fallback for uncached messages.

## Intentional limits

The parser only extracts fields that are clear in the observed format. It does
not yet infer tags, series aliases, disable-list status, or a
character's permanent global identity. Those need separate, explicit modeling
instead of being guessed from one message.
