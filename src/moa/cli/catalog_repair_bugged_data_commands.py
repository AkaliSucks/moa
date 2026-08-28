"""Direct registration for the catalog bugged-data repair command."""

from collections.abc import Callable
from datetime import datetime
import shutil
from pathlib import Path

import typer
from rich.console import Console

from moa.services.catalog_service import CatalogService


def register_catalog_repair_bugged_data_command(
    catalog_app: typer.Typer,
    console: Console,
    database_path_provider: Callable[[], Path],
) -> None:
    @catalog_app.command("repair-bugged-data")
    def catalog_repair_bugged_data(
        apply: bool = typer.Option(
            False,
            "--apply",
            help="Apply the targeted cleanup. Without this flag, only a dry-run report is shown.",
        ),
    ) -> None:
        """Remove known timer-as-roll imports and orphaned malformed characters."""
        service = CatalogService()
        import_count, character_count = service.inspect_bugged_imports()
        if not apply:
            console.print(
                f"Found {import_count} suspicious import event(s) and "
                f"{character_count} suspicious character row(s)."
            )
            console.print(
                "[yellow]Dry run only; no database changes were made. "
                "Stop the Discord listener, then rerun with --apply to clean these candidates.[/yellow]"
            )
            return

        if import_count == 0 and character_count == 0:
            console.print("[green]No targeted bugged data was found; nothing changed.[/green]")
            return

        database_path = Path(database_path_provider())
        backup_path = database_path.with_name(
            f"{database_path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        suffix = 1
        while backup_path.exists():
            backup_path = database_path.with_name(
                f"{database_path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{suffix}"
            )
            suffix += 1
        shutil.copy2(database_path, backup_path)

        cleaned_imports, deleted_characters = service.repair_bugged_imports()
        console.print(
            f"[green]Cleaned {cleaned_imports} suspicious import event(s) "
            "(timer misimports removed; stale character links repaired).[/green]"
        )
        console.print(f"[green]Deleted {deleted_characters} orphaned character row(s).[/green]")
        console.print(f"Backup saved to: {backup_path}")
