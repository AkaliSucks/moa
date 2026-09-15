from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

import moa.cli.main as main
from moa.database.sqlite import connect
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.data_health_service import DataHealthService
from moa.services.projection_expectations import (
    DurableRollBaseEvidenceState,
    DurableRollKeyEvidenceState,
    Expectedness,
    load_durable_projection_expectation_facts,
    load_durable_roll_projection_expectation_evidence,
    resolve_expected_projections,
)
from moa.services.retained_source_reprojection_preflight_service import (
    ReprojectionPreflightEligibility,
    RetainedRollDurableExpectednessState,
    RetainedRollParserDurableRelationship,
    RetainedRollParserProbeFailure,
    RetainedRollRawEvidenceState,
    RetainedSourceReprojectionPreflightService,
)
from test_retained_source_reprojection_admission_service import NOW, _fixture


NO_KEY_ROLL = "Character\nSeries\nClaims: #1\n100:kakera:"
DISPLAYED_KEY_ROLL = "Character\nSeries\n:bronzekey: (1) $embedcolor unlocked!\n100:kakera:"


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "retained-roll-diagnostic.sqlite3"
    CatalogRepository(path)
    return path


def _seed_roll(connection: sqlite3.Connection) -> tuple[int, int]:
    source_id = _fixture(connection, "roll")
    import_id = int(
        connection.execute(
            "SELECT legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_id,),
        ).fetchone()[0]
    )
    return source_id, import_id


def _record(
    connection: sqlite3.Connection,
    *,
    probe_parser: bool = False,
):
    report = RetainedSourceReprojectionPreflightService().preflight(
        connection,
        probe_roll_parser=probe_parser,
    )
    assert len(report.records) == 1
    record = report.records[0]
    assert record.source_family == "roll"
    assert record.roll_expectedness_diagnostic is not None
    return record


@pytest.mark.parametrize(
    ("mutation", "row_count", "state"),
    (
        (
            "DELETE FROM roll_observations",
            0,
            DurableRollBaseEvidenceState.ABSENT,
        ),
        (
            "INSERT INTO roll_observations "
            "(account_context_id, character_id, claim_rank, kakera_value, observed_at, "
            "import_event_id) SELECT account_context_id, character_id, claim_rank, "
            "kakera_value, observed_at, import_event_id FROM roll_observations",
            2,
            DurableRollBaseEvidenceState.MULTIPLE,
        ),
        (
            "UPDATE discord_source_event_account_attributions SET account_name = 'Other'",
            1,
            DurableRollBaseEvidenceState.INCOHERENT,
        ),
    ),
)
def test_diagnostic_distinguishes_base_roll_failures(
    database_path,
    mutation,
    row_count,
    state,
):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        _seed_roll(connection)
        connection.execute(mutation)

        record = _record(connection)
        diagnostic = record.roll_expectedness_diagnostic
        assert diagnostic is not None
        assert diagnostic.base_roll_row_count == row_count
        assert diagnostic.base_roll_coherence is state
        assert diagnostic.durable_expectedness_state is RetainedRollDurableExpectednessState.UNKNOWN
        assert diagnostic.durable_expected_link_count is None
        assert record.eligibility is ReprojectionPreflightEligibility.UNKNOWN
        assert record.rejection_code == "expectations_unknown"
        connection.rollback()


