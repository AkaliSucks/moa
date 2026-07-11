"""Transparent first-pass recommendations for Mudae key farming."""

from moa.models.catalog import KeyFarmRecommendation
from moa.services.catalog_service import CatalogService


class KeyFarmService:
    """Combine imported harem, bonus, wishlist, and rollability evidence."""

    def __init__(self, catalog_service: CatalogService | None = None) -> None:
        self._catalog = catalog_service or CatalogService()

    def recommend(self, server_name: str, account_name: str) -> tuple[KeyFarmRecommendation, ...]:
        bonus = self._catalog.player_bonus(server_name, account_name)
        wishlist = self._catalog.wishlist(server_name, account_name)
        if bonus is None:
            raise ValueError("Import a $bonus snapshot before requesting key-farm recommendations.")
        if wishlist is None:
            raise ValueError("Import a $wl snapshot before requesting key-farm recommendations.")

        wished_by_name = {entry.name.casefold(): entry for entry in wishlist.entries}
        unavailable_names = {
            entry.character.name.casefold()
            for entry in self._catalog.unavailable_characters(server_name, account_name)
        }
        wish_bonus = bonus.wish_spawn_bonus_percent or 0
        starwish_bonus = bonus.starwish_total_spawn_bonus_percent or wish_bonus
        additional_key_chance = bonus.additional_wish_key_chance_percent or 0
        recommendations: list[KeyFarmRecommendation] = []

        for entry in self._catalog.harem_keys(server_name, account_name):
            if entry.kakera_value is None:
                continue
            normalized_name = entry.character_name.casefold()
            if normalized_name in unavailable_names:
                continue
            wish = wished_by_name.get(normalized_name)
            if wish is None:
                status = "Not wished"
                spawn_bonus = 0
                key_chance = 0
            elif wish.is_starwish:
                status = "Starwish"
                spawn_bonus = starwish_bonus
                key_chance = additional_key_chance
            else:
                status = "Wish"
                spawn_bonus = wish_bonus
                key_chance = additional_key_chance

            spawn_multiplier = 1 + (spawn_bonus / 100)
            key_multiplier = 1 + (key_chance / 100)
            recommendations.append(
                KeyFarmRecommendation(
                    character_name=entry.character_name,
                    kakera_value=entry.kakera_value,
                    key_type=entry.key_type,
                    key_count=entry.key_count,
                    wishlist_status=status,
                    rollability="Unknown",
                    spawn_bonus_percent=spawn_bonus,
                    relative_spawn_multiplier=spawn_multiplier,
                    additional_key_chance_percent=key_chance,
                    value_weighted_opportunity_index=(
                        entry.kakera_value * spawn_multiplier * key_multiplier
                    ),
                )
            )

        return tuple(
            sorted(
                recommendations,
                key=lambda entry: entry.value_weighted_opportunity_index,
                reverse=True,
            )
        )
