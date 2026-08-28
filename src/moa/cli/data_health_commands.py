import sqlite3
from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from moa.models.retention import RetentionExpiryResult
from moa.repositories.data_health_repository import DataHealthSchemaError
from moa.repositories.retention_eligibility_repository import RetentionEligibilityDataError
from moa.repositories.retention_expiry_repository import RetentionExpiryError
from moa.services.data_health_service import DataHealthService
from moa.services.retention_eligibility_service import RetentionEligibilityService
from moa.services.retention_expiry_service import RetentionExpiryService


def build_data_health_app(
    console: Console,
    database_path_provider: Callable[[], Path],
) -> typer.Typer:
    """Build the catalog Data Health command subtree."""
    data_health_app = typer.Typer(help="Report read-only local catalog health findings")

    @data_health_app.command("orphans")
    def catalog_data_health_orphans() -> None:
        """Report physical and audited logical orphan findings without repairs."""
        try:
            findings = DataHealthService(Path(database_path_provider())).find_orphans()
        except (DataHealthSchemaError, OSError, ValueError, sqlite3.Error) as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error

        if not findings:
            console.print("No data-health findings.")
            return

        table = Table(title="Data-health orphan findings")
        table.add_column("Check ID", style="cyan")
        table.add_column("Category")
        table.add_column("Entity", style="green")
        table.add_column("Local identifier")
        table.add_column("Reason")
        for finding in findings:
            table.add_row(
                finding.check_id,
                finding.category,
                finding.entity,
                str(finding.local_identifier),
                finding.reason,
            )
        console.print(table)
        console.print(f"Total findings: {len(findings)}")

    @data_health_app.command("impossible-identities")
    def catalog_data_health_impossible_identities() -> None:
        """Report impossible identity findings without repairs."""
        try:
            findings = DataHealthService(
                Path(database_path_provider())
            ).find_impossible_identities()
        except (DataHealthSchemaError, OSError, ValueError, sqlite3.Error) as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error

        if not findings:
            console.print("No data-health findings.")
            return

        table = Table(title="Data-health impossible identity findings")
        table.add_column("Check ID", style="cyan")
        table.add_column("Category")
        table.add_column("Entity", style="green")
        table.add_column("Local identifier")
        table.add_column("Reason")
        for finding in findings:
            table.add_row(
                finding.check_id,
                finding.category,
                finding.entity,
                str(finding.local_identifier),
                finding.reason,
            )
        console.print(table)
        console.print(f"Total findings: {len(findings)}")

    @data_health_app.command("duplicates")
    def catalog_data_health_duplicates() -> None:
        """Report duplicate durable business identities without repairs."""
        try:
            findings = DataHealthService(Path(database_path_provider())).find_duplicates()
        except (DataHealthSchemaError, OSError, ValueError, sqlite3.Error) as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error

        if not findings:
            console.print("No data-health findings.")
            return

        table = Table(title="Data-health duplicate findings")
        table.add_column("Check ID", style="cyan")
        table.add_column("Category")
        table.add_column("Entity", style="green")
        table.add_column("Local identifier")
        table.add_column("Reason")
        for finding in findings:
            table.add_row(
                finding.check_id,
                finding.category,
                finding.entity,
                str(finding.local_identifier),
                finding.reason,
            )
        console.print(table)
        console.print(f"Total findings: {len(findings)}")

    @data_health_app.command("projection-gaps")
    def catalog_data_health_projection_gaps() -> None:
        """Report completed projection links owned by non-succeeded source events."""
        try:
            findings = DataHealthService(
                Path(database_path_provider())
            ).find_projection_gaps()
        except (DataHealthSchemaError, OSError, ValueError, sqlite3.Error) as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error

        if not findings:
            console.print("No data-health findings.")
            return

        table = Table(title="Data-health projection-gap findings")
        table.add_column("Check ID", style="cyan")
        table.add_column("Category")
        table.add_column("Entity", style="green")
        table.add_column("Local identifier")
        table.add_column("Reason")
        for finding in findings:
            table.add_row(
                finding.check_id,
                finding.category,
                finding.entity,
                str(finding.local_identifier),
                finding.reason,
            )
        console.print(table)
        console.print(f"Total findings: {len(findings)}")

    @data_health_app.command("retention")
    def catalog_data_health_retention(
        apply: bool = typer.Option(
            False,
            "--apply",
            help=(
                "Intentionally remove eligible raw evidence. Historical raw repair/reparse may "
                "become unavailable; this does not guarantee forensic secure erasure."
            ),
        ),
    ) -> None:
        """Report retention eligibility, or explicitly apply logical evidence expiry."""
        if apply:
            try:
                result = RetentionExpiryService(Path(database_path_provider())).apply()
            except (
                DataHealthSchemaError,
                RetentionEligibilityDataError,
                RetentionExpiryError,
                OSError,
                ValueError,
                TypeError,
                sqlite3.Error,
            ):
                console.print(
                    "[red]Unable to apply raw-evidence retention expiry; "
                    "no success is claimed.[/red]"
                )
                raise typer.Exit(1) from None

            try:
                _render_retention_expiry_result(console, result)
            except Exception:
                typer.echo(
                    "Retention expiry committed, but presentation failed; do not re-run automatically.",
                    err=True,
                )
                raise typer.Exit(1) from None
            return

        try:
            report = RetentionEligibilityService(Path(database_path_provider())).report()
        except (
            DataHealthSchemaError,
            RetentionEligibilityDataError,
            OSError,
            ValueError,
            TypeError,
            sqlite3.Error,
        ):
            console.print("[red]Unable to generate the retention eligibility report.[/red]")
            raise typer.Exit(1) from None

        console.print(
            f"[bold cyan]Retention eligibility[/bold cyan] — as of {report.as_of.isoformat()} "
            f"(cutoff {report.cutoff.isoformat()})"
        )
        table = Table()
        table.add_column("Category", style="cyan")
        table.add_column("Eligible", justify="right")
        table.add_column("Retained/blocked", justify="right")
        table.add_column("Already expired", justify="right")
        table.add_column("Absent", justify="right")
        table.add_column("Oldest eligible anchor")
        table.add_column("Newest eligible anchor")
        table.add_column("Blocked reasons")
        for category in report.categories:
            reasons = ", ".join(
                f"{reason}={count}" for reason, count in category.blocked_reason_counts
            )
            table.add_row(
                category.category,
                str(category.eligible_count),
                str(category.retained_blocked_count),
                str(category.already_expired_count),
                str(category.absent_count),
                category.oldest_eligible_anchor.isoformat()
                if category.oldest_eligible_anchor is not None
                else "-",
                category.newest_eligible_anchor.isoformat()
                if category.newest_eligible_anchor is not None
                else "-",
                reasons or "-",
            )
        console.print(table)

    return data_health_app


def _render_retention_expiry_result(
    console: Console,
    result: RetentionExpiryResult,
) -> None:
    console.print(
        f"[bold cyan]Retention expiry applied[/bold cyan] — committed at "
        f"{result.apply_as_of.isoformat()} (cutoff {result.cutoff.isoformat()})"
    )
    table = Table()
    table.add_column("Category", style="cyan")
    table.add_column("Recomputed eligible", justify="right")
    table.add_column("Expired", justify="right")
    table.add_column("Retained/blocked", justify="right")
    table.add_column("Already expired", justify="right")
    table.add_column("Absent", justify="right")
    table.add_column("Blocked reasons")
    for category in result.categories:
        reasons = ", ".join(
            f"{reason}={count}" for reason, count in category.blocked_reason_counts
        )
        table.add_row(
            category.category,
            str(category.recomputed_eligible_count),
            str(category.expired_count),
            str(category.retained_blocked_count),
            str(category.already_expired_count),
            str(category.absent_count),
            reasons or "-",
        )
    console.print(table)
