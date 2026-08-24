import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import ProfileSnapshot
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import (
    CatalogRepository,
    ImportEventDeletionBlockedError,
)
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.profile_projection_coordinator import (
    ProfileProjectionCoordinator,
    ProfileProjectionDatabasePathError,
    ProfileProjectionIntegrityError,
    ProfileProjectionResult,
    ProfileProjectionStateError,
    ProfileProjectionTargetError,
)
from moa.services.projection_expectations import server_account_projection_slot


def _slot(server: str, account: str) -> str:
    return server_account_projection_slot(
        CatalogRepository._normalize(server), CatalogRepository._normalize(account)
    )


OBSERVED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 21, 12, 1, tzinfo=timezone.utc)
PROFILE = ProfileSnapshot(
    profile_name="Account",
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
    silver_keys=None,
    gold_keys=None,
    sphere_stock=None,
    spheres={":spP:": 2},
    displayed_badges=(":silvmudae:", ":DiamondI:"),
    pokedex_observed=True,
    reactions_observed=True,
    mudapins_observed=False,
    kakera_balance_observed=True,
    keys_observed=True,
    bronze_keys_observed=True,
    silver_keys_observed=False,
    gold_keys_observed=False,
    sphere_stock_observed=False,
    sphere_counts_observed=True,
    badges_observed=True,
)
ABSENT_REACTIONS_PROFILE = PROFILE.model_copy(
    update={"kakera_reacts": None, "reactions_observed": False}
)
EMPTY_REACTIONS_PROFILE = PROFILE.model_copy(update={"kakera_reacts": {}})
ZERO_BRONZE_KEY_PROFILE = PROFILE.model_copy(update={"bronze_keys": 0})
ABSENT_BALANCE_PROFILE = PROFILE.model_copy(
    update={"kakera_balance": None, "kakera_balance_observed": False}
)


def _repositories(tmp_path):
    database_path = tmp_path / "profile-coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, ProfileProjectionCoordinator(catalog, discord)


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
        raw_text="profile payload",
        payload_json='{"content":"profile payload"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    attempt = discord.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
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
    return received.source_event_id, attempt.attempt_id


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "profile_observations",
        "roll_observations",
        "discord_projection_links",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _coordinate(
    coordinator,
    source_event_id,
    attempt_id,
    *,
    profile=PROFILE,
    server=" Server ",
    account=" Account ",
):
    return coordinator.coordinate_profile(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        profile=profile,
        server=server,
        account=account,
        raw="profile payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _set_attribution_failure(database_path, failure: str) -> None:
    statements = {
        "missing_server": "DELETE FROM discord_source_event_server_attributions",
        "unresolved_server": (
            "UPDATE discord_source_event_server_attributions "
            "SET status = 'unresolved', server_name = NULL"
        ),
        "ambiguous_server": (
            "UPDATE discord_source_event_server_attributions "
            "SET status = 'ambiguous', server_name = NULL"
        ),
        "server_mismatch": (
            "UPDATE discord_source_event_server_attributions SET server_name = 'Server B'"
        ),
        "missing_account": "DELETE FROM discord_source_event_account_attributions",
        "unresolved_account": (
            "UPDATE discord_source_event_account_attributions "
            "SET status = 'unresolved', server_name = NULL, account_name = NULL"
        ),
        "ambiguous_account": (
            "UPDATE discord_source_event_account_attributions "
            "SET status = 'ambiguous', server_name = NULL, account_name = NULL"
        ),
        "account_server_mismatch": (
            "UPDATE discord_source_event_account_attributions SET server_name = 'Server B'"
        ),
        "account_mismatch": (
            "UPDATE discord_source_event_account_attributions SET account_name = 'Account B'"
        ),
    }
    with connect(database_path) as connection:
        connection.execute(statements[failure])


def _attribution_rows(connection: sqlite3.Connection):
    return (
        tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM discord_source_event_server_attributions ORDER BY source_event_id"
            )
        ),
        tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM discord_source_event_account_attributions ORDER BY source_event_id"
            )
        ),
    )


def _durable_state(connection: sqlite3.Connection):
    return (
        tuple(connection.execute("SELECT * FROM discord_source_events").fetchone()),
        tuple(connection.execute("SELECT * FROM discord_processing_attempts").fetchone()),
        _counts(connection),
        _attribution_rows(connection),
    )


