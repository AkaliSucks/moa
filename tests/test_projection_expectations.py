from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from moa.services.projection_authority import PROJECTION_AUTHORITY_BY_KIND
from moa.services.projection_expectations import (
    PROJECTION_EXPECTATION_POLICIES,
    ExpectedProjectionIdentity,
    Expectedness,
    build_projection_expectation_facts,
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
)
from moa.database.sqlite import connect
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository


EXPECTED_FAMILY_KINDS = {
    "antidisable": ("catalog.antidisable_page",),
    "claim": ("catalog.claim",),
    "disablelist": ("catalog.disablelist",),
    "kakeraloot_settings": ("catalog.kakeraloot_settings",),
    "kakeraloot_state": ("catalog.kakeraloot_state",),
    "kakera_state": ("catalog.kakera_state",),
    "mudapins": ("catalog.mudapins",),
    "player_bonus": ("catalog.player_bonus",),
    "profile": ("catalog.profile",),
    "roll": (
        "catalog.roll",
        "catalog.roll_key",
        "catalog.roll_rank",
        "catalog.roll_server_character",
    ),
    "server_settings": ("catalog.server_settings",),
    "sphere_result": ("catalog.sphere_result",),
    "timer_state": ("catalog.timer_state",),
    "top_page": ("catalog.top_page",),
    "topx_page": ("catalog.topx_page",),
    "tower_state": ("catalog.tower_state",),
    "wishlist": ("catalog.wishlist",),
}


def test_policy_registry_has_exact_seventeen_family_twenty_kind_inventory() -> None:
    assert {
        family: policy.possible_projection_kinds
        for family, policy in PROJECTION_EXPECTATION_POLICIES.items()
    } == EXPECTED_FAMILY_KINDS
    owned_kind_sequence = tuple(
        kind
        for policy in PROJECTION_EXPECTATION_POLICIES.values()
        for kind in policy.possible_projection_kinds
    )
    assert len(PROJECTION_EXPECTATION_POLICIES) == 17
    assert len(owned_kind_sequence) == len(set(owned_kind_sequence)) == 20
    assert set(owned_kind_sequence) == set(PROJECTION_AUTHORITY_BY_KIND)
    with pytest.raises(TypeError):
        PROJECTION_EXPECTATION_POLICIES["other"] = next(
            iter(PROJECTION_EXPECTATION_POLICIES.values())
        )


@pytest.mark.parametrize(
    ("family", "server", "account", "kind", "slot"),
    (
        (
            "server_settings",
            " Server ",
            None,
            "catalog.server_settings",
            '{"server":"server"}',
        ),
        (
            "kakeraloot_settings",
            " Server ",
            None,
            "catalog.kakeraloot_settings",
            '{"server":"server"}',
        ),
        (
            "profile",
            " Server ",
            " Account ",
            "catalog.profile",
            '{"account":"account","server":"server"}',
        ),
        (
            "disablelist",
            " Server ",
            " Account ",
            "catalog.disablelist",
            '{"account":"account","server":"server"}',
        ),
        (
            "top_page",
            " Server ",
            None,
            "catalog.top_page",
            '{"server":"server"}',
        ),
        (
            "topx_page",
            " Server ",
            " Account ",
            "catalog.topx_page",
            '{"account":"account","server":"server"}',
        ),
    ),
)
def test_singleton_policies_preserve_exact_normalized_slots(
    family: str,
    server: str,
    account: str | None,
    kind: str,
    slot: str,
) -> None:
    facts = build_projection_expectation_facts(
        family, server=server, account=account
    )
    assert resolve_expected_projections(facts).known_expected_identities == (
        ExpectedProjectionIdentity(kind, slot),
    )


def test_claim_and_antidisable_slots_preserve_historical_field_names_and_types() -> None:
    claim = resolve_expected_projections(
        build_projection_expectation_facts(
            "claim",
            server=" Server ",
            account=" Account ",
            character=" Character ",
        )
    )
    assert claim.known_expected_identities == (
        ExpectedProjectionIdentity(
            "catalog.claim",
            '{"account":"account","character_name":"character","server":"server"}',
        ),
    )
    antidisable = resolve_expected_projections(
        build_projection_expectation_facts(
            "antidisable",
            server=" Server ",
            account=" Account ",
            scan_id=12,
            page_number=3,
        )
    )
    assert antidisable.known_expected_identities == (
        ExpectedProjectionIdentity(
            "catalog.antidisable_page",
            '{"account":"account","page_number":3,"scan_id":12,"server":"server"}',
        ),
    )


