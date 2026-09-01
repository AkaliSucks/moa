from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError

import pytest

from moa.database.sqlite import connect
from moa.models.character import TimerStateSnapshot
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.projection_link_repository import ProjectionLinkRepository
from moa.services.projection_authority import get_projection_authority
from moa.services.projection_expectations import (
    antidisable_page_projection_slot,
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
)
from moa.services.retained_source_reprojection_admission_service import (
    ReprojectionAdmissionRejection,
    RetainedSourceReprojectionAdmissionError,
    RetainedSourceReprojectionAdmissionService,
)


NOW = "2026-08-31T12:00:00+00:00"
ACCOUNT_FAMILIES = {
    "claim",
    "kakera_state",
    "mudapins",
    "player_bonus",
    "wishlist",
    "disablelist",
    "timer_state",
    "tower_state",
    "kakeraloot_state",
    "sphere_result",
    "profile",
    "roll",
    "antidisable",
}


def _insert_context(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO server_contexts (id, name, normalized_name, created_at, updated_at) VALUES (1, 'Server', 'server', ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO account_contexts (id, server_context_id, name, normalized_name, created_at, updated_at) VALUES (1, 1, 'Account', 'account', ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO characters (id, name, series, normalized_name, normalized_series, created_at, updated_at) VALUES (1, 'Character', 'Series', 'character', 'series', ?, ?)",
        (NOW, NOW),
    )


def _insert_target(connection: sqlite3.Connection, family: str, import_id: int) -> dict[str, int]:
    common = (NOW, import_id)
    if family == "timer_state":
        snapshot = TimerStateSnapshot(**{name: None for name in TimerStateSnapshot.model_fields})
        target_id = int(
            connection.execute(
                "INSERT INTO timer_state_observations (account_context_id, snapshot_json, observed_at, import_event_id) VALUES (1, ?, ?, ?)",
                (snapshot.model_dump_json(), NOW, import_id),
            ).lastrowid
        )
        return {"catalog.timer_state": target_id}
    statements: dict[str, tuple[str, tuple[object, ...]]] = {
        "claim": (
            "INSERT INTO claim_observations (account_context_id, character_id, character_name, normalized_character_name, observed_at, import_event_id) VALUES (1, 1, 'Character', 'character', ?, ?)",
            common,
        ),
        "server_settings": (
            "INSERT INTO server_settings_observations (server_context_id, server_premium, prefix, language, claim_reset_minutes, reset_minute, reset_shift_minutes, rolls_per_hour, claim_reaction_expiry_seconds, claimed_character_rarity_multiplier, kakera_bonus_percent, sphere_bonus_percent, game_mode, channel_instance, metrics_json, observed_at, import_event_id) VALUES (1, 1, '$', 'en', 180, '00', 0, 10, 30, 1, 0, 0, 1, 1, '[]', ?, ?)",
            common,
        ),
        "kakeraloot_settings": (
            "INSERT INTO kakeraloot_settings_observations (server_context_id, loot_cost, quantity_quality_base_cost, quantity_quality_level_increment, observed_at, import_event_id) VALUES (1, 100, 200, 20, ?, ?)",
            common,
        ),
        "kakera_state": (
            "INSERT INTO kakera_state_observations (account_context_id, kakera_balance, badges_json, observed_at, import_event_id) VALUES (1, 100, '[]', ?, ?)",
            common,
        ),
        "mudapins": (
            "INSERT INTO mudapin_observations (account_context_id, pin_markers_json, pin_count, observed_at, import_event_id) VALUES (1, '[\"pin\"]', 1, ?, ?)",
            common,
        ),
        "player_bonus": (
            "INSERT INTO player_bonus_observations (account_context_id, metrics_json, observed_at, import_event_id) VALUES (1, '[]', ?, ?)",
            common,
        ),
        "wishlist": (
            "INSERT INTO wishlist_observations (account_context_id, wishlist_count, wishlist_capacity, starwish_count, starwish_capacity, entries_json, observed_at, import_event_id) VALUES (1, 0, 10, 0, 1, '[]', ?, ?)",
            common,
        ),
        "disablelist": (
            "INSERT INTO disablelist_observations (account_context_id, slots_used, slots_capacity, total_disabled, disabled_wa, disabled_ha, disabled_wg, disabled_hg, wa_pool_limit, ha_pool_limit, western_disabled, irl_disabled, western_disabled_observed, irl_disabled_observed, entries_json, observed_at, import_event_id) VALUES (1, 0, 10, 0, 0, 0, 0, 0, NULL, NULL, 0, 0, 1, 1, '[]', ?, ?)",
            common,
        ),
        "tower_state": (
            "INSERT INTO tower_state_observations (account_context_id, current_level, completed_towers, next_level_cost, kakera_balance, built_perk_ids_json, observed_at, import_event_id, completed_towers_observed) VALUES (1, 1, 0, 1000, 500, '[]', ?, ?, 1)",
            common,
        ),
        "sphere_result": (
            'INSERT INTO sphere_result_observations (account_context_id, snapshot_json, total_gained, stock, observed_at, import_event_id) VALUES (1, \'{"clicks_available":null,"click_window_minutes":null,"purple_target":null,"purple_total":null,"gains":[],"total_gained":0,"stock":null}\', 0, NULL, ?, ?)',
            common,
        ),
        "profile": (
            "INSERT INTO profile_observations (account_context_id, profile_name, collection_size, female_percent, male_percent, pokedex_count, pokedex_json, kakera_reacts_json, mudapins_collected, mudapins_total, kakera_balance, bronze_keys, silver_keys, gold_keys, sphere_stock, spheres_json, displayed_badges_json, observed_at, import_event_id, pokedex_observed, reactions_observed, mudapins_observed, kakera_balance_observed, keys_observed, bronze_keys_observed, silver_keys_observed, gold_keys_observed, sphere_stock_observed, sphere_counts_observed, badges_observed) VALUES (1, 'Account', 0, 0, 0, NULL, '[]', '{}', NULL, NULL, NULL, 0, 0, 0, NULL, '{}', '[]', ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)",
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
        values = ", ".join("0" for _ in fields)
        connection.execute(
            f"INSERT INTO kakeraloot_state_observations (account_context_id, has_kakeraloots, status_note, {columns}, observed_at, import_event_id, {presence}) VALUES (1, 0, 'none', {values}, ?, ?, {values})",
            common,
        )
        return {
            "catalog.kakeraloot_state": int(
                connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            )
        }
    if family == "roll":
        targets = {}
        targets["catalog.roll"] = int(
            connection.execute(
                "INSERT INTO roll_observations (account_context_id, character_id, claim_rank, kakera_value, observed_at, import_event_id) VALUES (1, 1, 1, 100, ?, ?)",
                common,
            ).lastrowid
        )
        targets["catalog.roll_key"] = int(
            connection.execute(
                "INSERT INTO harem_key_observations (account_context_id, character_id, character_name, normalized_character_name, key_type, key_count, kakera_value, observed_at, import_event_id) VALUES (1, 1, 'Character', 'character', 'bronze', 1, 100, ?, ?)",
                common,
            ).lastrowid
        )
        targets["catalog.roll_rank"] = int(
            connection.execute(
                "INSERT INTO rank_snapshots (character_id, claim_rank, like_rank, owner_name, observed_at, import_event_id) VALUES (1, 1, NULL, NULL, ?, ?)",
                common,
            ).lastrowid
        )
        targets["catalog.roll_server_character"] = int(
            connection.execute(
                "INSERT INTO server_character_observations (server_context_id, character_id, kakera_value, observed_at, import_event_id) VALUES (1, 1, 100, ?, ?)",
                common,
            ).lastrowid
        )
        return targets
    sql, parameters = statements[family]
    target_id = int(connection.execute(sql, parameters).lastrowid)
    kind = {
        "server_settings": "catalog.server_settings",
        "kakeraloot_settings": "catalog.kakeraloot_settings",
        "kakera_state": "catalog.kakera_state",
        "mudapins": "catalog.mudapins",
        "player_bonus": "catalog.player_bonus",
        "wishlist": "catalog.wishlist",
        "disablelist": "catalog.disablelist",
        "timer_state": "catalog.timer_state",
        "tower_state": "catalog.tower_state",
        "sphere_result": "catalog.sphere_result",
        "profile": "catalog.profile",
        "claim": "catalog.claim",
    }[family]
    return {kind: target_id}


def _fixture(connection: sqlite3.Connection, family: str = "wishlist") -> int:
    _insert_context(connection)
    import_id = int(
        connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, 'discord', ?, 'retained')",
            (family, NOW),
        ).lastrowid
    )
    if family == "antidisable":
        connection.execute(
            "INSERT INTO harem_scans (id, account_context_id, expected_page_count, started_at, completed_at, scan_kind) VALUES (1, 1, 2, ?, NULL, 'antidisable')",
            (NOW,),
        )
        connection.execute(
            "INSERT INTO harem_scan_pages (harem_scan_id, page_number, import_event_id, slots_used, slots_capacity) VALUES (1, 1, ?, 2, 10)",
            (import_id,),
        )
        connection.executemany(
            "INSERT INTO antidisable_series_observations (account_context_id, series_name, normalized_series_name, antidisabled_character_count, observed_at, import_event_id, harem_scan_id) VALUES (1, ?, ?, 4, ?, ?, 1)",
            (
                ("Series", "series", NOW, import_id),
                ("Series", "series", NOW, import_id),
            ),
        )
        targets = {"catalog.antidisable_page": import_id}
    else:
        targets = _insert_target(connection, family, import_id)
    connection.execute(
        "INSERT INTO discord_message_aggregates (id, platform, guild_id, channel_id, message_id, first_received_at, last_received_at, created_at, updated_at) VALUES (1, 'discord', 'g', 'c', 'm', ?, ?, ?, ?)",
        (NOW, NOW, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO discord_message_revisions (id, aggregate_id, normalized_payload_hash, revision_state, first_received_at, last_received_at, created_at, updated_at) VALUES (1, 1, 'hash', 'active', ?, ?, ?, ?)",
        (NOW, NOW, NOW, NOW),
    )
    source_id = int(
        connection.execute(
            "INSERT INTO discord_source_events (event_key, revision_id, event_kind, status, raw_text, received_at, last_seen_at, legacy_import_event_id, created_at, updated_at) VALUES ('event', 1, 'MESSAGE_CREATE', 'succeeded', 'retained', ?, ?, ?, ?, ?)",
            (NOW, NOW, import_id, NOW, NOW),
        ).lastrowid
    )
    connection.execute(
        "INSERT INTO discord_processing_attempts (source_event_id, attempt_number, status, retryable, parser_version, router_version, started_at, finished_at, created_at) VALUES (?, 1, 'succeeded', 0, 'p', 'r', ?, ?, ?)",
        (source_id, NOW, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO discord_source_event_server_attributions (source_event_id, status, server_name, created_at, updated_at) VALUES (?, 'resolved', 'Server', ?, ?)",
        (source_id, NOW, NOW),
    )
    if family in ACCOUNT_FAMILIES:
        connection.execute(
            "INSERT INTO discord_source_event_account_attributions (source_event_id, status, server_name, account_name, created_at, updated_at) VALUES (?, 'resolved', 'Server', 'Account', ?, ?)",
            (source_id, NOW, NOW),
        )
    expected = resolve_expected_projections(
        load_durable_projection_expectation_facts(connection, source_id)
    )
    for identity in expected.known_expected_identities:
        authority = get_projection_authority(identity.projection_kind)
        target_id = targets[identity.projection_kind]
        connection.execute(
            "INSERT INTO discord_projection_links (source_event_id, generation_id, projection_kind, projection_slot, projection_table, projection_row_id, state, claimed_at, completed_at, created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)",
            (
                source_id,
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
    assert ProjectionLinkRepository(connection).switch_current_generation() == 2
    return source_id


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "admission.sqlite3"
    CatalogRepository(path)
    return path


@pytest.mark.parametrize(
    "family",
    sorted(ACCOUNT_FAMILIES - {"antidisable"}) + ["server_settings", "kakeraloot_settings"],
)
def test_admits_all_fourteen_reconstructible_families(database_path, family):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection, family)
        before = connection.total_changes
        admitted = RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert admitted.source_family == family
        assert admitted.current_generation_id == 2
        assert admitted.historical_generation_id == 1
        assert admitted.payloads
        assert connection.total_changes == before
        with pytest.raises(FrozenInstanceError):
            admitted.source_family = "changed"  # type: ignore[misc]
        connection.rollback()


def test_hypothetical_next_generation_uses_current_links_as_read_only_history(database_path):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection, "wishlist")
        connection.execute(
            "UPDATE discord_projection_links SET generation_id = 2 WHERE source_event_id = ?",
            (source_id,),
        )
        before = connection.total_changes
        admitted = RetainedSourceReprojectionAdmissionService().admit(
            connection, source_id, hypothetical_generation_id=3
        )
        assert admitted.current_generation_id == 3
        assert admitted.historical_generation_id == 2
        assert len(admitted.expected_identities) == 1
        assert connection.total_changes == before
        connection.rollback()


@pytest.mark.parametrize("hypothetical_generation_id", [False, 0, 2, 4])
def test_hypothetical_generation_must_be_unpersisted_exact_next(
    database_path, hypothetical_generation_id
):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection, "wishlist")
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(
                connection,
                source_id,
                hypothetical_generation_id=hypothetical_generation_id,
            )
        assert caught.value.reason in {
            ReprojectionAdmissionRejection.INVALID_REQUEST,
            ReprojectionAdmissionRejection.CURRENT_GENERATION_INVALID,
        }
        connection.rollback()


def test_requires_caller_owned_transaction_and_never_writes(database_path):
    with connect(database_path) as connection:
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, 1)
        assert caught.value.reason is ReprojectionAdmissionRejection.TRANSACTION_REQUIRED


def test_current_complete_links_are_idempotent_and_partial_links_conflict(database_path):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection)
        connection.execute("UPDATE projection_generations SET is_current = 0 WHERE id = 2")
        connection.execute("UPDATE projection_generations SET is_current = 1 WHERE id = 1")
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert caught.value.reason is ReprojectionAdmissionRejection.CURRENT_LINKS_ALREADY_COMPLETE
        connection.execute(
            "UPDATE discord_projection_links SET state = 'claimed', projection_table = NULL, projection_row_id = NULL, completed_at = NULL WHERE source_event_id = ?",
            (source_id,),
        )
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert caught.value.reason is ReprojectionAdmissionRejection.CURRENT_LINKS_CONFLICT
        connection.rollback()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            "UPDATE discord_source_events SET raw_evidence_expired_at = ?",
            ReprojectionAdmissionRejection.SOURCE_EXPIRED,
        ),
        (
            "UPDATE discord_source_event_account_attributions SET status = 'unresolved', server_name = NULL, account_name = NULL",
            ReprojectionAdmissionRejection.ATTRIBUTION_UNRESOLVED,
        ),
        (
            "UPDATE discord_processing_attempts SET finished_at = NULL",
            ReprojectionAdmissionRejection.ATTEMPT_INCOHERENT,
        ),
        (
            "UPDATE discord_projection_links SET projection_slot = '{}'",
            ReprojectionAdmissionRejection.HISTORICAL_LINKS_INCOHERENT,
        ),
        (
            "UPDATE wishlist_observations SET entries_json = 'not-json'",
            ReprojectionAdmissionRejection.PAYLOAD_INCOMPLETE,
        ),
    ],
)
def test_rejects_expired_unresolved_partial_conflicting_and_malformed_facts(
    database_path, mutation, reason
):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection)
        parameters = (NOW,) if mutation.endswith("= ?") else ()
        connection.execute(mutation + " WHERE 1 = 1", parameters)
        before = connection.total_changes
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert caught.value.reason is reason
        assert connection.total_changes == before
        connection.rollback()


