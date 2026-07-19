# MOA architecture and integrity audit

Scope: current working tree, including uncommitted changes. This was a read-only audit. I did not modify files, start a Discord listener, or run the full test suite.

The working tree was already substantially dirty, including parser, listener, repository, service, CLI, tests, documentation, and untracked database files. Findings therefore describe the repository exactly as it currently exists—not necessarily the last commit.

## A. Concise architecture map

```text
Discord Gateway
    │
    ▼
_MOADiscordClient
    │ Discord events, edits, reactions, interactions
    ▼
DiscordListenerService
    │ identifies user/server, associates commands with responses
    ▼
MessageRouter ───────────────► MudaeParser
    │                            parsing and normalization
    ▼
AutomaticImportService
    │ dispatches parsed observations
    ▼
CatalogService / TopSearchService
    │ domain orchestration and derived views
    ▼
CatalogRepository
    │ schema management, persistence, queries, repairs
    ▼
SQLite moa.db
    │
    ▼
Typer/Rich CLI reports and optimization views
```

Primary packages:

| Package | Current responsibility |
|---|---|
| `cli` | Typer commands, Rich tables, configuration, listener startup, repair commands |
| `core` | Configuration and profile/account context |
| `parser` | Discord text normalization, response routing, Mudae-specific parsing |
| `models` | Parsed records, catalog entities, observations and result models |
| `services` | Listener orchestration, automatic importing, catalog queries, top search |
| `repositories` | SQLite schema, migrations-in-place, CRUD, projections and repair operations |
| `simulator` | Optimization/simulation logic |
| `knowledge`, `loader` | Static/domain knowledge loading |
| `utils` | Cross-cutting helpers |
| `database` | SQLite connection defaults and helpers |

The intended dependency direction is broadly sensible:

```text
CLI/Discord → services → parsers/models → repository → SQLite
```

The most important boundary violation is that the listener currently performs significant command-state management, response classification, identity attribution, deduplication, workflow handling, and import orchestration in one class.

## B. Confirmed strengths

1. **There is a recognizable layered architecture.**

   Parsing, automatic importing, catalog orchestration and persistence are represented by separate classes rather than being embedded entirely in the Discord client. Key entry points include `DiscordListenerService`, `AutomaticImportService`, `CatalogService`, and `CatalogRepository`.

2. **Server and account identity are modeled explicitly.**

   Discord server IDs and user IDs are part of configuration and stored account contexts. This is materially safer than relying only on display names. See [config.py](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/core/config.py:51).

3. **Slash-command interaction metadata is used when available.**

   The listener attempts direct attribution through Discord interaction metadata before falling back to command context. That is the correct primary source for multi-account servers.

4. **Parsed observations retain time and provenance.**

   The schema has `import_events` and numerous observation tables rather than only overwriting a single current-state row. This is a useful foundation for audits, historical state, freshness checks and a future grounded assistant. Schema creation begins in [catalog_repository.py](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/repositories/catalog_repository.py:3371).

5. **SQLite foreign keys are enabled.**

   Each connection executes `PRAGMA foreign_keys = ON` in [sqlite.py](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/database/sqlite.py:8).

6. **Several in-memory listener collections are bounded.**

   Message and command-context caches are trimmed after reaching configured thresholds. This prevents straightforward unbounded growth during long listener sessions.

7. **The test layout targets important boundaries.**

   Dedicated tests exist for the parser, router, automatic importer, listener, catalog service, configuration and CLI. This is a better base than only end-to-end CLI tests.

8. **Token handling prefers environment variables.**

   `MOA_DISCORD_BOT_TOKEN` and `MOA_MUDAE_BOT_ID` are supported, and CLI help recommends an environment variable rather than exposing a token in shell history. See [main.py](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/cli/main.py:137).

## C. Confirmed problems

### C1. Channel-wide mutable command context can misattribute or discard data

Severity: P0

In [DiscordListenerService.handle_message](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/services/discord_listener_service.py:152), the fallback context is stored by channel:

```python
self._contexts[message.channel.id] = context
```

This means two configured users operating in the same channel share one mutable fallback slot. More importantly, an unsupported command removes that channel’s context:

```python
self._contexts.pop(message.channel.id, None)
```

Consequences:

