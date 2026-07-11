from moa.models.tower import TowerPerk
from moa.services.tower_service import TowerService


class InMemoryTowerRepository:
    """Minimal test double proving the service is storage-independent."""

    def __init__(self, floors: tuple[TowerPerk, ...]) -> None:
        self._floors = floors

    def all(self) -> tuple[TowerPerk, ...]:
        return self._floors

    def get(self, perk_id: int) -> TowerPerk | None:
        return next((floor for floor in self._floors if floor.id == perk_id), None)


def test_tower_reference_data_contains_all_first_tower_floors() -> None:
    floors = TowerService().all()

    assert len(floors) == 12
    assert [floor.id for floor in floors] == list(range(1, 13))


def test_tower_service_finds_floor_eleven() -> None:
    floor = TowerService().get(11)

    assert floor is not None
    assert floor.name == "Additional Rolls"
    assert floor.first_tower_effect == "+1 roll per hour"


def test_tower_service_returns_none_for_unknown_floor() -> None:
    assert TowerService().get(13) is None


def test_tower_service_accepts_a_storage_independent_repository() -> None:
    floor = TowerPerk(
        id=99,
        name="Test Floor",
        category="test",
        description="A test-only floor.",
        first_tower_effect="Test effect",
        progression_note="No progression.",
    )
    service = TowerService(InMemoryTowerRepository((floor,)))

    assert service.get(99) == floor
