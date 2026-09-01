from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.projection_link_repository import ProjectionLinkRepository
from moa.services.projection_authority import get_projection_authority
from moa.services.projection_expectations import (
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
)
from moa.services.retained_source_reprojection_admission_service import (
    RetainedSourceReprojectionAdmissionService,
)
from moa.services.retained_source_singleton_reprojection_executor import (
    RetainedSourceSingletonReprojectionError,
    RetainedSourceSingletonReprojectionExecutor,
)


NOW = "2026-08-31T12:00:00+00:00"
COMPLETED_AT = datetime(2026, 8, 31, 13, tzinfo=timezone.utc)
FAMILIES = (
    "claim",
    "kakera_state",
    "mudapins",
    "player_bonus",
    "wishlist",
    "disablelist",
    "tower_state",
    "kakeraloot_state",
    "sphere_result",
    "profile",
)
TABLES = {
    "claim": "claim_observations",
    "kakera_state": "kakera_state_observations",
    "mudapins": "mudapin_observations",
    "player_bonus": "player_bonus_observations",
    "wishlist": "wishlist_observations",
    "disablelist": "disablelist_observations",
    "tower_state": "tower_state_observations",
    "kakeraloot_state": "kakeraloot_state_observations",
    "sphere_result": "sphere_result_observations",
    "profile": "profile_observations",
}


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "singleton-reprojection.sqlite3"
    CatalogRepository(path)
    return path


def _insert_target(connection: sqlite3.Connection, family: str, import_id: int) -> int:
    common = (NOW, import_id)
    statements = {
        "claim": (
            "INSERT INTO claim_observations (account_context_id, character_id, "
            "character_name, normalized_character_name, observed_at, import_event_id) "
            "VALUES (1, 1, 'Character', 'character', ?, ?)",
            common,
        ),
        "kakera_state": (
            "INSERT INTO kakera_state_observations (account_context_id, kakera_balance, "
            "badges_json, observed_at, import_event_id) VALUES (1, 100, '[]', ?, ?)",
            common,
        ),
        "mudapins": (
            "INSERT INTO mudapin_observations (account_context_id, pin_markers_json, "
            "pin_count, observed_at, import_event_id) VALUES (1, '[\"pin\"]', 1, ?, ?)",
            common,
        ),
        "player_bonus": (
            "INSERT INTO player_bonus_observations (account_context_id, metrics_json, "
            "observed_at, import_event_id) VALUES (1, '[]', ?, ?)",
            common,
        ),
        "wishlist": (
            "INSERT INTO wishlist_observations (account_context_id, wishlist_count, "
            "wishlist_capacity, starwish_count, starwish_capacity, entries_json, observed_at, "
            "import_event_id) VALUES (1, 0, 10, 0, 1, '[]', ?, ?)",
            common,
        ),
        "disablelist": (
            "INSERT INTO disablelist_observations (account_context_id, slots_used, "
            "slots_capacity, total_disabled, disabled_wa, disabled_ha, disabled_wg, "
            "disabled_hg, wa_pool_limit, ha_pool_limit, western_disabled, irl_disabled, "
            "western_disabled_observed, irl_disabled_observed, entries_json, observed_at, "
            "import_event_id) VALUES (1, 0, 10, 0, 0, 0, 0, 0, NULL, NULL, 0, 0, 1, 1, "
            "'[]', ?, ?)",
            common,
        ),
        "tower_state": (
            "INSERT INTO tower_state_observations (account_context_id, current_level, "
            "completed_towers, next_level_cost, kakera_balance, built_perk_ids_json, "
            "observed_at, import_event_id, completed_towers_observed) "
            "VALUES (1, 1, 0, 1000, 500, '[]', ?, ?, 1)",
            common,
        ),
        "sphere_result": (
            "INSERT INTO sphere_result_observations (account_context_id, snapshot_json, "
            "total_gained, stock, observed_at, import_event_id) VALUES "
            '(1, \'{"clicks_available":null,"click_window_minutes":null,'
            '"purple_target":null,"purple_total":null,"gains":[],'
            '"total_gained":0,"stock":null}\', 0, NULL, ?, ?)',
            common,
        ),
        "profile": (
            "INSERT INTO profile_observations (account_context_id, profile_name, "
            "collection_size, female_percent, male_percent, pokedex_count, pokedex_json, "
            "kakera_reacts_json, mudapins_collected, mudapins_total, kakera_balance, "
            "bronze_keys, silver_keys, gold_keys, sphere_stock, spheres_json, "
            "displayed_badges_json, observed_at, import_event_id, pokedex_observed, "
            "reactions_observed, mudapins_observed, kakera_balance_observed, keys_observed, "
            "bronze_keys_observed, silver_keys_observed, gold_keys_observed, "
            "sphere_stock_observed, sphere_counts_observed, badges_observed) VALUES "
            "(1, 'Account', 0, 0, 0, NULL, '[]', '{}', NULL, NULL, NULL, 0, 0, 0, "
            "NULL, '{}', '[]', ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)",
            common,
        ),
    }
    if family == "kakeraloot_state":
        fields = (
            "rolls_stacked",
            "disable_wa_ha_reduction",
            "disable_wg_hg_reduction",
            "protected_wish_level",
            "protected_wish_denominator",
            "mudapins",
            "rt_cooldown_reduction_hours",
            "permanent_roll_bonus",
            "star_branches",
            "starwish_slots_from_branches",
            "quantity_level",
            "quality_level",
            "usage_count",
            "kakera_balance",
        )
        columns = ", ".join(fields)
        presence = ", ".join(f"{field}_observed" for field in fields)
        zeroes = ", ".join("0" for _ in fields)
        return int(
            connection.execute(
                f"INSERT INTO kakeraloot_state_observations (account_context_id, "
                f"has_kakeraloots, status_note, {columns}, observed_at, import_event_id, "
                f"{presence}) VALUES (1, 0, 'none', {zeroes}, ?, ?, {zeroes})",
                common,
            ).lastrowid
        )
    sql, parameters = statements[family]
    return int(connection.execute(sql, parameters).lastrowid)


