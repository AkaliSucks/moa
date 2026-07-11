"""Business operations for Kakera Badge reference data and costs."""

from moa.models.badge import BadgeDefinition
from moa.repositories.badge_repository import BadgeRepository, BadgeRepositoryProtocol


class BadgeService:
    """Badge queries and deterministic purchase-cost calculations."""

    def __init__(self, repository: BadgeRepositoryProtocol | None = None) -> None:
        self._repository = repository or BadgeRepository()

    def all(self) -> tuple[BadgeDefinition, ...]:
        return self._repository.all()

    def get(self, badge_id: str) -> BadgeDefinition | None:
        return self._repository.get(badge_id)

    def cost_for_level(
        self,
        badge_id: str,
        level: int,
        base_value: int,
        *,
        ruby_iv_active: bool = False,
    ) -> int:
        """Return a single badge-level cost for a server-specific base value.

        Ruby IV's 25% discount is supplied by account state rather than badge
        knowledge. The caller must not apply it to the Ruby IV purchase that
        activates the discount.
        """
        badge = self.get(badge_id)
        if badge is None:
            raise ValueError(f"Unknown badge: {badge_id}")
        if level not in {badge_level.level for badge_level in badge.levels}:
            raise ValueError(f"{badge.name} does not have level {level}")
        if base_value <= 0:
            raise ValueError("Badge base value must be positive")

        undiscounted_cost = base_value * level
        return undiscounted_cost * 3 // 4 if ruby_iv_active else undiscounted_cost
