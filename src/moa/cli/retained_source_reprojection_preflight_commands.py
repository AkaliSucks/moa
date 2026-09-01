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
    ) -> None:
        """Report a read-only retained-source reprojection preflight inventory."""
        connection: sqlite3.Connection | None = None
        try:
            connection = connect_read_only(database_path)
            connection.execute("BEGIN")
            report = RetainedSourceReprojectionPreflightService().preflight(connection)
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
    for total in sorted(report.family_totals, key=lambda item: item.source_family):
        family_table.add_row(
            total.source_family,
            str(total.total),
            str(total.eligible),
            str(total.ineligible),
            str(total.unknown),
        )
    console.print(family_table)

    rejection_table = Table(title="Rejection totals")
    rejection_table.add_column("Rejection code")
    rejection_table.add_column("Total", justify="right")
    for total in sorted(report.reason_totals, key=lambda item: item.rejection_code):
        rejection_table.add_row(total.rejection_code, str(total.total))
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