def _fixture(connection: sqlite3.Connection, family: str, historical_generations: int = 2):
    connection.execute(
        "INSERT INTO server_contexts (id, name, normalized_name, created_at, updated_at) "
        "VALUES (1, 'Server', 'server', ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO account_contexts (id, server_context_id, name, normalized_name, "
        "created_at, updated_at) VALUES (1, 1, 'Account', 'account', ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO characters (id, name, series, normalized_name, normalized_series, "
        "created_at, updated_at) VALUES (1, 'Character', 'Series', 'character', 'series', ?, ?)",
        (NOW, NOW),
    )
    import_id = int(
        connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) "
            "VALUES (?, 'discord', ?, 'retained')",
            (family, NOW),
        ).lastrowid
    )
    target_id = _insert_target(connection, family, import_id)
    connection.execute(
        "INSERT INTO discord_message_aggregates (id, platform, guild_id, channel_id, "
        "message_id, first_received_at, last_received_at, created_at, updated_at) "
        "VALUES (1, 'discord', 'g', 'c', 'm', ?, ?, ?, ?)",
        (NOW, NOW, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO discord_message_revisions (id, aggregate_id, normalized_payload_hash, "
        "revision_state, first_received_at, last_received_at, created_at, updated_at) "
        "VALUES (1, 1, 'hash', 'active', ?, ?, ?, ?)",
        (NOW, NOW, NOW, NOW),
    )
    source_id = int(
        connection.execute(
            "INSERT INTO discord_source_events (event_key, revision_id, event_kind, status, "
            "raw_text, received_at, last_seen_at, legacy_import_event_id, created_at, updated_at) "
            "VALUES ('event', 1, 'MESSAGE_CREATE', 'succeeded', 'retained', ?, ?, ?, ?, ?)",
            (NOW, NOW, import_id, NOW, NOW),
        ).lastrowid
    )
    connection.execute(
        "INSERT INTO discord_processing_attempts (source_event_id, attempt_number, status, "
        "retryable, parser_version, router_version, started_at, finished_at, created_at) "
        "VALUES (?, 1, 'succeeded', 0, 'p', 'r', ?, ?, ?)",
        (source_id, NOW, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO discord_source_event_server_attributions (source_event_id, status, "
        "server_name, created_at, updated_at) VALUES (?, 'resolved', 'Server', ?, ?)",
        (source_id, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO discord_source_event_account_attributions (source_event_id, status, "
        "server_name, account_name, created_at, updated_at) "
        "VALUES (?, 'resolved', 'Server', 'Account', ?, ?)",
        (source_id, NOW, NOW),
    )
    identity = resolve_expected_projections(
        load_durable_projection_expectation_facts(connection, source_id)
    ).known_expected_identities[0]
    authority = get_projection_authority(identity.projection_kind)
    for generation_id in range(1, historical_generations + 1):
        if generation_id > 1:
            assert ProjectionLinkRepository(connection).switch_current_generation() == generation_id
        connection.execute(
            "INSERT INTO discord_projection_links (source_event_id, generation_id, "
            "projection_kind, projection_slot, projection_table, projection_row_id, state, "
            "claimed_at, completed_at, created_at, updated_at) VALUES "
            "(?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)",
            (
                source_id,
                generation_id,
                identity.projection_kind,
                identity.projection_slot,
                authority.target_table,
                target_id,
                NOW,
                NOW,
                NOW,
                NOW,
            ),
        )
    assert (
        ProjectionLinkRepository(connection).switch_current_generation()
        == historical_generations + 1
    )
    return RetainedSourceReprojectionAdmissionService().admit(connection, source_id)


def _dump(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(connection.iterdump())


def _execute(connection, admission):
    return RetainedSourceSingletonReprojectionExecutor().execute(
        connection, admission, COMPLETED_AT
    )


@pytest.mark.parametrize("family", FAMILIES)
def test_all_ten_families_link_only_and_exact_replay(database_path, family) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection, family)
        immutable_tables = (
            "discord_source_events",
            "discord_processing_attempts",
            "discord_source_event_server_attributions",
            "discord_source_event_account_attributions",
            "import_events",
            TABLES[family],
            "projection_generations",
        )
        protected = {
            table: tuple(connection.execute(f"SELECT * FROM {table}").fetchall())
            for table in immutable_tables
        }
        result = _execute(connection, admission)
        assert result.source_family == family
        assert result.linked_count == 1 and result.replay_skipped is False
        assert result.projection_target == (
            TABLES[family],
            admission.payloads[0].historical_target_id,
        )
        for table, rows in protected.items():
            assert tuple(connection.execute(f"SELECT * FROM {table}").fetchall()) == rows
        before = _dump(connection)
        replay = _execute(connection, admission)
        assert replay.linked_count == 0 and replay.replay_skipped is True
        assert _dump(connection) == before


@pytest.mark.parametrize("family", FAMILIES)
def test_rejects_admission_drift_payload_and_presence_corruption(database_path, family) -> None:
    with connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection, family)
        forged = replace(admission, historical_generation_id=999)
        before = _dump(connection)
        with pytest.raises(RetainedSourceSingletonReprojectionError):
            _execute(connection, forged)
        assert _dump(connection) == before

        table = TABLES[family]
        if family == "tower_state":
            connection.execute(f"UPDATE {table} SET completed_towers_observed = 0")
        elif family == "kakeraloot_state":
            connection.execute(f"UPDATE {table} SET kakera_balance_observed = 1")
        elif family == "profile":
            connection.execute(f"UPDATE {table} SET pokedex_observed = 1")
        elif family in {"kakera_state", "mudapins", "player_bonus", "wishlist", "disablelist"}:
            json_column = {
                "kakera_state": "badges_json",
                "mudapins": "pin_markers_json",
                "player_bonus": "metrics_json",
                "wishlist": "entries_json",
                "disablelist": "entries_json",
            }[family]
            connection.execute(f"UPDATE {table} SET {json_column} = 'not-json'")
        elif family == "sphere_result":
            connection.execute(f"UPDATE {table} SET total_gained = 1")
        else:
            connection.execute(f"UPDATE {table} SET import_event_id = 999")
        corrupted = _dump(connection)
        with pytest.raises(RetainedSourceSingletonReprojectionError):
            _execute(connection, admission)
        assert _dump(connection) == corrupted


@pytest.mark.parametrize("family", FAMILIES)
def test_rejects_current_conflicts_and_additional_links(database_path, family) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection, family)
        connection.execute(
            "INSERT INTO discord_projection_links (source_event_id, generation_id, "
            "projection_kind, projection_slot, state, claimed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'claimed', ?, ?, ?)",
            (
                admission.source_event_id,
                admission.current_generation_id,
                admission.expected_identities[0].projection_kind,
                admission.expected_identities[0].projection_slot,
                NOW,
                NOW,
                NOW,
            ),
        )
        before = _dump(connection)
        with pytest.raises(RetainedSourceSingletonReprojectionError):
            _execute(connection, admission)
        assert _dump(connection) == before


def test_replay_revalidates_every_historical_generation(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection, "wishlist", historical_generations=3)
        _execute(connection, admission)
        connection.execute(
            "UPDATE discord_projection_links SET projection_row_id = 999 "
            "WHERE source_event_id = ? AND generation_id = 1",
            (admission.source_event_id,),
        )
        before = _dump(connection)
        with pytest.raises(RetainedSourceSingletonReprojectionError, match="historical evidence"):
            _execute(connection, admission)
        assert _dump(connection) == before


def test_completion_failure_rolls_back_only_new_current_link(database_path, monkeypatch) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection, "wishlist")
        before = _dump(connection)

        def fail_completion(*args, **kwargs):
            raise RuntimeError("completion failed")

        monkeypatch.setattr(ProjectionLinkRepository, "complete_claimed_link", fail_completion)
        with pytest.raises(RuntimeError, match="completion failed"):
            _execute(connection, admission)
        assert _dump(connection) == before
        assert connection.in_transaction


@pytest.mark.parametrize(
    "family",
    ("timer_state", "server_settings", "kakeraloot_settings", "roll", "antidisable"),
)
def test_excludes_non_cohort_admissions(database_path, family) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        # Shape rejection happens before any durable access for an otherwise valid frozen admission.
        admitted = _fixture(connection, "wishlist")
        forged = replace(admitted, source_family=family)
        before = _dump(connection)
        with pytest.raises(RetainedSourceSingletonReprojectionError, match="outside"):
            _execute(connection, forged)
        assert _dump(connection) == before
