import typer
from rich.console import Console
from rich.table import Table

from moa.services.badge_service import BadgeService
from moa.services.tower_service import TowerService

app = typer.Typer(help="MOA - Mudae Optimization Assistant")
tower_app = typer.Typer(help="Tower commands")
badge_app = typer.Typer(help="Kakera Badge commands")
console = Console()

app.add_typer(tower_app, name="tower")
app.add_typer(badge_app, name="badge")


@app.command()
def version():
    console.print("[cyan]MOA[/cyan] v0.1.0")


@badge_app.command("list")
def list_badges() -> None:
    """List the seven Kakera Badge definitions."""
    table = Table(title="Kakera Badges")
    table.add_column("Badge", style="green")
    table.add_column("Default base value", justify="right", style="cyan")
    table.add_column("Level IV highlight")

    for badge in BadgeService().all():
        table.add_row(
            badge.name,
            f"{badge.default_base_value:,}",
            badge.levels[-1].effects[-1],
        )

    console.print(table)


@badge_app.command("cost")
def badge_cost(
    badge_id: str,
    level: int,
    base_value: int = typer.Option(
        ..., "--base-value", "-b", help="Server-configured base badge value."
    ),
    ruby_iv_active: bool = typer.Option(
        False, "--ruby-iv", help="Apply Ruby IV's 25% discount."
    ),
) -> None:
    """Calculate one badge-level purchase cost for a server configuration."""
    service = BadgeService()
    try:
        cost = service.cost_for_level(
            badge_id,
            level,
            base_value,
            ruby_iv_active=ruby_iv_active,
        )
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    discount_label = " with Ruby IV" if ruby_iv_active else ""
    console.print(
        f"[green]{badge_id.strip().upper()} {level}[/green] costs "
        f"[cyan]{cost:,} Kakera[/cyan]{discount_label}."
    )


@tower_app.command("list")
def list_towers():
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
def show_tower(perk_id: int):
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


if __name__ == "__main__":
    app()
