from moa.loader.json_loader import load_json
from moa.loader.knowledge_loader import DATA_DIR
from moa.models.command import MudaeFlagDefinition


def load_commands() -> tuple[MudaeFlagDefinition, ...]:
    raw = load_json(DATA_DIR / "commands.json")
    return tuple(MudaeFlagDefinition.model_validate(item) for item in raw)
