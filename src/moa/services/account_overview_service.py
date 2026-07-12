"""Build a coherent, read-only overview from imported Mudae account state."""

from moa.models.catalog import AccountOverview
from moa.services.catalog_service import CatalogService


class AccountOverviewService:
    """Combine independent account snapshots without mutating their history."""

    def __init__(self, catalog_service: CatalogService | None = None) -> None:
        self._catalog = catalog_service or CatalogService()

    def overview(self, server_name: str, account_name: str) -> AccountOverview:
        """Return one summary using `$k` as the canonical Kakera balance source."""
        kakera = self._catalog.kakera_state(server_name, account_name)
        personal_rare = self._catalog.personal_rare(server_name, account_name)
        server_settings = self._catalog.server_settings(server_name)
        tower = self._catalog.tower_state(server_name, account_name)
        loots = self._catalog.kakeraloot_state(server_name, account_name)
        wishlist = self._catalog.wishlist(server_name, account_name)
        disablelist = self._catalog.disablelist(server_name, account_name)
        harem = self._catalog.harem_keys(server_name, account_name)

        # `$k` is the canonical balance command. Other state messages may echo a
        # balance, but they are not used as a fallback because their snapshots
        # can be taken at different times and create a misleading overview.
        balance = kakera.kakera_balance if kakera is not None else None
        source = "$k" if kakera is not None else None
        if kakera is None:
            kakeraloots_unlocked = None
            missing_kakeraloot_prerequisites: tuple[str, ...] = ()
        else:
            badge_levels = {badge.badge_name.casefold(): badge.level for badge in kakera.badges}
            required_loot_badges = (
                ("sapphire", "Sapphire I"),
                ("ruby", "Ruby I"),
                ("emerald", "Emerald I"),
            )
            missing_kakeraloot_prerequisites = tuple(
                label for badge_name, label in required_loot_badges if badge_levels.get(badge_name, 0) < 1
            )
            kakeraloots_unlocked = not missing_kakeraloot_prerequisites

        server_rare_multiplier = (
            server_settings.claimed_character_rarity_multiplier if server_settings is not None else None
        )
        if personal_rare is None:
            effective_rare_multiplier = None
            rare_multiplier_source = None
        elif personal_rare.personal_rare_multiplier == 0:
            effective_rare_multiplier = server_rare_multiplier
            rare_multiplier_source = "$setrare" if server_rare_multiplier is not None else None
        else:
            effective_rare_multiplier = personal_rare.personal_rare_multiplier
            rare_multiplier_source = "$personalrare"

        return AccountOverview(
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            kakera_balance=balance,
            kakera_balance_source=source,
            personal_rare_multiplier=(
                personal_rare.personal_rare_multiplier if personal_rare is not None else None
            ),
            server_rare_multiplier=server_rare_multiplier,
            effective_rare_multiplier=effective_rare_multiplier,
            rare_multiplier_source=rare_multiplier_source,
            badge_count=7 if kakera is not None else 0,
            max_badge_count=(
                sum(badge.max_reached for badge in kakera.badges) if kakera is not None else 0
            ),
            tower_level=tower.current_level if tower is not None else None,
            completed_towers=tower.completed_towers if tower is not None else None,
            next_tower_cost=tower.next_level_cost if tower is not None else None,
            tower_shortfall=(max(0, tower.next_level_cost - balance) if tower is not None and balance is not None else None),
            kakeraloots_unlocked=kakeraloots_unlocked,
            missing_kakeraloot_prerequisites=missing_kakeraloot_prerequisites,
            has_kakeraloots=loots.has_kakeraloots if loots is not None else None,
            kakeraloot_status_note=loots.status_note if loots is not None else None,
            quantity_level=loots.quantity_level if loots is not None else None,
            quality_level=loots.quality_level if loots is not None else None,
            loot_usage_count=loots.usage_count if loots is not None else None,
            wishlist_count=wishlist.wishlist_count if wishlist is not None else None,
            wishlist_capacity=wishlist.wishlist_capacity if wishlist is not None else None,
            starwish_count=wishlist.starwish_count if wishlist is not None else None,
            starwish_capacity=wishlist.starwish_capacity if wishlist is not None else None,
            disable_slots_used=disablelist.slots_used if disablelist is not None else None,
            disable_slots_capacity=disablelist.slots_capacity if disablelist is not None else None,
            keyed_harem_count=len(harem),
        )
