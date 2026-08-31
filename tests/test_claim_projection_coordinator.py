import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import ClaimConfirmation, RollObservation
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.claim_projection_coordinator import (
    ClaimProjectionCoordinator,
    ClaimProjectionDatabasePathError,
    ClaimProjectionIntegrityError,
    ClaimProjectionResult,
    ClaimProjectionStateError,
    ClaimProjectionTargetError,
)
from moa.services.projection_expectations import Expectedness, claim_projection_slot


OBSERVED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 21, 12, 1, tzinfo=timezone.utc)
CLAIM = ClaimConfirmation(account_name="Account", character_name="Claim Character")


def _repositories(tmp_path):
    database_path = tmp_path / "claim-coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, ClaimProjectionCoordinator(catalog, discord)


def _receive_and_begin(discord, *, suffix="one"):
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", f"message-{suffix}"
    )
    received = discord.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, f"payload-{suffix}", "revision-1"
        ),
        event_key=f"event-{suffix}",
        event_kind="message_create",
        raw_text="claim payload",
        payload_json='{"content":"claim payload"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    discord.record_server_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Server",
        recorded_at=OBSERVED_AT,
    )
    discord.record_account_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Server",
        account_name="Account",
        recorded_at=OBSERVED_AT,
    )
    attempt = discord.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    return received.source_event_id, attempt.attempt_id


def _activate_test_projection_generation(
    connection: sqlite3.Connection, generation_id: int
) -> None:
    """Move a temporary test database to a later projection generation."""
    connection.execute("DROP TRIGGER projection_generations_keep_current_on_update")
    connection.execute(
        "INSERT INTO projection_generations (id, is_current) VALUES (?, 0)",
        (generation_id,),
    )
    connection.execute("UPDATE projection_generations SET is_current = 0 WHERE id = 1")
    connection.execute(
        "UPDATE projection_generations SET is_current = 1 WHERE id = ?",
        (generation_id,),
    )


def _insert_historical_completed_link(
    connection: sqlite3.Connection, source_event_id: int
) -> None:
    value = OBSERVED_AT.isoformat()
    connection.execute(
        """
        INSERT INTO discord_projection_links (
            source_event_id, generation_id, projection_kind, projection_slot,
            projection_table, projection_row_id, state,
            claimed_at, completed_at, created_at, updated_at
        ) VALUES (?, 1, 'catalog.claim', ?,
                  'claim_observations', 987, 'completed', ?, ?, ?, ?)
        """,
        (
            source_event_id,
            claim_projection_slot(
                CatalogRepository._normalize("Server"),
                CatalogRepository._normalize("Account"),
                CatalogRepository._normalize(CLAIM.character_name),
            ),
            value,
            value,
            value,
            value,
        ),
    )


def _projection_snapshot(database_path) -> tuple[object, ...]:
    with connect(database_path) as connection:
        return (
            tuple(tuple(row) for row in connection.execute(
                "SELECT * FROM projection_generations ORDER BY id"
            )),
            tuple(tuple(row) for row in connection.execute(
                "SELECT * FROM discord_projection_links ORDER BY id"
            )),
            tuple(tuple(row) for row in connection.execute(
                "SELECT * FROM import_events ORDER BY id"
            )),
            tuple(tuple(row) for row in connection.execute(
                "SELECT * FROM claim_observations ORDER BY id"
            )),
            tuple(connection.execute(
                "SELECT status, legacy_import_event_id, updated_at FROM discord_source_events"
            ).fetchone()),
            tuple(connection.execute(
                "SELECT status, finished_at, failure_code FROM discord_processing_attempts"
            ).fetchone()),
        )


