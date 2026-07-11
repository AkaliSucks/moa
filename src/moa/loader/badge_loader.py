from moa.loader.json_loader import load_json
from moa.loader.knowledge_loader import DATA_DIR
from moa.models.badge import BadgeDefinition


def load_badges() -> tuple[BadgeDefinition, ...]:
    raw = load_json(DATA_DIR / "badges.json")
    return tuple(BadgeDefinition.model_validate(item) for item in raw)
