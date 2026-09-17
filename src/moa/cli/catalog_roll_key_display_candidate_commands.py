"""Direct registration for the read-only Roll key-display inventory command."""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from moa.services.roll_key_display_candidate_inventory_service import (
    RollKeyDisplayCandidateInventoryService,
    RollKeyDisplayInventoryMode,
    RollKeyDisplayInventoryReport,
)


def register_catalog_roll_key_display_candidate_command(
    catalog_app: typer.Typer,
    console: Console,
) -> None:
    @catalog_app.command("roll-key-display-candidates")
    def catalog_roll_key_display_candidates(
        database_path: Path = typer.Argument(
            ...,
            help="Explicit existing MOA SQLite database path.",
        ),
        limit: int = typer.Option(
            100,
            "--limit",
            min=1,
            max=1000,
            help="Maximum number of Roll observations to evaluate.",
        ),
        after_roll_observation_id: int = typer.Option(
            0,
            "--after-roll-observation-id",
            min=0,
            help="Resume after this durable roll observation ID.",
        ),
        audit: bool = typer.Option(
            False,
            "--audit",
            help="Include established observations as well as NULL candidates.",
        ),
        artifact_label: str | None = typer.Option(
            None,
            "--artifact-label",
            help="Optional short opaque label for the informational report.",
        ),
    ) -> None:
        """Inventory retained Roll key-display candidates without mutation."""
        try:
            report = RollKeyDisplayCandidateInventoryService(database_path).scan(
                mode=(
                    RollKeyDisplayInventoryMode.AUDIT
                    if audit
                    else RollKeyDisplayInventoryMode.CANDIDATES
                ),
                limit=limit,
                after_roll_observation_id=after_roll_observation_id,
                artifact_label=artifact_label,
            )
        except (OSError, ValueError, TypeError):
            console.print(
                "[red]Unable to open or inspect the explicit database path; "
                "no inventory result is available.[/red]"
            )
            raise typer.Exit(1) from None
        except Exception:
            console.print(
                "[red]Unable to generate the Roll key-display inventory; "
                "no result is available.[/red]"
            )
            raise typer.Exit(1) from None

        _render_roll_key_display_inventory(console, report)


def _render_roll_key_display_inventory(
    console: Console,
    report: RollKeyDisplayInventoryReport,
) -> None:
    console.print("Roll key-display candidate inventory")
    console.print(f"Mode: {report.mode.value}")
    console.print(f"Rows evaluated: {report.rows_evaluated}")
    console.print(f"Next cursor: {report.next_cursor}")
    console.print(f"More available: {'yes' if report.summary.more_available else 'no'}")
    console.print("Informational only: yes; re-evaluate before mutation: yes")

    summary_table = Table(title="Summary")
    summary_table.add_column("Measure")
    summary_table.add_column("Count", justify="right")
    summary = report.summary
    for label, value in (
        ("Total evaluated", summary.total_evaluated),
        ("NULL targets", summary.null_targets),
        ("Eligible proposed false", summary.eligible_proposed_false),
        ("Eligible proposed true", summary.eligible_proposed_true),
        ("Already established matching", summary.established_matching),
        ("Already established conflict", summary.established_conflict),
    ):
        summary_table.add_row(label, str(value))
    console.print(summary_table)

    status_table = Table(title="Status counts")
    status_table.add_column("Status")
    status_table.add_column("Count", justify="right")
    for status, count in sorted(summary.status_counts.items()):
        if count:
            status_table.add_row(status, str(count))
    console.print(status_table)

    row_table = Table(title="Roll observations")
    row_table.add_column("Source event ID", justify="right")
    row_table.add_column("Roll observation ID", justify="right")
    row_table.add_column("Status")
    row_table.add_column("Current")
    row_table.add_column("Proposed")
    row_table.add_column("Reason")
    for row in report.rows:
        row_table.add_row(
            "-" if row.source_event_id is None else str(row.source_event_id),
            str(row.roll_observation_id),
            row.candidate_status.value,
            _safe_presence(row.current_displayed_key_count_present),
            _safe_presence(row.proposed_value),
            row.failure_category or "-",
        )
    console.print(row_table)


def _safe_presence(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "present" if value else "absent"
