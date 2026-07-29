import json
import sqlite3
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import (
    BadgeLevel,
    ClaimConfirmation,
    KakeraStateSnapshot,
    ProfileSnapshot,
    RollObservation,
    ServerSettingMetric,
    ServerSettingsSnapshot,
    KakeralootSettingsSnapshot,
    MudapinSnapshot,
    TowerStateSnapshot,
    TimerStateSnapshot,
)
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
SETTINGS = ServerSettingsSnapshot(
    server_premium=False,
    prefix="$",
    language="English",
    claim_reset_minutes=60,
    reset_minute="00",
    reset_shift_minutes=0,
    rolls_per_hour=10,
    claim_reaction_expiry_seconds=30,
    claimed_character_rarity_multiplier=2,
    kakera_bonus_percent=15,
    sphere_bonus_percent=5,
    game_mode=1,
    channel_instance=2,
    metrics=(
        ServerSettingMetric(label="Prefix", value="$"),
        ServerSettingMetric(label="Lang", value="English"),
    ),
)
KAKERALOOT_SETTINGS = KakeralootSettingsSnapshot(
    loot_cost=500,
    quantity_quality_base_cost=2000,
    quantity_quality_level_increment=200,
)
TIMER_STATE = TimerStateSnapshot(
    can_claim_now=True,
    claim_reset_minutes=0,
    rolls_left=17,
    rolls_reset_minutes=42,
    rolls_reset_stock=0,
    vote_reset_minutes=None,
    daily_reset_minutes=613,
    daily_kakera_ready=False,
    rt_available=None,
    can_react_kakera_now=True,
    reaction_power_percent=72,
    kakera_button_power_cost_percent=36,
    soulmate_button_power_cost_percent=18,
    kakera_stock=12114,
    gold_key_stock_remaining=0,
    gold_key_reset_minutes=None,
    bku_reset_probability_percent=10,
    oh_remaining=3,
    oc_remaining=None,
    oq_remaining=1,
    oq_stored=0,
    ot_remaining=8,
    ouro_refill_minutes=918,
    rolls_reset_status="limited_timer",
    rolls_per_hour_limit=17,
    rt_reset_minutes=612,
)
KAKERA_STATE = KakeraStateSnapshot(
    kakera_balance=7_673,
    badges=(
        BadgeLevel(badge_name="bronze", level=4, max_reached=True),
        BadgeLevel(badge_name="silver", level=3, max_reached=False),
        BadgeLevel(badge_name="gold", level=2, max_reached=True),
    ),
)
ZERO_KAKERA_STATE = KakeraStateSnapshot(kakera_balance=0, badges=())
TOWER_STATE = TowerStateSnapshot(
    current_level=2,
    completed_towers=3,
    next_level_cost=75_000,
    kakera_balance=7_673,
    built_perk_ids=(2, 7),
)
TOWER_STATE_WITHOUT_COMPLETED_TOWERS = TOWER_STATE.model_copy(update={"completed_towers": None})
MUDAPINS = MudapinSnapshot(pin_markers=(":pin139:", ":pin182:", ":logopin6:"))
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


def _settings_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "server_settings_observations",
        "discord_projection_links",
        "discord_source_event_server_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _kakeraloot_settings_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "kakeraloot_settings_observations",
        "discord_projection_links",
        "discord_source_event_server_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _timer_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "timer_state_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _mudapins_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "mudapin_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _kakera_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "kakera_state_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _tower_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "tower_state_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def test_public_kakera_import_preserves_compatibility_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_kakera_state(
        KAKERA_STATE,
        "  Server  ",
        "  Account  ",
        "kakera payload",
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _kakera_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "kakera_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, kakera_balance, badges_json, observed_at, import_event_id
            FROM kakera_state_observations
            WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == (
            "kakera_state",
            "discord",
            result.observed_at.isoformat(),
            "kakera payload",
        )
        assert observation["id"] > 0
        assert observation["account_context_id"] > 0
        assert observation["kakera_balance"] == 7_673
        assert json.loads(observation["badges_json"]) == [badge.model_dump() for badge in KAKERA_STATE.badges]
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id


