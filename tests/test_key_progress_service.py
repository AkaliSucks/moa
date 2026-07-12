from datetime import datetime, timezone

from moa.models.catalog import HaremKeyObservation
from moa.models.key import KeyMilestone, KeyTierDefinition
from moa.services.key_progress_service import KeyProgressService


class InMemoryCatalogService:
    """Small harem-only test double for key-progress calculations."""

    def __init__(self, entries: tuple[HaremKeyObservation, ...]) -> None:
        self._entries = entries

    def harem_keys(self, server_name: str, account_name: str) -> tuple[HaremKeyObservation, ...]:
        return self._entries


class InMemoryKeyService:
    """Small universal-key test double."""

    def __init__(self, tiers: tuple[KeyTierDefinition, ...]) -> None:
        self._tiers = tiers

    def all(self) -> tuple[KeyTierDefinition, ...]:
        return self._tiers


def _entry(name: str, key_count: int, value: int | None = None) -> HaremKeyObservation:
    return HaremKeyObservation(
        character_name=name,
        character=None,
        key_type="chaos" if key_count >= 10 else "gold",
        key_count=key_count,
        kakera_value=value,
        observed_at=datetime.now(timezone.utc),
    )


def _tiers() -> tuple[KeyTierDefinition, ...]:
    return (
        KeyTierDefinition(
            id="SILVER",
            name="Silver Key",
            minimum_key_count=3,
            maximum_key_count=5,
            description="Test silver tier.",
            milestones=(
                KeyMilestone(key_count=3, effects=("Silver unlock.",)),
                KeyMilestone(key_count=5, effects=("Final silver bonus.",)),
            ),
        ),
        KeyTierDefinition(
            id="GOLD",
            name="Gold Key",
            minimum_key_count=6,
            maximum_key_count=9,
            description="Test gold tier.",
            milestones=(KeyMilestone(key_count=6, effects=("Gold bonus.",)), KeyMilestone(key_count=9, effects=("Final gold bonus.",))),
        ),
        KeyTierDefinition(
            id="CHAOS",
            name="Chaos Key",
            minimum_key_count=10,
            maximum_key_count=None,
            description="Test chaos tier.",
            milestones=(
                KeyMilestone(key_count=10, effects=("Chaos unlock.",)),
                KeyMilestone(key_count=11, effects=("+5% Kakera value.",)),
                KeyMilestone(key_count=25, effects=("Second reaction.",)),
            ),
        ),
    )


def test_key_progress_reports_the_next_fixed_milestone() -> None:
    service = KeyProgressService(InMemoryCatalogService((_entry("Saber", 7, 1400),)), InMemoryKeyService(_tiers()))

    progress = service.progress("Lake Arrowhead 2025", "ernieuuu")

    assert progress[0].current_tier == "Gold Key"
    assert progress[0].next_milestone_key_count == 9
    assert progress[0].keys_until_next_milestone == 2


def test_key_progress_crosses_from_the_end_of_one_tier_to_the_next() -> None:
    service = KeyProgressService(
        InMemoryCatalogService((_entry("Power", 5, 1400),)), InMemoryKeyService(_tiers())
    )

    progress = service.progress("Lake Arrowhead 2025", "ernieuuu")

    assert progress[0].current_tier == "Silver Key"
    assert progress[0].next_milestone_key_count == 6
    assert progress[0].next_effects == ("Gold bonus.",)


def test_key_progress_reports_chaos_value_growth_on_each_following_key() -> None:
    service = KeyProgressService(InMemoryCatalogService((_entry("Miku", 24, 1200),)), InMemoryKeyService(_tiers()))

    progress = service.progress("Lake Arrowhead 2025", "ernieuuu")

    assert progress[0].next_milestone_key_count == 25
    assert progress[0].keys_until_next_milestone == 1
    assert progress[0].next_effects == ("+5% Kakera value.", "Second reaction.")