@pytest.mark.parametrize(
    (
        "key_count_present",
        "key_type",
        "rank_present",
        "kakera_present",
        "expected_kinds",
    ),
    (
        (True, " GOLD ", True, True, {"catalog.roll", "catalog.roll_key", "catalog.roll_rank", "catalog.roll_server_character"}),
        (False, " GOLD ", False, False, {"catalog.roll"}),
        (True, None, False, False, {"catalog.roll"}),
        (False, None, True, False, {"catalog.roll", "catalog.roll_rank"}),
    ),
)
def test_roll_first_processing_presence_policy_and_slot_parity(
    key_count_present: bool,
    key_type: str | None,
    rank_present: bool,
    kakera_present: bool,
    expected_kinds: set[str],
) -> None:
    resolved = resolve_expected_projections(
        build_projection_expectation_facts(
            "roll",
            server=" Server ",
            account=" Account ",
            character=" Character ",
            series=" Series ",
            roll_key_count_present=key_count_present,
            roll_key_type=key_type,
            roll_rank_present=rank_present,
            roll_kakera_value_present=kakera_present,
        )
    )
    assert {identity.projection_kind for identity in resolved.known_expected_identities} == expected_kinds
    assert resolved.known_expected_identities[0].projection_slot == (
        '{"account":"account","character":"character",'
        '"series":"series","server":"server"}'
    )
    if "catalog.roll_key" in expected_kinds:
        key = next(
            identity
            for identity in resolved.known_expected_identities
            if identity.projection_kind == "catalog.roll_key"
        )
        assert key.projection_slot == (
            '{"account":"account","character":"character",'
            '"key_type":"gold","series":"series","server":"server"}'
        )


def test_expected_set_extra_identity_and_partial_unknown_semantics() -> None:
    resolved = resolve_expected_projections(
        build_projection_expectation_facts(
            "roll",
            server="server",
            account="account",
            character="character",
            series="series",
            roll_key_present=None,
            roll_rank_present=True,
            roll_kakera_value_present=False,
        )
    )
    base = resolved.known_expected_identities[0]
    assert resolved.expectedness_for(base) is Expectedness.EXPECTED
    assert resolved.expectedness_for(
        ExpectedProjectionIdentity("catalog.roll", "wrong")
    ) is Expectedness.NOT_EXPECTED
    assert resolved.expectedness_for(
        ExpectedProjectionIdentity("catalog.roll_key", "unknown")
    ) is Expectedness.UNKNOWN
    assert resolved.expectedness_for(
        ExpectedProjectionIdentity("catalog.roll_server_character", "anything")
    ) is Expectedness.NOT_EXPECTED
    assert resolved.expectedness_for(
        ExpectedProjectionIdentity("catalog.profile", "anything")
    ) is Expectedness.NOT_EXPECTED
    with pytest.raises(FrozenInstanceError):
        base.projection_slot = "other"


def test_missing_required_identity_facts_are_explicitly_unknown() -> None:
    resolved = resolve_expected_projections(
        build_projection_expectation_facts("profile", server="server")
    )
    assert resolved.assessments[0].expectedness is Expectedness.UNKNOWN
    assert resolved.known_expected_identities == ()


def _durable_fixture(tmp_path, family: str):
    database_path = tmp_path / f"{family}.db"
    CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    observed_at = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    aggregate = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", f"message-{family}"
    )
    received = discord.receive_message(
        aggregate_key=aggregate,
        revision_key=MessageRevisionKey.versioned(
            aggregate, f"payload-{family}", "revision-1"
        ),
        event_key=f"event-{family}",
        event_kind="message_create",
        raw_text="payload",
        payload_json='{"content":"payload"}',
        payload_capture_version="capture-1",
        source_observed_at=observed_at,
        received_at=observed_at,
    )
    discord.record_server_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Server",
        recorded_at=observed_at,
    )
    discord.record_account_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Server",
        account_name="Account",
        recorded_at=observed_at,
    )
    value = observed_at.isoformat()
    with connect(database_path) as connection:
        import_event_id = int(
            connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) "
                "VALUES (?, 'discord', ?, 'payload')",
                (family, value),
            ).lastrowid
        )
        connection.execute(
            "UPDATE discord_source_events SET status = 'succeeded', "
            "legacy_import_event_id = ? WHERE id = ?",
            (import_event_id, received.source_event_id),
        )
        server_id = int(
            connection.execute(
                "INSERT INTO server_contexts "
                "(name, normalized_name, created_at, updated_at) "
                "VALUES ('Server', 'server', ?, ?)",
                (value, value),
            ).lastrowid
        )
        account_id = int(
            connection.execute(
                "INSERT INTO account_contexts "
                "(server_context_id, name, normalized_name, created_at, updated_at) "
                "VALUES (?, 'Account', 'account', ?, ?)",
                (server_id, value, value),
            ).lastrowid
        )
    return (
        database_path,
        received.source_event_id,
        import_event_id,
        server_id,
        account_id,
        value,
    )


