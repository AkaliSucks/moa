"""Typer commands for the Kakera Badge reference."""

import typer
from rich.console import Console
from rich.table import Table

from moa.services.badge_service import BadgeService


def build_badge_app(console: Console) -> typer.Typer:
    """Build the Kakera Badge command tree using the CLI's shared console."""
    badge_app = typer.Typer(help="Kakera Badge commands")

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

    return badge_app