def test_kakera_helper_uses_supplied_connection_and_returns_actual_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakera_state_with_connection(
            connection,
            state=KAKERA_STATE,
            server=" Server ",
            account=" Account ",
            raw="raw kakera",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        assert is_dataclass(imported)
        assert [field.name for field in fields(imported)] == [
            "import_event_id",
            "kakera_state_observation_id",
        ]
        assert imported.import_event_id > 0
        assert imported.kakera_state_observation_id > 0
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM kakera_state_observations").fetchone()[0] == 1
        observation = connection.execute(
            """
            SELECT kakera_state_observations.*, account_contexts.normalized_name AS account_name
            FROM kakera_state_observations
            JOIN account_contexts ON account_contexts.id = kakera_state_observations.account_context_id
            WHERE kakera_state_observations.id = ?
            """,
            (imported.kakera_state_observation_id,),
        ).fetchone()
        assert observation["account_name"] == "account"
        assert observation["kakera_balance"] == 7_673
        assert json.loads(observation["badges_json"]) == [badge.model_dump() for badge in KAKERA_STATE.badges]
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM kakera_state_observations").fetchone()[0] == 0
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM kakera_state_observations WHERE id = ?",
            (imported.kakera_state_observation_id,),
        ).fetchone()[0] == imported.kakera_state_observation_id