def _coordinate(coordinator, source_event_id, attempt_id, *, server=" Server ", account=" Account ", claim=CLAIM):
    return coordinator.coordinate_claim(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        claim=claim,
        server=server,
        account=account,
        raw="claim payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "characters",
        "server_contexts",
        "account_contexts",
        "claim_observations",
        "roll_observations",
        "profile_observations",
        "discord_projection_links",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _mutate_attribution(database_path, source_event_id: int, category: str) -> None:
    with connect(database_path) as connection:
        if category == "missing_server":
            connection.execute(
                "DELETE FROM discord_source_event_server_attributions WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "unresolved_server":
            connection.execute(
                """
                UPDATE discord_source_event_server_attributions
                SET status = 'unresolved', server_name = NULL
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            )
        elif category == "ambiguous_server":
            connection.execute(
                """
                UPDATE discord_source_event_server_attributions
                SET status = 'ambiguous', server_name = NULL
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            )
        elif category == "server_mismatch":
            connection.execute(
                """
                UPDATE discord_source_event_server_attributions
                SET server_name = 'Other Server'
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            )
        elif category == "missing_account":
            connection.execute(
                "DELETE FROM discord_source_event_account_attributions WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "unresolved_account":
            connection.execute(
                """
                UPDATE discord_source_event_account_attributions
                SET status = 'unresolved', server_name = NULL, account_name = NULL
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            )
        elif category == "ambiguous_account":
            connection.execute(
                """
                UPDATE discord_source_event_account_attributions
                SET status = 'ambiguous', server_name = NULL, account_name = NULL
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            )
        elif category == "account_server_mismatch":
            connection.execute(
                """
                UPDATE discord_source_event_account_attributions
                SET server_name = 'Other Server'
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            )
        elif category == "account_mismatch":
            connection.execute(
                """
                UPDATE discord_source_event_account_attributions
                SET account_name = 'Other Account'
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            )
        else:
            raise AssertionError(f"unknown attribution category: {category}")


def _database_snapshot(database_path) -> dict[str, object]:
    with connect(database_path) as connection:
        return {
            "counts": _counts(connection),
            "event": tuple(
                connection.execute(
                    "SELECT status, legacy_import_event_id, updated_at FROM discord_source_events"
                ).fetchone()
            ),
            "attempt": tuple(
                connection.execute(
                    "SELECT status, finished_at, failure_code FROM discord_processing_attempts"
                ).fetchone()
            ),
            "links": [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT projection_kind, projection_slot, projection_table,
                           projection_row_id, state, completed_at
                    FROM discord_projection_links
                    ORDER BY id
                    """
                ).fetchall()
            ],
            "server_attribution": tuple(
                connection.execute(
                    """
                    SELECT status, server_name, created_at, updated_at
                    FROM discord_source_event_server_attributions
                    """
                ).fetchone()
            )
            if connection.execute(
                "SELECT 1 FROM discord_source_event_server_attributions"
            ).fetchone()
            else None,
            "account_attribution": tuple(
                connection.execute(
                    """
                    SELECT status, server_name, account_name, created_at, updated_at
                    FROM discord_source_event_account_attributions
                    """
                ).fetchone()
            )
            if connection.execute(
                "SELECT 1 FROM discord_source_event_account_attributions"
            ).fetchone()
            else None,
        }


def test_first_claim_processing_and_projection_slot(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result == ClaimProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        claim_observation_id=result.claim_observation_id,
        character_id=None,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("claim_observations", result.claim_observation_id),
    )
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "characters": 0,
            "server_contexts": 1,
            "account_contexts": 1,
            "claim_observations": 1,
            "roll_observations": 0,
            "profile_observations": 0,
            "discord_projection_links": 1,
        }
        link = connection.execute(
            "SELECT projection_kind, projection_slot, projection_table, projection_row_id, state, completed_at "
            "FROM discord_projection_links"
        ).fetchone()
        assert tuple(link) == (
            "catalog.claim",
            '{"account":"account","character_name":"claim character","server":"server"}',
            "claim_observations",
            result.claim_observation_id,
            "completed",
            FINISHED_AT.isoformat(),
        )
        event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()
        attempt = connection.execute("SELECT status FROM discord_processing_attempts").fetchone()
        assert tuple(event) == ("succeeded", result.import_event_id)
        assert tuple(attempt) == ("succeeded",)