@pytest.mark.parametrize("terminal_status", ["failed", "unresolved_attribution"])
def test_rejects_malformed_finished_non_successful_attempt(database_path, terminal_status):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection)
        connection.execute(
            "UPDATE discord_processing_attempts SET attempt_number = 2 WHERE source_event_id = ?",
            (source_id,),
        )
        connection.execute(
            "INSERT INTO discord_processing_attempts (source_event_id, attempt_number, status, retryable, parser_version, router_version, started_at, finished_at, created_at) VALUES (?, 1, ?, 1, 'p', 'r', ?, 'not-a-timestamp', ?)",
            (source_id, terminal_status, NOW, NOW),
        )
        before = connection.total_changes
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert caught.value.reason is ReprojectionAdmissionRejection.ATTEMPT_INCOHERENT
        assert connection.total_changes == before
        connection.rollback()


def test_rejects_timezone_mismatch_on_failed_attempt_before_unresolved_attribution(
    database_path,
):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection)
        connection.execute(
            "UPDATE discord_processing_attempts SET attempt_number = 2 WHERE source_event_id = ?",
            (source_id,),
        )
        connection.execute(
            "INSERT INTO discord_processing_attempts (source_event_id, attempt_number, status, retryable, parser_version, router_version, started_at, finished_at, created_at) VALUES (?, 1, 'failed', 1, 'p', 'r', ?, ?, ?)",
            (source_id, NOW, "2026-08-31T12:01:00", NOW),
        )
        connection.execute(
            "UPDATE discord_source_event_account_attributions SET status = 'unresolved', server_name = NULL, account_name = NULL WHERE source_event_id = ?",
            (source_id,),
        )
        before = connection.total_changes
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert caught.value.reason is ReprojectionAdmissionRejection.ATTEMPT_INCOHERENT
        assert connection.total_changes == before
        connection.rollback()


