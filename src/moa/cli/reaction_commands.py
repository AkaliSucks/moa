"""Typer commands for the Kakera reaction reference."""

import typer
from rich.console import Console
from rich.table import Table

from moa.services.reaction_service import ReactionService


def build_reaction_app(console: Console) -> typer.Typer:
    """Build the reaction command tree using the CLI's shared console."""
    reaction_app = typer.Typer(help="Kakera reaction commands")

    @reaction_app.command("list")
    def list_reactions() -> None:
        """List the known Kakera reaction types and baseline values."""
        table = Table(title="Kakera Reactions")
        table.add_column("Reaction", style="green")
        table.add_column("Value range", justify="right", style="cyan")
        table.add_column("Base average", justify="right")
        table.add_column("Power")

        for reaction in ReactionService().all():
            if reaction.minimum_value is None:
                value_range = "Variable"
            elif reaction.minimum_value == reaction.maximum_value:
                value_range = f"{reaction.minimum_value:,}"
            else:
                value_range = f"{reaction.minimum_value:,}-{reaction.maximum_value:,}"

            average = "-" if reaction.average_value is None else f"{reaction.average_value:,.1f}"
            table.add_row(reaction.name, value_range, average, reaction.power_cost_policy.title())

        console.print(table)

    @reaction_app.command("show")
    def show_reaction(reaction_id: str) -> None:
        """Show the baseline rules for one Kakera reaction type."""
        reaction = ReactionService().get(reaction_id)
        if reaction is None:
            console.print("[red]Kakera reaction not found.[/red]")
            raise typer.Exit(1)

        console.print(f"[bold cyan]{reaction.name}[/bold cyan]")
        console.print(f"[bold]Type:[/bold] {reaction.reaction_type}")
        console.print(f"[bold]Reaction power:[/bold] {reaction.power_cost_policy}")
        if reaction.average_value is not None:
            console.print(f"[bold]Base average:[/bold] {reaction.average_value:,.4f} Kakera")
        console.print(f"[bold]Details:[/bold] {reaction.description}")

    return reaction_app
