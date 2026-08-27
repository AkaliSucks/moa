"""Typer commands for comparing imported server configuration."""

import typer
from rich.console import Console
from rich.table import Table

from moa.services.server_comparison_service import ServerComparisonService


def build_server_app(console: Console) -> typer.Typer:
    """Build the imported server comparison command tree."""
    server_app = typer.Typer(help="Compare imported server-wide configuration")

    @server_app.command("compare")
    def compare_servers(
        left: str = typer.Option(..., "--left", help="First imported server label."),
        right: str = typer.Option(..., "--right", help="Second imported server label."),
    ) -> None:
        """Compare the latest imported `$settings` snapshots for two servers."""
        try:
            comparison = ServerComparisonService().compare(left, right)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        table = Table(title=f"{comparison.left_server_name} vs {comparison.right_server_name}")
        table.add_column("Setting", style="green")
        table.add_column(comparison.left_server_name)
        table.add_column(comparison.right_server_name)
        table.add_column("Match", justify="center")
        for entry in comparison.entries:
            table.add_row(
                entry.label,
                entry.left_value,
                entry.right_value,
                "Yes" if entry.matches else "No",
            )
        console.print(table)
        console.print("[dim]This compares imported server configuration only; it does not compare player state.[/dim]")

    return server_app