def test_durable_claim_reconstruction_is_link_independent_and_ambiguous_safe(tmp_path) -> None:
    database_path, source_id, import_id, _server_id, account_id, value = _durable_fixture(
        tmp_path, "claim"
    )
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO claim_observations "
            "(account_context_id, character_id, character_name, "
            "normalized_character_name, observed_at, import_event_id) "
            "VALUES (?, NULL, 'Character', 'character', ?, ?)",
            (account_id, value, import_id),
        )
        resolved = resolve_expected_projections(
            load_durable_projection_expectation_facts(connection, source_id)
        )
        assert resolved.known_expected_identities == (
            ExpectedProjectionIdentity(
                "catalog.claim",
                '{"account":"account","character_name":"character","server":"server"}',
            ),
        )
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 0
        connection.execute(
            "INSERT INTO claim_observations "
            "(account_context_id, character_id, character_name, "
            "normalized_character_name, observed_at, import_event_id) "
            "VALUES (?, NULL, 'Other', 'other', ?, ?)",
            (account_id, value, import_id),
        )
        ambiguous = resolve_expected_projections(
            load_durable_projection_expectation_facts(connection, source_id)
        )
        assert ambiguous.assessments[0].expectedness is Expectedness.UNKNOWN


def test_durable_roll_key_absence_and_ambiguity_are_unknown_per_kind(tmp_path) -> None:
    database_path, source_id, import_id, _server_id, account_id, value = _durable_fixture(
        tmp_path, "roll"
    )
    with connect(database_path) as connection:
        character_id = int(
            connection.execute(
                "INSERT INTO characters "
                "(name, series, normalized_name, normalized_series, created_at, updated_at) "
                "VALUES ('Character', 'Series', 'character', 'series', ?, ?)",
                (value, value),
            ).lastrowid
        )
        connection.execute(
            "INSERT INTO roll_observations "
            "(account_context_id, character_id, claim_rank, kakera_value, observed_at, import_event_id) "
            "VALUES (?, ?, 7, NULL, ?, ?)",
            (account_id, character_id, value, import_id),
        )
        absent = resolve_expected_projections(
            load_durable_projection_expectation_facts(connection, source_id)
        )
        by_kind = {assessment.projection_kind: assessment for assessment in absent.assessments}
        assert by_kind["catalog.roll"].expectedness is Expectedness.EXPECTED
        assert by_kind["catalog.roll_key"].expectedness is Expectedness.UNKNOWN
        assert by_kind["catalog.roll_rank"].expectedness is Expectedness.EXPECTED
        assert by_kind["catalog.roll_server_character"].expectedness is Expectedness.NOT_EXPECTED

        def insert_key(key_type: str) -> None:
            connection.execute(
                "INSERT INTO harem_key_observations "
                "(account_context_id, character_id, character_name, "
                "normalized_character_name, key_type, key_count, kakera_value, "
                "observed_at, import_event_id) VALUES (?, ?, 'Character', 'character', ?, 1, NULL, ?, ?)",
                (account_id, character_id, key_type, value, import_id),
            )

        insert_key("Gold")
        unique = resolve_expected_projections(
            load_durable_projection_expectation_facts(connection, source_id)
        )
        assert unique.assessments[1].expectedness is Expectedness.EXPECTED
        assert '"key_type":"gold"' in unique.assessments[1].identity.projection_slot
        insert_key("Silver")
        ambiguous = resolve_expected_projections(
            load_durable_projection_expectation_facts(connection, source_id)
        )
        assert ambiguous.assessments[1].expectedness is Expectedness.UNKNOWN
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 0


def test_durable_antidisable_reconstruction_uses_scan_page_relationships(tmp_path) -> None:
    database_path, source_id, import_id, _server_id, account_id, value = _durable_fixture(
        tmp_path, "antidisable"
    )
    with connect(database_path) as connection:
        scan_id = int(
            connection.execute(
                "INSERT INTO harem_scans "
                "(account_context_id, expected_page_count, started_at, scan_kind) "
                "VALUES (?, 2, ?, 'antidisable')",
                (account_id, value),
            ).lastrowid
        )
        connection.execute(
            "INSERT INTO harem_scan_pages (harem_scan_id, page_number, import_event_id) "
            "VALUES (?, 1, ?)",
            (scan_id, import_id),
        )
        resolved = resolve_expected_projections(
            load_durable_projection_expectation_facts(connection, source_id)
        )
        assert resolved.known_expected_identities == (
            ExpectedProjectionIdentity(
                "catalog.antidisable_page",
                f'{{"account":"account","page_number":1,"scan_id":{scan_id},"server":"server"}}',
            ),
        )
        connection.execute("DELETE FROM harem_scan_pages WHERE import_event_id = ?", (import_id,))
        missing = resolve_expected_projections(
            load_durable_projection_expectation_facts(connection, source_id)
        )
        assert missing.assessments[0].expectedness is Expectedness.UNKNOWN
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 0
