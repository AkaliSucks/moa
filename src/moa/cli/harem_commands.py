import typer
from rich.console import Console

from moa.services.catalog_service import CatalogService


def build_harem_app(console: Console) -> typer.Typer:
    harem_app = typer.Typer(help="Build complete keyed-harem snapshots safely")

    @harem_app.command("begin")
    def harem_begin(
        server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
        account: str = typer.Option(..., "--account", "-a", help="Account whose harem you are scanning."),
        scan_kind: str = typer.Option(
            "keys", "--kind", help="Scan `keys` with $mmyk or `owned` with $mmrkty+."
        ),
    ) -> None:
        """Start a new multi-page harem scan that activates only when complete."""
        try:
            scan = CatalogService().begin_harem_scan(server, account, scan_kind)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        import_command = "mm" if scan.scan_kind == "keys" else "mmr"
        mudae_command = "$mmyk" if scan.scan_kind == "keys" else "$mmrkty+"
        console.print(
            f"[green]Started {scan.scan_kind} harem scan {scan.id}[/green] for [cyan]{scan.account_name}[/cyan].\n"
            f"In Discord, run [bold]{mudae_command}[/bold] and copy each full page.\n"
            "Import each Mudae page with:\n"
            f"[bold]uv run moa import {import_command} --scan {scan.id} --server {scan.server_name!r} "
            f"--account {scan.account_name!r} --clipboard[/bold]"
        )

    @harem_app.command("status")
    def harem_status(scan_id: int) -> None:
        """Show pages captured for a harem scan."""
        scan = CatalogService().harem_scan_progress(scan_id)
        if scan is None:
            console.print("[red]Harem scan not found.[/red]")
            raise typer.Exit(1)
        expected = str(scan.expected_page_count) if scan.expected_page_count is not None else "unknown"
        captured = ", ".join(str(page) for page in scan.imported_pages) or "none"
        status = "complete" if scan.completed_at is not None else "in progress"
        console.print(
            f"[bold cyan]Harem scan {scan.id}[/bold cyan] — {scan.server_name} / {scan.account_name}\n"
            f"Pages: {captured} of {expected} · Status: {status}"
        )

    @harem_app.command("complete")
    def harem_complete(scan_id: int) -> None:
        """Validate and activate a fully imported harem scan."""
        try:
            scan = CatalogService().complete_harem_scan(scan_id)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        console.print(
            f"[green]Harem scan {scan.id} is complete and active[/green] for "
            f"{scan.server_name} / {scan.account_name}."
        )

    return harem_app