@pytest.mark.parametrize(
    ("mutation", "matching", "other", "key_state", "expected_state", "expected_count"),
    (
        (
            "DELETE FROM harem_key_observations",
            0,
            0,
            DurableRollKeyEvidenceState.ABSENT,
            RetainedRollDurableExpectednessState.UNKNOWN,
            None,
        ),
        (
            None,
            1,
            0,
            DurableRollKeyEvidenceState.COHERENT_MATCH,
            RetainedRollDurableExpectednessState.FULLY_KNOWN,
            4,
        ),
        (
            "INSERT INTO harem_key_observations "
            "(account_context_id, character_id, character_name, normalized_character_name, "
            "key_type, key_count, kakera_value, observed_at, import_event_id) "
            "SELECT account_context_id, character_id, character_name, "
            "normalized_character_name, 'silver', key_count, kakera_value, observed_at, "
            "import_event_id FROM harem_key_observations",
            2,
            0,
            DurableRollKeyEvidenceState.MULTIPLE,
            RetainedRollDurableExpectednessState.UNKNOWN,
            None,
        ),
        (
            "UPDATE harem_key_observations SET character_id = NULL",
            0,
            1,
            DurableRollKeyEvidenceState.MISMATCHED,
            RetainedRollDurableExpectednessState.UNKNOWN,
            None,
        ),
    ),
)
def test_diagnostic_distinguishes_key_evidence_without_changing_expectedness(
    database_path,
    mutation,
    matching,
    other,
    key_state,
    expected_state,
    expected_count,
):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        source_id, _ = _seed_roll(connection)
        if mutation is not None:
            connection.execute(mutation)

        evidence = load_durable_roll_projection_expectation_evidence(connection, source_id)
        canonical = load_durable_projection_expectation_facts(connection, source_id)
        assert evidence.facts == canonical
        resolved = resolve_expected_projections(canonical)
        key_expectedness = next(
            item.expectedness
            for item in resolved.assessments
            if item.projection_kind == "catalog.roll_key"
        )
        assert key_expectedness is (
            Expectedness.EXPECTED
            if key_state is DurableRollKeyEvidenceState.COHERENT_MATCH
            else Expectedness.UNKNOWN
        )

        diagnostic = _record(connection).roll_expectedness_diagnostic
        assert diagnostic is not None
        assert diagnostic.base_roll_coherence is DurableRollBaseEvidenceState.COHERENT
        assert diagnostic.matching_key_row_count == matching
        assert diagnostic.nonmatching_or_ambiguous_key_row_count == other
        assert diagnostic.key_evidence_state is key_state
        assert diagnostic.durable_expectedness_state is expected_state
        assert diagnostic.durable_expected_link_count == expected_count
        connection.rollback()


@pytest.mark.parametrize(
    ("claim_rank", "kakera_value", "expected_count"),
    ((None, None, 2), (1, None, 3), (None, 100, 3), (1, 100, 4)),
)
def test_diagnostic_reports_optional_projection_presence_and_expected_count(
    database_path,
    claim_rank,
    kakera_value,
    expected_count,
):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        _seed_roll(connection)
        connection.execute(
            "UPDATE roll_observations SET claim_rank = ?, kakera_value = ?",
            (claim_rank, kakera_value),
        )

        diagnostic = _record(connection).roll_expectedness_diagnostic
        assert diagnostic is not None
        assert diagnostic.claim_rank_present is (claim_rank is not None)
        assert diagnostic.kakera_value_present is (kakera_value is not None)
        assert (
            diagnostic.durable_expectedness_state
            is RetainedRollDurableExpectednessState.FULLY_KNOWN
        )
        assert diagnostic.durable_expected_link_count == expected_count
        connection.rollback()


def test_parser_probe_reports_no_displayed_key_without_changing_durable_unknown(
    database_path,
):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        _seed_roll(connection)
        connection.execute("DELETE FROM harem_key_observations")
        connection.execute("UPDATE discord_source_events SET raw_text = ?", (NO_KEY_ROLL,))
        before = connection.total_changes

        record = _record(connection, probe_parser=True)
        diagnostic = record.roll_expectedness_diagnostic
        assert diagnostic is not None
        assert diagnostic.key_evidence_state is DurableRollKeyEvidenceState.ABSENT
        assert diagnostic.durable_expectedness_state is RetainedRollDurableExpectednessState.UNKNOWN
        assert diagnostic.parser_attempted is True
        assert diagnostic.parser_succeeded is True
        assert diagnostic.parsed_displayed_key_count is False
        assert diagnostic.parser_failure_reason is None
        assert diagnostic.parser_durable_expectedness_relationship is (
            RetainedRollParserDurableRelationship.DURABLE_UNKNOWN_PARSER_ABSENT
        )
        assert record.eligibility is ReprojectionPreflightEligibility.UNKNOWN
        assert record.rejection_code == "expectations_unknown"
        assert connection.total_changes == before
        connection.rollback()


