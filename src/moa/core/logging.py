"""Privacy-safe structured operational logging for MOA runtime events."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Literal


OperationalEventName = Literal[
    "source.received",
    "parser.rejected",
    "projection.completed",
    "projection.replayed",
    "processing.failed",
]

_COMPONENT = "discord_listener"
_EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "source.received": ("event", "component", "outcome", "source_event_id"),
    "parser.rejected": (
        "event",
        "component",
        "outcome",
        "source_event_id",
        "parser_outcome",
    ),
    "projection.completed": (
        "event",
        "component",
        "outcome",
        "source_event_id",
        "processing_attempt_id",
        "projection_kind",
    ),
    "projection.replayed": (
        "event",
        "component",
        "source_event_id",
        "projection_kind",
        "replayed",
    ),
    "processing.failed": (
        "event",
        "component",
        "outcome",
        "source_event_id",
        "processing_attempt_id",
        "failure_code",
    ),
}
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "source.received": ("event", "component", "outcome", "source_event_id"),
    "parser.rejected": (
        "event",
        "component",
        "outcome",
        "source_event_id",
        "parser_outcome",
    ),
    "projection.completed": (
        "event",
        "component",
        "outcome",
        "source_event_id",
        "projection_kind",
    ),
    "projection.replayed": (
        "event",
        "component",
        "source_event_id",
        "projection_kind",
        "replayed",
    ),
    "processing.failed": (
        "event",
        "component",
        "outcome",
        "source_event_id",
        "failure_code",
    ),
}
_PROJECTION_KINDS = frozenset(
    {
        "antidisable",
        "bonus",
        "claim",
        "infokl",
        "kakera",
        "mudapins",
        "profile",
        "roll",
        "settings",
        "sphere_result",
        "timers",
        "towerstate",
        "lootstate",
        "wishlist",
        "disablelist",
    }
)
_FAILURE_CODES = frozenset({"downstream_processing_error"})


@dataclass(frozen=True, slots=True)
class OperationalEvent:
    """One validated, non-persistent operational event."""

    event: str
    component: str = _COMPONENT
    outcome: str | None = None
    source_event_id: int | None = None
    processing_attempt_id: int | None = None
    projection_kind: str | None = None
    parser_outcome: str | None = None
    failure_code: str | None = None
    replayed: bool | None = None


def emit_operational_event(
    logger: logging.Logger,
    event: OperationalEventName,
    *,
    outcome: str | None = None,
    source_event_id: int | None = None,
    processing_attempt_id: int | None = None,
    projection_kind: str | None = None,
    parser_outcome: str | None = None,
    failure_code: str | None = None,
    replayed: bool | None = None,
) -> None:
    """Validate and render one event through the existing stdlib logger."""
    if event not in _EVENT_FIELDS:
        raise ValueError(f"Unknown operational event: {event}")
    record = OperationalEvent(
        event=event,
        outcome=outcome,
        source_event_id=source_event_id,
        processing_attempt_id=processing_attempt_id,
        projection_kind=projection_kind,
        parser_outcome=parser_outcome,
        failure_code=failure_code,
        replayed=replayed,
    )
    values = {
        "event": record.event,
        "component": record.component,
        "outcome": record.outcome,
        "source_event_id": record.source_event_id,
        "processing_attempt_id": record.processing_attempt_id,
        "projection_kind": record.projection_kind,
        "parser_outcome": record.parser_outcome,
        "failure_code": record.failure_code,
        "replayed": record.replayed,
    }
    fields = _EVENT_FIELDS[event]
    required = _REQUIRED_FIELDS[event]
    if any(values[name] is None for name in required):
        missing = ", ".join(name for name in required if values[name] is None)
        raise ValueError(f"Missing required operational event fields: {missing}")
    if any(values[name] is not None for name in values if name not in fields):
        unexpected = ", ".join(
            name for name in values if name not in fields and values[name] is not None
        )
        raise ValueError(f"Unexpected operational event fields: {unexpected}")
    if record.component != _COMPONENT:
        raise ValueError(f"Unsupported operational event component: {record.component}")
    for name in ("source_event_id", "processing_attempt_id"):
        value = values[name]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
            raise ValueError(f"{name} must be a positive internal ID")
    if record.outcome is not None and record.outcome not in {
        "received",
        "rejected",
        "succeeded",
        "failed",
    }:
        raise ValueError(f"Unsupported operational event outcome: {record.outcome}")
    if event == "parser.rejected" and record.parser_outcome != "rejected":
        raise ValueError("Unsupported parser outcome")
    if event != "parser.rejected" and record.parser_outcome is not None:
        raise ValueError("Unsupported parser outcome")
    if record.projection_kind is not None and record.projection_kind not in _PROJECTION_KINDS:
        raise ValueError(f"Unsupported projection kind: {record.projection_kind}")
    if record.failure_code is not None and record.failure_code not in _FAILURE_CODES:
        raise ValueError(f"Unsupported failure code: {record.failure_code}")
    if event == "projection.replayed" and record.replayed is not True:
        raise ValueError("projection.replayed requires replayed=true")
    rendered = " ".join(
        f"{name}={str(values[name]).lower() if isinstance(values[name], bool) else values[name]}"
        for name in fields
        if values[name] is not None
    )
    logger.info(rendered)
