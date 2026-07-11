from moa.models.reaction import ReactionDefinition
from moa.services.reaction_service import ReactionService


class InMemoryReactionRepository:
    """Minimal test double proving the service is storage-independent."""

    def __init__(self, reactions: tuple[ReactionDefinition, ...]) -> None:
        self._reactions = reactions

    def all(self) -> tuple[ReactionDefinition, ...]:
        return self._reactions

    def get(self, reaction_id: str) -> ReactionDefinition | None:
        normalized_id = reaction_id.strip().upper()
        return next(
            (reaction for reaction in self._reactions if reaction.id == normalized_id),
            None,
        )


def test_reaction_reference_data_contains_all_types() -> None:
    reactions = ReactionService().all()

    assert [reaction.id for reaction in reactions] == [
        "PURPLE",
        "BLUE",
        "TEAL",
        "GREEN",
        "YELLOW",
        "ORANGE",
        "RED",
        "RAINBOW",
        "LIGHT",
        "DARK",
        "CHAOS",
    ]


def test_reaction_service_finds_red_kakera() -> None:
    reaction = ReactionService().get("red")

    assert reaction is not None
    assert reaction.minimum_value == 1401
    assert reaction.maximum_value == 1500
    assert reaction.average_value == 1450.5


def test_reaction_service_returns_none_for_unknown_reaction() -> None:
    assert ReactionService().get("not-a-reaction") is None


def test_reaction_service_accepts_a_storage_independent_repository() -> None:
    reaction = ReactionDefinition(
        id="TEST",
        name="Test Kakera",
        reaction_type="fixed",
        minimum_value=1,
        maximum_value=1,
        average_value=1.0,
        power_cost_policy="free",
        description="A test-only reaction.",
    )
    service = ReactionService(InMemoryReactionRepository((reaction,)))

    assert service.get("test") == reaction