- One user’s command can replace another user’s pending context.
- An unrelated unsupported command can interrupt an active paginated scan.
- Delayed edits, confirmations or roll responses can be associated with the latest command rather than their actual initiator.
- Exact interaction metadata can prevent some failures, but prefix commands and Mudae messages without metadata still rely on this fallback.

This directly conflicts with MOA’s multi-account-per-server design.

### C2. Duplicate protection is not durable and has arbitrary eviction

Severity: P0

The listener maintains `_seen_payloads` only in memory. Once it grows beyond the threshold, it converts the set to a list and retains an arbitrary subset:

```python
self._seen_payloads = set(list(self._seen_payloads)[-1000:])
```

See [discord_listener_service.py](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/services/discord_listener_service.py:361) and the equivalent later path around line 555.

Because sets are unordered, this is not recency-based eviction. A recently processed payload can be discarded while an old one is retained.

The deduplication state also disappears on restart. Message edits, reconnect replay, reaction events or repeated imports can therefore produce duplicate observations unless every downstream table happens to reject them independently.

### C3. Event persistence and projection updates lack an evident unit-of-work boundary

Severity: P0

`AutomaticImportService` parses and then calls individual catalog import methods, for example sphere routing in [automatic_import_service.py](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/services/automatic_import_service.py:306).

The repository opens ordinary SQLite connections through:

```python
def _connection(self) -> sqlite3.Connection:
    return connect(self._database_path)
```

See [catalog_repository.py](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/repositories/catalog_repository.py:3973).

There is no repository-wide event transaction abstraction visible between:

1. recording the raw import event;
2. updating normalized entities;
3. adding the command-specific observation;
4. updating current ownership or derived state.

A process failure between those operations can leave a recorded event without its projection, or a projection without a reliably deduplicated source event.

### C4. Synchronous SQLite operations run in the Discord async event path

Severity: P1

Discord handlers are asynchronous, but parsing and repository imports are called synchronously. The connection helper uses standard `sqlite3`, not `aiosqlite`, despite `aiosqlite` being a declared dependency.

The SQLite helper also does not configure:

- WAL mode;
- `busy_timeout`;
- a dedicated serialized writer;
- retry behavior for `database is locked`.

See [sqlite.py](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/database/sqlite.py:8).

A large page import, database contention, backup or repair operation can block the Discord event loop. Under concurrent message/edit/reaction traffic, this increases latency and raises the chance of lock failures.

### C5. Persistence responsibilities are concentrated in one oversized module

Severity: P1

[CatalogRepository](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/repositories/catalog_repository.py) is approximately 180 KB. It contains:

- schema creation;
- compatibility/schema evolution;
- character persistence;
- account and server contexts;
- roll, claim, divorce and reaction observations;
- harem scans;
- wishlist and disablelist data;
- timers, tower, loot, profile and sphere observations;
- top searches;
- repair and backup behavior.

This mixes schema administration, command-specific repositories, read models, write models and repair operations. It makes transaction boundaries hard to see and increases regression risk when adding commands.

### C6. Listener, parser and CLI are oversized command registries

Severity: P1/P2

Approximate sizes:

- `catalog_repository.py`: 180 KB
- `cli/main.py`: 132 KB
- `parser/mudae.py`: 69 KB
- `discord_listener_service.py`: 58 KB

The listener’s `_expected_kind_for_command` and `_resolve_message_kind`, the router and the Mudae parser each encode overlapping knowledge about command names and response shapes.

Adding a command currently tends to require coordinated changes across:

- command aliases;
- expected listener kind;
- response classification;
- parsing;
- importer dispatch;
- repository storage;
- tests;
- possibly CLI presentation.

That is hidden coupling rather than a single explicit command adapter contract.

### C7. Runtime schema evolution is embedded in application code

Severity: P1

The repository creates and evolves tables directly through a long block of `CREATE TABLE`, index and compatibility operations beginning around [catalog_repository.py](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/repositories/catalog_repository.py:3371).

Although Alembic is declared as a dependency, no normal Alembic migration tree was present in the inspected repository layout. This creates risks:

- migration order depends on application startup code;
- downgrade and rollback behavior is unclear;
- migrations are difficult to test independently;
- production data backup requirements are not enforced by migration tooling;
- schema compatibility logic remains permanently mixed with ordinary queries.

### C8. The default live database is inside the repository

Severity: P1

The default path resolves to:

```text
data/database/moa.db
```

See [sqlite.py](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/database/sqlite.py:5).

`data/database/` is currently untracked. Keeping live state and backups inside the worktree creates several hazards:

