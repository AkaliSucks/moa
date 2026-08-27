"""Typer commands for imported timer-backed action readiness."""

from collections.abc import Callable

import typer
from rich.console import Console
from rich.table import Table

from moa.services.action_service import ActionService


def build_action_app(
    console: Console,
    resolve_account_context: Callable[[str | None, str | None], tuple[str, str]],
) -> typer.Typer:
    """Build the Action command subtree without resolving durable state eagerly."""
    action_app = typer.Typer(help="Use fresh imported timers to show available actions")

    @action_app.command("now")
    def action_now(
        server: str | None = typer.Option(
            None, "--server", "-s", help="Your label for the Mudae server."
        ),
        account: str | None = typer.Option(
            None, "--account", "-a", help="Account whose latest $tu snapshot to use."
        ),
    ) -> None:
        """Show the action checklist supported by a recent imported `$tu` snapshot."""
        server, account = resolve_account_context(server, account)
        readiness = ActionService().readiness(server, account)
        console.print(f"[bold cyan]{readiness.account_name} - action readiness[/bold cyan]")
        console.print(readiness.status)
        if readiness.observed_at is not None:
            console.print(
                f"[dim]Snapshot age: {readiness.snapshot_age_seconds}s | observed "
                f"{readiness.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]"
            )
        if readiness.is_stale:
            return
        if readiness.available_actions:
            console.print("[green]Available when imported:[/green] " + ", ".join(readiness.available_actions))
        else:
            console.print("[yellow]No immediately available actions were reported.[/yellow]")
        if readiness.upcoming_events:
            table = Table(title="Upcoming timers from this snapshot")
            table.add_column("Event", style="green")
            table.add_column("In", justify="right", style="cyan")
            for label, minutes in readiness.upcoming_events:
                hours, remaining_minutes = divmod(minutes, 60)
                duration = f"{hours}h {remaining_minutes} min" if hours else f"{remaining_minutes} min"
                table.add_row(label, duration)
            console.print(table)

    return action_app
