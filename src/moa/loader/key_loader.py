"""Validated loading for immutable character-key definitions."""

from moa.loader.json_loader import load_json
from moa.loader.knowledge_loader import DATA_DIR
from moa.models.key import KeyTierDefinition


def load_key_tiers() -> tuple[KeyTierDefinition, ...]:
    """Load the universal Mudae key tiers and their milestone rewards."""
    raw = load_json(DATA_DIR / "keys.json")
    return tuple(KeyTierDefinition.model_validate(item) for item in raw)
