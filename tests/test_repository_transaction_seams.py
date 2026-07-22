import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import ClaimConfirmation, ProfileSnapshot, RollObservation
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository


ROLL = RollObservation(
    name="Transaction Character",
    series="Transaction Series",
    claim_rank=7,
    kakera_value=12,
    displayed_key_type="gold",
    displayed_key_count=3,
)
CLAIM = ClaimConfirmation(account_name="Account", character_name="Transaction Character")
PROFILE = ProfileSnapshot(
    profile_name="profile-account",
    collection_size=35,
    female_percent=100,
    male_percent=0,
    pokedex_count=2,
    pokedex_pokemon=("gulpin", "piloswine"),
    kakera_reacts={":kakeraY:": 497},
    mudapins_collected=None,
    mudapins_total=None,
    kakera_balance=812,
    bronze_keys=3,
    silver_keys=0,
    gold_keys=0,
    sphere_stock=None,
    spheres={":spP:": 2},
    displayed_badges=(":silvmudae:", ":DiamondI:"),
)
OBSERVED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 21, 12, 1, tzinfo=timezone.utc)


def _repositories(tmp_path):
    database_path = tmp_path / "transaction-seams.db"
    return database_path, CatalogRepository(database_path), DiscordMessageRepository(database_path)


def _receive_and_begin(repository: DiscordMessageRepository):
    aggregate_key = MessageAggregateKey(SourcePlatform.DISCORD, "guild", "channel", "message")
    received = repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(aggregate_key, "payload-hash", "revision"),
        event_key="event",
        event_kind="message_create",
        raw_text="raw",
        payload_json='{"content":"raw"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    return received.source_event_id, attempt.attempt_id


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "characters",
        "server_contexts",
        "account_contexts",
        "claim_observations",
        "profile_observations",
        "roll_observations",
        "harem_key_observations",
        "rank_snapshots",
        "server_character_observations",
        "discord_projection_links",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def test_roll_helper_writes_all_projections_on_supplied_connection(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        result = catalog._import_roll_with_connection(
            connection,
            roll=ROLL,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert result.import_event_id > 0
        assert result.character_id > 0
        assert result.roll_observation_id > 0
        assert result.harem_key_observation_id > 0
        assert result.rank_snapshot_id > 0
        assert result.server_character_observation_id > 0

    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts == {
            "import_events": 1,
            "characters": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "claim_observations": 0,
            "profile_observations": 0,
            "roll_observations": 1,
            "harem_key_observations": 1,
            "rank_snapshots": 1,
            "server_character_observations": 1,
            "discord_projection_links": 0,
        }


def test_public_roll_wrapper_keeps_public_result_and_transaction_behavior(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_roll(ROLL, " Server ", " Account ", "roll payload", "discord")

    assert result.import_event_id > 0
    assert result.character_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1


def test_public_claim_wrapper_keeps_result_and_writes_expected_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_claim(CLAIM, " Server ", " Account ", "claim payload", "discord")

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.character_name == "Transaction Character"
    assert result.character_id is None
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["server_contexts"] == 1
        assert _counts(connection)["account_contexts"] == 1
        assert _counts(connection)["claim_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 0
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("claim", "discord", "claim payload")
        observation = connection.execute(
            """
            SELECT id, character_id, character_name, normalized_character_name, import_event_id
            FROM claim_observations
            """
        ).fetchone()
        assert tuple(observation) == (
            observation["id"],
            None,
            "Transaction Character",
            "transaction character",
            result.import_event_id,
        )


def test_claim_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_claim_with_connection(
            connection,
            claim=CLAIM,
            server="Server",
            account="Account",
            raw="claim payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM claim_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT import_event_id FROM claim_observations WHERE id = ?",
            (imported.claim_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM claim_observations").fetchone()[0] == 0


def test_claim_helper_commit_persists_all_rows_and_result_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_claim_with_connection(
            connection,
            claim=CLAIM,
            server="Server",
            account="Account",
            raw="claim payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM claim_observations WHERE id = ?", (imported.claim_observation_id,)
        ).fetchone()[0] == imported.claim_observation_id
        assert connection.execute(
            "SELECT import_event_id FROM claim_observations WHERE id = ?",
            (imported.claim_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        assert _counts(connection)["claim_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 0


def test_claim_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_claim_with_connection(
                connection,
                claim=CLAIM,
                server="Server",
                account="Account",
                raw="claim payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["claim_observations"] == 0
        assert _counts(connection)["server_contexts"] == 0
        assert _counts(connection)["account_contexts"] == 0
        assert _counts(connection)["characters"] == 0
        assert _counts(connection)["discord_projection_links"] == 0


def test_claim_helper_reuses_canonical_rows_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    roll_result = catalog.import_roll(ROLL, "Server", "Account", "roll payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id,
                   characters.id AS character_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            JOIN characters ON characters.normalized_name = ?
            """,
            ("transaction character",),
        ).fetchone()
        before_counts = _counts(connection)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            imported = catalog._import_claim_with_connection(
                connection,
                claim=CLAIM,
                server=" SERVER ",
                account=" ACCOUNT ",
                raw="claim payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            assert imported.character_id == roll_result.character_id
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id,
                   characters.id AS character_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            JOIN characters ON characters.normalized_name = ?
            """,
            ("transaction character",),
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _counts(connection) == before_counts


def test_public_profile_wrapper_keeps_result_and_writes_expected_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_profile(PROFILE, " Server ", " Account ", "profile payload", "discord")

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "characters": 0,
            "server_contexts": 1,
            "account_contexts": 1,
            "claim_observations": 0,
            "profile_observations": 1,
            "roll_observations": 0,
            "harem_key_observations": 0,
            "rank_snapshots": 0,
            "server_character_observations": 0,
            "discord_projection_links": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("profile", "discord", "profile payload")
        row = connection.execute(
            """
            SELECT id, profile_name, collection_size, pokedex_json, kakera_reacts_json,
                   displayed_badges_json, import_event_id
            FROM profile_observations
            """
        ).fetchone()
        assert row["id"] > 0
        assert (row["profile_name"], row["collection_size"], row["import_event_id"]) == (
            "profile-account",
            35,
            result.import_event_id,
        )
        assert row["pokedex_json"] == '["gulpin", "piloswine"]'
        assert row["kakera_reacts_json"] == '{":kakeraY:": 497}'
        assert row["displayed_badges_json"] == '[":silvmudae:", ":DiamondI:"]'


def test_profile_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_profile_with_connection(
            connection,
            profile=PROFILE,
            server="Server",
            account="Account",
            raw="profile payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM profile_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT import_event_id FROM profile_observations WHERE id = ?",
            (imported.profile_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM profile_observations").fetchone()[0] == 0


def test_profile_helper_commit_persists_all_rows_and_result_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_profile_with_connection(
            connection,
            profile=PROFILE,
            server="Server",
            account="Account",
            raw="profile payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM profile_observations WHERE id = ?", (imported.profile_observation_id,)
        ).fetchone()[0] == imported.profile_observation_id
        assert _counts(connection)["profile_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 0


def test_profile_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_profile_with_connection(
                connection,
                profile=PROFILE,
                server="Server",
                account="Account",
                raw="profile payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "characters": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "claim_observations": 0,
            "profile_observations": 0,
            "roll_observations": 0,
            "harem_key_observations": 0,
            "rank_snapshots": 0,
            "server_character_observations": 0,
            "discord_projection_links": 0,
        }


def test_profile_helper_reuses_canonical_rows_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_profile(PROFILE, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_profile_with_connection(
                connection,
                profile=PROFILE,
                server=" SERVER ",
                account=" ACCOUNT ",
                raw="second payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["profile_observations"] == 1


def test_processing_success_helper_updates_supplied_connection(tmp_path) -> None:
    database_path, _catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        import_event_id = int(
            connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("roll", "discord", OBSERVED_AT.isoformat(), "roll payload"),
            ).lastrowid
        )
        result = discord._mark_processing_success_with_connection(
            connection,
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
            legacy_import_event_id=import_event_id,
        )
        assert connection.in_transaction is True
        assert result.attempt_status == "succeeded"
        assert result.source_event_status == "succeeded"
        assert result.legacy_import_event_id == import_event_id
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0] == "succeeded"


def test_one_caller_transaction_commits_roll_and_success_atomically(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        roll_result = catalog._import_roll_with_connection(
            connection,
            roll=ROLL,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        success = discord._mark_processing_success_with_connection(
            connection,
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
            legacy_import_event_id=roll_result.import_event_id,
        )
        assert success.attempt_status == "succeeded"

    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts["import_events"] == 1
        assert counts["roll_observations"] == 1
        assert counts["discord_projection_links"] == 0
        event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()
        assert (event["status"], event["legacy_import_event_id"]) == (
            "succeeded",
            roll_result.import_event_id,
        )
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "succeeded"


def test_failure_after_roll_helper_rolls_back_catalog_and_canonical_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_roll_with_connection(
                connection,
                roll=ROLL,
                server="Server",
                account="Account",
                raw="roll payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "characters": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "claim_observations": 0,
            "profile_observations": 0,
            "roll_observations": 0,
            "harem_key_observations": 0,
            "rank_snapshots": 0,
            "server_character_observations": 0,
            "discord_projection_links": 0,
        }


def test_failure_after_both_helpers_rolls_back_catalog_and_processing_success(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            roll_result = catalog._import_roll_with_connection(
                connection,
                roll=ROLL,
                server="Server",
                account="Account",
                raw="roll payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            discord._mark_processing_success_with_connection(
                connection,
                source_event_id=source_event_id,
                attempt_id=attempt_id,
                finished_at=FINISHED_AT,
                legacy_import_event_id=roll_result.import_event_id,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts["import_events"] == 0
        assert counts["characters"] == 0
        assert counts["roll_observations"] == 0
        assert counts["discord_projection_links"] == 0
        event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()
        assert (event["status"], event["legacy_import_event_id"]) == ("processing", None)
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "processing"
