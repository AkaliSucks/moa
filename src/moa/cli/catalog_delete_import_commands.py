"""Direct registration for the catalog import-deletion command."""

import typer
from rich.console import Console

from moa.repositories.catalog_repository import ImportEventDeletionBlockedError
from moa.services.catalog_service import CatalogService


def register_catalog_delete_import_command(
    catalog_app: typer.Typer,
    console: Console,
) -> None:
    @catalog_app.command("delete-import")
    def catalog_delete_import(
        import_event_id: int,
        confirm: bool = typer.Option(False, "--confirm", help="Confirm the permanent deletion."),
    ) -> None:
        """Delete one mistaken import while preserving all other catalog data."""
        if not confirm:
            console.print(
                "[yellow]Declined unsafe invocation: this command permanently removes the "
                "selected unlinked local import event and its import-derived observations, "
                "may change derived/current reports, is not retention expiry or privacy "
                "erasure, and requires --confirm to proceed.[/yellow]"
            )
            raise typer.Exit(1)
        try:
            deleted = CatalogService().delete_import_event(import_event_id)
        except ImportEventDeletionBlockedError:
            console.print(
                "[red]Deletion blocked: this import belongs to durable/replayable source state.[/red]"
            )
            raise typer.Exit(1) from None
        if not deleted:
            console.print("[red]Import event not found.[/red]")
            raise typer.Exit(1)
        console.print(f"[green]Deleted import event {import_event_id}.[/green]")