def test_rejects_unknown_roll_assessment(database_path):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        roll_id = _fixture(connection, "roll")
        connection.execute(
            "DELETE FROM discord_projection_links WHERE source_event_id = ?", (roll_id,)
        )
        connection.execute("DELETE FROM harem_key_observations")
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, roll_id)
        assert caught.value.reason is ReprojectionAdmissionRejection.EXPECTATIONS_UNKNOWN
        connection.rollback()


@pytest.mark.parametrize(("slots_used", "slots_capacity"), [(2, 10), (0, 0)])
def test_admits_prospective_antidisable_pages_without_writes(
    database_path, slots_used, slots_capacity
):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection, "antidisable")
        connection.execute(
            "UPDATE harem_scan_pages SET slots_used = ?, slots_capacity = ?",
            (slots_used, slots_capacity),
        )
        before = connection.total_changes
        admitted = RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        fields = dict(admitted.payloads[0].fields)
        assert admitted.expected_identities[0].projection_slot == antidisable_page_projection_slot(
            "server", "account", 1, 1
        )
        assert admitted.payloads[0].target_table == "import_events"
        assert admitted.payloads[0].historical_target_id == admitted.import_event_id
        assert fields["slots_used"] == slots_used
        assert fields["slots_capacity"] == slots_capacity
        assert fields["series"] == (
            (
                ("antidisabled_character_count", 4),
                ("normalized_series_name", "series"),
                ("series_name", "Series"),
            ),
            (
                ("antidisabled_character_count", 4),
                ("normalized_series_name", "series"),
                ("series_name", "Series"),
            ),
        )
        assert connection.total_changes == before
        connection.rollback()


