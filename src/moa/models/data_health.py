"""Models used by local data-health diagnostics."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DataHealthFinding:
    """One deterministic, report-only data-health finding."""

    check_id: str
    category: str
    entity: str
    local_identifier: int | str
    reason: str
