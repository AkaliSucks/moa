"""Typer commands for the character-key reference."""

import typer
from rich.console import Console
from rich.table import Table

from moa.services.key_service import KeyService


def build_key_app(console: Console) -> typer.Typer:
    """Build the character-key command tree using the CLI's shared console."""
    key_app = typer.Typer(help="Character key reference commands")

    @key_app.command("list")
    def list_keys() -> None:
        """List every character-key tier, including Chaos keys not present in an account."""
        table = Table(title="Character Key Tiers (universal reference)")
        table.add_column("Tier", style="green")
        table.add_column("Key counts", justify="right", style="cyan")
        table.add_column("Milestones")
        for tier in KeyService().all():
            key_counts = (
                f"{tier.minimum_key_count}-{tier.maximum_key_count}"
                if tier.maximum_key_count is not None
                else f"{tier.minimum_key_count}+"
            )
            table.add_row(tier.name, key_counts, str(len(tier.milestones)))
        console.print(table)
        console.print(
            "[dim]This is universal key knowledge. Account harem imports only show which tiers "
            "your characters currently have.[/dim]"
        )

    @key_app.command("show")
    def show_key(key_id: str) -> None:
        """Show every milestone for one character-key tier."""
        tier = KeyService().get(key_id)
        if tier is None:
            console.print("[red]Character key tier not found.[/red]")
            raise typer.Exit(1)
        key_counts = (
            f"{tier.minimum_key_count}-{tier.maximum_key_count}"
            if tier.maximum_key_count is not None
            else f"{tier.minimum_key_count}+"
        )
        console.print(f"[bold cyan]{tier.name}[/bold cyan] - Keys {key_counts}")
        console.print(f"[bold]Details:[/bold] {tier.description}")
        table = Table()
        table.add_column("Key count", justify="right", style="cyan")
        table.add_column("Unlocked effects")
        for milestone in tier.milestones:
            table.add_row(str(milestone.key_count), "\n".join(milestone.effects))
        console.print(table)

    return key_app