- accidental Git inclusion;
- OneDrive synchronization contention;
- backup files cluttering or affecting repository tools;
- tests or repair commands accidentally targeting the live database;
- reduced portability between source checkout and application state.

### C9. There is no process-level single-listener guard

Severity: P1

`DiscordListenerService.run` constructs a client and calls `client.run(...)`; no PID file, database lease, named mutex or other single-instance mechanism is present. See [discord_listener_service.py](/C:/Users/bacon/OneDrive/Documents/Projects/GitHub/moa/src/moa/services/discord_listener_service.py:96).

Running the command twice can create duplicate Gateway listeners that write to the same SQLite file. In-memory deduplication cannot protect across processes.

### C10. Raw Discord data is retained without a clear retention/privacy boundary

Severity: P2

`import_events` stores raw message text. This is valuable for audits, but can include:

- Discord display names and IDs;
- mentions;
- server-specific activity;
- command arguments;
- transaction details.

No explicit retention, redaction or export policy was found. This matters before MOA is distributed to other users or used as a hosted chatbot.

### C11. Token passing remains possible through a CLI option

Severity: P2

Environment handling is good, but `--token` remains supported. Tokens supplied this way may be visible in shell history or process inspection. It should remain a development escape hatch at most, with a prominent warning or be removed from ordinary documentation.

## D. Risks requiring more evidence

These are not confirmed defects.

1. **Character identity collisions — likely risk.**

   The repository uses normalized names and series in several places, but some upsert paths appear to conflict by normalized name while others use `(normalized_name, normalized_series)`. This needs a focused review with duplicate-name fixtures before concluding that cross-series identities can merge.

2. **Restart recovery for paginated scans — likely risk.**

   Scan rows are persisted, but some active scan correlation remains in listener dictionaries. A targeted restart-in-the-middle test is needed to establish whether page navigation continues correctly after process restart.

3. **SQLite lock failures under real concurrency — likely risk.**

   The connection configuration makes them plausible, but a controlled two-writer integration test is needed.

4. **Background-task/resource leakage — low-evidence risk.**

   I found no MOA-created recurring task or watcher in the listener service. `discord.py` manages its own Gateway tasks, but shutdown behavior was not dynamically exercised.

5. **Stale derived optimizer outputs — likely risk.**

   Observation timestamps exist, but there is no clearly centralized freshness policy applied to all optimizer/reporting consumers. Individual views may make different assumptions about what “latest” or “complete” means.

6. **Database integrity of the current live file — unverified.**

   Static schema code was inspected, but this audit did not claim a successful live `PRAGMA integrity_check` or foreign-key audit.

## E/F. Prioritized recommendations

| Priority | Recommendation | Impact | Effort | Regression risk |
|---|---|---:|---:|---:|
| P0 | Introduce a durable Discord event envelope keyed by guild/channel/message/event type, with a unique constraint | Very high | Medium | Medium |
| P0 | Replace channel-wide fallback context with correlation keyed by interaction/message/user/channel | Very high | Medium-high | High |
| P0 | Persist raw event and all resulting projections in one transaction | Very high | Medium-high | Medium |
| P1 | Add a serialized database writer, WAL, busy timeout and lock retry policy | High | Medium | Medium |
| P1 | Move schema changes into versioned migrations with mandatory backup/integrity checks | High | Medium-high | Medium-high |
| P1 | Add a single-listener lock or lease per database/config profile | High | Low-medium | Low |
| P1 | Move the live database outside the repository by default, with an explicit migration command | High | Medium | Medium |
| P1 | Extract repository modules by aggregate while preserving the existing facade | High | High | Medium |
| P2 | Create a declarative Mudae command registry: aliases, response kinds, parsers, persistence adapter | High | Medium-high | Medium |
| P2 | Build a sanitized captured-payload fixture corpus for prefix, slash, edit and reaction variants | High | Medium | Low |
| P2 | Add data-health commands for duplicates, orphans, impossible identities and projection gaps | Medium-high | Medium | Low |
| P2 | Add structured logging with event ID, guild ID, account ID and parser outcome | Medium | Medium | Low |
| P2 | Add raw-event retention/redaction configuration | Medium | Low-medium | Low |
| P3 | Split CLI commands into domain command modules | Medium | Medium | Low |
| P3 | Separate parsing helpers by response family | Medium | Medium | Medium |
| P3 | Document freshness/confidence semantics for every report | Medium | Low | Low |