def test_first_processing_coordinates_profile_and_success(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result.imported_count == 1
    assert result.profile_observation_id > 0
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True
    assert result.projection_target == ("profile_observations", result.profile_observation_id)
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "profile_observations": 1,
            "roll_observations": 0,
            "discord_projection_links": 1,
        }
        link = connection.execute(
            "SELECT projection_kind, projection_slot, projection_table, projection_row_id, state FROM discord_projection_links"
        ).fetchone()
        assert tuple(link) == (
            "catalog.profile",
            '{"account":"account","server":"server"}',
            "profile_observations",
            result.profile_observation_id,
            "completed",
        )
        event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()
        attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts"
        ).fetchone()
        assert tuple(event) == ("succeeded", result.import_event_id)
        assert tuple(attempt) == ("succeeded",)


def test_failure_after_catalog_writes_rolls_back_every_coordinator_write(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    def fail_after_catalog_writes(*_args, **_kwargs):
        raise RuntimeError("forced failure after profile catalog writes")

    monkeypatch.setattr(coordinator, "_complete_projection_link", fail_after_catalog_writes)
    with pytest.raises(RuntimeError, match="forced failure after profile catalog writes"):
        _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "profile_observations": 0,
            "roll_observations": 0,
            "discord_projection_links": 0,
        }
        assert connection.execute("SELECT status, legacy_import_event_id FROM discord_source_events").fetchone()[:2] == (
            "processing",
            None,
        )
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
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "profile_observations": 1,
            "roll_observations": 0,
            "discord_projection_links": 1,
        }


@pytest.mark.parametrize(
    "profile",
    [PROFILE, ABSENT_REACTIONS_PROFILE, EMPTY_REACTIONS_PROFILE, ZERO_BRONZE_KEY_PROFILE],
    ids=["present", "absent", "observed-empty", "observed-zero"],
)
def test_successful_replay_validates_exact_new_value_and_presence(
    tmp_path, profile
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id, profile=profile)
    with connect(database_path) as connection:
        before = _counts(connection)

    replay = _coordinate(coordinator, source_event_id, None, profile=profile)

    assert replay == ProfileProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        profile_observation_id=first.profile_observation_id,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=first.projection_target,
    )
    with connect(database_path) as connection:
        assert _counts(connection) == before
        assert connection.execute("SELECT COUNT(*) FROM discord_processing_attempts").fetchone()[0] == 1


def test_new_profile_replay_rejects_presence_mismatch_with_same_storage_value(
    tmp_path,
) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(
        coordinator,
        source_event_id,
        attempt_id,
        profile=EMPTY_REACTIONS_PROFILE,
    )

    with pytest.raises(ProfileProjectionTargetError, match="values or presence"):
        _coordinate(
            coordinator,
            source_event_id,
            None,
            profile=ABSENT_REACTIONS_PROFILE,
        )


@pytest.mark.parametrize(
    ("replay_profile", "compatible"),
    [
        (ABSENT_REACTIONS_PROFILE, True),
        (EMPTY_REACTIONS_PROFILE, True),
        (PROFILE, False),
    ],
    ids=["absent", "observed-empty", "nonempty"],
)
def test_legacy_empty_reaction_replay_uses_old_serialization_without_mutation(
    tmp_path, replay_profile, compatible
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(
        coordinator,
        source_event_id,
        attempt_id,
        profile=EMPTY_REACTIONS_PROFILE,
    )
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE profile_observations SET reactions_observed = NULL"
        )

    if compatible:
        replay = _coordinate(
            coordinator, source_event_id, None, profile=replay_profile
        )
        assert replay.replay_skipped is True
    else:
        with pytest.raises(ProfileProjectionTargetError, match="values or presence"):
            _coordinate(coordinator, source_event_id, None, profile=replay_profile)

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT reactions_observed FROM profile_observations"
        ).fetchone()[0] is None


