"""Typer commands for the Kakera Tower reference."""

import typer
from rich.console import Console
from rich.table import Table

from moa.services.tower_service import TowerService


def build_tower_app(console: Console) -> typer.Typer:
    """Build the tower command tree using the CLI's shared console."""
    tower_app = typer.Typer(help="Tower commands")

    @tower_app.command("list")
    def list_towers() -> None:
        service = TowerService()
        table = Table(title="Kakera Tower Floors")
        table.add_column("#", justify="right", style="cyan")
        table.add_column("Floor", style="green")
        table.add_column("Category")
        table.add_column("First-tower effect")

        for perk in service.all():
            table.add_row(
                str(perk.id),
                perk.name,
                perk.category,
                perk.first_tower_effect,
            )

        console.print(table)

    @tower_app.command("show")
    def show_tower(perk_id: int) -> None:
        service = TowerService()

        perk = service.get(perk_id)

        if perk is None:
            console.print("[red]Tower floor not found.[/red]")
            raise typer.Exit(1)

        console.print(f"[bold cyan]Floor {perk.id}: {perk.name}[/bold cyan]")
        console.print(f"[bold]Category:[/bold] {perk.category}")
        console.print(f"[bold]Description:[/bold] {perk.description}")
        console.print(f"[bold]First tower:[/bold] {perk.first_tower_effect}")
        console.print(f"[bold]Progression:[/bold] {perk.progression_note}")
        if perk.initial_cap_level is not None:
            console.print(f"[bold]Initial cap:[/bold] {perk.initial_cap_level}")

    return tower_app