def test_generation_one_compatibility_persists_generation_qualified_claim(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        link = connection.execute(
            "SELECT generation_id, projection_kind, projection_slot, projection_table, "
            "projection_row_id, state FROM discord_projection_links"
        ).fetchone()
    assert tuple(link) == (
        1,
        "catalog.claim",
        claim_projection_slot("server", "account", "claim character"),
        "claim_observations",
        result.claim_observation_id,
        "completed",
    )


def test_generation_two_isolates_history_and_current_replay_is_no_write(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    with connect(database_path) as connection:
        _activate_test_projection_generation(connection, 2)
        _insert_historical_completed_link(connection, source_event_id)
        historical_before = tuple(
            connection.execute(
                "SELECT * FROM discord_projection_links WHERE generation_id = 1"
            ).fetchone()
        )

    first = _coordinate(coordinator, source_event_id, attempt_id)
    before_replay = _projection_snapshot(database_path)
    replay = _coordinate(coordinator, source_event_id, None)

    assert replay == ClaimProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        claim_observation_id=first.claim_observation_id,
        character_id=None,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=first.projection_target,
    )
    with connect(database_path) as connection:
        assert _projection_snapshot(database_path) == before_replay
        assert tuple(
            connection.execute(
                "SELECT * FROM discord_projection_links WHERE generation_id = 1"
            ).fetchone()
        ) == historical_before
        assert tuple(connection.execute(
            "SELECT generation_id, projection_table, projection_row_id, state "
            "FROM discord_projection_links WHERE source_event_id = ? AND generation_id = 2",
            (source_event_id,),
        ).fetchone()) == (
            2,
            "claim_observations",
            first.claim_observation_id,
            "completed",
        )


def test_generation_two_replay_fails_when_only_historical_link_exists(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        _activate_test_projection_generation(connection, 2)
    before = _projection_snapshot(database_path)

    with pytest.raises(
        ClaimProjectionIntegrityError,
        match="inconsistent claim projection link",
    ):
        _coordinate(coordinator, source_event_id, None)

    assert _projection_snapshot(database_path) == before


@pytest.mark.parametrize("corruption", ("malformed", "ownership", "slot"))
def test_generation_two_replay_rejects_current_link_corruption_without_writes(
    tmp_path, corruption
) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix=corruption)
    with connect(database_path) as connection:
        _activate_test_projection_generation(connection, 2)
    _coordinate(coordinator, source_event_id, attempt_id)

    if corruption == "malformed":
        with connect(database_path) as connection:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "UPDATE discord_projection_links SET projection_row_id = NULL "
                "WHERE source_event_id = ? AND generation_id = 2",
                (source_event_id,),
            )
    elif corruption == "ownership":
        other = catalog.import_claim(
            CLAIM, "Server", "Account", "other claim", "discord"
        )
        with connect(database_path) as connection:
            target = connection.execute(
                "SELECT id FROM claim_observations WHERE import_event_id = ?",
                (other.import_event_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE discord_projection_links SET projection_row_id = ? "
                "WHERE source_event_id = ? AND generation_id = 2",
                (target, source_event_id),
            )
    else:
        with connect(database_path) as connection:
            connection.execute(
                "UPDATE discord_projection_links SET projection_slot = ? "
                "WHERE source_event_id = ? AND generation_id = 2",
                (claim_projection_slot("server", "other account", "claim character"), source_event_id),
            )

    before = _projection_snapshot(database_path)
    with pytest.raises((ClaimProjectionIntegrityError, ClaimProjectionTargetError)):
        _coordinate(coordinator, source_event_id, None)
    assert _projection_snapshot(database_path) == before


@pytest.mark.parametrize("failure", ("post_import", "completion"))
def test_generation_two_failure_rolls_back_new_writes_and_retains_history(
    tmp_path, monkeypatch: pytest.MonkeyPatch, failure
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    with connect(database_path) as connection:
        _activate_test_projection_generation(connection, 2)
        _insert_historical_completed_link(connection, source_event_id)
    before = _projection_snapshot(database_path)

    if failure == "post_import":
        monkeypatch.setattr(
            coordinator,
            "_validate_claim_target",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("forced post-import rollback")
            ),
        )
    else:
        monkeypatch.setattr(
            coordinator,
            "_complete_projection_link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("forced completion rollback")
            ),
        )

    with pytest.raises(RuntimeError, match="forced (post-import|completion) rollback"):
        _coordinate(coordinator, source_event_id, attempt_id)

    assert _projection_snapshot(database_path) == before


