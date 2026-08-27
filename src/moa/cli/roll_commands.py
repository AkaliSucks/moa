"""Typer commands for browsing imported roll observations."""

from collections.abc import Callable

import typer
from rich.console import Console
from rich.table import Table

from moa.services.catalog_service import CatalogService
from moa.utils.display import format_mudae_kakera


def _format_optional_number(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _format_optional_rank(value: int | None) -> str:
    return "-" if value is None else f"#{value:,}"


def _format_optional_average_rank(value: float | None) -> str:
    return "-" if value is None else f"#{value:,.1f}"


def _format_optional_average_number(value: float | None) -> str:
    return "-" if value is None else f"{value:,.1f}"


def build_roll_app(
    console: Console,
    resolve_account_context: Callable[[str | None, str | None], tuple[str, str]],
) -> typer.Typer:
    """Build the Roll command subtree without resolving durable state eagerly."""
    roll_app = typer.Typer(help="Browse imported roll observations")

    @roll_app.command("recent")
    def recent_rolls(
        server: str | None = typer.Option(
            None, "--server", "-s", help="Your label for the Mudae server."
        ),
        account: str | None = typer.Option(
            None, "--account", "-a", help="Account whose rolls to show."
        ),
        limit: int = typer.Option(
            20, "--limit", "-n", min=1, help="Maximum number of recent rolls."
        ),
    ) -> None:
        """Show raw roll observations imported for one account context."""
        server, account = resolve_account_context(server, account)
        rolls = CatalogService().recent_rolls(server, account, limit)
        if not rolls:
            console.print("[yellow]No rolls imported for this server/account yet.[/yellow]")
            raise typer.Exit()
        table = Table(title=f"{account} - recent imported rolls")
        table.add_column("Observed (UTC)")
        table.add_column("Character", style="green")
        table.add_column("Series")
        table.add_column("Claim rank", justify="right")
        table.add_column("Kakera", justify="right", style="cyan")
        for roll in rolls:
            table.add_row(
                roll.observed_at.strftime("%Y-%m-%d %H:%M"),
                roll.character.name,
                roll.character.series,
                _format_optional_rank(roll.claim_rank),
                format_mudae_kakera(roll.kakera_value),
            )
        console.print(table)

    @roll_app.command("stats")
    def roll_statistics(
        server: str | None = typer.Option(
            None, "--server", "-s", help="Your label for the Mudae server."
        ),
        account: str | None = typer.Option(
            None, "--account", "-a", help="Account whose rolls to summarize."
        ),
    ) -> None:
        """Summarize imported roll history without estimating probabilities."""
        server, account = resolve_account_context(server, account)
        statistics = CatalogService().roll_statistics(server, account)
        if statistics.roll_count == 0:
            console.print("[yellow]No rolls imported for this server/account yet.[/yellow]")
            raise typer.Exit()

        table = Table(title=f"{account} - imported roll statistics")
        table.add_column("Metric", style="green")
        table.add_column("Observed value", justify="right", style="cyan")
        table.add_row("Imported rolls", f"{statistics.roll_count:,}")
        table.add_row("Lowest (best) claim rank", _format_optional_rank(statistics.best_claim_rank))
        table.add_row(
            "Average claim rank",
            "-" if statistics.average_claim_rank is None else f"#{statistics.average_claim_rank:,.1f}",
        )
        table.add_row(
            "Average Kakera value",
            "-" if statistics.average_kakera_value is None else f"{statistics.average_kakera_value:,.1f}",
        )
        table.add_row(
            "Highest Kakera value",
            _format_optional_number(statistics.highest_kakera_value),
        )
        console.print(table)
        console.print(
            "[dim]These are descriptive results from stored rolls only. They are not a full roll-pool "
            "or probability estimate.[/dim]"
        )

    @roll_app.command("compare")
    def compare_roll_statistics(
        left_server: str = typer.Option(..., "--left-server", help="First server label."),
        left_account: str = typer.Option(..., "--left-account", help="First account label."),
        right_server: str = typer.Option(..., "--right-server", help="Second server label."),
        right_account: str = typer.Option(..., "--right-account", help="Second account label."),
    ) -> None:
        """Compare descriptive imported-roll statistics between two account contexts."""
        service = CatalogService()
        left = service.roll_statistics(left_server, left_account)
        right = service.roll_statistics(right_server, right_account)
        if left.roll_count == 0 or right.roll_count == 0:
            missing = left_account if left.roll_count == 0 else right_account
            console.print(
                f"[yellow]No rolls imported for {missing} in the selected server/account context yet.[/yellow]"
            )
            raise typer.Exit()

        table = Table(title=f"{left.account_name} vs {right.account_name} - imported roll statistics")
        table.add_column("Metric", style="green")
        table.add_column(f"{left.account_name} ({left.server_name})", justify="right", style="cyan")
        table.add_column(f"{right.account_name} ({right.server_name})", justify="right", style="magenta")
        table.add_row("Imported rolls", f"{left.roll_count:,}", f"{right.roll_count:,}")
        table.add_row(
            "Lowest (best) claim rank",
            _format_optional_rank(left.best_claim_rank),
            _format_optional_rank(right.best_claim_rank),
        )
        table.add_row(
            "Average claim rank",
            _format_optional_average_rank(left.average_claim_rank),
            _format_optional_average_rank(right.average_claim_rank),
        )
        table.add_row(
            "Average Kakera value",
            _format_optional_average_number(left.average_kakera_value),
            _format_optional_average_number(right.average_kakera_value),
        )
        table.add_row(
            "Highest Kakera value",
            _format_optional_number(left.highest_kakera_value),
            _format_optional_number(right.highest_kakera_value),
        )
        console.print(table)
        console.print(
            "[dim]This compares imported observations only. It does not infer a complete roll pool, "
            "spawn rate, or long-term advantage.[/dim]"
        )

    return roll_app
