from moa.loader.json_loader import load_json
from moa.loader.knowledge_loader import DATA_DIR
from moa.models.reaction import ReactionDefinition


def load_reactions() -> tuple[ReactionDefinition, ...]:
    """Load and validate immutable Kakera reaction reference data."""
    raw = load_json(DATA_DIR / "reactions.json")
    return tuple(ReactionDefinition.model_validate(item) for item in raw)