@pytest.mark.parametrize(
    ("category", "message"),
    (
        ("missing_server", "no persisted server attribution"),
        ("unresolved_server", "non-resolved server attribution"),
        ("ambiguous_server", "non-resolved server attribution"),
        ("server_mismatch", "another server"),
        ("missing_account", "no persisted account attribution"),
        ("unresolved_account", "non-resolved account attribution"),
        ("ambiguous_account", "non-resolved account attribution"),
        ("account_server_mismatch", "mismatched account attribution server"),
        ("account_mismatch", "another account"),
    ),
)
def test_first_claim_attribution_failures_are_atomic(
    tmp_path, category: str, message: str
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _mutate_attribution(database_path, source_event_id, category)
    before = _database_snapshot(database_path)

    with pytest.raises(ClaimProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, attempt_id)

    assert _database_snapshot(database_path) == before
    assert before["event"][:2] == ("processing", None)
    assert before["attempt"][:1] == ("processing",)
    assert before["counts"]["discord_projection_links"] == 0
    assert before["counts"]["import_events"] == 0
    assert before["counts"]["claim_observations"] == 0


def test_blank_typed_claimant_fails_before_any_writes(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    before = _database_snapshot(database_path)

    with pytest.raises(ClaimProjectionIntegrityError, match="valid typed claimant"):
        _coordinate(
            coordinator,
            source_event_id,
            attempt_id,
            claim=ClaimConfirmation(account_name="   ", character_name=CLAIM.character_name),
        )

    assert _database_snapshot(database_path) == before


def test_typed_claimant_mismatch_with_persisted_account_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _mutate_attribution(database_path, source_event_id, "account_mismatch")
    before = _database_snapshot(database_path)

    with pytest.raises(ClaimProjectionIntegrityError, match="another account"):
        _coordinate(
            coordinator,
            source_event_id,
            attempt_id,
            claim=ClaimConfirmation(
                account_name="Other Account", character_name=CLAIM.character_name
            ),
        )

    assert _database_snapshot(database_path) == before


def test_equivalent_casing_and_whitespace_reuses_projection_slot_on_replay(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)

    replay = _coordinate(
        coordinator,
        source_event_id,
        None,
        server="  sErVeR ",
        account=" aCcOuNt ",
        claim=ClaimConfirmation(account_name=" ACCOUNT ", character_name=" CLAIM   CHARACTER "),
    )

    assert replay.import_event_id == first.import_event_id
    assert replay.claim_observation_id == first.claim_observation_id
    assert replay.replay_skipped is True


def test_failure_after_catalog_writes_rolls_back_every_coordinator_write(tmp_path, monkeypatch) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    existing = catalog.import_roll(
        RollObservation(
            name="Existing Character",
            series="Existing Series",
            claim_rank=1,
            kakera_value=10,
        ),
        "Server",
        "Account",
        "existing roll",
        "discord",
    )
    source_event_id, attempt_id = _receive_and_begin(discord)
    before = _database_snapshot(database_path)
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced failure after claim writes")),
    )

    with pytest.raises(RuntimeError, match="forced failure after claim writes"):
        _coordinate(coordinator, source_event_id, attempt_id)

    assert _database_snapshot(database_path) == before
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM characters WHERE id = ?", (existing.character_id,)
        ).fetchone() is not None
        assert connection.execute(
            "SELECT 1 FROM roll_observations WHERE import_event_id = ?",
            (existing.import_event_id,),
        ).fetchone() is not None
        assert connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()[:2] == ("processing", None)
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "processing"


