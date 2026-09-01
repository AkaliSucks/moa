from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from moa.database.sqlite import connect
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.retained_source_reprojection_admission_service import (
    ReprojectionAdmissionRejection,
    RetainedSourceReprojectionAdmissionError,
)
from moa.services.retained_source_reprojection_preflight_service import (
    ReprojectionPreflightEligibility,
    ReprojectionPreflightFailure,
    RetainedSourceReprojectionPreflightError,
    RetainedSourceReprojectionPreflightService,
)
from test_retained_source_reprojection_admission_service import NOW, _fixture


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "preflight.sqlite3"
    CatalogRepository(path)
    return path


def _seed_inventory(connection: sqlite3.Connection) -> None:
    for source_id, family in ((3, "wishlist"), (1, "antidisable"), (2, "roll")):
        connection.execute(
            "INSERT INTO discord_message_aggregates (id, platform, guild_id, channel_id, message_id, first_received_at, last_received_at, created_at, updated_at) VALUES (?, 'discord', 'private-guild', 'private-channel', ?, ?, ?, ?, ?)",
            (source_id, f"private-message-{source_id}", NOW, NOW, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO discord_message_revisions (id, aggregate_id, normalized_payload_hash, revision_state, first_received_at, last_received_at, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
            (
                source_id,
                source_id,
                f"private-hash-{source_id}",
                NOW,
                NOW,
                NOW,
                NOW,
            ),
        )
        import_id = int(
            connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, 'discord', ?, 'PRIVATE RAW MESSAGE')",
                (family, NOW),
            ).lastrowid
        )
        connection.execute(
            "INSERT INTO discord_source_events (id, event_key, revision_id, event_kind, status, raw_text, received_at, last_seen_at, legacy_import_event_id, created_at, updated_at) VALUES (?, ?, ?, 'MESSAGE_CREATE', 'succeeded', 'PRIVATE SOURCE TEXT', ?, ?, ?, ?, ?)",
            (source_id, f"event-{source_id}", source_id, NOW, NOW, import_id, NOW, NOW),
        )


class _ClassifyingAdmission:
    def admit(
        self,
        connection: sqlite3.Connection,
        source_event_id: int,
        *,
        hypothetical_generation_id: int | None = None,
    ):
        assert connection.in_transaction
        assert hypothetical_generation_id == 2
        if source_event_id == 1:
            raise RetainedSourceReprojectionAdmissionError(
                ReprojectionAdmissionRejection.EXPECTATIONS_UNKNOWN, "private detail"
            )
        if source_event_id == 2:
            raise RetainedSourceReprojectionAdmissionError(
                ReprojectionAdmissionRejection.SOURCE_EXPIRED, "private detail"
            )
        return SimpleNamespace(source_family="wishlist", expected_identities=(object(), object()))


def test_preflight_orders_aggregates_fingerprints_and_redacts_inventory(database_path):
    with connect(database_path) as connection:
        _seed_inventory(connection)
        connection.commit()
    before = database_path.read_bytes()

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        service = RetainedSourceReprojectionPreflightService(_ClassifyingAdmission())
        first = service.preflight(connection)
        second = service.preflight(connection)
        connection.rollback()

    assert [record.source_event_id for record in first.records] == [1, 2, 3]
    assert [record.eligibility for record in first.records] == [
        ReprojectionPreflightEligibility.UNKNOWN,
        ReprojectionPreflightEligibility.INELIGIBLE,
        ReprojectionPreflightEligibility.ELIGIBLE,
    ]
    assert [record.expected_link_count for record in first.records] == [None, None, 2]
    assert [(item.source_family, item.total) for item in first.family_totals] == [
        ("antidisable", 1),
        ("roll", 1),
        ("wishlist", 1),
    ]
    assert [(item.rejection_code, item.total) for item in first.reason_totals] == [
        ("expectations_unknown", 1),
        ("source_expired", 1),
    ]
    assert first == second
    assert len(first.inventory_fingerprint) == 64
    rendered = repr(first)
    assert "PRIVATE" not in rendered
    assert "private-" not in rendered
    assert database_path.read_bytes() == before


