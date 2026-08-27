from collections.abc import Callable
from datetime import UTC

import typer
from rich.console import Console
from rich.table import Table

from moa.services.account_comparison_service import AccountComparisonService
from moa.services.account_overview_service import AccountOverviewService
from moa.services.action_service import ActionService
from moa.services.catalog_service import CatalogService
from moa.services.keyfarm_service import KeyFarmService
from moa.services.progress_service import ProgressService
from moa.utils.display import (
    format_mudae_kakera,
    format_mudae_key_marker,
    format_mudae_reaction_kakera,
)


def _format_observed_at(observed_at) -> str:
    """Render imported timestamps consistently as UTC in compact CLI output."""
    if observed_at.tzinfo is not None:
        observed_at = observed_at.astimezone(UTC)
    return observed_at.strftime("%Y-%m-%d %H:%M UTC")


def _format_age_seconds(seconds: int) -> str:
    """Render a non-negative snapshot age compactly for the activity dashboard."""
    remaining = max(0, seconds)
    minutes, seconds = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _format_optional_rank(value: int | None) -> str:
    return "-" if value is None else f"#{value:,}"


def build_account_app(
    console: Console,
    resolve_account_context: Callable[[str | None, str | None], tuple[str, str]],
) -> typer.Typer:
    account_app = typer.Typer(help="Imported account-state summary commands")

    @account_app.command("activity")
    def account_activity(
        server: str | None = typer.Option(None, "--server", "-s"),
        account: str | None = typer.Option(None, "--account", "-a"),
    ) -> None:
        """Show current imported activity signals without making spending decisions."""
        server, account = resolve_account_context(server, account)
        overview = AccountOverviewService().overview(server, account)
        readiness = ActionService().readiness(server, account)
        reactions = CatalogService().kakera_reaction_summary(server, account)
        recent_reactions = CatalogService().kakera_reactions(server, account, 1)
        recent_rolls = CatalogService().recent_rolls(server, account, 1)
        roll_stats = CatalogService().roll_statistics(server, account)
        recent_gains = CatalogService().recent_key_gains(server, account, 1)
        try:
            keyfarm = KeyFarmService().recommend(server, account)
        except ValueError:
            keyfarm = ()
        table = Table(title=f"{account} - activity dashboard")
        table.add_column("Area", style="green")
        table.add_column("Imported state")
        table.add_row("Kakera balance", "Not imported" if overview.kakera_balance is None else f"{overview.kakera_balance:,} ($k)")
        table.add_row("Timer status", readiness.status)
        if readiness.observed_at is None:
            table.add_row("Timer snapshot", "Not imported")
        else:
            age = (
                "age unavailable"
                if readiness.snapshot_age_seconds is None
                else f"{_format_age_seconds(readiness.snapshot_age_seconds)} old"
            )
            table.add_row("Timer snapshot", f"{age} | {_format_observed_at(readiness.observed_at)}")
        table.add_row("Available actions", ", ".join(readiness.available_actions) or "None / refresh $tu")
        table.add_row("Reaction receipts", f"{reactions.receipt_count:,} | +{reactions.total_kakera_earned:,} Kakera")
        if recent_reactions:
            latest_reaction = recent_reactions[0]
            table.add_row(
                "Latest reaction",
                f"{format_mudae_reaction_kakera(latest_reaction.kakera_earned, latest_reaction.reaction_label)} | "
                f"{_format_observed_at(latest_reaction.observed_at)}",
            )
        else:
            table.add_row("Latest reaction", "None imported")
        if recent_rolls:
            latest_roll = recent_rolls[0]
            table.add_row(
                "Latest roll",
                f"{latest_roll.character.name} | {format_mudae_kakera(latest_roll.kakera_value)} | "
                f"{_format_optional_rank(latest_roll.claim_rank)} | {_format_observed_at(latest_roll.observed_at)}",
            )
        else:
            table.add_row("Latest roll", "None imported")
        table.add_row(
            "Roll sample",
            "Not imported" if roll_stats.roll_count == 0 else f"{roll_stats.roll_count:,} rolls | avg {roll_stats.average_kakera_value:,.1f} Kakera | best {_format_optional_rank(roll_stats.best_claim_rank)}",
        )
        table.add_row(
            "Badges",
            "Not imported" if overview.kakera_balance is None else f"{overview.max_badge_count}/7 maxed",
        )
        table.add_row("Tower", "Not imported" if overview.tower_level is None else f"Level {overview.tower_level}; next floor shortfall {overview.tower_shortfall:,} Kakera")
        table.add_row(
            "Wishlist",
            "Not imported" if overview.wishlist_count is None else f"{overview.wishlist_count}/{overview.wishlist_capacity} wishes; {overview.starwish_count}/{overview.starwish_capacity} Starwishes",
        )
        table.add_row(
            "Kakeraloots",
            "Not fully observed"
            if overview.quantity_level is None
            or overview.quality_level is None
            or overview.loot_usage_count is None
            else f"Quantity {overview.quantity_level}; Quality {overview.quality_level}; {overview.loot_usage_count:,} uses",
        )
        table.add_row(
            "Disable list",
            "Not imported" if overview.disable_slots_used is None else f"{overview.disable_slots_used}/{overview.disable_slots_capacity} slots used",
        )
        table.add_row("Keyed harem", f"{overview.keyed_harem_count:,} imported characters")
        if keyfarm:
            target = keyfarm[0]
            table.add_row(
                "Top key-farm target",
                f"{target.character_name} | {format_mudae_kakera(target.kakera_value)} | "
                f"{format_mudae_key_marker(target.key_type, target.key_count)} | {target.wishlist_status}",
            )
        else:
            table.add_row("Top key-farm target", "No eligible valued harem entry imported")
        if recent_gains:
            gain = recent_gains[0]
            table.add_row(
                "Latest key observation",
                f"{gain.character_name} | {format_mudae_key_marker(gain.key_type, gain.key_count)} | "
                f"{_format_observed_at(gain.observed_at)}",
            )
        else:
            table.add_row("Latest key observation", "None imported from rolls")
        console.print(table)
        if readiness.upcoming_events:
            console.print("[dim]Upcoming: " + " · ".join(f"{name} in {minutes} min" for name, minutes in readiness.upcoming_events) + "[/dim]")

    @account_app.command("overview")
    def account_overview(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose imported state to summarize."),
    ) -> None:
        """Show one read-only summary of the latest imported account state."""
        server, account = resolve_account_context(server, account)
        overview = AccountOverviewService().overview(server, account)
        table = Table(title=f"{overview.account_name} - account overview")
        table.add_column("Area", style="green")
        table.add_column("Latest imported state")
        balance = (
            f"{overview.kakera_balance:,} Kakera ({overview.kakera_balance_source})"
            if overview.kakera_balance is not None
            else "Not imported"
        )
        table.add_row("Kakera balance", balance)
        if overview.personal_rare_multiplier is None:
            rare_state = "Not imported"
        elif overview.personal_rare_multiplier == 0:
            rare_state = (
                f"0 (uses server $setrare {overview.server_rare_multiplier})"
                if overview.server_rare_multiplier is not None
                else "0 (uses server $setrare; server settings not imported)"
            )
        else:
            server_value = (
                str(overview.server_rare_multiplier)
                if overview.server_rare_multiplier is not None
                else "not imported"
            )
            rare_state = (
                f"{overview.personal_rare_multiplier} ($personalrare override; "
                f"server $setrare {server_value})"
            )
        table.add_row("Claimed-roll rarity", rare_state)
        table.add_row("Badges", f"{overview.max_badge_count}/{overview.badge_count} maxed" if overview.badge_count else "Not imported")
        if overview.tower_level is None:
            table.add_row("Tower", "Not imported")
        else:
            shortfall = (
                f"; shortfall {overview.tower_shortfall:,} Kakera"
                if overview.tower_shortfall is not None
                else ""
            )
            table.add_row(
                "Tower",
                f"Level {overview.tower_level}, {overview.completed_towers} completed; "
                f"next floor {overview.next_tower_cost:,} Kakera{shortfall}",
            )
        if overview.kakeraloots_unlocked is False:
            loot_state = "Locked: requires " + " and ".join(overview.missing_kakeraloot_prerequisites)
        elif overview.has_kakeraloots is False:
            loot_state = overview.kakeraloot_status_note or "No Kakeraloots bought"
        elif (
            overview.quantity_level is not None
            and overview.quality_level is not None
            and overview.loot_usage_count is not None
        ):
            loot_state = (
                f"Quantity {overview.quantity_level} | Quality {overview.quality_level} | "
                f"{overview.loot_usage_count:,} uses"
            )
        else:
            loot_state = "Not fully observed"
        table.add_row("Kakeraloots", loot_state)
        wishlist_state = (
            f"{overview.wishlist_count}/{overview.wishlist_capacity} wishes | "
            f"{overview.starwish_count}/{overview.starwish_capacity} Starwishes"
            if overview.wishlist_count is not None
            else "Not imported"
        )
        table.add_row("Wishlist", wishlist_state)
        disable_state = (
            f"{overview.disable_slots_used}/{overview.disable_slots_capacity} slots used"
            if overview.disable_slots_used is not None
            else "Not imported"
        )
        table.add_row("Disablelist", disable_state)
        table.add_row("Keyed harem", f"{overview.keyed_harem_count} imported characters")
        console.print(table)
        console.print(
            "[dim]Each source is retained separately. Kakera balance comes only from the latest imported $k snapshot.[/dim]"
        )

    @account_app.command("compare")
    def account_compare(
        left_server: str = typer.Option(..., "--left-server", help="First imported server label."),
        left_account: str = typer.Option(..., "--left-account", help="First account name."),
        right_server: str = typer.Option(..., "--right-server", help="Second imported server label."),
        right_account: str = typer.Option(..., "--right-account", help="Second account name."),
    ) -> None:
        """Compare the latest imported state for two account contexts."""
        comparison = AccountComparisonService().compare(
            left_server, left_account, right_server, right_account
        )
        table = Table(
            title=(
                f"{comparison.left_account_name} ({comparison.left_server_name}) vs "
                f"{comparison.right_account_name} ({comparison.right_server_name})"
            )
        )
        table.add_column("Area", style="green")
        table.add_column(comparison.left_account_name)
        table.add_column(comparison.right_account_name)
        for row in comparison.rows:
            table.add_row(row.label, row.left_value, row.right_value)
        console.print(table)
        console.print("[dim]Only imported state is compared; 'Not imported' is never treated as zero.[/dim]")

    @account_app.command("progress")
    def account_progress(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose imported $k history to measure."),
    ) -> None:
        """Measure Kakera progression from the account's timestamped `$k` imports."""
        server, account = resolve_account_context(server, account)
        progress = ProgressService().kakera_progress(server, account)
        if not progress.observations:
            console.print("[yellow]No $k snapshots imported for this server/account yet.[/yellow]")
            raise typer.Exit()
        table = Table(title=f"{progress.account_name} - Kakera progression")
        table.add_column("Observed (UTC)")
        table.add_column("Kakera", justify="right", style="cyan")
        table.add_column("Max badges", justify="right")
        for point in progress.observations:
            table.add_row(
                point.observed_at.strftime("%Y-%m-%d %H:%M"),
                f"{point.kakera_balance:,}",
                str(point.max_badge_count),
            )
        console.print(table)
        if progress.kakera_change is None:
            console.print("[dim]Import another $k snapshot later to measure a change rate.[/dim]")
            return
        hours, remaining_seconds = divmod(progress.elapsed_seconds or 0, 3_600)
        minutes = remaining_seconds // 60
        rate = f"{progress.kakera_per_day:,.1f} Kakera/day" if progress.kakera_per_day is not None else "N/A"
        console.print(
            f"[bold]Change:[/bold] {progress.kakera_change:+,} Kakera over {hours}h {minutes} min | "
            f"[bold]Measured rate:[/bold] {rate}"
        )

    return account_app
