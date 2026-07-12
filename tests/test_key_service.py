from moa.models.key import KeyMilestone, KeyTierDefinition
from moa.services.key_service import KeyService


class InMemoryKeyRepository:
    """Minimal test double proving the service is storage-independent."""

    def __init__(self, tiers: tuple[KeyTierDefinition, ...]) -> None:
        self._tiers = tiers

    def all(self) -> tuple[KeyTierDefinition, ...]:
        return self._tiers

    def get(self, key_id: str) -> KeyTierDefinition | None:
        normalized_id = key_id.strip().upper()
        return next((tier for tier in self._tiers if tier.id == normalized_id), None)


def test_key_reference_data_contains_all_four_tiers() -> None:
    tiers = KeyService().all()

    assert [tier.id for tier in tiers] == ["BRONZE", "SILVER", "GOLD", "CHAOS"]
    assert [(tier.minimum_key_count, tier.maximum_key_count) for tier in tiers] == [
        (1, 2),
        (3, 5),
        (6, 9),
        (10, None),
    ]


def test_key_service_finds_chaos_even_when_no_harem_character_has_it() -> None:
    tier = KeyService().get("chaos")

    assert tier is not None
    assert tier.minimum_key_count == 10
    assert tier.maximum_key_count is None
    assert tier.milestones[0].key_count == 10


def test_key_service_returns_none_for_unknown_tier() -> None:
    assert KeyService().get("not-a-tier") is None


def test_key_service_accepts_a_storage_independent_repository() -> None:
    tier = KeyTierDefinition(
        id="TEST",
        name="Test Key",
        minimum_key_count=1,
        maximum_key_count=1,
        description="A test-only tier.",
        milestones=(KeyMilestone(key_count=1, effects=("Test effect.",)),),
    )
    service = KeyService(InMemoryKeyRepository((tier,)))

    assert service.get("test") == tier