@pytest.mark.parametrize("reason", list(ReprojectionAdmissionRejection))
def test_every_admission_rejection_has_bounded_fail_closed_classification(database_path, reason):
    class RejectingAdmission:
        def admit(self, connection, source_event_id, *, hypothetical_generation_id=None):
            raise RetainedSourceReprojectionAdmissionError(reason, "sensitive detail")

    with connect(database_path) as connection:
        _seed_inventory(connection)
        connection.commit()
        connection.execute("BEGIN")
        report = RetainedSourceReprojectionPreflightService(RejectingAdmission()).preflight(
            connection
        )
        connection.rollback()

    expected = (
        ReprojectionPreflightEligibility.UNKNOWN
        if reason
        in {
            ReprojectionAdmissionRejection.ATTRIBUTION_UNRESOLVED,
            ReprojectionAdmissionRejection.EXPECTATIONS_UNKNOWN,
            ReprojectionAdmissionRejection.ANTIDISABLE_UNSUPPORTED,
            ReprojectionAdmissionRejection.PAYLOAD_INCOMPLETE,
        }
        else ReprojectionPreflightEligibility.INELIGIBLE
    )
    assert {record.eligibility for record in report.records} == {expected}
    assert {record.rejection_code for record in report.records} == {reason.value}
    assert "sensitive detail" not in repr(report)


def test_real_admission_marks_complete_antidisable_slots_eligible(database_path):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id = _fixture(connection, "antidisable")
        report = RetainedSourceReprojectionPreflightService().preflight(connection)
        assert report.hypothetical_generation_id == 3
        assert report.records[0].source_event_id == source_id
        assert report.records[0].source_family == "antidisable"
        assert report.records[0].eligibility is ReprojectionPreflightEligibility.ELIGIBLE
        assert report.records[0].expected_link_count == 1
        connection.rollback()


def test_legacy_incomplete_antidisable_is_unknown_and_ineligible(database_path):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        _fixture(connection, "antidisable")
        connection.execute("UPDATE harem_scan_pages SET slots_used = NULL")
        report = RetainedSourceReprojectionPreflightService().preflight(connection)
        assert report.records[0].eligibility is ReprojectionPreflightEligibility.UNKNOWN
        assert report.records[0].rejection_code == "payload_incomplete"
        assert report.records[0].expected_link_count is None
        connection.rollback()


def test_preflight_requires_caller_owned_transaction(database_path):
    with connect(database_path) as connection:
        with pytest.raises(RetainedSourceReprojectionPreflightError) as caught:
            RetainedSourceReprojectionPreflightService().preflight(connection)
    assert caught.value.reason is ReprojectionPreflightFailure.TRANSACTION_REQUIRED


@pytest.mark.parametrize("invalid_state", ["schema", "no_current", "multiple_current"])
def test_preflight_fails_closed_on_schema_or_generation_state(database_path, invalid_state):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        if invalid_state == "schema":
            connection.execute("DROP TABLE wishlist_observations")
            expected = ReprojectionPreflightFailure.SCHEMA_INVALID
        elif invalid_state == "no_current":
            connection.execute("UPDATE projection_generations SET is_current = 0")
            expected = ReprojectionPreflightFailure.CURRENT_GENERATION_INVALID
        else:
            connection.execute("DROP INDEX uq_projection_generations_one_current")
            connection.execute("INSERT INTO projection_generations (id, is_current) VALUES (2, 1)")
            expected = ReprojectionPreflightFailure.CURRENT_GENERATION_INVALID
        with pytest.raises(RetainedSourceReprojectionPreflightError) as caught:
            RetainedSourceReprojectionPreflightService().preflight(connection)
        assert caught.value.reason is expected
        connection.rollback()