## G. Proposed implementation sequence

1. **Characterize current behavior first.**

   Add fixtures for interleaved users, edits, reactions, confirmations, pagination and listener restart. Do not change storage yet.

2. **Add a canonical ingestion envelope.**

   Suggested fields:

   ```text
   source
   guild_id
   channel_id
   message_id
   event_kind
   author_id
   interaction_user_id
   interaction_name
   observed_at
   payload_hash
   raw_payload
   processing_status
   parser_version
   ```

3. **Make ingestion durable and idempotent.**

   Insert the envelope with a unique key, parse once, and transactionally produce projections.

4. **Replace listener context correlation.**

   Use this precedence:

   ```text
   interaction user ID
   → referenced command message
   → pending workflow for exact user/channel
   → uniquely configured account for server
   → unresolved quarantine
   ```

   Never silently select a channel-wide “last user” when multiple accounts are configured.

5. **Strengthen SQLite operation.**

   Add WAL, busy timeout, serialized writes and explicit transaction boundaries. Keep synchronous read methods temporarily if necessary, but move writes off the Gateway callback.

6. **Introduce versioned migrations.**

   Baseline the existing schema, verify backups, run `foreign_key_check` and `integrity_check`, then migrate incrementally.

7. **Extract modules behind compatibility facades.**

   Start with:

   - `EventRepository`
   - `IdentityRepository`
   - `CharacterRepository`
   - `OwnershipRepository`
   - `EconomyRepository`
   - `TimerRepository`
   - `ScanRepository`

   Existing callers can continue using `CatalogRepository` while it delegates.

8. **Build the grounded fact layer before a chatbot.**

## Future AI assistant readiness

MOA has a useful starting point because it already stores observations, timestamps, raw messages and account/server contexts. It is not yet ready to let a chatbot answer freely from repository methods.

The assistant should consume three distinct layers:

1. **Verified facts**

   Raw or normalized observations with source event, server, account, timestamp and parser version.

2. **Derived account state**

   Materialized/latest-state views with freshness, completeness and confidence indicators.

3. **Recommendations**

   Optimizer output with assumptions, model/version, evidence inputs and explanation.

A future answer object should resemble:

```text
answer
facts_used[]
server_id
account_id
observed_at
freshness
completeness
confidence
assumptions[]
recommendation_version
```

Required safeguards:

- refuse account-specific answers without an exact server/account context;
- mark missing data as unknown, not false;
- distinguish “not observed” from “not owned”, “not keyed” or “rollable”;
- expose stale timestamps;
- cite the source import event;
- prevent recommendations from being written back as facts;
- rebuild projections when parser logic changes.

## H. Suggested AGENTS.md updates

Add rules such as:

- Every Discord parser change must include at least one sanitized real-payload fixture.
- Never attribute a multi-account Discord event using channel context alone.
- Every persisted Discord event must have a durable source identity and idempotency test.
- Recording an `import_event` and updating its projections must be transactional.
- Schema changes require a versioned migration, pre-migration backup and post-migration integrity checks.
- Tests must use temporary databases unless a live-database test is explicitly authorized.
- Never put the default live database under the repository worktree.
- Parser rejection must preserve the raw event and a machine-readable failure reason.
- Every “latest state” report must define freshness and completeness semantics.
- Listener tests must include interleaved users, message edits, reactions and restart recovery.
- No repair command may mutate data without preview, backup and an explicit apply flag.
- Routing a response type is not proof that its account balance or projection was persisted.

## I. Three highest-value next tasks

1. **Durable event identity plus transactional imports**

   This addresses duplicate processing, crash consistency and replay safety across every command family.

2. **Multi-account correlation redesign**

   Replace the channel-wide context slot with interaction/message/user-aware correlation and quarantine unresolved events.

3. **Captured Discord payload integration harness**

   Cover slash commands, prefix commands, edits, buttons/reactions, two-step confirmations, pagination and simultaneous accounts using sanitized real payloads.

## Audit validation and limitations

- Files changed by this audit: none.
- Persistent processes started: none.
- Full test suite: not run, per request.
- Runtime listener behavior: not exercised.
- Current live database integrity and process list: not claimed as verified.
- Static architecture, schema, listener state handling, connection behavior and current test organization were inspected.
- Because the worktree was already dirty, findings apply to its current uncommitted state.