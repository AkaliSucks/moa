"""Validated loading for immutable Kakeraloot reward definitions."""

from moa.loader.json_loader import load_json
from moa.loader.knowledge_loader import DATA_DIR
from moa.models.loot import KakeralootDefinition


def load_kakeraloots() -> tuple[KakeralootDefinition, ...]:
    """Load the universal Kakeraloot reward list, not account-owned rewards."""
    raw = load_json(DATA_DIR / "kakera_loots.json")
    return tuple(KakeralootDefinition.model_validate(item) for item in raw)