@pytest.mark.parametrize(
    ("remove_durable_key", "key_state", "relationship", "eligibility"),
    (
        (
            False,
            DurableRollKeyEvidenceState.COHERENT_MATCH,
            RetainedRollParserDurableRelationship.DURABLE_EXPECTED_PARSER_DISPLAYED,
            ReprojectionPreflightEligibility.ELIGIBLE,
        ),
        (
            True,
            DurableRollKeyEvidenceState.ABSENT,
            RetainedRollParserDurableRelationship.DURABLE_UNKNOWN_PARSER_DISPLAYED,
            ReprojectionPreflightEligibility.UNKNOWN,
        ),
    ),
)
def test_parser_probe_reports_displayed_key_with_and_without_durable_key(
    database_path,
    remove_durable_key,
    key_state,
    relationship,
    eligibility,
):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        _seed_roll(connection)
        if remove_durable_key:
            connection.execute("DELETE FROM harem_key_observations")
        connection.execute("UPDATE discord_source_events SET raw_text = ?", (DISPLAYED_KEY_ROLL,))

        record = _record(connection, probe_parser=True)
        diagnostic = record.roll_expectedness_diagnostic
        assert diagnostic is not None
        assert diagnostic.key_evidence_state is key_state
        assert diagnostic.parser_succeeded is True
        assert diagnostic.parsed_displayed_key_count is True
        assert diagnostic.parser_durable_expectedness_relationship is relationship
        assert record.eligibility is eligibility
        if remove_durable_key:
            assert record.rejection_code == "expectations_unknown"
        connection.rollback()


def test_parser_probe_is_opt_in_and_expired_evidence_is_not_parsed(database_path):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        _seed_roll(connection)
        connection.execute(
            "UPDATE discord_source_events SET raw_text = '[moa:raw-evidence-expired:v1]', "
            "raw_evidence_expired_at = ?",
            (NOW,),
        )

        diagnostic = _record(connection, probe_parser=True).roll_expectedness_diagnostic
        assert diagnostic is not None
        assert diagnostic.raw_evidence_state is RetainedRollRawEvidenceState.EXPIRED
        assert diagnostic.parser_attempted is False
        assert diagnostic.parser_succeeded is False
        assert diagnostic.parsed_displayed_key_count is None
        assert diagnostic.parser_failure_reason is (
            RetainedRollParserProbeFailure.RAW_EVIDENCE_UNAVAILABLE
        )

        without_probe = _record(connection).roll_expectedness_diagnostic
        assert without_probe is not None
        assert without_probe.parser_failure_reason is RetainedRollParserProbeFailure.NOT_REQUESTED
        connection.rollback()


def test_parser_failure_and_incoherent_raw_evidence_are_bounded_and_private(database_path):
    private_text = "PRIVATE CHARACTER PRIVATE SERIES PRIVATE ACCOUNT"
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        _seed_roll(connection)
        connection.execute(
            "UPDATE discord_source_events SET raw_text = ?",
            (private_text,),
        )

        record = _record(connection, probe_parser=True)
        diagnostic = record.roll_expectedness_diagnostic
        assert diagnostic is not None
        assert diagnostic.raw_evidence_state is RetainedRollRawEvidenceState.RETAINED
        assert diagnostic.parser_attempted is True
        assert diagnostic.parser_succeeded is False
        assert diagnostic.parser_failure_reason is RetainedRollParserProbeFailure.PARSE_FAILED
        assert private_text not in repr(record)
        assert "PRIVATE" not in repr(record)

        connection.execute(
            "UPDATE discord_source_events SET raw_text = ?",
            (sqlite3.Binary(b"not-text"),),
        )
        incoherent = _record(connection, probe_parser=True).roll_expectedness_diagnostic
        assert incoherent is not None
        assert incoherent.raw_evidence_state is RetainedRollRawEvidenceState.INCOHERENT
        assert incoherent.parser_attempted is False
        assert incoherent.parser_failure_reason is (
            RetainedRollParserProbeFailure.RAW_EVIDENCE_UNAVAILABLE
        )
        connection.rollback()