@pytest.mark.parametrize(
    ("bronze_keys", "bronze_observed", "compatible"),
    [(None, False, True), (0, True, True), (4, True, False)],
    ids=["absent", "observed-zero", "nonzero"],
)
def test_legacy_zero_key_replay_uses_marker_serialization_without_mutation(
    tmp_path, bronze_keys, bronze_observed, compatible
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(
        coordinator,
        source_event_id,
        attempt_id,
        profile=ZERO_BRONZE_KEY_PROFILE,
    )
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE profile_observations SET bronze_keys_observed = NULL"
        )
    replay_profile = ZERO_BRONZE_KEY_PROFILE.model_copy(
        update={
            "bronze_keys": bronze_keys,
            "bronze_keys_observed": bronze_observed,
        }
    )

    if compatible:
        assert _coordinate(
            coordinator, source_event_id, None, profile=replay_profile
        ).replay_skipped
    else:
        with pytest.raises(ProfileProjectionTargetError, match="values or presence"):
            _coordinate(coordinator, source_event_id, None, profile=replay_profile)

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT bronze_keys_observed FROM profile_observations"
        ).fetchone()[0] is None


@pytest.mark.parametrize(
    ("balance", "observed", "compatible"),
    [(None, False, True), (0, True, False), (812, True, False)],
    ids=["absent", "explicit-zero", "nonzero"],
)
def test_legacy_null_balance_replay_requires_old_nullable_serialization(
    tmp_path, balance, observed, compatible
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(
        coordinator,
        source_event_id,
        attempt_id,
        profile=ABSENT_BALANCE_PROFILE,
    )
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE profile_observations SET kakera_balance_observed = NULL"
        )
    replay_profile = ABSENT_BALANCE_PROFILE.model_copy(
        update={"kakera_balance": balance, "kakera_balance_observed": observed}
    )

    if compatible:
        assert _coordinate(
            coordinator, source_event_id, None, profile=replay_profile
        ).replay_skipped
    else:
        with pytest.raises(ProfileProjectionTargetError, match="values or presence"):
            _coordinate(coordinator, source_event_id, None, profile=replay_profile)

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT kakera_balance_observed FROM profile_observations"
        ).fetchone()[0] is None


def test_unlinked_profile_import_deletion_exposes_previous_snapshot(tmp_path) -> None:
    database_path, catalog, _discord, _coordinator = _repositories(tmp_path)
    previous_snapshot = PROFILE.model_copy(update={"collection_size": 10})
    deleted_snapshot = PROFILE.model_copy(update={"collection_size": 20})
    unrelated_snapshot = PROFILE.model_copy(
        update={"profile_name": "Other", "collection_size": 30}
    )
    previous = catalog.import_profile(
        previous_snapshot, "Server", "Account", "previous profile", "clipboard"
    )
    deleted = catalog.import_profile(
        deleted_snapshot, "Server", "Account", "mistaken profile", "clipboard"
    )
    unrelated = catalog.import_profile(
        unrelated_snapshot, "Other Server", "Other Account", "other profile", "clipboard"
    )

    assert catalog.profile("Server", "Account").snapshot == deleted_snapshot
    assert catalog.delete_import_event(deleted.import_event_id) is True

    assert catalog.profile("Server", "Account").snapshot == previous_snapshot
    assert catalog.profile("Other Server", "Other Account").snapshot == unrelated_snapshot
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM import_events WHERE id = ?", (deleted.import_event_id,)
        ).fetchone() is None
        assert connection.execute(
            "SELECT COUNT(*) FROM profile_observations WHERE import_event_id = ?",
            (deleted.import_event_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM profile_observations WHERE import_event_id IN (?, ?)",
            (previous.import_event_id, unrelated.import_event_id),
        ).fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 2


def test_durably_linked_profile_import_refuses_deletion_and_replays_after_restart(
    tmp_path,
) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        before = (
            tuple(connection.execute("SELECT * FROM discord_source_events").fetchone()),
            tuple(connection.execute("SELECT * FROM discord_processing_attempts").fetchone()),
            tuple(connection.execute("SELECT * FROM discord_projection_links").fetchone()),
            tuple(connection.execute("SELECT * FROM profile_observations").fetchone()),
            _attribution_rows(connection),
            _counts(connection),
        )

    with pytest.raises(ImportEventDeletionBlockedError, match="durable/replayable"):
        catalog.delete_import_event(first.import_event_id)

    restarted = ProfileProjectionCoordinator(
        CatalogRepository(database_path), DiscordMessageRepository(database_path)
    )
    replay = _coordinate(restarted, source_event_id, None)
    assert replay == ProfileProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        profile_observation_id=first.profile_observation_id,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=first.projection_target,
    )
    with connect(database_path) as connection:
        after = (
            tuple(connection.execute("SELECT * FROM discord_source_events").fetchone()),
            tuple(connection.execute("SELECT * FROM discord_processing_attempts").fetchone()),
            tuple(connection.execute("SELECT * FROM discord_projection_links").fetchone()),
            tuple(connection.execute("SELECT * FROM profile_observations").fetchone()),
            _attribution_rows(connection),
            _counts(connection),
        )
    assert after == before