def test_kakera_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_kakera_state_with_connection(
            connection,
            state=ZERO_KAKERA_STATE,
            server="Server",
            account="Account",
            raw="rollback kakera",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        connection.rollback()

    with connect(database_path) as connection:
        assert _kakera_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "kakera_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_kakera_helper_reuses_existing_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_kakera_state(ZERO_KAKERA_STATE, "Server", "Account", "existing", "discord")

    with connect(database_path) as connection:
        before = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _kakera_state_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_kakera_state_with_connection(
            connection,
            state=KAKERA_STATE,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="reused contexts",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert (current["server_id"], current["account_id"]) == (before["server_id"], before["account_id"])
        assert (current["server_name"], current["account_name"]) == ("SERVER", "ACCOUNT")
        assert _kakera_state_counts(connection)["server_contexts"] == 1
        assert _kakera_state_counts(connection)["account_contexts"] == 1
        assert imported.import_event_id > 0
        assert imported.kakera_state_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(before)
        assert _kakera_state_counts(connection) == before_counts


def test_public_tower_import_preserves_compatibility_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_tower_state(
        TOWER_STATE,
        "  Server  ",
        "  Account  ",
        "tower payload",
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    with connect(database_path) as connection:
        assert _tower_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "tower_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, current_level, completed_towers, next_level_cost,
                   kakera_balance, built_perk_ids_json, observed_at, import_event_id
            FROM tower_state_observations
            WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("tower_state", "discord", result.observed_at.isoformat(), "tower payload")
        assert observation["id"] > 0
        assert observation["account_context_id"] > 0
        assert observation["current_level"] == TOWER_STATE.current_level
        assert observation["completed_towers"] == TOWER_STATE.completed_towers
        assert observation["next_level_cost"] == TOWER_STATE.next_level_cost
        assert observation["kakera_balance"] == TOWER_STATE.kakera_balance
        assert json.loads(observation["built_perk_ids_json"]) == list(TOWER_STATE.built_perk_ids)
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id


def test_tower_helper_uses_supplied_connection_returns_actual_ids_and_commits(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_tower_state_with_connection(
            connection,
            state=TOWER_STATE,
            server=" Server ",
            account=" Account ",
            raw="raw tower",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        assert is_dataclass(imported)
        assert [field.name for field in fields(imported)] == [
            "import_event_id",
            "tower_state_observation_id",
        ]
        assert imported.import_event_id > 0
        assert imported.tower_state_observation_id > 0
        assert connection.in_transaction is True
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM tower_state_observations WHERE id = ?",
            (imported.tower_state_observation_id,),
        ).fetchone()[0] == imported.tower_state_observation_id
        assert _tower_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "tower_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        observation = connection.execute(
            "SELECT observed_at, import_event_id FROM tower_state_observations WHERE id = ?",
            (imported.tower_state_observation_id,),
        ).fetchone()
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM tower_state_observations").fetchone()[0] == 0
        connection.commit()

    with connect(database_path) as connection:
        assert _tower_state_counts(connection)["tower_state_observations"] == 1


def test_tower_helper_rollback_removes_new_rows_and_contexts(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_tower_state_with_connection(
            connection,
            state=TOWER_STATE,
            server="Server",
            account="Account",
            raw="rollback tower",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _tower_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "tower_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_tower_helper_reuses_existing_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_tower_state(TOWER_STATE, "Server", "Account", "existing", "discord")

    with connect(database_path) as connection:
        before = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _tower_state_counts(connection)

    with connect(database_path) as connection:
        catalog._import_tower_state_with_connection(
            connection,
            state=TOWER_STATE,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="reused contexts",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert (current["server_id"], current["account_id"]) == (before["server_id"], before["account_id"])
        assert (current["server_name"], current["account_name"]) == ("SERVER", "ACCOUNT")
        assert _tower_state_counts(connection)["server_contexts"] == 1
        assert _tower_state_counts(connection)["account_contexts"] == 1
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(before)
        assert _tower_state_counts(connection) == before_counts


@pytest.mark.parametrize(
    ("state", "expected_completed_towers"),
    [
        (TOWER_STATE, 3),
        (TOWER_STATE_WITHOUT_COMPLETED_TOWERS, 0),
    ],
)
def test_tower_helper_preserves_completed_tower_values(tmp_path, state, expected_completed_towers) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_tower_state_with_connection(
            connection,
            state=state,
            server="Server",
            account=f"Account {expected_completed_towers}",
            raw="completed towers",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.execute(
            "SELECT completed_towers FROM tower_state_observations WHERE id = ?",
            (imported.tower_state_observation_id,),
        ).fetchone()[0] == expected_completed_towers


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


def test_public_server_settings_wrapper_preserves_result_rows_and_metrics(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_server_settings(SETTINGS, " Server ", "settings payload", "discord")

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    with connect(database_path) as connection:
        assert _settings_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "server_settings_observations": 1,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("server_settings", "discord", "settings payload")
        observation = connection.execute(
            """
            SELECT id, server_premium, prefix, language, claim_reset_minutes, reset_minute,
                   reset_shift_minutes, rolls_per_hour, claim_reaction_expiry_seconds,
                   claimed_character_rarity_multiplier, kakera_bonus_percent, sphere_bonus_percent,
                   game_mode, channel_instance, metrics_json, import_event_id
            FROM server_settings_observations
            """
        ).fetchone()
        assert observation["id"] > 0
        assert tuple(observation)[1:14] == (
            0,
            "$",
            "English",
            60,
            "00",
            0,
            10,
            30,
            2,
            15,
            5,
            1,
            2,
        )
        assert observation["metrics_json"] == '[{"label": "Prefix", "value": "$"}, {"label": "Lang", "value": "English"}]'
        assert observation["import_event_id"] == result.import_event_id


def test_server_settings_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_server_settings_with_connection(
            connection,
            settings=SETTINGS,
            server="Server",
            raw="settings payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_settings_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT import_event_id FROM server_settings_observations WHERE id = ?",
            (imported.server_settings_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_settings_observations").fetchone()[0] == 0


def test_server_settings_helper_commit_persists_rows_and_result_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_server_settings_with_connection(
            connection,
            settings=SETTINGS,
            server="Server",
            raw="settings payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM server_settings_observations WHERE id = ?",
            (imported.server_settings_observation_id,),
        ).fetchone()[0] == imported.server_settings_observation_id
        assert _settings_counts(connection)["server_settings_observations"] == 1


def test_server_settings_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_server_settings_with_connection(
                connection,
                settings=SETTINGS,
                server="Server",
                raw="settings payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _settings_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "server_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_server_settings_helper_reuses_server_and_rollback_preserves_it(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_server_settings(SETTINGS, "Server", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        before_counts = _settings_counts(connection)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_server_settings_with_connection(
                connection,
                settings=SETTINGS,
                server=" SERVER ",
                raw="second payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        current = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _settings_counts(connection) == before_counts


def test_server_settings_helper_does_not_write_projection_or_attribution_state(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        before = _settings_counts(connection)
        catalog._import_server_settings_with_connection(
            connection,
            settings=SETTINGS,
            server="Server",
            raw="settings payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        after = _settings_counts(connection)

    assert after["import_events"] == before["import_events"] + 1
    assert after["server_contexts"] == before["server_contexts"] + 1
    assert after["server_settings_observations"] == before["server_settings_observations"] + 1
    assert after["discord_projection_links"] == before["discord_projection_links"]
    assert after["discord_source_event_server_attributions"] == before["discord_source_event_server_attributions"]
    assert after["discord_processing_attempts"] == before["discord_processing_attempts"]


def test_public_kakeraloot_settings_wrapper_preserves_result_rows_and_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_kakeraloot_settings(
        KAKERALOOT_SETTINGS, " Server ", "infokl payload", "discord"
    )

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    settings = catalog.kakeraloot_settings("Server")
    assert settings is not None
    assert (settings.loot_cost, settings.quantity_quality_base_cost, settings.quantity_quality_level_increment) == (
        500,
        2000,
        200,
    )
    with connect(database_path) as connection:
        assert _kakeraloot_settings_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "kakeraloot_settings_observations": 1,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("kakeraloot_settings", "discord", "infokl payload")
        observation = connection.execute(
            """
            SELECT id, server_context_id, loot_cost, quantity_quality_base_cost,
                   quantity_quality_level_increment, observed_at, import_event_id
            FROM kakeraloot_settings_observations
            """
        ).fetchone()
        assert observation["id"] > 0
        assert observation["server_context_id"] == connection.execute(
            "SELECT id FROM server_contexts WHERE normalized_name = ?",
            ("server",),
        ).fetchone()[0]
        assert tuple(observation)[2:] == (
            500,
            2000,
            200,
            result.observed_at.isoformat(),
            result.import_event_id,
        )


def test_kakeraloot_settings_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakeraloot_settings_with_connection(
            connection,
            settings=KAKERALOOT_SETTINGS,
            server="Server",
            raw="infokl payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM kakeraloot_settings_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT import_event_id FROM kakeraloot_settings_observations WHERE id = ?",
            (imported.kakeraloot_settings_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM kakeraloot_settings_observations").fetchone()[0] == 0


def test_kakeraloot_settings_helper_commit_persists_rows_and_result_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakeraloot_settings_with_connection(
            connection,
            settings=KAKERALOOT_SETTINGS,
            server="Server",
            raw="infokl payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM kakeraloot_settings_observations WHERE id = ?",
            (imported.kakeraloot_settings_observation_id,),
        ).fetchone()[0] == imported.kakeraloot_settings_observation_id
        row = connection.execute(
            """
            SELECT loot_cost, quantity_quality_base_cost, quantity_quality_level_increment,
                   import_event_id
            FROM kakeraloot_settings_observations
            WHERE id = ?
            """,
            (imported.kakeraloot_settings_observation_id,),
        ).fetchone()
        assert tuple(row) == (500, 2000, 200, imported.import_event_id)


def test_kakeraloot_settings_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_kakeraloot_settings_with_connection(
                connection,
                settings=KAKERALOOT_SETTINGS,
                server="Server",
                raw="infokl payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _kakeraloot_settings_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "kakeraloot_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_kakeraloot_settings_helper_reuses_server_and_rollback_preserves_it(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_kakeraloot_settings(KAKERALOOT_SETTINGS, "Server", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        before_counts = _kakeraloot_settings_counts(connection)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_kakeraloot_settings_with_connection(
                connection,
                settings=KAKERALOOT_SETTINGS,
                server=" SERVER ",
                raw="second payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        current = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _kakeraloot_settings_counts(connection) == before_counts


def test_kakeraloot_settings_helper_does_not_write_projection_or_discord_state(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        before_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        before_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0]
        before_counts = _kakeraloot_settings_counts(connection)
        catalog._import_kakeraloot_settings_with_connection(
            connection,
            settings=KAKERALOOT_SETTINGS,
            server="Server",
            raw="infokl payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        after_counts = _kakeraloot_settings_counts(connection)
        after_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        after_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0]

    assert after_counts["import_events"] == before_counts["import_events"] + 1
    assert after_counts["server_contexts"] == before_counts["server_contexts"] + 1
    assert after_counts["kakeraloot_settings_observations"] == before_counts["kakeraloot_settings_observations"] + 1
    assert after_counts["discord_projection_links"] == before_counts["discord_projection_links"]
    assert after_counts["discord_source_event_server_attributions"] == before_counts[
        "discord_source_event_server_attributions"
    ]
    assert after_counts["discord_processing_attempts"] == before_counts["discord_processing_attempts"]
    assert tuple(after_event) == tuple(before_event)
    assert after_attempt == before_attempt


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


def test_public_timer_state_wrapper_preserves_result_rows_and_snapshot(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "timer payload with exact source text"

    result = catalog.import_timer_state(
        TIMER_STATE,
        " Server ",
        " Account ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    with connect(database_path) as connection:
        assert _timer_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "timer_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("timer_state", "discord", raw_message, result.observed_at.isoformat())
        observation = connection.execute(
            """
            SELECT id, account_context_id, snapshot_json, observed_at, import_event_id
            FROM timer_state_observations
            WHERE id = (SELECT MAX(id) FROM timer_state_observations)
            """
        ).fetchone()
        account = connection.execute(
            "SELECT id, name, normalized_name FROM account_contexts"
        ).fetchone()
        server = connection.execute(
            "SELECT name, normalized_name FROM server_contexts"
        ).fetchone()
        assert tuple(account) == (account["id"], "Account", "account")
        assert tuple(server) == ("Server", "server")
        assert observation["id"] > 0
        assert observation["account_context_id"] == account["id"]
        assert json.loads(observation["snapshot_json"]) == TIMER_STATE.model_dump()
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id


def test_timer_state_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_timer_state_with_connection(
            connection,
            state=TIMER_STATE,
            server="Server",
            account="Account",
            raw="timer payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert _timer_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "timer_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        observation = connection.execute(
            "SELECT import_event_id FROM timer_state_observations WHERE id = ?",
            (imported.timer_state_observation_id,),
        ).fetchone()
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM timer_state_observations").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0


def test_timer_state_helper_commit_persists_rows_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_timer_state_with_connection(
            connection,
            state=TIMER_STATE,
            server="Server",
            account="Account",
            raw="timer payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            "SELECT id, observed_at, import_event_id, account_context_id FROM timer_state_observations WHERE id = ?",
            (imported.timer_state_observation_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT id FROM account_contexts WHERE normalized_name = ?",
            ("account",),
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == ("timer_state", "discord", "timer payload", OBSERVED_AT.isoformat())
        assert observation["id"] == imported.timer_state_observation_id
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        assert observation["account_context_id"] == account["id"]


def test_timer_state_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_timer_state_with_connection(
            connection,
            state=TIMER_STATE,
            server="Server",
            account="Account",
            raw="timer payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _timer_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "timer_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_timer_state_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_timer_state(TIMER_STATE, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _timer_state_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_timer_state_with_connection(
            connection,
            state=TIMER_STATE,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.timer_state_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _timer_state_counts(connection) == before_counts


def test_public_mudapins_wrapper_preserves_compatibility_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "mudapins payload with exact source text"

    result = catalog.import_mudapins(
        MUDAPINS,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _mudapins_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "mudapin_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, pin_markers_json, pin_count, observed_at, import_event_id
            FROM mudapin_observations WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        account = connection.execute("SELECT id, name, normalized_name FROM account_contexts").fetchone()
        server = connection.execute("SELECT name, normalized_name FROM server_contexts").fetchone()
        assert tuple(event) == ("mudapins", "discord", result.observed_at.isoformat(), raw_message)
        assert observation["id"] > 0
        assert observation["account_context_id"] == account["id"]
        assert json.loads(observation["pin_markers_json"]) == list(MUDAPINS.pin_markers)
        assert observation["pin_count"] == len(MUDAPINS.pin_markers)
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id
        assert tuple(account) == (account["id"], "Account", "account")
        assert tuple(server) == ("Server", "server")


def test_mudapins_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server="Server",
            account="Account",
            raw="mudapins payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert _mudapins_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "mudapin_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        assert connection.execute(
            "SELECT import_event_id FROM mudapin_observations WHERE id = ?",
            (imported.mudapin_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM mudapin_observations").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0


def test_mudapins_helper_commit_persists_rows_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server="Server",
            account="Account",
            raw="mudapins payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, observed_at, import_event_id, account_context_id
            FROM mudapin_observations WHERE id = ?
            """,
            (imported.mudapin_observation_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT id FROM account_contexts WHERE normalized_name = ?", ("account",)
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == (
            "mudapins",
            "clipboard",
            "mudapins payload",
            OBSERVED_AT.isoformat(),
        )
        assert observation["id"] == imported.mudapin_observation_id
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        assert observation["account_context_id"] == account["id"]


def test_mudapins_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server="Server",
            account="Account",
            raw="mudapins payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _mudapins_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "mudapin_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_mudapins_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_mudapins(MUDAPINS, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _mudapins_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.mudapin_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _mudapins_counts(connection) == before_counts


def test_mudapins_marker_storage_preserves_order_duplicates_and_empty_inventory(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    snapshots = (
        (MudapinSnapshot(pin_markers=(":pin1:", ":pin2:")), (":pin1:", ":pin2:")),
        (MudapinSnapshot(pin_markers=(":pin3:", ":logopin4:")), (":pin3:", ":logopin4:")),
        (
            MudapinSnapshot(pin_markers=(":pin5:", ":pin5:", ":logopin6:")),
            (":pin5:", ":pin5:", ":logopin6:"),
        ),
        (MudapinSnapshot(pin_markers=()), ()),
    )

    results = [
        catalog.import_mudapins(snapshot, "Server", f"Account {index}", "payload", "discord")
        for index, (snapshot, _expected) in enumerate(snapshots)
    ]

    with connect(database_path) as connection:
        for result, (_snapshot, expected) in zip(results, snapshots):
            observation = connection.execute(
                "SELECT pin_markers_json, pin_count FROM mudapin_observations WHERE import_event_id = ?",
                (result.import_event_id,),
            ).fetchone()
            assert json.loads(observation["pin_markers_json"]) == list(expected)
            assert observation["pin_count"] == len(expected)


def test_mudapins_helper_does_not_write_discord_or_legacy_state(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        before_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        before_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]
        before_counts = _mudapins_counts(connection)
        imported = catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server="Server",
            account="Account",
            raw="mudapins payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        after_counts = _mudapins_counts(connection)
        after_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        after_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]

    assert after_counts["import_events"] == before_counts["import_events"] + 1
    assert after_counts["server_contexts"] == before_counts["server_contexts"] + 1
    assert after_counts["account_contexts"] == before_counts["account_contexts"] + 1
    assert after_counts["mudapin_observations"] == before_counts["mudapin_observations"] + 1
    assert after_counts["discord_projection_links"] == before_counts["discord_projection_links"]
    assert after_counts["discord_source_event_server_attributions"] == before_counts[
        "discord_source_event_server_attributions"
    ]
    assert after_counts["discord_source_event_account_attributions"] == before_counts[
        "discord_source_event_account_attributions"
    ]
    assert after_counts["discord_processing_attempts"] == before_counts["discord_processing_attempts"]
    assert tuple(after_event) == tuple(before_event)
    assert after_attempt == before_attempt
    assert imported.import_event_id > 0
    assert imported.mudapin_observation_id > 0
