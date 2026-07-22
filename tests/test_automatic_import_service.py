import sqlite3
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from moa.models.character import ProfileSnapshot, RollObservation
from moa.models.catalog import AutomaticImportResult
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.parser.mudae import MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.automatic_import_service import (
    AutomaticImportService,
    DurableProfileImportContext,
    DurableRollImportContext,
)
from moa.services.catalog_service import CatalogService
from moa.services.profile_projection_coordinator import (
    ProfileProjectionCoordinator,
    ProfileProjectionResult,
)
from moa.services.roll_projection_coordinator import (
    RollProjectionCoordinator,
    RollProjectionResult,
)


ROLL_MESSAGE = "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:"
PROFILE_MESSAGE = "moa\nCollection size: 0 (0%:female: 0% :male:)"
OBSERVED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 21, 12, 1, tzinfo=timezone.utc)

PROFILE = ProfileSnapshot(
    profile_name="profile-account",
    collection_size=0,
    female_percent=0,
    male_percent=0,
    pokedex_count=None,
    pokedex_pokemon=(),
    kakera_reacts={},
    mudapins_collected=None,
    mudapins_total=None,
    kakera_balance=None,
    bronze_keys=0,
    silver_keys=0,
    gold_keys=0,
    sphere_stock=None,
    spheres={},
    displayed_badges=(),
)


def _durable_roll_importer(tmp_path):
    database_path = tmp_path / "durable-roll.db"
    catalog_repository = CatalogRepository(database_path)
    discord_repository = DiscordMessageRepository(database_path)
    coordinator = RollProjectionCoordinator(catalog_repository, discord_repository)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", "message"
    )
    received = discord_repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, "payload-hash", "revision-1"
        ),
        event_key="event",
        event_kind="message_create",
        raw_text=ROLL_MESSAGE,
        payload_json='{"content":"roll"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    attempt = discord_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    service = AutomaticImportService(
        CatalogService(catalog_repository),
        roll_projection_coordinator=coordinator,
    )
    return service, received.source_event_id, attempt.attempt_id


def _durable_profile_importer(tmp_path):
    database_path = tmp_path / "durable-profile.db"
    catalog_repository = CatalogRepository(database_path)
    discord_repository = DiscordMessageRepository(database_path)
    coordinator = ProfileProjectionCoordinator(catalog_repository, discord_repository)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", "profile-message"
    )
    received = discord_repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, "profile-payload-hash", "revision-1"
        ),
        event_key="profile-event",
        event_kind="message_create",
        raw_text=PROFILE_MESSAGE,
        payload_json='{"content":"profile"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    attempt = discord_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    service = AutomaticImportService(
        CatalogService(catalog_repository),
        profile_projection_coordinator=coordinator,
    )
    return service, received.source_event_id, attempt.attempt_id


