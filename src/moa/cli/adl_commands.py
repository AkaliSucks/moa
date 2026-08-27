import typer
from rich.console import Console

from moa.services.catalog_service import CatalogService


def build_adl_app(console: Console) -> typer.Typer:
    adl_app = typer.Typer(help="Build complete antidisable series snapshots safely")

    @adl_app.command("begin")
    def adl_begin(
        server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
        account: str = typer.Option(..., "--account", "-a", help="Account whose `$adl` list is being scanned."),
    ) -> None:
        """Start a complete multi-page `$adl` scan."""
        try:
            scan = CatalogService().begin_antidisable_scan(server, account)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        console.print(
            f"[green]Started antidisable scan {scan.id}[/green] for [cyan]{scan.account_name}[/cyan].\n"
            "In Discord, run `$adl` and copy each full page. Import page 1 with:\n"
            f"[bold]moa import adl --scan {scan.id} --server {scan.server_name!r} "
            f"--account {scan.account_name!r} --clipboard[/bold]"
        )

    @adl_app.command("status")
    def adl_status(scan_id: int) -> None:
        """Show pages captured for an antidisable scan."""
        scan = CatalogService().harem_scan_progress(scan_id)
        if scan is None or scan.scan_kind != "antidisable":
            console.print("[red]Antidisable scan not found.[/red]")
            raise typer.Exit(1)
        expected = str(scan.expected_page_count) if scan.expected_page_count is not None else "unknown"
        captured = ", ".join(str(page) for page in scan.imported_pages) or "none"
        status = "complete" if scan.completed_at is not None else "in progress"
        console.print(
            f"[bold cyan]Antidisable scan {scan.id}[/bold cyan] — {scan.server_name} / {scan.account_name}\n"
            f"Pages: {captured} of {expected} · Status: {status}"
        )

    @adl_app.command("complete")
    def adl_complete(scan_id: int) -> None:
        """Validate and activate a fully imported `$adl` scan."""
        try:
            scan = CatalogService().complete_antidisable_scan(scan_id)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        console.print(
            f"[green]Antidisable scan {scan.id} is complete and active[/green] for "
            f"{scan.server_name} / {scan.account_name}."
        )

    return adl_app
