"""Typer commands for imported-state key-farm recommendations."""

from collections.abc import Callable

import typer
from rich.console import Console
from rich.table import Table

from moa.services.keyfarm_service import KeyFarmService
from moa.utils.display import format_mudae_kakera, format_mudae_key_marker


def build_recommend_app(
    console: Console,
    resolve_account_context: Callable[[str | None, str | None], tuple[str, str]],
) -> typer.Typer:
    """Build the Recommend command subtree without resolving durable state eagerly."""
    recommend_app = typer.Typer(help="Make transparent recommendations from imported Mudae state")

    @recommend_app.command("keyfarm")
    def recommend_keyfarm(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose harem to prioritize."),
        limit: int = typer.Option(15, "--limit", "-n", min=1, help="Number of recommendations to show."),
    ) -> None:
        """Rank key-farm targets from imported value, wish bonuses, and key chance."""
        server, account = resolve_account_context(server, account)
        try:
            recommendations = KeyFarmService().recommend(server, account)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        if not recommendations:
            console.print("[yellow]No valued, currently eligible harem entries were found.[/yellow]")
            raise typer.Exit()

        table = Table(title=f"{account} - key-farm recommendations")
        table.add_column("#", justify="right", style="cyan")
        table.add_column("Character", style="green")
        table.add_column("Kakera", justify="right", style="magenta")
        table.add_column("Keys", justify="right")
        table.add_column("Boost")
        table.add_column("Spawn", justify="right")
        table.add_column("Key chance", justify="right")
        table.add_column("Opportunity", justify="right", style="yellow")
        for index, entry in enumerate(recommendations[:limit], start=1):
            table.add_row(
                str(index),
                entry.character_name,
                format_mudae_kakera(entry.kakera_value),
                format_mudae_key_marker(entry.key_type, entry.key_count),
                entry.wishlist_status,
                f"{entry.relative_spawn_multiplier:.2f}x",
                f"+{entry.additional_key_chance_percent}%",
                f"{entry.value_weighted_opportunity_index:,.0f}",
            )
        console.print(table)
        console.print(
            "[dim]Opportunity = current Kakera value × relative wish/Starwish spawn multiplier × "
            "the imported extra-key multiplier. It is a relative priority, not an absolute drop-rate forecast. "
            "Directly observed unavailable characters are excluded; unobserved characters remain eligible.[/dim]"
        )

    return recommend_app
