"""Typer commands for the Mudae command and flag reference."""

import typer
from rich.console import Console
from rich.table import Table

from moa.services.command_service import CommandService


def build_command_app(console: Console) -> typer.Typer:
    """Build the command reference tree using the CLI's shared console."""
    command_app = typer.Typer(help="Mudae command and flag reference")

    @command_app.command("explain")
    def explain_mudae_command(
        query: str = typer.Argument(
            ..., help="Quoted Mudae query, such as '$mmwy= Re:Zero$--Some series'."
        ),
    ) -> None:
        """Explain combined Mudae flags and include/exclude search arguments."""
        try:
            parsed = CommandService().explain(query)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error

        console.print(f"[bold cyan]Command:[/bold cyan] ${parsed.command}")
        if parsed.arguments:
            console.print(f"[bold]Include:[/bold] {', '.join(parsed.arguments)}")
        if parsed.exclusions:
            console.print(f"[bold]Exclude:[/bold] {', '.join(parsed.exclusions)}")
        if not parsed.flags:
            console.print("[dim]No flags supplied.[/dim]")
            return

        table = Table(title="Mudae flags")
        table.add_column("Flag", style="cyan")
        table.add_column("Category", style="green")
        table.add_column("Meaning")
        table.add_column("Notes")
        for flag in parsed.flags:
            table.add_row(
                flag.token,
                flag.definition.category,
                flag.definition.meaning,
                flag.definition.notes or "",
            )
        console.print(table)

    @command_app.command("flags")
    def list_mudae_flags(
        category: str | None = typer.Option(
            None, "--category", "-c", help="Only show one flag category."
        ),
    ) -> None:
        """List the supported Mudae flag reference."""
        definitions = CommandService().all()
        if category:
            definitions = tuple(
                definition
                for definition in definitions
                if definition.category.casefold() == category.strip().casefold()
            )
        if not definitions:
            console.print("[yellow]No Mudae flags matched that category.[/yellow]")
            raise typer.Exit(1)
        table = Table(title="Mudae command flags")
        table.add_column("Flag", style="cyan")
        table.add_column("Category", style="green")
        table.add_column("Meaning")
        table.add_column("Notes")
        for definition in definitions:
            table.add_row(
                definition.token,
                definition.category,
                definition.meaning,
                definition.notes or "",
            )
        console.print(table)

    return command_app