def test_retry_after_rollback_succeeds_once_without_duplicates(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    original = coordinator._complete_projection_link
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced retry failure")),
    )
    with pytest.raises(RuntimeError, match="forced retry failure"):
        _coordinate(coordinator, source_event_id, attempt_id)
    monkeypatch.setattr(coordinator, "_complete_projection_link", original)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result.imported_count == 1
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["claim_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 1


def test_succeeded_replay_returns_existing_ids_and_inserts_nothing(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    before = _database_snapshot(database_path)

    replay = _coordinate(coordinator, source_event_id, None)

    assert replay == ClaimProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        claim_observation_id=first.claim_observation_id,
        character_id=None,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=first.projection_target,
    )
    assert _database_snapshot(database_path) == before


def test_succeeded_replay_preserves_unknown_durable_claim_identity_without_parsed_fallback(
    tmp_path, monkeypatch
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO claim_observations (
                account_context_id, character_id, character_name,
                normalized_character_name, observed_at, import_event_id
            )
            SELECT account_context_id, character_id, character_name,
                   normalized_character_name, observed_at, import_event_id
            FROM claim_observations
            WHERE id = ?
            """,
            (first.claim_observation_id,),
        )
    observed_expectedness = []
    original_replay = coordinator._coordinate_replay

    def capture_expected_set(
        connection,
        event,
        expected_set,
        *,
        target_projection_slot,
        projection_links,
        generation_id,
    ):
        observed_expectedness.append(expected_set.assessments[0].expectedness)
        return original_replay(
            connection,
            event,
            expected_set,
            target_projection_slot=target_projection_slot,
            projection_links=projection_links,
            generation_id=generation_id,
        )

    monkeypatch.setattr(coordinator, "_coordinate_replay", capture_expected_set)

    replay = _coordinate(coordinator, source_event_id, None)

    assert replay.replay_skipped is True
    assert observed_expectedness == [Expectedness.UNKNOWN]


def test_coordinator_resolves_existing_account_character_inside_runner(tmp_path) -> None:
    _database_path, catalog, discord, coordinator = _repositories(tmp_path)
    roll = catalog.import_roll(
        RollObservation(
            name=CLAIM.character_name,
            series="Claim Series",
            claim_rank=1,
            kakera_value=10,
        ),
        "Server",
        "Account",
        "existing roll",
        "discord",
    )
    source_event_id, attempt_id = _receive_and_begin(discord)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result.character_id == roll.character_id


def test_coordinator_preserves_ambiguous_character_as_unresolved(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    for suffix in ("one", "two"):
        catalog.import_roll(
            RollObservation(
                name=CLAIM.character_name,
                series=f"Claim Series {suffix}",
                claim_rank=1,
                kakera_value=10,
            ),
            "Server",
            f"Other Account {suffix}",
            f"existing roll {suffix}",
            "discord",
        )
    source_event_id, attempt_id = _receive_and_begin(discord)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result.character_id is None
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 2
        assert connection.execute(
            "SELECT character_id FROM claim_observations WHERE id = ?",
            (result.claim_observation_id,),
        ).fetchone()[0] is None


def test_coordinator_calls_supplied_claim_helper_without_public_wrapper(
    tmp_path, monkeypatch
) -> None:
    _database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    original = catalog._import_claim_with_connection
    calls = 0

    def supplied_helper(connection, **kwargs):
        nonlocal calls
        calls += 1
        return original(connection, **kwargs)

    def forbidden_public_wrapper(*_args, **_kwargs):
        raise AssertionError("coordinator called public claim wrapper")

    monkeypatch.setattr(catalog, "_import_claim_with_connection", supplied_helper)
    monkeypatch.setattr(catalog, "import_claim", forbidden_public_wrapper)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result.imported_count == 1
    assert calls == 1


@pytest.mark.parametrize(
    ("category", "message"),
    (
        ("missing_server", "no persisted server attribution"),
        ("unresolved_server", "non-resolved server attribution"),
        ("ambiguous_server", "non-resolved server attribution"),
        ("server_mismatch", "another server"),
        ("missing_account", "no persisted account attribution"),
        ("unresolved_account", "non-resolved account attribution"),
        ("ambiguous_account", "non-resolved account attribution"),
        ("account_server_mismatch", "mismatched account attribution server"),
        ("account_mismatch", "another account"),
    ),
)
def test_succeeded_replay_attribution_failures_leave_all_rows_unchanged(
    tmp_path, category: str, message: str
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)
    _mutate_attribution(database_path, source_event_id, category)
    before = _database_snapshot(database_path)

    with pytest.raises(ClaimProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, None)

    assert _database_snapshot(database_path) == before
    assert before["event"][:2] == ("succeeded", before["event"][1])
    assert before["attempt"][:1] == ("succeeded",)
    assert before["counts"]["import_events"] == 1
    assert before["counts"]["claim_observations"] == 1
    assert before["counts"]["discord_projection_links"] == 1


def test_historical_succeeded_replay_without_account_attribution_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)
    _mutate_attribution(database_path, source_event_id, "missing_account")
    before = _database_snapshot(database_path)

    with pytest.raises(ClaimProjectionIntegrityError, match="no persisted account attribution"):
        _coordinate(coordinator, source_event_id, None)

    assert _database_snapshot(database_path) == before


def test_global_character_reuse_does_not_bypass_account_validation(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    catalog.import_roll(
        RollObservation(
            name=CLAIM.character_name,
            series="Claim Series",
            claim_rank=1,
            kakera_value=10,
        ),
        "Server",
        "Other Account",
        "existing roll",
        "discord",
    )
    source_event_id, attempt_id = _receive_and_begin(discord)
    _mutate_attribution(database_path, source_event_id, "account_mismatch")
    before = _database_snapshot(database_path)

    with pytest.raises(ClaimProjectionIntegrityError, match="another account"):
        _coordinate(coordinator, source_event_id, attempt_id)

    assert _database_snapshot(database_path) == before


def test_edited_revision_gets_independent_claim_projection(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    first_event, first_attempt = _receive_and_begin(discord)
    second_event, second_attempt = _receive_and_begin(discord, suffix="edited")

    first = _coordinate(coordinator, first_event, first_attempt)
    second = _coordinate(coordinator, second_event, second_attempt)

    assert first.claim_observation_id != second.claim_observation_id
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM claim_observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 2


def test_persisted_claimed_link_fails_closed(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    slot = claim_projection_slot(
        CatalogRepository._normalize("Server"),
        CatalogRepository._normalize("Account"),
        CatalogRepository._normalize(CLAIM.character_name),
    )
    with connect(coordinator._database_path) as connection:
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, 'catalog.claim', ?, 'claimed', ?, ?, ?)
            """,
            (source_event_id, slot, OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat()),
        )

    with pytest.raises(ClaimProjectionIntegrityError, match="already exists"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_completed_link_with_missing_target_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute("DELETE FROM claim_observations WHERE id = ?", (first.claim_observation_id,))

    with pytest.raises(ClaimProjectionTargetError, match="missing"):
        _coordinate(coordinator, source_event_id, None)


def test_completed_link_with_wrong_target_table_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute("UPDATE discord_projection_links SET projection_table = 'roll_observations'")

    with pytest.raises(ClaimProjectionIntegrityError, match="inconsistent claim projection link"):
        _coordinate(coordinator, source_event_id, None)


def test_completed_link_with_mismatched_import_event_fails_closed(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    second = catalog.import_claim(CLAIM, "Server", "Account", "second claim", "discord")
    with connect(database_path) as connection:
        second_observation_id = connection.execute(
            "SELECT id FROM claim_observations WHERE import_event_id = ?", (second.import_event_id,)
        ).fetchone()[0]
        connection.execute(
            "UPDATE discord_projection_links SET projection_row_id = ?", (second_observation_id,)
        )

    with pytest.raises(ClaimProjectionTargetError, match="another import event"):
        _coordinate(coordinator, source_event_id, None)
    assert first.import_event_id != second.import_event_id


def test_completed_link_with_mismatched_scope_fails_closed(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    catalog.import_claim(CLAIM, "Other Server", "Other Account", "second claim", "discord")
    with connect(database_path) as connection:
        other_account_context_id = connection.execute(
            "SELECT account_context_id FROM claim_observations WHERE import_event_id = (SELECT MAX(id) FROM import_events)"
        ).fetchone()[0]
        connection.execute(
            "UPDATE claim_observations SET account_context_id = ? WHERE id = ?",
            (other_account_context_id, first.claim_observation_id),
        )

    with pytest.raises(ClaimProjectionTargetError, match="mismatched claim scope"):
        _coordinate(coordinator, source_event_id, None)


def test_semantic_slot_mismatch_on_replay_fails_closed(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)

    with pytest.raises(ClaimProjectionTargetError, match="mismatched claim scope"):
        _coordinate(
            coordinator,
            source_event_id,
            None,
            claim=ClaimConfirmation(account_name="Account", character_name="Other Character"),
        )


def test_attempt_ownership_and_lifecycle_state_are_validated(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix="one")
    other_event_id, other_attempt_id = _receive_and_begin(discord, suffix="two")

    with pytest.raises(ClaimProjectionStateError, match="another source event"):
        _coordinate(coordinator, source_event_id, other_attempt_id)
    discord.mark_processing_failure(
        source_event_id=other_event_id,
        attempt_id=other_attempt_id,
        status="failed",
        retryable=False,
        failure_code="test",
        failure_detail="done",
        finished_at=FINISHED_AT,
    )
    with pytest.raises(ClaimProjectionStateError, match="not processing"):
        _coordinate(coordinator, other_event_id, other_attempt_id)
    with pytest.raises(ClaimProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)
    assert attempt_id > 0


def test_received_event_without_active_attempt_is_rejected(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, _attempt_id = _receive_and_begin(discord)
    with connect(database_path) as connection:
        connection.execute("DELETE FROM discord_processing_attempts WHERE source_event_id = ?", (source_event_id,))
        connection.execute("UPDATE discord_source_events SET status = 'received' WHERE id = ?", (source_event_id,))

    with pytest.raises(ClaimProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)


def test_claimant_mismatch_is_rejected_without_writes(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with pytest.raises(ClaimProjectionIntegrityError, match="claimant"):
        _coordinate(
            coordinator,
            source_event_id,
            attempt_id,
            claim=ClaimConfirmation(
                account_name="Other Account",
                character_name=CLAIM.character_name,
            ),
        )
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0


def test_repository_database_path_mismatch_is_rejected_before_coordination(tmp_path) -> None:
    catalog = CatalogRepository(tmp_path / "catalog.db")
    discord = DiscordMessageRepository(tmp_path / "discord.db")

    with pytest.raises(ClaimProjectionDatabasePathError, match="same database path"):
        ClaimProjectionCoordinator(catalog, discord)


def test_only_claim_projection_links_are_created(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT projection_kind, projection_table FROM discord_projection_links"
        ).fetchall()
    assert [tuple(row) for row in rows] == [("catalog.claim", "claim_observations")]
