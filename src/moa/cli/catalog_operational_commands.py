"""Typer commands for operational and historical catalog views."""

from collections.abc import Callable

import typer
from rich.console import Console
from rich.table import Table

from moa.services.catalog_service import CatalogService


def build_catalog_operational_app(
    console: Console,
    resolve_account_context: Callable[[str | None, str | None], tuple[str, str]],
    format_optional_rank: Callable[[int | None], str],
) -> typer.Typer:
    """Build the flat catalog operational/history command subtree."""
    catalog_operational_app = typer.Typer()

    @catalog_operational_app.command("imports")
    def catalog_imports(
        limit: int = typer.Option(
            20, "--limit", "-n", min=1, help="Number of local import-event summaries to display."
        ),
    ) -> None:
        """Show local import-event history."""
        service = CatalogService()
        try:
            imports = service.recent_imports(limit)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error

        if not imports:
            console.print("[yellow]No local import events recorded yet.[/yellow]")
            raise typer.Exit()

        table = Table(title="Recent local import-event history")
        table.add_column("ID", justify="right", style="cyan")
        table.add_column("Kind")
        table.add_column("Server", style="green")
        table.add_column("Source")
        table.add_column("Observed (UTC)")
        for import_event in imports:
            table.add_row(
                str(import_event.id),
                import_event.kind,
                import_event.server_name or "-",
                import_event.source,
                import_event.observed_at.strftime("%Y-%m-%d %H:%M"),
            )
        console.print(table)
        console.print(
            "[dim]Provenance: rows are limited local import-event summaries ordered by newest stored event ID; "
            "the server label is best-effort. Rows do not establish account scope, processing/replay/success "
            "status, freshness, completeness, or current state.[/dim]"
        )

    @catalog_operational_app.command("reactions")
    def catalog_reactions(
        server: str | None = typer.Option(None, "--server", "-s"),
        account: str | None = typer.Option(None, "--account", "-a"),
    ) -> None:
        """Show recent standalone Kakera payouts reported by Mudae."""
        server, account = resolve_account_context(server, account)
        reactions = CatalogService().kakera_reactions(server, account)
        if not reactions:
            console.print("[yellow]No reaction receipts imported for this server/account yet.[/yellow]")
            raise typer.Exit()
        table = Table(title=f"{account} - Kakera reaction payouts")
        table.add_column("Local import time (UTC)")
        table.add_column("Reaction")
        table.add_column("Kakera", justify="right", style="cyan")
        for reaction in reactions:
            table.add_row(reaction.observed_at.strftime("%Y-%m-%d %H:%M"), reaction.reaction_label, f"+{reaction.kakera_earned:,}")
        console.print(table)
        console.print(
            "[dim]Provenance: rows are up to 20 locally stored Mudae-reported receipt observations, newest stored "
            "first; they do not establish current reaction state, freshness/completeness, ownership, or successful "
            "causal action.[/dim]"
        )

    @catalog_operational_app.command("spheres")
    def catalog_spheres(
        server: str | None = typer.Option(None, "--server", "-s"),
        account: str | None = typer.Option(None, "--account", "-a"),
    ) -> None:
        """Show the latest imported `$oq` sphere payout."""
        server, account = resolve_account_context(server, account)
        observation = CatalogService().sphere_result(server, account)
        if observation is None:
            console.print("[yellow]No $oq sphere result imported for this server/account yet.[/yellow]")
            raise typer.Exit()
        snapshot = observation.snapshot
        table = Table(title=f"{account} - latest $oq sphere result")
        table.add_column("Sphere", style="green")
        table.add_column("Amount", justify="right", style="cyan")
        table.add_column("Free")
        for gain in snapshot.gains:
            table.add_row(gain.sphere_type, f"+{gain.amount:,}", "Yes" if gain.is_free else "No")
        console.print(table)
        stock = f"{snapshot.stock:,}" if snapshot.stock is not None else "unknown"
        console.print(
            f"Total gained: [cyan]+{snapshot.total_gained:,}[/cyan] spheres · Mudae-reported stock: "
            f"[cyan]{stock}[/cyan] · Recorded observation: {observation.observed_at.strftime('%Y-%m-%d %H:%M UTC')}"
        )
        console.print(
            "[dim]Provenance: this is the latest locally stored `$oq` observation for the selected server/account; "
            "it does not establish current stock/sphere state, freshness, availability/enabled state, ownership, "
            "completeness, successful action, or causality.[/dim]"
        )

    @catalog_operational_app.command("reaction-summary")
    def catalog_reaction_summary(
        server: str | None = typer.Option(None, "--server", "-s"),
        account: str | None = typer.Option(None, "--account", "-a"),
    ) -> None:
        """Summarize Kakera-reaction receipts stored for one account."""
        server, account = resolve_account_context(server, account)
        summary = CatalogService().kakera_reaction_summary(server, account)
        if summary.receipt_count == 0:
            console.print("[yellow]No reaction receipts imported for this server/account yet.[/yellow]")
            raise typer.Exit()
        console.print(f"[bold cyan]{account} - Kakera reaction summary[/bold cyan]\nReceipts: {summary.receipt_count:,} | Total: +{summary.total_kakera_earned:,} | Average: +{summary.average_kakera_earned:,.1f} | Highest: +{summary.highest_kakera_earned:,}")
        table = Table()
        table.add_column("Reaction")
        table.add_column("Receipts", justify="right")
        table.add_column("Kakera", justify="right", style="cyan")
        for label, count, total in summary.by_reaction:
            table.add_row(label, str(count), f"+{total:,}")
        console.print(table)
        console.print(
            "[dim]Provenance: values are descriptive aggregates of all currently stored Mudae-reported receipt rows "
            "for the selected server/account; no displayed time window or timestamps are provided. They do not "
            "establish current reaction state, freshness, complete history, ownership, successful action, causality, "
            "or dedup/replay assurance.[/dim]"
        )

    @catalog_operational_app.command("rank-history")
    def catalog_rank_history(
        name: str = typer.Argument(..., help="Character name."),
        series: str = typer.Option(..., "--series", help="Exact character series."),
        limit: int = typer.Option(20, "--limit", "-n", min=1, help="Maximum observations to display."),
    ) -> None:
        """Show MOA's directly imported global-rank history for one character."""
        history = CatalogService().rank_history(name, series, limit)
        if not history:
            console.print("[yellow]No rank observations imported for that character/series yet.[/yellow]")
            raise typer.Exit()
        table = Table(title=f"{name} - imported rank history")
        table.add_column("Observed (UTC)")
        table.add_column("Claim rank", justify="right", style="cyan")
        table.add_column("Like rank", justify="right", style="magenta")
        for observation in history:
            table.add_row(
                observation.observed_at.strftime("%Y-%m-%d %H:%M"),
                format_optional_rank(observation.claim_rank),
                format_optional_rank(observation.like_rank),
            )
        console.print(table)
        console.print("[dim]Only ranks MOA imported from Mudae are shown; this is not a complete rank timeline.[/dim]")

    return catalog_operational_app
