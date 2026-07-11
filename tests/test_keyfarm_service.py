from moa.parser.mudae import MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.catalog_service import CatalogService
from moa.services.keyfarm_service import KeyFarmService


def test_keyfarm_recommendation_uses_starwish_wish_and_excludes_observed_unavailable(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    server = "Lake Arrowhead 2025"
    account = "ernieuuu"
    harem = (
        "Power · :silverkey: (5) 1,448 ka\n"
        "Emilia · :silverkey: (5) 1,295 ka\n"
        "Megumin · :silverkey: (5) 1,505 ka\n"
        "Page 1 / 1"
    )
    bonus = (
        "Spawn bonus for wishes: +210% ($k)\n"
        "Additional % spawn bonus for $starwish: +180% ($kt) (= 390%)\n"
        "Chance to get an additional key on wishes: +10% ($kt)"
    )
    wishlist = (
        "ernieuuu's Wishlist - 3/13 $wl, 2/2 $sw\n"
        "Power ⭐\n"
        "Emilia ⭐\n"
        "Megumin"
    )
    unavailable = "#500 - Emilia - Re:Zero 🚫"

    catalog.import_harem_key_page(
        MudaeTextParser().parse_harem_key_page(harem), server, account, harem, "test"
    )
    catalog.import_player_bonus(
        MudaeTextParser().parse_player_bonus(bonus), server, account, bonus, "test"
    )
    catalog.import_wishlist(MudaeTextParser().parse_wishlist(wishlist), server, account, wishlist, "test")
    catalog.import_unavailable_characters(
        MudaeTextParser().parse_unavailable_characters(unavailable),
        server,
        account,
        unavailable,
        "test",
    )

    recommendations = KeyFarmService(catalog).recommend(server, account)

    assert [entry.character_name for entry in recommendations] == ["Power", "Megumin"]
    assert recommendations[0].wishlist_status == "Starwish"
    assert recommendations[0].relative_spawn_multiplier == 4.9
    assert recommendations[0].additional_key_chance_percent == 10
    assert recommendations[1].wishlist_status == "Wish"
