import pytest

from moa.models.badge import BadgeDefinition, BadgeLevel
from moa.services.badge_service import BadgeService


class InMemoryBadgeRepository:
    """Minimal test double proving the service is storage-independent."""

    def __init__(self, badges: tuple[BadgeDefinition, ...]) -> None:
        self._badges = badges

    def all(self) -> tuple[BadgeDefinition, ...]:
        return self._badges

    def get(self, badge_id: str) -> BadgeDefinition | None:
        normalized_id = badge_id.strip().upper()
        return next(
            (badge for badge in self._badges if badge.id == normalized_id),
            None,
        )


def test_badge_reference_data_contains_all_badges() -> None:
    badges = BadgeService().all()

    assert [badge.id for badge in badges] == [
        "BRONZE",
        "SILVER",
        "GOLD",
        "SAPPHIRE",
        "RUBY",
        "EMERALD",
        "DIAMOND",
    ]


def test_cost_uses_server_specific_badge_value() -> None:
    service = BadgeService()

    assert service.cost_for_level("GOLD", 4, 1000) == 4000
    assert service.cost_for_level("GOLD", 4, 1000, ruby_iv_active=True) == 3000


def test_cost_rejects_unknown_badges_and_levels() -> None:
    service = BadgeService()

    with pytest.raises(ValueError, match="Unknown badge"):
        service.cost_for_level("NOT_A_BADGE", 1, 1000)
    with pytest.raises(ValueError, match="does not have level"):
        service.cost_for_level("GOLD", 5, 1000)


def test_badge_service_accepts_a_storage_independent_repository() -> None:
    badge = BadgeDefinition(
        id="TEST",
        name="Test",
        default_base_value=1,
        levels=(BadgeLevel(level=1, effects=("Test effect",)),),
    )
    service = BadgeService(InMemoryBadgeRepository((badge,)))

    assert service.cost_for_level("test", 1, 500) == 500