@pytest.mark.parametrize(
    ("slots_used", "slots_capacity"),
    [(None, None), (None, 10), (2, None), ("bad", 10), (-1, 10), (11, 10)],
)
def test_rejects_legacy_partial_and_invalid_antidisable_slot_evidence(
    database_path, slots_used, slots_capacity
):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection, "antidisable")
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE harem_scan_pages SET slots_used = ?, slots_capacity = ?",
            (slots_used, slots_capacity),
        )
        before = connection.total_changes
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert caught.value.reason is ReprojectionAdmissionRejection.PAYLOAD_INCOMPLETE
        assert connection.total_changes == before
        connection.rollback()


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE harem_scans SET scan_kind = 'keys'",
        "UPDATE harem_scans SET expected_page_count = 0",
        "UPDATE harem_scan_pages SET page_number = 3",
        "UPDATE harem_scan_pages SET harem_scan_id = 2",
        "UPDATE antidisable_series_observations SET account_context_id = 2 WHERE id = (SELECT MIN(id) FROM antidisable_series_observations)",
        "UPDATE antidisable_series_observations SET normalized_series_name = 'wrong' WHERE id = (SELECT MIN(id) FROM antidisable_series_observations)",
        "UPDATE antidisable_series_observations SET antidisabled_character_count = NULL WHERE id = (SELECT MIN(id) FROM antidisable_series_observations)",
        "UPDATE antidisable_series_observations SET harem_scan_id = 2 WHERE id = (SELECT MIN(id) FROM antidisable_series_observations)",
    ],
)
def test_rejects_antidisable_scan_page_context_and_series_corruption(database_path, mutation):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection, "antidisable")
        connection.execute(
            "INSERT INTO account_contexts (id, server_context_id, name, normalized_name, created_at, updated_at) VALUES (2, 1, 'Other', 'other', ?, ?)",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO harem_scans (id, account_context_id, expected_page_count, started_at, completed_at, scan_kind) VALUES (2, 2, 2, ?, NULL, 'antidisable')",
            (NOW,),
        )
        connection.execute(mutation)
        before = connection.total_changes
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert caught.value.reason in {
            ReprojectionAdmissionRejection.EXPECTATIONS_UNKNOWN,
            ReprojectionAdmissionRejection.HISTORICAL_LINKS_INCOHERENT,
            ReprojectionAdmissionRejection.PAYLOAD_INCOMPLETE,
        }
        assert connection.total_changes == before
        connection.rollback()


