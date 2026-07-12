from moa.parser.mudae import MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.catalog_service import CatalogService
from moa.services.server_comparison_service import ServerComparisonService


def test_compare_server_settings_reports_matching_and_different_values(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = ServerComparisonService(catalog)
    lake = (
        "(Server not premium)\n"
        "· Prefix: $ ($prefix)\n"
        "· Lang: en ($lang)\n"
        "· Claim reset: every 180 min. ($setclaim)\n"
        "· Exact minute of the reset: xx:14 ($setinterval)\n"
        "· Reset shifted: by +0 min. ($shifthour)\n"
        "· Rolls per hour: 10 ($setrolls)\n"
        "· Time before the claim reaction expires: 45 sec. ($settimer)\n"
        "· Spawn rarity multiplier for already claimed characters: 4 ($setrare)\n"
        "· % kakera bonus: +0 ($setkakerabonus)\n"
        "· % sphere bonus: +0 ($setspherebonus)\n"
        "· Game mode: 1 ($gamemode)\n"
        "· This channel instance: 1 ($channelinstance)"
    )
    comparison = lake.replace("· Rolls per hour: 10", "· Rolls per hour: 12")
    parser = MudaeTextParser()
    catalog.import_server_settings(parser.parse_server_settings(lake), "Lake", lake, "test")
    catalog.import_server_settings(parser.parse_server_settings(comparison), "Fresh", comparison, "test")

    result = service.compare("Lake", "Fresh")

    rolls = next(entry for entry in result.entries if entry.label == "Rolls per hour")
    prefix = next(entry for entry in result.entries if entry.label == "Prefix")
    assert rolls.left_value == "10"
    assert rolls.right_value == "12"
    assert not rolls.matches
    assert prefix.matches
