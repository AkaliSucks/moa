"""Direct registration for the catalog reset command."""

from collections.abc import Callable
from datetime import datetime
import shutil
from pathlib import Path

import typer
from rich.console import Console


def register_catalog_reset_command(
    catalog_app: typer.Typer,
    console: Console,
    database_path_provider: Callable[[], Path],
) -> None:
    @catalog_app.command("reset")
    def catalog_reset(
        confirm: bool = typer.Option(
            False,
            "--confirm",
            help="Delete the current catalog after making a timestamped backup.",
        ),
    ) -> None:
        """Reset imported catalog data while preserving the MOA configuration."""
        database_path = Path(database_path_provider())
        if not confirm:
            console.print(
                "[yellow]No changes made. This removes all imported catalog data but keeps your "
                "MOA config.[/yellow]"
            )
            console.print("Run `uv run moa catalog reset --confirm` after stopping the listener.")
            return

        if not database_path.exists():
            console.print("[green]No catalog database exists; it will be created on the next import.[/green]")
            return

        backup_path = database_path.with_name(
            f"{database_path.name}.bak-full-reset-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        suffix = 1
        while backup_path.exists():
            backup_path = database_path.with_name(
                f"{database_path.name}.bak-full-reset-"
                f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{suffix}"
            )
            suffix += 1
        shutil.copy2(database_path, backup_path)
        database_path.unlink()
        console.print("[green]Catalog database reset. MOA config was preserved.[/green]")
        console.print(f"Backup saved to: {backup_path}")