def test_edited_profile_revision_gets_independent_projection(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    first_event, first_attempt = _receive_and_begin(discord)
    second_event, second_attempt = _receive_and_begin(discord, suffix="edited")

    first = _coordinate(coordinator, first_event, first_attempt)
    second = _coordinate(coordinator, second_event, second_attempt)

    assert first.profile_observation_id != second.profile_observation_id
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM profile_observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 2


def test_completed_link_with_missing_target_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute("DELETE FROM profile_observations WHERE id = ?", (first.profile_observation_id,))

    with pytest.raises(ProfileProjectionTargetError, match="missing"):
        _coordinate(coordinator, source_event_id, None)


def test_completed_link_with_wrong_target_table_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE discord_projection_links SET projection_table = 'roll_observations'"
        )

    with pytest.raises(ProfileProjectionIntegrityError, match="inconsistent profile projection link"):
        _coordinate(coordinator, source_event_id, None)


def test_completed_link_with_mismatched_import_event_fails_closed(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    second = catalog.import_profile(PROFILE, "Server", "Account", "second profile", "discord")
    with connect(database_path) as connection:
        second_observation_id = connection.execute(
            "SELECT id FROM profile_observations WHERE import_event_id = ?", (second.import_event_id,)
        ).fetchone()[0]
        connection.execute(
            "UPDATE discord_projection_links SET projection_row_id = ?", (second_observation_id,)
        )

    with pytest.raises(ProfileProjectionTargetError, match="another import event"):
        _coordinate(coordinator, source_event_id, None)
    assert first.import_event_id != second.import_event_id


def test_unexpected_claimed_link_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    slot = _slot("Server", "Account")
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, 'catalog.profile', ?, 'claimed', ?, ?, ?)
            """,
            (source_event_id, slot, OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat()),
        )

    with pytest.raises(ProfileProjectionIntegrityError, match="still claimed"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_succeeded_replay_rejects_semantic_slot_mismatch(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)

    with pytest.raises(ProfileProjectionIntegrityError, match="another account"):
        _coordinate(coordinator, source_event_id, None, account="Other Account")


def test_attempt_ownership_and_state_are_validated(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix="one")
    other_event_id, other_attempt_id = _receive_and_begin(discord, suffix="two")

    with pytest.raises(ProfileProjectionStateError, match="another source event"):
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
    with pytest.raises(ProfileProjectionStateError, match="not processing"):
        _coordinate(coordinator, other_event_id, other_attempt_id)
    with pytest.raises(ProfileProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)
    assert attempt_id > 0


def test_received_event_without_attempt_is_rejected(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, _attempt_id = _receive_and_begin(discord)
    with connect(_database_path) as connection:
        connection.execute(
            "DELETE FROM discord_processing_attempts WHERE source_event_id = ?", (source_event_id,)
        )
        connection.execute(
            "UPDATE discord_source_events SET status = 'received' WHERE id = ?", (source_event_id,)
        )

    with pytest.raises(ProfileProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)


def test_repository_database_path_mismatch_is_rejected_before_coordination(tmp_path) -> None:
    catalog = CatalogRepository(tmp_path / "catalog.db")
    discord = DiscordMessageRepository(tmp_path / "discord.db")

    with pytest.raises(ProfileProjectionDatabasePathError, match="same database path"):
        ProfileProjectionCoordinator(catalog, discord)


def test_only_profile_projection_links_are_created(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT projection_kind, projection_table FROM discord_projection_links"
        ).fetchall()
    assert [tuple(row) for row in rows] == [("catalog.profile", "profile_observations")]


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("missing_server", "no persisted server attribution"),
        ("unresolved_server", "non-resolved server attribution"),
        ("ambiguous_server", "non-resolved server attribution"),
        ("server_mismatch", "another server"),
        ("missing_account", "no persisted account attribution"),
        ("unresolved_account", "non-resolved account attribution"),
        ("ambiguous_account", "non-resolved account attribution"),
        ("account_server_mismatch", "mismatched account attribution server"),
        ("account_mismatch", "another account"),
    ],
)
def test_first_processing_requires_matching_persisted_attribution_before_writes(
    tmp_path, failure, message
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _set_attribution_failure(database_path, failure)

    with connect(database_path) as connection:
        before = _durable_state(connection)

    with pytest.raises(ProfileProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert _durable_state(connection) == before
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0] == "processing"


@pytest.mark.parametrize(
    "profile",
    [
        ProfileSnapshot(
            profile_name="Other Account",
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
            silver_keys=None,
            gold_keys=None,
            sphere_stock=None,
            spheres={":spP:": 2},
            displayed_badges=(":silvmudae:", ":DiamondI:"),
            pokedex_observed=True,
            reactions_observed=True,
            mudapins_observed=False,
            kakera_balance_observed=True,
            keys_observed=True,
            bronze_keys_observed=True,
            silver_keys_observed=False,
            gold_keys_observed=False,
            sphere_stock_observed=False,
            sphere_counts_observed=True,
            badges_observed=True,
        ),
        ProfileSnapshot(
            profile_name="   ",
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
            silver_keys=None,
            gold_keys=None,
            sphere_stock=None,
            spheres={":spP:": 2},
            displayed_badges=(":silvmudae:", ":DiamondI:"),
            pokedex_observed=True,
            reactions_observed=True,
            mudapins_observed=False,
            kakera_balance_observed=True,
            keys_observed=True,
            bronze_keys_observed=True,
            silver_keys_observed=False,
            gold_keys_observed=False,
            sphere_stock_observed=False,
            sphere_counts_observed=True,
            badges_observed=True,
        ),
    ],
    ids=["owner-caller-mismatch", "blank-owner"],
)
def test_first_processing_requires_valid_typed_profile_owner_before_writes(
    tmp_path, profile
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        before = _durable_state(connection)

    with pytest.raises(ProfileProjectionIntegrityError, match="typed profile owner|valid typed owner"):
        _coordinate(coordinator, source_event_id, attempt_id, profile=profile)

    with connect(database_path) as connection:
        assert _durable_state(connection) == before


def test_matching_succeeded_replay_validates_attribution_before_returning_existing_ids(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        before = _durable_state(connection)

    replay = _coordinate(coordinator, source_event_id, None)

    assert replay == ProfileProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        profile_observation_id=first.profile_observation_id,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=first.projection_target,
    )
    with connect(database_path) as connection:
        assert _durable_state(connection) == before


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("missing_server", "no persisted server attribution"),
        ("unresolved_server", "non-resolved server attribution"),
        ("ambiguous_server", "non-resolved server attribution"),
        ("server_mismatch", "another server"),
        ("missing_account", "no persisted account attribution"),
        ("unresolved_account", "non-resolved account attribution"),
        ("ambiguous_account", "non-resolved account attribution"),
        ("account_server_mismatch", "mismatched account attribution server"),
        ("account_mismatch", "another account"),
    ],
)
def test_succeeded_replay_requires_matching_persisted_attribution_and_changes_nothing(
    tmp_path, failure, message
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)
    _set_attribution_failure(database_path, failure)
    with connect(database_path) as connection:
        before = _durable_state(connection)

    with pytest.raises(ProfileProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, None)

    with connect(database_path) as connection:
        assert _durable_state(connection) == before


def test_succeeded_replay_requires_matching_typed_profile_owner(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)
    mismatched_profile = PROFILE.model_copy(update={"profile_name": "Other Account"})
    with connect(database_path) as connection:
        before = _durable_state(connection)

    with pytest.raises(ProfileProjectionIntegrityError, match="typed profile owner"):
        _coordinate(coordinator, source_event_id, None, profile=mismatched_profile)

    with connect(database_path) as connection:
        assert _durable_state(connection) == before


def test_historical_succeeded_replay_without_account_attribution_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)
    _set_attribution_failure(database_path, "missing_account")
    with connect(database_path) as connection:
        before = _durable_state(connection)

    with pytest.raises(ProfileProjectionIntegrityError, match="no persisted account attribution"):
        _coordinate(coordinator, source_event_id, None)

    with connect(database_path) as connection:
        assert _durable_state(connection) == before