def test_admits_antidisable_continuation_page_with_nullable_character_count(database_path):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection, "antidisable")
        connection.execute("UPDATE harem_scan_pages SET page_number = 2")
        connection.execute(
            "UPDATE antidisable_series_observations SET antidisabled_character_count = NULL"
        )
        connection.execute(
            "UPDATE discord_projection_links SET projection_slot = ?",
            (antidisable_page_projection_slot("server", "account", 1, 2),),
        )
        before = connection.total_changes
        admitted = RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        series = dict(admitted.payloads[0].fields)["series"]
        assert all(dict(item)["antidisabled_character_count"] is None for item in series)
        assert connection.total_changes == before
        connection.rollback()


def test_rejects_antidisable_import_owned_by_multiple_page_rows(database_path):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection, "antidisable")
        import_id = connection.execute(
            "SELECT legacy_import_event_id FROM discord_source_events WHERE id = ?", (source_id,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO harem_scan_pages (harem_scan_id, page_number, import_event_id, slots_used, slots_capacity) VALUES (1, 2, ?, 2, 10)",
            (import_id,),
        )
        before = connection.total_changes
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert caught.value.reason in {
            ReprojectionAdmissionRejection.EXPECTATIONS_UNKNOWN,
            ReprojectionAdmissionRejection.PROVENANCE_INCOHERENT,
            ReprojectionAdmissionRejection.PAYLOAD_INCOMPLETE,
        }
        assert connection.total_changes == before
        connection.rollback()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            "UPDATE discord_source_events SET raw_evidence_expired_at = '2026-09-01T00:00:00+00:00'",
            ReprojectionAdmissionRejection.SOURCE_EXPIRED,
        ),
        (
            "UPDATE import_events SET raw_message_expired_at = '2026-09-01T00:00:00+00:00'",
            ReprojectionAdmissionRejection.SOURCE_EXPIRED,
        ),
        (
            "UPDATE discord_processing_attempts SET retryable = 1",
            ReprojectionAdmissionRejection.ATTEMPT_INCOHERENT,
        ),
        (
            "UPDATE discord_source_events SET legacy_import_event_id = NULL",
            ReprojectionAdmissionRejection.PROVENANCE_INCOHERENT,
        ),
        (
            "UPDATE discord_source_event_account_attributions SET status = 'unresolved', server_name = NULL, account_name = NULL",
            ReprojectionAdmissionRejection.ATTRIBUTION_UNRESOLVED,
        ),
        (
            "DELETE FROM harem_scan_pages",
            ReprojectionAdmissionRejection.EXPECTATIONS_UNKNOWN,
        ),
        (
            "UPDATE discord_projection_links SET projection_table = 'wishlist_observations'",
            ReprojectionAdmissionRejection.HISTORICAL_LINKS_INCOHERENT,
        ),
        (
            "UPDATE discord_projection_links SET projection_row_id = projection_row_id + 1",
            ReprojectionAdmissionRejection.PAYLOAD_INCOMPLETE,
        ),
    ],
)
def test_rejects_antidisable_lifecycle_authority_and_expectedness_failures(
    database_path, mutation, reason
):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection, "antidisable")
        connection.execute(mutation)
        before = connection.total_changes
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert caught.value.reason is reason
        assert connection.total_changes == before
        connection.rollback()


