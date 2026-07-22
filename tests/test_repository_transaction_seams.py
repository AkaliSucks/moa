import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import RollObservation
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
