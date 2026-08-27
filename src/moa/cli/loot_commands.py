from collections.abc import Callable

import typer
from rich.console import Console
from rich.table import Table

from moa.services.kakeraloot_budget_service import KakeralootBudgetService
from moa.services.loot_service import KakeralootService


def build_loot_app(
    console: Console,
    resolve_account_context: Callable[[str | None, str | None], tuple[str, str]],
) -> typer.Typer:
    loot_app = typer.Typer(help="Kakeraloot reference commands")

    @loot_app.command("list")
    def list_loots() -> None:
        """List every known Kakeraloot reward, whether or not the account owns it."""
        table = Table(title="Kakeraloot Rewards (universal reference)")
        table.add_column("ID", style="cyan")
        table.add_column("Reward", style="green")
        table.add_column("Category")
        table.add_column("Guaranteed")
        for loot in KakeralootService().all():
            table.add_row(loot.id, loot.name, loot.category.title(), "Yes" if loot.guaranteed else "No")
        console.print(table)
        console.print(
            "[dim]This is the complete known reward list, not the account's current loot state. "
            "Reward weights and expected value are intentionally not modeled yet.[/dim]"
        )

    @loot_app.command("show")
    def show_loot(loot_id: str) -> None:
        """Show the reference rules for one possible Kakeraloot reward."""
        loot = KakeralootService().get(loot_id)
        if loot is None:
            console.print("[red]Kakeraloot reward not found.[/red]")
            raise typer.Exit(1)
        console.print(f"[bold cyan]{loot.name}[/bold cyan]")
        console.print(f"[bold]Category:[/bold] {loot.category.title()}")
        console.print(f"[bold]Guaranteed:[/bold] {'Yes' if loot.guaranteed else 'No'}")
        console.print(f"[bold]Unlocks after:[/bold] {', '.join(loot.unlock_prerequisites)}")
        console.print(f"[bold]Details:[/bold] {loot.description}")
        console.print(f"[bold]Progression:[/bold] {loot.progression_note}")

    @loot_app.command("next")
    def next_loot_spending_step(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose Kakeraloot state is shown."),
    ) -> None:
        """Show the next Quantity and Quality costs from imported server/account state."""
        server, account = resolve_account_context(server, account)
        plan = KakeralootBudgetService().plan(server, account)
        console.print(
            f"[bold cyan]{plan.account_name} - Kakeraloot spending readiness[/bold cyan]\n"
            f"{plan.status}"
        )
        if plan.kakera_balance is not None:
            console.print(f"Kakera balance: [cyan]{plan.kakera_balance:,}[/cyan] ($k)")
        if plan.missing_prerequisites:
            console.print("[yellow]Missing: " + ", ".join(plan.missing_prerequisites) + "[/yellow]")
            return
        if plan.loot_cost is not None and plan.affordable_loot_count is not None:
            console.print(
                f"Each $kl: [cyan]{plan.loot_cost:,}[/cyan] Kakera | "
                f"affordable now: [cyan]{plan.affordable_loot_count:,}[/cyan]"
            )
        if not plan.upgrades:
            return
        table = Table()
        table.add_column("Upgrade", style="green")
        table.add_column("Current", justify="right")
        table.add_column("Next", justify="right")
        table.add_column("Cost", justify="right", style="cyan")
        table.add_column("Affordable")
        for upgrade in plan.upgrades:
            affordability = "Yes" if upgrade.affordable else "No"
            if upgrade.remaining_kakera is not None:
                affordability += f" ({upgrade.remaining_kakera:,} left)"
            table.add_row(
                upgrade.name,
                str(upgrade.current_level),
                str(upgrade.next_level),
                f"{upgrade.cost:,}",
                affordability,
            )
        console.print(table)

    return loot_app