def test_production_shaped_zero_gap_unknown_preflight_has_zero_key_explanation(
    database_path,
):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        _seed_roll(connection)
        connection.execute(
            "DELETE FROM discord_projection_links WHERE projection_kind = 'catalog.roll_key'"
        )
        connection.execute("UPDATE discord_projection_links SET generation_id = 2")
        connection.execute("DELETE FROM harem_key_observations")
        connection.commit()

    assert DataHealthService(database_path).find_projection_gaps() == ()

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        record = _record(connection)
        diagnostic = record.roll_expectedness_diagnostic
        assert diagnostic is not None
        assert record.eligibility is ReprojectionPreflightEligibility.UNKNOWN
        assert record.rejection_code == "expectations_unknown"
        assert diagnostic.base_roll_coherence is DurableRollBaseEvidenceState.COHERENT
        assert diagnostic.key_evidence_state is DurableRollKeyEvidenceState.ABSENT
        connection.rollback()


def test_historical_link_incoherence_remains_downstream_of_known_expectedness(database_path):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        _seed_roll(connection)
        connection.execute(
            "UPDATE discord_projection_links SET projection_slot = '{}' "
            "WHERE projection_kind = 'catalog.roll_key'"
        )

        record = _record(connection)
        diagnostic = record.roll_expectedness_diagnostic
        assert diagnostic is not None
        assert (
            diagnostic.durable_expectedness_state
            is RetainedRollDurableExpectednessState.FULLY_KNOWN
        )
        assert diagnostic.durable_expected_link_count == 4
        assert record.eligibility is ReprojectionPreflightEligibility.INELIGIBLE
        assert record.rejection_code == "historical_links_incoherent"
        connection.rollback()


def test_diagnostic_is_deterministic_and_performs_zero_logical_mutations(database_path):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        _seed_roll(connection)
        connection.execute("UPDATE discord_source_events SET raw_text = ?", (DISPLAYED_KEY_ROLL,))
        before_changes = connection.total_changes
        before_counts = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "discord_source_events",
                "roll_observations",
                "harem_key_observations",
                "discord_projection_links",
            )
        )

        first = RetainedSourceReprojectionPreflightService().preflight(
            connection, probe_roll_parser=True
        )
        second = RetainedSourceReprojectionPreflightService().preflight(
            connection, probe_roll_parser=True
        )
        without_probe = RetainedSourceReprojectionPreflightService().preflight(connection)

        assert first == second
        assert first.inventory_fingerprint == second.inventory_fingerprint
        assert first != without_probe
        assert first.inventory_fingerprint == without_probe.inventory_fingerprint
        assert connection.total_changes == before_changes
        assert (
            tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "discord_source_events",
                    "roll_observations",
                    "harem_key_observations",
                    "discord_projection_links",
                )
            )
            == before_counts
        )
        connection.rollback()


def test_existing_cli_surface_renders_only_fixed_diagnostic_fields(database_path):
    with connect(database_path) as connection:
        connection.execute("BEGIN")
        _seed_roll(connection)
        connection.execute("DELETE FROM harem_key_observations")
        connection.execute("UPDATE discord_source_events SET raw_text = ?", (NO_KEY_ROLL,))
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    result = CliRunner().invoke(
        main.app,
        [
            "catalog",
            "retained-source-reprojection-preflight",
            str(database_path),
            "--probe-roll-parser",
        ],
    )

    assert result.exit_code == 0
    for field in (
        "sourceEventId",
        "safeFamily",
        "baseRollRowCount",
        "baseRollCoherence",
        "matchingKeyRowCount",
        "nonmatchingOrAmbiguousKeyRowCount",
        "keyEvidenceState",
        "claimRankPresent",
        "kakeraValuePresent",
        "rawEvidenceState",
        "parserAttempted",
        "parserSucceeded",
        "parsedDisplayedKeyCount",
        "parserFailureReason",
        "parserDurableExpectednessRelationship",
        "durableExpectednessState",
        "durableExpectedLinkCount",
    ):
        assert field in result.stdout
    assert "durable_unknown_parser_absent" in result.stdout
    assert "Character" not in result.stdout
    assert "Series" not in result.stdout
    assert NO_KEY_ROLL not in result.stdout
