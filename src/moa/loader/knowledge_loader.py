from pathlib import Path

from moa.loader.json_loader import load_json
from moa.models.tower import TowerPerk


DATA_DIR = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "knowledge"
    / "data"
)


def load_tower():
    raw = load_json(DATA_DIR / "tower.json")
    return [TowerPerk.model_validate(item) for item in raw]