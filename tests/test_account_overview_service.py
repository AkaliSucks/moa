from datetime import datetime, timedelta, timezone

from moa.models.catalog import (
    DisableListObservation,
    HaremKeyObservation,
    KakeraStateObservation,
    KakeralootStateObservation,
    PersonalRareObservation,
    ServerSettingsObservation,
    TowerStateObservation,
    WishlistObservation,
)
from moa.models.character import BadgeLevel, ServerSettingMetric
from moa.services.account_overview_service import AccountOverviewService


class InMemoryCatalogService:
    """A focused state-provider test double for the overview service."""

    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self._kakera = KakeraStateObservation(
            server_name="Lake Arrowhead 2025",
            account_name="ernieuuu",
            kakera_balance=7673,
            badges=(
                BadgeLevel(badge_name="bronze", level=4, max_reached=True),
                BadgeLevel(badge_name="sapphire", level=1, max_reached=False),
                BadgeLevel(badge_name="ruby", level=1, max_reached=False),
                BadgeLevel(badge_name="emerald", level=1, max_reached=False),
            ),
            observed_at=now - timedelta(minutes=2),
        )
        self._tower = TowerStateObservation(
            server_name="Lake Arrowhead 2025",
            account_name="ernieuuu",
            current_level=2,
            completed_towers=1,
            next_level_cost=75000,
            kakera_balance=9710,
            built_perk_ids=(5, 11),
            observed_at=now - timedelta(minutes=1),
        )
        self._loots = KakeralootStateObservation(
            server_name="Lake Arrowhead 2025",
            account_name="ernieuuu",
            rolls_stacked=1,
            disable_wa_ha_reduction=102,
            disable_wg_hg_reduction=68,
            protected_wish_level=42,
            protected_wish_denominator=4642,
            mudapins=22,
            rt_cooldown_reduction_hours=2,
            permanent_roll_bonus=1,
            star_branches=1,
            starwish_slots_from_branches=0,
            quantity_level=23,
            quality_level=6,
            usage_count=256,
            kakera_balance=9210,
            observed_at=now,
        )
        self._personal_rare = PersonalRareObservation(
            server_name="Lake Arrowhead 2025",
            account_name="ernieuuu",
            personal_rare_multiplier=1,
            observed_at=now,
        )
        self._settings = ServerSettingsObservation(
            server_name="Lake Arrowhead 2025",
            server_premium=False,
            prefix="$",
            language="en",
            claim_reset_minutes=180,
            reset_minute="xx:14",
            reset_shift_minutes=0,
            rolls_per_hour=10,
            claim_reaction_expiry_seconds=45,
            claimed_character_rarity_multiplier=4,
            kakera_bonus_percent=0,
            sphere_bonus_percent=0,
            game_mode=1,
            channel_instance=1,
            metrics=(ServerSettingMetric(label="Prefix", value="$"),),
            observed_at=now,
        )

    def kakera_state(self, server_name: str, account_name: str) -> KakeraStateObservation:
        return self._kakera

    def tower_state(self, server_name: str, account_name: str) -> TowerStateObservation:
        return self._tower

    def kakeraloot_state(self, server_name: str, account_name: str) -> KakeralootStateObservation:
        return self._loots

    def personal_rare(self, server_name: str, account_name: str) -> PersonalRareObservation | None:
        return self._personal_rare

    def server_settings(self, server_name: str) -> ServerSettingsObservation | None:
        return self._settings

    def wishlist(self, server_name: str, account_name: str) -> WishlistObservation | None:
        return None

    def disablelist(self, server_name: str, account_name: str) -> DisableListObservation | None:
        return None

    def harem_keys(self, server_name: str, account_name: str) -> tuple[HaremKeyObservation, ...]:
        return ()


def test_overview_uses_only_the_canonical_kakera_balance_and_computes_tower_gap() -> None:
    overview = AccountOverviewService(InMemoryCatalogService()).overview(
        "Lake Arrowhead 2025", "ernieuuu"
    )

    assert overview.kakera_balance == 7673
    assert overview.kakera_balance_source == "$k"
    assert overview.tower_shortfall == 67327
    assert overview.quantity_level == 23
    assert overview.kakeraloots_unlocked
    assert overview.missing_kakeraloot_prerequisites == ()
    assert overview.personal_rare_multiplier == 1
    assert overview.server_rare_multiplier == 4
    assert overview.effective_rare_multiplier == 1


def test_overview_reports_only_unmet_kakeraloot_badge_prerequisites() -> None:
    catalog = InMemoryCatalogService()
    catalog._kakera = KakeraStateObservation(
        server_name="ernieuuu's server",
        account_name="cute_beagle_91130",
        kakera_balance=354,
        badges=(BadgeLevel(badge_name="sapphire", level=1, max_reached=False),),
        observed_at=datetime.now(timezone.utc),
    )
    overview = AccountOverviewService(catalog).overview("ernieuuu's server", "cute_beagle_91130")

    assert overview.badge_count == 7
    assert overview.max_badge_count == 0
    assert not overview.kakeraloots_unlocked
    assert overview.missing_kakeraloot_prerequisites == ("Ruby I", "Emerald I")
