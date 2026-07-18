import sqlite3

import pytest

from moa.parser.mudae import MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.automatic_import_service import AutomaticImportService
from moa.services.catalog_service import CatalogService


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
