"""Focused contract tests for privacy-safe operational events."""

from __future__ import annotations

import logging

import pytest

from moa.core.logging import emit_operational_event


def test_registered_event_renders_deterministically(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="test.operational")
    logger = logging.getLogger("test.operational")

    emit_operational_event(
        logger,
        "projection.completed",
        source_event_id=7,
        processing_attempt_id=8,
        projection_kind="roll",
        outcome="succeeded",
    )

    assert caplog.records[-1].message == (
        "event=projection.completed component=discord_listener outcome=succeeded "
        "source_event_id=7 processing_attempt_id=8 projection_kind=roll"
    )


def test_bounded_fields_are_accepted(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="test.operational.bounded")

    emit_operational_event(
        logging.getLogger("test.operational.bounded"),
        "projection.replayed",
        source_event_id=2,
        projection_kind="profile",
        replayed=True,
    )

    assert "replayed=true" in caplog.records[-1].message


def test_unknown_event_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown operational event"):
        emit_operational_event(logging.getLogger("test.operational"), "unknown.event")  # type: ignore[arg-type]


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(TypeError):
        emit_operational_event(  # type: ignore[call-arg]
            logging.getLogger("test.operational"),
            "source.received",
            source_event_id=1,
            outcome="received",
            arbitrary="blocked",
        )


@pytest.mark.parametrize(
    ("event", "kwargs", "match"),
    [
        ("source.received", {"source_event_id": 0, "outcome": "received"}, "positive"),
        ("source.received", {"source_event_id": 1, "outcome": "invalid"}, "outcome"),
        (
            "projection.completed",
            {"source_event_id": 1, "projection_kind": "unknown", "outcome": "succeeded"},
            "projection kind",
        ),
        (
            "processing.failed",
            {"source_event_id": 1, "failure_code": "unsafe detail", "outcome": "failed"},
            "failure code",
        ),
    ],
)
def test_invalid_bounded_values_are_rejected(event, kwargs, match) -> None:
    with pytest.raises(ValueError, match=match):
        emit_operational_event(logging.getLogger("test.operational"), event, **kwargs)


def test_required_fields_are_enforced() -> None:
    with pytest.raises(ValueError, match="source_event_id"):
        emit_operational_event(
            logging.getLogger("test.operational"),
            "source.received",
            outcome="received",
        )


def test_operational_rendering_contains_no_forbidden_payload_or_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="test.operational.privacy")

    emit_operational_event(
        logging.getLogger("test.operational.privacy"),
        "processing.failed",
        source_event_id=9,
        processing_attempt_id=10,
        failure_code="downstream_processing_error",
        outcome="failed",
    )

    message = caplog.records[-1].message
    assert "guild_id" not in message
    assert "raw payload" not in message
    assert "exception" not in message
    assert "unsafe detail" not in message
