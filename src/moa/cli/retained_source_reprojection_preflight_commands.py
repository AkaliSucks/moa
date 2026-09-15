"""Direct registration for the retained-source reprojection preflight command."""

import sqlite3
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from moa.database.sqlite import connect_read_only
from moa.services.retained_source_reprojection_preflight_service import (
    ReprojectionPreflightFailure,
    RetainedSourceReprojectionPreflight,
    RetainedSourceReprojectionPreflightError,
    RetainedSourceReprojectionPreflightService,
)


_BOUNDED_FAILURES = frozenset(
    {
        ReprojectionPreflightFailure.SCHEMA_INVALID,
        ReprojectionPreflightFailure.CURRENT_GENERATION_INVALID,
        ReprojectionPreflightFailure.INVENTORY_NONDETERMINISTIC,
    }
)


def register_retained_source_reprojection_preflight_command(
    catalog_app: typer.Typer,
    console: Console,
) -> None:
    @catalog_app.command("retained-source-reprojection-preflight")
    def retained_source_reprojection_preflight(
        database_path: Path = typer.Argument(
            ...,
            help="Explicit existing MOA SQLite database path.",
        ),
        probe_roll_parser: bool = typer.Option(
            False,
            "--probe-roll-parser",
            help="Parse retained Roll source text and report only bounded diagnostic facts.",
        ),
    ) -> None:
        """Report a read-only retained-source reprojection preflight inventory."""
        connection: sqlite3.Connection | None = None
        try:
            connection = connect_read_only(database_path)
            connection.execute("BEGIN")
            report = RetainedSourceReprojectionPreflightService().preflight(
                connection,
                probe_roll_parser=probe_roll_parser,
            )
        except RetainedSourceReprojectionPreflightError as error:
            if error.reason in _BOUNDED_FAILURES:
                console.print(f"[red]Preflight failed: {error.reason.value}[/red]")
            else:
                console.print(
                    "[red]Unable to generate the retained-source reprojection preflight.[/red]"
                )
            raise typer.Exit(1) from None
        except (OSError, ValueError, TypeError, sqlite3.Error):
            console.print(
                "[red]Unable to open or inspect the explicit database path; "
                "no preflight result is available.[/red]"
            )
            raise typer.Exit(1) from None
        except Exception:
            console.print(
                "[red]Unable to generate the retained-source reprojection preflight; "
                "no result is available.[/red]"
            )
            raise typer.Exit(1) from None
        finally:
            if connection is not None:
                try:
                    if connection.in_transaction:
                        connection.rollback()
                except Exception:
                    pass
                try:
                    connection.close()
                except Exception:
                    pass

        _render_preflight(console, report)


def _render_preflight(
    console: Console,
    report: RetainedSourceReprojectionPreflight,
) -> None:
    console.print("Retained-source reprojection preflight")
    console.print(f"Current generation ID: {report.current_generation_id}")
    console.print(f"Hypothetical generation ID: {report.hypothetical_generation_id}")
    console.print(f"Inventory fingerprint: {report.inventory_fingerprint}", soft_wrap=True)

    family_table = Table(title="Family totals")
    family_table.add_column("Family")
    family_table.add_column("Total", justify="right")
    family_table.add_column("Eligible", justify="right")
    family_table.add_column("Ineligible", justify="right")
    family_table.add_column("Unknown", justify="right")
    for family_total in sorted(report.family_totals, key=lambda item: item.source_family):
        family_table.add_row(
            family_total.source_family,
            str(family_total.total),
            str(family_total.eligible),
            str(family_total.ineligible),
            str(family_total.unknown),
        )
    console.print(family_table)

    rejection_table = Table(title="Rejection totals")
    rejection_table.add_column("Rejection code")
    rejection_table.add_column("Total", justify="right")
    for reason_total in sorted(report.reason_totals, key=lambda item: item.rejection_code):
        rejection_table.add_row(reason_total.rejection_code, str(reason_total.total))
    console.print(rejection_table)

    source_table = Table(title="Source-event inventory")
    source_table.add_column("Source event ID", justify="right")
    source_table.add_column("Family")
    source_table.add_column("Eligibility")
    source_table.add_column("Rejection code")
    source_table.add_column("Expected links", justify="right")
    for record in sorted(report.records, key=lambda item: item.source_event_id):
        source_table.add_row(
            str(record.source_event_id),
            record.source_family,
            record.eligibility.value,
            record.rejection_code or "-",
            "-" if record.expected_link_count is None else str(record.expected_link_count),
        )
    console.print(source_table)

    roll_records = tuple(
        record
        for record in sorted(report.records, key=lambda item: item.source_event_id)
        if record.roll_expectedness_diagnostic is not None
    )
    if not roll_records:
        return

    for record in roll_records:
        diagnostic = record.roll_expectedness_diagnostic
        assert diagnostic is not None
        roll_table = Table(
            title=f"Retained Roll expectedness diagnostic: source {record.source_event_id}"
        )
        roll_table.add_column("Field")
        roll_table.add_column("Value")
        fields = (
            ("sourceEventId", str(record.source_event_id)),
            ("safeFamily", record.source_family),
            ("baseRollRowCount", str(diagnostic.base_roll_row_count)),
            ("baseRollCoherence", diagnostic.base_roll_coherence.value),
            ("matchingKeyRowCount", str(diagnostic.matching_key_row_count)),
            (
                "nonmatchingOrAmbiguousKeyRowCount",
                str(diagnostic.nonmatching_or_ambiguous_key_row_count),
            ),
            ("keyEvidenceState", diagnostic.key_evidence_state.value),
            ("claimRankPresent", _safe_boolean(diagnostic.claim_rank_present)),
            ("kakeraValuePresent", _safe_boolean(diagnostic.kakera_value_present)),
            ("rawEvidenceState", diagnostic.raw_evidence_state.value),
            ("parserAttempted", _safe_boolean(diagnostic.parser_attempted)),
            ("parserSucceeded", _safe_boolean(diagnostic.parser_succeeded)),
            (
                "parsedDisplayedKeyCount",
                _safe_boolean(diagnostic.parsed_displayed_key_count),
            ),
            (
                "parserFailureReason",
                diagnostic.parser_failure_reason.value
                if diagnostic.parser_failure_reason is not None
                else "none",
            ),
            (
                "parserDurableExpectednessRelationship",
                diagnostic.parser_durable_expectedness_relationship.value,
            ),
            ("durableExpectednessState", diagnostic.durable_expectedness_state.value),
            (
                "durableExpectedLinkCount",
                "not_available"
                if diagnostic.durable_expected_link_count is None
                else str(diagnostic.durable_expected_link_count),
            ),
        )
        for field, value in fields:
            roll_table.add_row(field, value)
        console.print(roll_table)


def _safe_boolean(value: bool | None) -> str:
    if value is None:
        return "not_available"
    return "true" if value else "false"
