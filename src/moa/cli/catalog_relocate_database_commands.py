"""Direct registration for the catalog database-relocation command."""

from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console

from moa.database.legacy_database_relocation import (
    DatabaseRelocationError,
    relocate_database,
)


def register_catalog_relocate_database_command(
    catalog_app: typer.Typer,
    console: Console,
    target_path_provider: Callable[[], Path],
) -> None:
    @catalog_app.command("relocate-database")
    def catalog_relocate_database(
        source: Path = typer.Argument(..., help="Explicit legacy MOA database path."),
        apply: bool = typer.Option(
            False,
            "--apply",
            help="Create the new database and retire the explicit source path.",
        ),
    ) -> None:
        """Move database authority to MOA's per-user application-data location."""
        target = Path(target_path_provider()).resolve(strict=False)
        resolved_source = source.expanduser().resolve(strict=False)
        console.print(f"Source: {resolved_source}")
        console.print(f"Target: {target}")
        console.print("[yellow]The Discord listener must be stopped before relocation.[/yellow]")
        console.print(
            "[yellow]Do not resume old MOA checkouts that write the legacy database after "
            "relocation; no cross-version synchronization is provided.[/yellow]"
        )
        if not apply:
            console.print(
                "[yellow]No changes made. Rerun with --apply after stopping the listener.[/yellow]"
            )
            return
        try:
            result = relocate_database(resolved_source, target)
        except DatabaseRelocationError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        console.print(f"[green]Database relocated to: {result.target}[/green]")
        console.print(f"Legacy source archived at: {result.source_archive}")