def test_antidisable_current_and_historical_link_divergence_fail_closed(database_path):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection, "antidisable")
        historical = connection.execute(
            "SELECT projection_kind, projection_slot, projection_table, projection_row_id "
            "FROM discord_projection_links WHERE source_event_id = ?",
            (source_id,),
        ).fetchone()
        connection.execute(
            "INSERT INTO discord_projection_links (source_event_id, generation_id, projection_kind, projection_slot, projection_table, projection_row_id, state, claimed_at, completed_at, created_at, updated_at) VALUES (?, 2, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)",
            (
                source_id,
                historical["projection_kind"],
                historical["projection_slot"],
                historical["projection_table"],
                historical["projection_row_id"],
                NOW,
                NOW,
                NOW,
                NOW,
            ),
        )
        before = connection.total_changes
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert caught.value.reason is ReprojectionAdmissionRejection.CURRENT_LINKS_ALREADY_COMPLETE
        assert connection.total_changes == before

        connection.execute(
            "UPDATE discord_projection_links SET projection_slot = '{}' WHERE generation_id = 2"
        )
        before = connection.total_changes
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert caught.value.reason is ReprojectionAdmissionRejection.CURRENT_LINKS_CONFLICT
        assert connection.total_changes == before

        connection.execute("DELETE FROM discord_projection_links WHERE generation_id = 2")
        assert ProjectionLinkRepository(connection).switch_current_generation() == 3
        connection.execute(
            "INSERT INTO discord_projection_links (source_event_id, generation_id, projection_kind, projection_slot, projection_table, projection_row_id, state, claimed_at, completed_at, created_at, updated_at) VALUES (?, 2, ?, '{}', ?, ?, 'completed', ?, ?, ?, ?)",
            (
                source_id,
                historical["projection_kind"],
                historical["projection_table"],
                historical["projection_row_id"],
                NOW,
                NOW,
                NOW,
                NOW,
            ),
        )
        before = connection.total_changes
        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert caught.value.reason is ReprojectionAdmissionRejection.HISTORICAL_LINKS_INCOHERENT
        assert connection.total_changes == before
        connection.rollback()


def test_rejection_preserves_source_attempt_provenance_history_and_rolls_back(database_path):
    with connect(database_path) as connection:
        durable_before = tuple(connection.iterdump())
        connection.execute("BEGIN")
        source_id = _fixture(connection)
        connection.execute("UPDATE wishlist_observations SET entries_json = 'bad'")
        changed = tuple(connection.iterdump())
        with pytest.raises(RetainedSourceReprojectionAdmissionError):
            RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
        assert tuple(connection.iterdump()) == changed
        connection.rollback()
        assert tuple(connection.iterdump()) == durable_before
