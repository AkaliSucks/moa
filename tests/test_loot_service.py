from moa.models.loot import KakeralootDefinition
from moa.services.loot_service import KakeralootService


class InMemoryKakeralootRepository:
    """Minimal test double proving the service is storage-independent."""

    def __init__(self, loots: tuple[KakeralootDefinition, ...]) -> None:
        self._loots = loots

    def all(self) -> tuple[KakeralootDefinition, ...]:
        return self._loots

    def get(self, loot_id: str) -> KakeralootDefinition | None:
        normalized_id = loot_id.strip().upper()
        return next((loot for loot in self._loots if loot.id == normalized_id), None)


def test_kakeraloot_reference_data_contains_every_known_reward_type() -> None:
    loots = KakeralootService().all()

    assert [loot.id for loot in loots] == [
        "ADDITIONAL_ROLLS",
        "KAKERA",
        "RESET_TIMER_COOLDOWN",
        "DISABLELIST_CAPACITY",
        "PERMANENT_ROLLS",
        "WISHLIST_SLOTS",
        "WISHPROTECT",
        "MUDAPINS",
        "BKU_RESET_CHANCE",
        "STAR_BRANCHES",
    ]


def test_kakeraloot_service_finds_unowned_universal_reward() -> None:
    loot = KakeralootService().get("bku_reset_chance")

    assert loot is not None
    assert loot.name == "$bku Reset Chance"
    assert not loot.guaranteed


def test_kakeraloot_service_returns_none_for_unknown_reward() -> None:
    assert KakeralootService().get("not-a-loot") is None


def test_kakeraloot_service_accepts_a_storage_independent_repository() -> None:
    loot = KakeralootDefinition(
        id="TEST",
        name="Test Loot",
        category="utility",
        guaranteed=False,
        progression_note="Test-only.",
        description="A test-only reward.",
    )
    service = KakeralootService(InMemoryKakeralootRepository((loot,)))

    assert service.get("test") == loot