def test_automatic_import_routes_top_pages_without_server_context(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "TOP 1000\n#1 - Hatsune Miku - VOCALOID\nPage 1 / 67"

    result = service.import_message(message, "test")

    assert result.kind == "top"
    assert result.imported_count == 1
    assert catalog.character_count() == 1


def test_automatic_import_requires_context_only_for_account_scoped_messages(tmp_path) -> None:
    service = AutomaticImportService(CatalogService(CatalogRepository(tmp_path / "catalog.db")))
    message = "ernieuuu, you can claim right now! The next claim reset is in 2h 32 min."

    with pytest.raises(ValueError, match="--server"):
        service.import_message(message, "test")


def test_automatic_import_observes_help_and_tutorial_without_creating_characters(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    help_result = service.import_message("Looking for a specific command? Try $search", "test")
    tutorial_result = service.import_message("2/17 - Tutorial\nReward: +200:kakera:", "test")

    assert help_result.kind == "help"
    assert tutorial_result.kind == "tutorial"
    assert help_result.imported_count == tutorial_result.imported_count == 0
    assert catalog.character_count() == 0


def test_automatic_import_persists_kakeraloot_purchase_guard_as_empty_state(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = (
        "You need to buy kakeraloots before using this command ($kl)\n"
        "Type $infokl to get more infos about kakeraloots."
    )

    result = service.import_message(message, "test", "Lake", "cute_beagle_91130")
    state = catalog.kakeraloot_state("Lake", "cute_beagle_91130")

    assert result.kind == "lootstate"
    assert result.imported_count == 1
    assert state is not None
    assert not state.has_kakeraloots


def test_automatic_import_persists_kakeraloot_prerequisite_guard_as_empty_state(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "Prerequisites: Sapphire I + Ruby I + Emerald I ($infokl)"

    result = service.import_message(message, "test", "Lake", "cute_beagle_91130")
    state = catalog.kakeraloot_state("Lake", "cute_beagle_91130")

    assert result.kind == "lootstate"
    assert result.imported_count == 1
    assert state is not None
    assert not state.has_kakeraloots


def test_automatic_import_persists_rankless_rolls_for_future_history(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:"

    result = service.import_message(message, "test", "Lake", "ernieuuu")
    rolls = catalog.recent_rolls("Lake", "ernieuuu")

    assert result.kind == "roll"
    assert result.imported_count == 1
    assert rolls[0].character.name == "Hips"
    assert rolls[0].claim_rank is None


def test_automatic_import_durable_roll_delegates_once_with_all_context(tmp_path) -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_roll.return_value = RollObservation(
        name="Hips",
        series="Dekoboko Majo no Oyako Jijou",
        claim_rank=None,
        kakera_value=30,
    )
    router = Mock()
    coordinator = Mock()
    coordinator.coordinate_roll.return_value = RollProjectionResult(
        imported_count=1,
        import_event_id=42,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_targets=(),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=router,
        roll_projection_coordinator=coordinator,
    )
    context = DurableRollImportContext(
        source_event_id=17,
        attempt_id=19,
        finished_at=FINISHED_AT,
    )

    result = service.import_message(
        ROLL_MESSAGE,
        "discord:message",
        " Lake ",
        " ernieuuu ",
        detected_kind="roll",
        observed_at=OBSERVED_AT,
        durable_roll_context=context,
    )

    parser.parse_roll.assert_called_once_with(ROLL_MESSAGE)
    coordinator.coordinate_roll.assert_called_once_with(
        source_event_id=17,
        attempt_id=19,
        roll=parser.parse_roll.return_value,
        server="Lake",
        account="ernieuuu",
        raw=ROLL_MESSAGE,
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_roll.assert_not_called()
    assert result.imported_count == 1
    assert result.import_event_id == 42
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True


def test_automatic_import_durable_roll_maps_completed_replay(tmp_path) -> None:
    service, source_event_id, attempt_id = _durable_roll_importer(tmp_path)
    first = service.import_message(
        ROLL_MESSAGE,
        "discord",
        "Lake",
        "ernieuuu",
        detected_kind="roll",
        observed_at=OBSERVED_AT,
        durable_roll_context=DurableRollImportContext(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
        ),
    )

    replay = service.import_message(
        ROLL_MESSAGE,
        "discord",
        "Lake",
        "ernieuuu",
        detected_kind="roll",
        observed_at=OBSERVED_AT,
        durable_roll_context=DurableRollImportContext(
            source_event_id=source_event_id,
            attempt_id=None,
            finished_at=FINISHED_AT,
        ),
    )

    assert first.imported_count == 1
    assert first.import_event_id is not None
    assert replay.imported_count == 0
    assert replay.import_event_id == first.import_event_id
    assert replay.replay_skipped is True
    assert replay.durable_success_recorded is True


def test_automatic_import_durable_roll_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_roll.return_value = RollObservation(
        name="Hips", series="Series", claim_rank=None, kakera_value=30
    )
    coordinator = Mock()
    coordinator.coordinate_roll.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        roll_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            ROLL_MESSAGE,
            "discord",
            "Lake",
            "ernieuuu",
            detected_kind="roll",
            durable_roll_context=DurableRollImportContext(
                source_event_id=17,
                attempt_id=19,
                finished_at=FINISHED_AT,
            ),
        )

    catalog.import_roll.assert_not_called()


def test_automatic_import_durable_context_requires_coordinator_before_catalog_write(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    with pytest.raises(RuntimeError, match="RollProjectionCoordinator"):
        service.import_message(
            ROLL_MESSAGE,
            "discord",
            "Lake",
            "ernieuuu",
            detected_kind="roll",
            durable_roll_context=DurableRollImportContext(
                source_event_id=17,
                attempt_id=19,
                finished_at=FINISHED_AT,
            ),
        )

    assert catalog.character_count() == 0
    with sqlite3.connect(tmp_path / "catalog.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0


def test_automatic_import_non_durable_roll_keeps_catalog_path_and_neutral_result() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_roll.return_value = RollObservation(
        name="Hips", series="Series", claim_rank=None, kakera_value=30
    )
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    result = service.import_message(
        ROLL_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="roll",
    )

    catalog.import_roll.assert_called_once_with(
        parser.parse_roll.return_value,
        "Lake",
        "ernieuuu",
        ROLL_MESSAGE,
        "clipboard",
    )
    assert result.imported_count == 1
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_durable_profile_delegates_once_with_all_context() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_profile.return_value = PROFILE
    coordinator = Mock()
    coordinator.coordinate_profile.return_value = ProfileProjectionResult(
        imported_count=1,
        import_event_id=43,
        profile_observation_id=44,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("profile_observations", 44),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        profile_projection_coordinator=coordinator,
    )
    context = DurableProfileImportContext(
        source_event_id=21,
        attempt_id=23,
        finished_at=FINISHED_AT,
    )

    result = service.import_message(
        PROFILE_MESSAGE,
        "discord:message",
        " Lake ",
        " ernieuuu ",
        detected_kind="profile",
        observed_at=OBSERVED_AT,
        durable_profile_context=context,
    )

    parser.parse_profile.assert_called_once_with(PROFILE_MESSAGE)
    coordinator.coordinate_profile.assert_called_once_with(
        source_event_id=21,
        attempt_id=23,
        profile=PROFILE,
        server="Lake",
        account="ernieuuu",
        raw=PROFILE_MESSAGE,
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_profile.assert_not_called()
    assert result.imported_count == 1
    assert result.import_event_id == 43
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True


def test_automatic_import_durable_profile_maps_completed_replay(tmp_path) -> None:
    service, source_event_id, attempt_id = _durable_profile_importer(tmp_path)
    first = service.import_message(
        PROFILE_MESSAGE,
        "discord",
        "Lake",
        "moa",
        detected_kind="profile",
        observed_at=OBSERVED_AT,
        durable_profile_context=DurableProfileImportContext(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
        ),
    )

    replay = service.import_message(
        PROFILE_MESSAGE,
        "discord",
        "Lake",
        "moa",
        detected_kind="profile",
        observed_at=OBSERVED_AT,
        durable_profile_context=DurableProfileImportContext(
            source_event_id=source_event_id,
            attempt_id=None,
            finished_at=FINISHED_AT,
        ),
    )

    assert first.imported_count == 1
    assert first.import_event_id is not None
    assert replay.imported_count == 0
    assert replay.import_event_id == first.import_event_id
    assert replay.replay_skipped is True
    assert replay.durable_success_recorded is True


def test_automatic_import_durable_profile_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_profile.return_value = PROFILE
    coordinator = Mock()
    coordinator.coordinate_profile.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        profile_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            PROFILE_MESSAGE,
            "discord",
            "Lake",
            "moa",
            detected_kind="profile",
            durable_profile_context=DurableProfileImportContext(
                source_event_id=21,
                attempt_id=23,
                finished_at=FINISHED_AT,
            ),
        )

    catalog.import_profile.assert_not_called()


def test_automatic_import_durable_profile_requires_coordinator_before_catalog_write() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_profile.return_value = PROFILE
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    with pytest.raises(RuntimeError, match="ProfileProjectionCoordinator"):
        service.import_message(
            PROFILE_MESSAGE,
            "discord",
            "Lake",
            "moa",
            detected_kind="profile",
            durable_profile_context=DurableProfileImportContext(
                source_event_id=21,
                attempt_id=23,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_profile.assert_called_once_with(PROFILE_MESSAGE)
    catalog.import_profile.assert_not_called()


def test_automatic_import_non_durable_profile_keeps_catalog_path_and_parses_once() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_profile.return_value = PROFILE
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        profile_projection_coordinator=coordinator,
    )

    result = service.import_message(
        PROFILE_MESSAGE,
        "clipboard",
        "Lake",
        "moa",
        detected_kind="profile",
    )

    parser.parse_profile.assert_called_once_with(PROFILE_MESSAGE)
    catalog.import_profile.assert_called_once_with(
        PROFILE,
        "Lake",
        "moa",
        PROFILE_MESSAGE,
        "clipboard",
    )
    coordinator.coordinate_profile.assert_not_called()
    assert result.imported_count == 1
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_profile_context_does_not_affect_roll_route() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_roll.return_value = RollObservation(
        name="Hips", series="Series", claim_rank=None, kakera_value=30
    )
    profile_coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        profile_projection_coordinator=profile_coordinator,
    )

    result = service.import_message(
        ROLL_MESSAGE,
        "clipboard",
        "Lake",
        "moa",
        detected_kind="roll",
        durable_profile_context=DurableProfileImportContext(
            source_event_id=21,
            attempt_id=23,
            finished_at=FINISHED_AT,
        ),
    )

    catalog.import_roll.assert_called_once_with(
        parser.parse_roll.return_value,
        "Lake",
        "moa",
        ROLL_MESSAGE,
        "clipboard",
    )
    profile_coordinator.coordinate_profile.assert_not_called()
    assert result.imported_count == 1


def test_automatic_import_non_roll_routes_never_call_roll_coordinator(tmp_path) -> None:
    coordinator = Mock()
    service = AutomaticImportService(
        CatalogService(CatalogRepository(tmp_path / "catalog.db")),
        roll_projection_coordinator=coordinator,
    )

    result = service.import_message(
        "Looking for a specific command? Try $search", "clipboard"
    )

    assert result.kind == "help"
    coordinator.coordinate_roll.assert_not_called()


def test_automatic_import_result_remains_compatible_for_existing_callers() -> None:
    result = AutomaticImportResult(kind="help", imported_count=0, message="message")

    assert result.model_dump() == {
        "kind": "help",
        "imported_count": 0,
        "message": "message",
        "import_event_id": None,
        "replay_skipped": False,
        "durable_success_recorded": False,
    }


def test_automatic_import_persists_account_scoped_claim_evidence(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    roll = "Pakunoda\nHunter × Hunter\n116:kakera:"
    claim = "💖 **ernieuuu** and **Pakunoda** are now married! 💖\n+128:kakera:"

    service.import_message(roll, "discord", "Lake", "ernieuuu")
    result = service.import_message(claim, "discord", "Lake", "ernieuuu")

    observations = catalog.claim_observations("Lake", "ernieuuu")
    assert result.kind == "claim"
    assert result.imported_count == 1
    assert observations[0].character_name == "Pakunoda"
    assert observations[0].character is not None
    assert observations[0].character.series == "Hunter × Hunter"

    with sqlite3.connect(tmp_path / "catalog.db") as connection:
        event = connection.execute(
            "SELECT kind FROM import_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert event[0] == "claim"


def test_automatic_import_observes_divorce_prompt_without_mutating_catalog(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    prompt = (
        "Professor Layton: Do you confirm the divorce? (y/n/yes/no)\n"
        "Characters divorced by $divorce are also removed from the $restorelist "
        "(+54:kakera:if you confirm)"
    )

    result = service.import_message(prompt, "discord", "Lake", "cute_beagle_91130")

    assert result.kind == "divorce_prompt"
    assert result.imported_count == 0
    assert "Professor Layton" in result.message
    assert catalog.character_count() == 0


def test_automatic_import_observes_declined_divorce_without_mutating_catalog(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    result = service.import_message("Divorce declined.", "discord", "Lake", "cute_beagle_91130")

    assert result.kind == "divorce_declined"
    assert result.imported_count == 0
    assert catalog.character_count() == 0


def test_automatic_import_persists_completed_divorce_and_hides_old_claim(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    service.import_message(
        "Professor Layton\nProfessor Layton\n24:kakera:",
        "discord:roll",
        "Lake",
        "ernieuuu",
    )
    service.import_message(
        "ernieuuu and Professor Layton are now married!",
        "discord:claim",
        "Lake",
        "ernieuuu",
    )

    result = service.import_message(
        "💔 Professor Layton and ernieuuu are now divorced. 💔 (+54:kakera:)",
        "discord:divorce",
        "Lake",
        "ernieuuu",
    )

    assert result.kind == "divorce_complete"
    assert result.imported_count == 1
    assert catalog.claim_observations("Lake", "ernieuuu") == ()
    with sqlite3.connect(tmp_path / "catalog.db") as connection:
        event = connection.execute(
            "SELECT kind FROM import_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        observation = connection.execute(
            "SELECT character_name, kakera_refund FROM divorce_observations"
        ).fetchone()
    assert event[0] == "divorce"
    assert observation == ("Professor Layton", 54)


def test_automatic_import_persists_sphere_result(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = ":sp: +158\n:spG: +43 (Stock: 3,655)"

    result = service.import_message(message, "test", "Lake", "ernieuuu")
    observation = catalog.sphere_result("Lake", "ernieuuu")

    assert result.kind == "sphere_result"
    assert result.imported_count == 1
    assert observation is not None
    assert observation.snapshot.total_gained == 158
    assert observation.snapshot.stock == 3655


def test_automatic_import_audits_transaction_steps_without_creating_characters(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    result = service.import_message(
        "ernieuuu, do you really want to give 1:kakera: ? (y/n/yes/no)",
        "discord:givek",
        "Lake",
        "ernieuuu",
    )

    assert result.kind == "gift_kakera"
    assert result.imported_count == 0
    assert catalog.character_count() == 0
    with sqlite3.connect(tmp_path / "catalog.db") as connection:
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert event == ("command_observation", "discord:givek:command=$givek", "ernieuuu, do you really want to give 1:kakera: ? (y/n/yes/no)")


def test_automatic_import_persists_profile_snapshot_without_character_rows(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = (
        "cute_beagle_91130\n"
        "Collection size: 35 (100%:female: 0% :male:)\n"
        "Pokédex: 2 Pokémon :gulpin: :piloswine:\n"
        "Reacts:\n"
        "1x:kakeraP: 7x:kakera: 1x:kakeraT:\n"
        "812:kakera:\n"
        "Keys: 3:bronzekey:\n"
        "110 :sp:\n"
        "2x:spP: 12x:spB: 7x:spT: 4x:spG: 1x:spY: 1x:sp: 4x:spL:\n"
        ":silvmudae::MudaeBirthday7::MudaeBirthday8::DiamondI:"
    )

    result = service.import_message(message, "discord", "Lake", "cute_beagle_91130")
    observation = catalog.profile("Lake", "cute_beagle_91130")

    assert result.kind == "profile"
    assert result.imported_count == 1
    assert observation is not None
    assert observation.snapshot.collection_size == 35
    assert observation.snapshot.kakera_balance == 812
    assert observation.snapshot.mudapins_collected is None
    assert catalog.character_count() == 0


def test_automatic_import_persists_empty_profile_without_optional_sections(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    result = service.import_message(
        "moa\nCollection size: 0 (0%:female: 0% :male:)",
        "discord",
        "League of Draven",
        "moa",
    )
    observation = catalog.profile("League of Draven", "moa")

    assert result.kind == "profile"
    assert result.imported_count == 1
    assert observation is not None
    assert observation.snapshot.collection_size == 0
    assert observation.snapshot.kakera_balance is None
    assert observation.snapshot.pokedex_count is None


def test_automatic_import_persists_mudapin_inventory(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    result = service.import_message(
        ":pin139::pin182::pin2157::logopin6::logopin141:",
        "discord",
        "Lake Arrowhead 2025",
        "ernieuuu",
    )
    observation = catalog.mudapins("Lake Arrowhead 2025", "ernieuuu")

    assert result.kind == "mudapins"
    assert result.imported_count == 5
    assert result.message == "Imported 5 Mudapin markers."
    assert observation is not None
    assert observation.snapshot.pin_markers == (
        ":pin139:",
        ":pin182:",
        ":pin2157:",
        ":logopin6:",
        ":logopin141:",
    )


def test_automatic_import_persists_empty_mudapin_inventory(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    result = service.import_message(
        "No mudapins found! Collect them with kakeraloots ($kl)",
        "discord",
        "League of Draven",
        "cute_beagle_91130",
    )
    observation = catalog.mudapins("League of Draven", "cute_beagle_91130")

    assert result.kind == "mudapins"
    assert result.imported_count == 0
    assert result.message == "Imported no Mudapins."
    assert observation is not None
    assert observation.snapshot.pin_markers == ()


def test_automatic_import_routes_bold_kakera_receipt(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = ":kakeraY: **ernieuuu +524** ($k)"

    result = service.import_message(message, "test", "Lake")
    receipts = catalog.kakera_reactions("Lake", "ernieuuu", 1)

    assert result.kind == "reaction_receipt"
    assert result.imported_count == 1
    assert [(receipt.reaction_label, receipt.kakera_earned) for receipt in receipts] == [
        (":kakeraY:", 524)
    ]


def test_automatic_import_routes_blocked_kakera_reaction_without_writing_timer_state(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "**cute_beagle_91130**, You can't react to kakera for **34** min. ($ku)"

    result = service.import_message(message, "test", "Lake", "cute_beagle_91130")

    assert result.kind == "reaction_blocked"
    assert result.imported_count == 0
    assert "no $ku snapshot imported" in result.message
    assert catalog.timer_state("Lake", "cute_beagle_91130") is None


def test_automatic_import_routes_keyed_harem_pages(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "ernieuuu's harem\nAlbedo \u00b7 :goldkey:  (7) 1,453 ka\nPage 1 / 6"

    result = service.import_message(message, "test", "Lake", "ernieuuu")

    assert result.kind == "harem"
    assert result.imported_count == 1
    assert "page 1/6" in result.message
    entries = catalog.harem_keys("Lake", "ernieuuu")
    assert [(entry.character_name, entry.key_count, entry.kakera_value) for entry in entries] == [
        ("Albedo", 7, 1453)
    ]


def test_automatic_import_messages_show_character_and_series(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    result = service.import_message(
        "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:",
        "test",
        "Lake",
        "ernieuuu",
    )

    assert result.message == (
        "Imported roll observation: Hips / Dekoboko Majo no Oyako Jijou."
    )


def test_automatic_import_routes_antidisable_pages(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = (
        "ernieuuu's Antidisablelist (1/500)\n"
        "10 antidisabled characters\n"
        "Chainsaw Man\n"
        "Page 1 / 1"
    )

    result = service.import_message(message, "test", "Lake", "ernieuuu")

    assert result.kind == "antidisable"
    assert result.imported_count == 1
    assert "page 1/1" in result.message


def test_automatic_import_routes_antidisable_continuation_page_without_count(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = (
        "ernieuuu's Antidisablelist (1/500)\n"
        "Chainsaw Man\n"
        "Page 2 / 2"
    )

    result = service.import_message(message, "test", "Lake", "ernieuuu")

    assert result.kind == "antidisable"
    assert result.imported_count == 1
    assert "page 2/2" in result.message


def test_automatic_import_routes_ranked_harem_pages(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    catalog.import_top_page(
        MudaeTextParser().parse_top_page("#2 - Zero Two - DARLING in the FRANXX"),
        "#2 - Zero Two - DARLING in the FRANXX",
        "clipboard",
    )
    service = AutomaticImportService(catalog)
    message = "ernieuuu's harem\n#2 - Zero Two 1,440 ka\nPage 1 / 38"

    result = service.import_message(message, "test", "Lake", "ernieuuu")

    assert result.kind == "ranked_harem"
    assert result.imported_count == 1
    assert "page 1/38" in result.message
    entries = catalog.owned_characters("Lake", "ernieuuu")
    assert [(entry.character_name, entry.claim_rank, entry.kakera_value) for entry in entries] == [
        ("Zero Two", 2, 1440)
    ]
