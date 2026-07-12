from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.parser.message_router import MudaeMessageRouter
from moa.services.badge_service import BadgeService
from moa.services.account_overview_service import AccountOverviewService
from moa.services.account_comparison_service import AccountComparisonService
from moa.services.action_service import ActionService
from moa.services.automatic_import_service import AutomaticImportService
from moa.services.catalog_service import CatalogService
from moa.services.keyfarm_service import KeyFarmService
from moa.services.key_service import KeyService
from moa.services.key_progress_service import KeyProgressService
from moa.services.kakeraloot_budget_service import KakeralootBudgetService
from moa.services.loot_service import KakeralootService
from moa.services.reaction_service import ReactionService
from moa.services.progress_service import ProgressService
from moa.services.roll_analysis_service import RollAnalysisService
from moa.services.server_comparison_service import ServerComparisonService
from moa.services.tower_service import TowerService

app = typer.Typer(help="MOA - Mudae Optimization Assistant")
tower_app = typer.Typer(help="Tower commands")
badge_app = typer.Typer(help="Kakera Badge commands")
reaction_app = typer.Typer(help="Kakera reaction commands")
loot_app = typer.Typer(help="Kakeraloot reference commands")
key_app = typer.Typer(help="Character key reference commands")
roll_app = typer.Typer(help="Browse imported roll observations")
account_app = typer.Typer(help="Imported account-state summary commands")
action_app = typer.Typer(help="Use fresh imported timers to show available actions")
parse_app = typer.Typer(help="Parse copied Mudae bot output")
import_app = typer.Typer(help="Save parsed Mudae data to the local catalog")
catalog_app = typer.Typer(help="Browse MOA's local character catalog")
harem_app = typer.Typer(help="Build complete keyed-harem snapshots safely")
recommend_app = typer.Typer(help="Make transparent recommendations from imported Mudae state")
server_app = typer.Typer(help="Compare imported server-wide configuration")
console = Console()

app.add_typer(tower_app, name="tower")
app.add_typer(badge_app, name="badge")
app.add_typer(reaction_app, name="reaction")
app.add_typer(loot_app, name="loot")
app.add_typer(key_app, name="key")
app.add_typer(roll_app, name="roll")
app.add_typer(account_app, name="account")
app.add_typer(action_app, name="action")
app.add_typer(parse_app, name="parse")
app.add_typer(import_app, name="import")
app.add_typer(catalog_app, name="catalog")
app.add_typer(harem_app, name="harem")
app.add_typer(recommend_app, name="recommend")
app.add_typer(server_app, name="server")


@app.command()
def version():
    console.print("[cyan]MOA[/cyan] v0.1.0")


@app.command("detect")
def detect_mudae_message(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Identify which supported Mudae format one raw message uses."""
    detection = MudaeMessageRouter().detect(_read_message_source(path, clipboard))
    style = "green" if detection.kind != "unknown" else "yellow"
    console.print(f"[{style}]Detected: {detection.kind}[/{style}]")
    console.print(f"[dim]{detection.reason}[/dim]")


@account_app.command("activity")
def account_activity(
    server: str = typer.Option(..., "--server", "-s"),
    account: str = typer.Option(..., "--account", "-a"),
) -> None:
    """Show current imported activity signals without making spending decisions."""
    overview = AccountOverviewService().overview(server, account)
    readiness = ActionService().readiness(server, account)
    reactions = CatalogService().kakera_reaction_summary(server, account)
    try:
        keyfarm = KeyFarmService().recommend(server, account)
    except ValueError:
        keyfarm = ()
    table = Table(title=f"{account} - activity dashboard")
    table.add_column("Area", style="green")
    table.add_column("Imported state")
    table.add_row("Kakera balance", "Not imported" if overview.kakera_balance is None else f"{overview.kakera_balance:,} ($k)")
    table.add_row("Timer status", readiness.status)
    table.add_row("Available actions", ", ".join(readiness.available_actions) or "None / refresh $tu")
    table.add_row("Reaction receipts", f"{reactions.receipt_count:,} | +{reactions.total_kakera_earned:,} Kakera")
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
        "Not imported" if overview.quantity_level is None else f"Quantity {overview.quantity_level}; Quality {overview.quality_level}; {overview.loot_usage_count:,} uses",
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
            f"{target.character_name} | {target.kakera_value:,} Kakera | {target.key_type.title()} {target.key_count} | {target.wishlist_status}",
        )
    else:
        table.add_row("Top key-farm target", "No eligible valued harem entry imported")
    console.print(table)
    if readiness.upcoming_events:
        console.print("[dim]Upcoming: " + " · ".join(f"{name} in {minutes} min" for name, minutes in readiness.upcoming_events) + "[/dim]")


@server_app.command("compare")
def compare_servers(
    left: str = typer.Option(..., "--left", help="First imported server label."),
    right: str = typer.Option(..., "--right", help="Second imported server label."),
) -> None:
    """Compare the latest imported `$settings` snapshots for two servers."""
    try:
        comparison = ServerComparisonService().compare(left, right)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    table = Table(title=f"{comparison.left_server_name} vs {comparison.right_server_name}")
    table.add_column("Setting", style="green")
    table.add_column(comparison.left_server_name)
    table.add_column(comparison.right_server_name)
    table.add_column("Match", justify="center")
    for entry in comparison.entries:
        table.add_row(
            entry.label,
            entry.left_value,
            entry.right_value,
            "Yes" if entry.matches else "No",
        )
    console.print(table)
    console.print("[dim]This compares imported server configuration only; it does not compare player state.[/dim]")


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


@reaction_app.command("list")
def list_reactions() -> None:
    """List the known Kakera reaction types and baseline values."""
    table = Table(title="Kakera Reactions")
    table.add_column("Reaction", style="green")
    table.add_column("Value range", justify="right", style="cyan")
    table.add_column("Base average", justify="right")
    table.add_column("Power")

    for reaction in ReactionService().all():
        if reaction.minimum_value is None:
            value_range = "Variable"
        elif reaction.minimum_value == reaction.maximum_value:
            value_range = f"{reaction.minimum_value:,}"
        else:
            value_range = f"{reaction.minimum_value:,}-{reaction.maximum_value:,}"

        average = "-" if reaction.average_value is None else f"{reaction.average_value:,.1f}"
        table.add_row(reaction.name, value_range, average, reaction.power_cost_policy.title())

    console.print(table)


@reaction_app.command("show")
def show_reaction(reaction_id: str) -> None:
    """Show the baseline rules for one Kakera reaction type."""
    reaction = ReactionService().get(reaction_id)
    if reaction is None:
        console.print("[red]Kakera reaction not found.[/red]")
        raise typer.Exit(1)

    console.print(f"[bold cyan]{reaction.name}[/bold cyan]")
    console.print(f"[bold]Type:[/bold] {reaction.reaction_type}")
    console.print(f"[bold]Reaction power:[/bold] {reaction.power_cost_policy}")
    if reaction.average_value is not None:
        console.print(f"[bold]Base average:[/bold] {reaction.average_value:,.4f} Kakera")
    console.print(f"[bold]Details:[/bold] {reaction.description}")


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


@roll_app.command("recent")
def recent_rolls(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose rolls to show."),
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="Maximum number of recent rolls."),
) -> None:
    """Show raw roll observations imported for one account context."""
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
            _format_optional_number(roll.kakera_value),
        )
    console.print(table)


@roll_app.command("stats")
def roll_statistics(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose rolls to summarize."),
) -> None:
    """Summarize imported roll history without estimating probabilities."""
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
        console.print(f"[yellow]No rolls imported for {missing} in the selected server/account context yet.[/yellow]")
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


@loot_app.command("next")
def next_loot_spending_step(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose Kakeraloot state is shown."),
) -> None:
    """Show the next Quantity and Quality costs from imported server/account state."""
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


@account_app.command("overview")
def account_overview(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose imported state to summarize."),
) -> None:
    """Show one read-only summary of the latest imported account state."""
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
    elif overview.quantity_level is not None:
        loot_state = (
            f"Quantity {overview.quantity_level} | Quality {overview.quality_level} | "
            f"{overview.loot_usage_count:,} uses"
        )
    else:
        loot_state = "Not imported"
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
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose imported $k history to measure."),
) -> None:
    """Measure Kakera progression from the account's timestamped `$k` imports."""
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


@action_app.command("now")
def action_now(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose latest $tu snapshot to use."),
) -> None:
    """Show the action checklist supported by a recent imported `$tu` snapshot."""
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


def _read_copied_message(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        console.print(f"[red]Could not read {path}: {error}[/red]")
        raise typer.Exit(1) from error


def _read_clipboard() -> str:
    """Read text the user has copied from Discord on the local desktop."""
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        try:
            text = root.clipboard_get()
        finally:
            root.destroy()
    except Exception as error:
        console.print(f"[red]Could not read text from the clipboard: {error}[/red]")
        raise typer.Exit(1) from error

    if not text.strip():
        console.print("[red]The clipboard does not contain text.[/red]")
        raise typer.Exit(1)
    return str(text)


def _read_message_source(path: Path | None, clipboard: bool) -> str:
    if clipboard:
        if path is not None:
            console.print("[red]Use either a file path or --clipboard, not both.[/red]")
            raise typer.Exit(1)
        return _read_clipboard()

    if path is None:
        console.print("[red]Provide a text-file path or use --clipboard.[/red]")
        raise typer.Exit(1)
    return _read_copied_message(path)


def _format_optional_number(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _format_optional_rank(value: int | None) -> str:
    return "-" if value is None else f"#{value:,}"


def _format_optional_average_rank(value: float | None) -> str:
    return "-" if value is None else f"#{value:,.1f}"


def _format_optional_average_number(value: float | None) -> str:
    return "-" if value is None else f"{value:,.1f}"


@parse_app.command("top")
def parse_top(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $top page."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse one copied Mudae `$top` page from a file or the clipboard."""
    try:
        page = MudaeTextParser().parse_top_page(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if page.limit is None or page.page_number is None or page.page_count is None:
        console.print("[bold cyan]Ranked characters (partial import)[/bold cyan]")
    else:
        console.print(
            f"[bold cyan]TOP {page.limit:,} - Page {page.page_number}/{page.page_count}[/bold cyan]"
        )
    table = Table()
    table.add_column("Claim rank", justify="right", style="cyan")
    table.add_column("Character", style="green")
    table.add_column("Series")
    for character in page.characters:
        table.add_row(f"#{character.claim_rank:,}", character.name, character.series)
    console.print(table)


@parse_app.command("im")
def parse_im(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $im response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse one copied Mudae `$im` response from a file or the clipboard."""
    try:
        character = MudaeTextParser().parse_character_details(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    console.print(f"[bold cyan]{character.name}[/bold cyan] — {character.series}")
    console.print(f"[bold]Claim rank:[/bold] {_format_optional_rank(character.claim_rank)}")
    console.print(f"[bold]Like rank:[/bold] {_format_optional_rank(character.like_rank)}")
    console.print(f"[bold]Kakera value:[/bold] {_format_optional_number(character.kakera_value)}")


@parse_app.command("roll")
def parse_roll(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae roll card."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse one copied Mudae roll card from a file or the clipboard."""
    try:
        roll = MudaeTextParser().parse_roll(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    console.print(f"[bold cyan]{roll.name}[/bold cyan] — {roll.series}")
    console.print(f"[bold]Claim rank:[/bold] {_format_optional_rank(roll.claim_rank)}")
    console.print(f"[bold]Kakera value:[/bold] {_format_optional_number(roll.kakera_value)}")


@parse_app.command("reaction")
def parse_kakera_reaction(
    path: Path | None = typer.Argument(None, help="Text file containing one Mudae reaction receipt."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse a standalone Mudae Kakera-reaction receipt."""
    try:
        receipt = MudaeTextParser().parse_kakera_reaction_receipt(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    console.print(f"[bold cyan]{receipt.account_name}[/bold cyan] received [green]+{receipt.kakera_earned:,} Kakera[/green]")
    console.print(f"Reaction label: {receipt.reaction_label}")


@app.command("analyze-roll")
def analyze_roll(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account deciding what to do with this roll."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae roll card."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Explain a copied roll using directly imported account context."""
    try:
        roll = MudaeTextParser().parse_roll(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    analysis = RollAnalysisService().analyze(roll, server, account)
    table = Table(title=f"{analysis.character_name} - roll context")
    table.add_column("Signal", style="green")
    table.add_column("Imported/direct value")
    table.add_row("Series", analysis.series)
    table.add_row("Claim rank", _format_optional_rank(analysis.claim_rank))
    table.add_row("This roll's Kakera", _format_optional_number(analysis.kakera_value))
    table.add_row("Wishlist", analysis.wishlist_state)
    table.add_row("Keyed harem", analysis.keyed_harem_state)
    table.add_row("Rollability", analysis.rollability_state)
    table.add_row("Claim window", analysis.claim_window_state)
    console.print(table)
    console.print(
        "[dim]This is factual roll context, not a claim/skip recommendation. "
        "A missing keyed entry does not prove the character is unowned, and $tu state is not live.[/dim]"
    )


@parse_app.command("mm")
def parse_mm(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $mmy= page."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse one copied Mudae `$mmy=`/`$mmyk=` keyed-harem page."""
    try:
        page = MudaeTextParser().parse_harem_key_page(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    page_label = (
        f"Page {page.page_number}/{page.page_count}"
        if page.page_number is not None and page.page_count is not None
        else "Partial import"
    )
    console.print(f"[bold cyan]Keyed harem - {page_label}[/bold cyan]")
    table = Table()
    table.add_column("Character", style="green")
    table.add_column("Key type")
    table.add_column("Keys", justify="right", style="cyan")
    table.add_column("Kakera", justify="right", style="magenta")
    for entry in page.entries:
        table.add_row(
            entry.name,
            entry.key_type.title(),
            str(entry.key_count),
            _format_optional_number(entry.kakera_value),
        )
    console.print(table)
    if page.total_harem_value is not None:
        console.print(f"[bold]Total harem value:[/bold] {page.total_harem_value:,} Kakera")


@parse_app.command("bonus")
def parse_bonus(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $bonus response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse one copied Mudae `$bonus` response."""
    try:
        bonus = MudaeTextParser().parse_player_bonus(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    table = Table(title="Parsed player bonuses")
    table.add_column("Metric", style="green")
    table.add_column("Mudae value")
    for metric in bonus.metrics:
        table.add_row(metric.label, metric.detail)
    console.print(table)


@parse_app.command("wishlist")
def parse_wishlist(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $wl response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse one copied Mudae `$wl` response."""
    try:
        wishlist = MudaeTextParser().parse_wishlist(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    table = Table(
        title=(
            f"Wishlist {wishlist.wishlist_count}/{wishlist.wishlist_capacity} · "
            f"Starwish {wishlist.starwish_count}/{wishlist.starwish_capacity}"
        )
    )
    table.add_column("Character", style="green")
    table.add_column("Status")
    for entry in wishlist.entries:
        table.add_row(entry.name, "Starwish" if entry.is_starwish else "Wish")
    console.print(table)


@parse_app.command("disablelist")
def parse_disablelist(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $dl response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse one copied Mudae `$dl` response."""
    try:
        disablelist = MudaeTextParser().parse_disablelist(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    console.print(
        f"[bold cyan]Disablelist:[/bold cyan] {disablelist.slots_used}/{disablelist.slots_capacity} slots · "
        f"{disablelist.total_disabled:,} total disabled"
    )
    console.print(
        f"$wa {disablelist.disabled_wa:,} · $ha {disablelist.disabled_ha:,} · "
        f"$wg {disablelist.disabled_wg:,} · $hg {disablelist.disabled_hg:,}"
    )
    table = Table()
    table.add_column("Disabled bundle", style="green")
    table.add_column("Characters", justify="right", style="cyan")
    for entry in disablelist.entries:
        table.add_row(entry.name, f"{entry.disabled_count:,}")
    console.print(table)


@parse_app.command("topx")
def parse_topx(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $topx response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse one copied Mudae `$topx` page of unavailable characters."""
    try:
        page = MudaeTextParser().parse_unavailable_characters(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    page_label = (
        f"TOP {page.limit:,} — Page {page.page_number}/{page.page_count}"
        if page.limit is not None and page.page_number is not None and page.page_count is not None
        else "Unavailable characters (partial import)"
    )
    table = Table(title=page_label)
    table.add_column("Claim rank", justify="right", style="cyan")
    table.add_column("Character", style="green")
    table.add_column("Series")
    table.add_column("Reason")
    for character in page.characters:
        table.add_row(
            f"#{character.claim_rank:,}", character.name, character.series, character.reason or "Disabled"
        )
    console.print(table)


@parse_app.command("kakera")
def parse_kakera(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $k response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse one copied Mudae `$k` balance and badge-state response."""
    try:
        state = MudaeTextParser().parse_kakera_state(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    table = Table(title=f"Kakera balance: {state.kakera_balance:,}")
    table.add_column("Badge", style="green")
    table.add_column("Level", justify="right", style="cyan")
    table.add_column("Status")
    for badge in state.badges:
        table.add_row(badge.badge_name.title(), str(badge.level), "Max" if badge.max_reached else "In progress")
    console.print(table)


@parse_app.command("personalrare")
def parse_personalrare(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $persr response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse the account-scoped `$personalrare` value from `$persr`."""
    try:
        state = MudaeTextParser().parse_personal_rare(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    source = "server $setrare" if state.personal_rare_multiplier == 0 else "$personalrare override"
    console.print(
        f"[bold cyan]Personal rare multiplier:[/bold cyan] {state.personal_rare_multiplier} "
        f"([dim]{source}[/dim])"
    )


@parse_app.command("timers")
def parse_timers(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $tu response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse whichever timer categories the current `$tu` layout displays."""
    try:
        state = MudaeTextParser().parse_timer_state(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    claim = "ready" if state.can_claim_now else f"in {state.claim_reset_minutes} min" if state.can_claim_now is False else "hidden"
    rolls = f"{state.rolls_left} left" if state.rolls_left is not None else "hidden"
    console.print(
        f"[bold cyan]Action timers[/bold cyan] | claim {claim} | rolls {rolls}\n"
        f"$dk: {'ready' if state.daily_kakera_ready else 'not ready' if state.daily_kakera_ready is False else 'hidden'} | "
        f"$rt: {'available' if state.rt_available else 'not available' if state.rt_available is False else 'hidden'}"
    )


@parse_app.command("towerstate")
def parse_towerstate(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $kt response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse one copied Mudae `$kt` tower-state response."""
    try:
        state = MudaeTextParser().parse_tower_state(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    console.print(
        f"[bold cyan]Tower level {state.current_level}[/bold cyan] · "
        f"{state.completed_towers} completed tower(s)\n"
        f"Next floor: {state.next_level_cost:,} Kakera · Balance: {state.kakera_balance:,} Kakera\n"
        f"Built perks: {', '.join(str(perk) for perk in state.built_perk_ids) or 'none'}"
    )


@parse_app.command("lootstate")
def parse_lootstate(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $lk response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse one copied Mudae `$lk` Kakeraloot-state response."""
    try:
        state = MudaeTextParser().parse_kakeraloot_state(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    if not state.has_kakeraloots:
        console.print(f"[yellow]{state.status_note}[/yellow]")
        return
    console.print(
        f"[bold cyan]Kakeraloots[/bold cyan] · Quantity {state.quantity_level} · "
        f"Quality {state.quality_level}\n"
        f"Usage: {state.usage_count:,} · Balance: {state.kakera_balance:,} Kakera · "
        f"Rolls stacked: {state.rolls_stacked}\n"
        f"Wishprotect: LVL {state.protected_wish_level} (1/{state.protected_wish_denominator:,}) · "
        f"Permanent rolls: +{state.permanent_roll_bonus}"
    )


@parse_app.command("infokl")
def parse_infokl(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $infokl response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse server-specific Kakeraloot prices from `$infokl`."""
    try:
        settings = MudaeTextParser().parse_kakeraloot_settings(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    console.print(
        f"[bold cyan]Kakeraloot configuration[/bold cyan]\n"
        f"Each $kl: {settings.loot_cost:,} Kakera | Quantity/Quality: "
        f"{settings.quantity_quality_base_cost:,} + "
        f"{settings.quantity_quality_level_increment:,} per current level"
    )


@parse_app.command("settings")
def parse_settings(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $settings response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse one copied Mudae `$settings` response."""
    try:
        settings = MudaeTextParser().parse_server_settings(_read_message_source(path, clipboard))
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    console.print(
        f"[bold cyan]Server settings[/bold cyan] | Gamemode {settings.game_mode} | "
        f"{settings.rolls_per_hour} rolls/hour | claim reset {settings.claim_reset_minutes} min\n"
        f"Claim timer: {settings.claim_reaction_expiry_seconds}s | rare multiplier: "
        f"{settings.claimed_character_rarity_multiplier} | premium: {settings.server_premium}"
    )


@import_app.command("auto")
def import_auto(
    server: str | None = typer.Option(None, "--server", "-s", help="Server label when the message needs one."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account name when the message needs one."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Detect and import one supported Mudae response using the existing import rules."""
    raw_message = _read_message_source(path, clipboard)
    source = "clipboard" if clipboard else f"file:{path}"
    try:
        result = AutomaticImportService().import_message(raw_message, source, server, account)
    except (MudaeParseError, ValueError) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    console.print(
        f"[green]Detected {result.kind} and imported {result.imported_count} item(s).[/green] "
        f"{result.message}"
    )


@import_app.command("reaction")
def import_reaction(
    server: str = typer.Option(..., "--server", "-s"),
    path: Path | None = typer.Argument(None),
    clipboard: bool = typer.Option(False, "--clipboard", "-c"),
) -> None:
    """Parse and persist one standalone Mudae Kakera-reaction receipt."""
    raw_message = _read_message_source(path, clipboard)
    try:
        receipt = MudaeTextParser().parse_kakera_reaction_receipt(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    result = CatalogService().import_kakera_reaction(
        receipt, server, raw_message, "clipboard" if clipboard else f"file:{path}"
    )
    console.print(f"[green]Imported +{receipt.kakera_earned:,} Kakera for {result.account_name}.[/green]")


@import_app.command("top")
def import_top(
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $top page."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist a `$top` page as a timestamped local rank snapshot."""
    raw_message = _read_message_source(path, clipboard)
    try:
        page = MudaeTextParser().parse_top_page(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_top_page(page, raw_message, source)
    total = CatalogService().character_count()
    console.print(
        f"[green]Imported {result.characters_imported} ranked characters.[/green] "
        f"Catalog now contains [cyan]{total}[/cyan] characters."
    )


@import_app.command("im")
def import_im(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the server this $im came from."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $im response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist one `$im` response with its server-specific Kakera value."""
    raw_message = _read_message_source(path, clipboard)
    try:
        details = MudaeTextParser().parse_character_details(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_character_details(details, server, raw_message, source)
    console.print(
        f"[green]Imported {details.name} for {result.server_name}.[/green] "
        f"Recorded [cyan]{_format_optional_number(details.kakera_value)} Kakera[/cyan]."
    )


@import_app.command("mm")
def import_mm(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose harem is shown."),
    scan: int | None = typer.Option(
        None, "--scan", help="Optional active harem scan ID created by `moa harem begin`."
    ),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $mmy= page."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist one `$mmy=` or `$mmyk=` page for a server/account harem."""
    raw_message = _read_message_source(path, clipboard)
    try:
        page = MudaeTextParser().parse_harem_key_page(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    source = "clipboard" if clipboard else f"file:{path}"
    try:
        result = CatalogService().import_harem_key_page(
            page,
            server,
            account,
            raw_message,
            source,
            scan,
        )
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    console.print(
        f"[green]Imported {result.entries_imported} keyed harem entries for "
        f"{result.account_name}.[/green] "
        f"[cyan]{result.entries_linked}[/cyan] linked to the current catalog."
    )
    if result.scan_id is not None and result.page_number is not None and result.page_count is not None:
        console.print(
            f"[cyan]Scan {result.scan_id}:[/cyan] saved page {result.page_number}/{result.page_count}. "
            f"Keep using [bold]--scan {result.scan_id}[/bold] for every remaining page."
        )


@import_app.command("bonus")
def import_bonus(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose bonuses are shown."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $bonus response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist one `$bonus` response as account-scoped player state."""
    raw_message = _read_message_source(path, clipboard)
    try:
        bonus = MudaeTextParser().parse_player_bonus(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_player_bonus(bonus, server, account, raw_message, source)
    console.print(
        f"[green]Imported {len(bonus.metrics)} player bonus metrics for "
        f"{result.account_name}.[/green]"
    )


@import_app.command("wishlist")
def import_wishlist(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose wishlist is shown."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $wl response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist one `$wl` response as account-scoped wishlist state."""
    raw_message = _read_message_source(path, clipboard)
    try:
        wishlist = MudaeTextParser().parse_wishlist(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_wishlist(wishlist, server, account, raw_message, source)
    console.print(
        f"[green]Imported {len(wishlist.entries)} wishlist entries for {result.account_name}.[/green] "
        f"[cyan]{wishlist.starwish_count}[/cyan] marked as Starwish."
    )


@import_app.command("disablelist")
def import_disablelist(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose disable list is shown."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $dl response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist one `$dl` response as account-scoped roll-pool state."""
    raw_message = _read_message_source(path, clipboard)
    try:
        disablelist = MudaeTextParser().parse_disablelist(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_disablelist(disablelist, server, account, raw_message, source)
    console.print(
        f"[green]Imported {len(disablelist.entries)} disabled bundles for {result.account_name}.[/green] "
        f"[cyan]{disablelist.slots_used}/{disablelist.slots_capacity}[/cyan] slots used."
    )


@import_app.command("topx")
def import_topx(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose roll pool is shown."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $topx response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Persist direct Mudae evidence that `$topx` characters cannot currently roll."""
    raw_message = _read_message_source(path, clipboard)
    try:
        page = MudaeTextParser().parse_unavailable_characters(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_unavailable_characters(page, server, account, raw_message, source)
    console.print(
        f"[green]Imported {result.characters_imported} unavailable-character observations for "
        f"{result.account_name}.[/green]"
    )


@import_app.command("kakera")
def import_kakera(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose Kakera state is shown."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $k response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist one `$k` response as account-scoped Kakera state."""
    raw_message = _read_message_source(path, clipboard)
    try:
        state = MudaeTextParser().parse_kakera_state(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_kakera_state(state, server, account, raw_message, source)
    console.print(
        f"[green]Imported {state.kakera_balance:,} Kakera and {len(state.badges)} badge levels for "
        f"{result.account_name}.[/green]"
    )


@import_app.command("personalrare")
def import_personalrare(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose personal rarity is shown."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $persr response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Persist one `$persr` response as account-scoped roll configuration."""
    raw_message = _read_message_source(path, clipboard)
    try:
        state = MudaeTextParser().parse_personal_rare(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_personal_rare(state, server, account, raw_message, source)
    console.print(
        f"[green]Imported $personalrare {state.personal_rare_multiplier} for "
        f"{result.account_name}.[/green]"
    )


@import_app.command("timers")
def import_timers(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose $tu state is shown."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $tu response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Persist one `$tu` response as a short-lived account action snapshot."""
    raw_message = _read_message_source(path, clipboard)
    try:
        state = MudaeTextParser().parse_timer_state(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_timer_state(state, server, account, raw_message, source)
    console.print(
        f"[green]Imported $tu timer snapshot for {result.account_name}.[/green] "
        "Use [cyan]moa action now[/cyan] immediately for a current checklist."
    )


@import_app.command("towerstate")
def import_towerstate(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose tower state is shown."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $kt response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist one `$kt` response as account-scoped tower state."""
    raw_message = _read_message_source(path, clipboard)
    try:
        state = MudaeTextParser().parse_tower_state(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_tower_state(state, server, account, raw_message, source)
    console.print(
        f"[green]Imported tower level {state.current_level} for {result.account_name}.[/green] "
        f"Next floor costs [cyan]{state.next_level_cost:,} Kakera[/cyan]."
    )


@import_app.command("lootstate")
def import_lootstate(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose Kakeraloot state is shown."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $lk response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist one `$lk` response as account-scoped Kakeraloot state."""
    raw_message = _read_message_source(path, clipboard)
    try:
        state = MudaeTextParser().parse_kakeraloot_state(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_kakeraloot_state(state, server, account, raw_message, source)
    if not state.has_kakeraloots:
        console.print(
            f"[green]Imported Kakeraloot status for {result.account_name}.[/green] "
            f"[yellow]{state.status_note}[/yellow]"
        )
        return
    console.print(
        f"[green]Imported Kakeraloot state for {result.account_name}.[/green] "
        f"Quantity [cyan]{state.quantity_level}[/cyan] · Quality [cyan]{state.quality_level}[/cyan]."
    )


@import_app.command("infokl")
def import_infokl(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $infokl response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Persist one `$infokl` response as server-scoped Kakeraloot configuration."""
    raw_message = _read_message_source(path, clipboard)
    try:
        settings = MudaeTextParser().parse_kakeraloot_settings(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_kakeraloot_settings(settings, server, raw_message, source)
    console.print(
        f"[green]Imported Kakeraloot configuration for {result.server_name}.[/green] "
        f"Each $kl costs [cyan]{settings.loot_cost:,} Kakera[/cyan]."
    )


@import_app.command("settings")
def import_settings(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $settings response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist one `$settings` response as server-scoped configuration."""
    raw_message = _read_message_source(path, clipboard)
    try:
        settings = MudaeTextParser().parse_server_settings(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_server_settings(settings, server, raw_message, source)
    console.print(
        f"[green]Imported {len(settings.metrics)} server settings for {result.server_name}.[/green] "
        f"Gamemode [cyan]{settings.game_mode}[/cyan] | rolls/hour [cyan]{settings.rolls_per_hour}[/cyan]."
    )


@harem_app.command("begin")
def harem_begin(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose harem you are scanning."),
) -> None:
    """Start a new multi-page harem scan that activates only when complete."""
    scan = CatalogService().begin_harem_scan(server, account)
    console.print(
        f"[green]Started harem scan {scan.id}[/green] for [cyan]{scan.account_name}[/cyan].\n"
        "Import each Mudae page with:\n"
        f"[bold]uv run moa import mm --scan {scan.id} --server {scan.server_name!r} "
        f"--account {scan.account_name!r} --clipboard[/bold]"
    )


@harem_app.command("status")
def harem_status(scan_id: int) -> None:
    """Show pages captured for a harem scan."""
    scan = CatalogService().harem_scan_progress(scan_id)
    if scan is None:
        console.print("[red]Harem scan not found.[/red]")
        raise typer.Exit(1)
    expected = str(scan.expected_page_count) if scan.expected_page_count is not None else "unknown"
    captured = ", ".join(str(page) for page in scan.imported_pages) or "none"
    status = "complete" if scan.completed_at is not None else "in progress"
    console.print(
        f"[bold cyan]Harem scan {scan.id}[/bold cyan] — {scan.server_name} / {scan.account_name}\n"
        f"Pages: {captured} of {expected} · Status: {status}"
    )


@harem_app.command("complete")
def harem_complete(scan_id: int) -> None:
    """Validate and activate a fully imported harem scan."""
    try:
        scan = CatalogService().complete_harem_scan(scan_id)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    console.print(
        f"[green]Harem scan {scan.id} is complete and active[/green] for "
        f"{scan.server_name} / {scan.account_name}."
    )


@catalog_app.command("top")
def catalog_top(
    limit: int = typer.Option(15, "--limit", "-n", min=1, help="Number of characters to display."),
) -> None:
    """Show the best ranks currently stored in MOA's local catalog."""
    service = CatalogService()
    try:
        characters = service.top(limit)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if not characters:
        console.print("[yellow]Catalog is empty. Import a $top page first.[/yellow]")
        raise typer.Exit()

    table = Table(title="Imported Character Catalog")
    table.add_column("Claim rank", justify="right", style="cyan")
    table.add_column("Character", style="green")
    table.add_column("Series")
    table.add_column("Observed (UTC)")
    for ranked_character in characters:
        table.add_row(
            f"#{ranked_character.claim_rank:,}",
            ranked_character.character.name,
            ranked_character.character.series,
            ranked_character.observed_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@catalog_app.command("show")
def catalog_show(
    name: str,
    series: str = typer.Option(..., "--series", "-s", help="Character's Mudae series name."),
) -> None:
    """Show global ranks and latest server-specific observations for one character."""
    profile = CatalogService().get_profile(name, series)
    if profile is None:
        console.print("[yellow]Character not found in the local catalog.[/yellow]")
        raise typer.Exit(1)

    console.print(f"[bold cyan]{profile.character.name}[/bold cyan] - {profile.character.series}")
    console.print(f"[bold]Gender:[/bold] {profile.character.gender or '-'}")
    console.print(f"[bold]Roulette:[/bold] {profile.character.roulette or '-'}")
    console.print(f"[bold]Claim rank:[/bold] {_format_optional_rank(profile.claim_rank)}")
    console.print(f"[bold]Like rank:[/bold] {_format_optional_rank(profile.like_rank)}")

    if not profile.server_observations:
        console.print("[yellow]No server-specific observations imported yet.[/yellow]")
        return

    table = Table(title="Latest server observations")
    table.add_column("Server", style="green")
    table.add_column("Kakera value", justify="right", style="cyan")
    table.add_column("Observed (UTC)")
    for observation in profile.server_observations:
        table.add_row(
            observation.server_name,
            _format_optional_number(observation.kakera_value),
            observation.observed_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@catalog_app.command("harem")
def catalog_harem(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose harem to show."),
) -> None:
    """Show the latest keyed-harem observations for one server/account pair."""
    entries = CatalogService().harem_keys(server, account)
    if not entries:
        console.print("[yellow]No keyed harem entries imported for this server/account yet.[/yellow]")
        raise typer.Exit()

    table = Table(title=f"{account} - keyed harem")
    table.add_column("Character", style="green")
    table.add_column("Key type")
    table.add_column("Keys", justify="right", style="cyan")
    table.add_column("Kakera", justify="right", style="magenta")
    table.add_column("Catalog link")
    table.add_column("Observed (UTC)")
    for entry in entries:
        table.add_row(
            entry.character_name,
            entry.key_type.title(),
            str(entry.key_count),
            _format_optional_number(entry.kakera_value),
            "Resolved" if entry.character else "Needs $im",
            entry.observed_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@catalog_app.command("keyfarm")
def catalog_keyfarm(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose harem to shortlist."),
    limit: int = typer.Option(15, "--limit", "-n", min=1, help="Number of entries to display."),
) -> None:
    """Show the highest-value imported keyed characters for a future key-farm plan."""
    service = CatalogService()
    entries = service.harem_keys(server, account)
    wishlist = service.wishlist(server, account)
    wishlist_by_name = {
        entry.name.casefold(): entry for entry in wishlist.entries
    } if wishlist is not None else {}
    unavailable_names = {
        observation.character.name.casefold()
        for observation in service.unavailable_characters(server, account)
    }
    valued_entries = [entry for entry in entries if entry.kakera_value is not None][:limit]
    if not valued_entries:
        console.print(
            "[yellow]No harem Kakera values imported yet. Copy a `$mmyk=` page and run "
            "`moa import mm`.[/yellow]"
        )
        raise typer.Exit()

    table = Table(title=f"{account} - current key-farm shortlist")
    table.add_column("Character", style="green")
    table.add_column("Kakera", justify="right", style="magenta")
    table.add_column("Key type")
    table.add_column("Keys", justify="right", style="cyan")
    table.add_column("Wishlist")
    table.add_column("Rollability")
    for entry in valued_entries:
        wishlist_entry = wishlist_by_name.get(entry.character_name.casefold())
        wishlist_status = (
            "Starwish" if wishlist_entry and wishlist_entry.is_starwish
            else "Wish" if wishlist_entry
            else "-"
        )
        table.add_row(
            entry.character_name,
            _format_optional_number(entry.kakera_value),
            entry.key_type.title(),
            str(entry.key_count),
            wishlist_status,
            "Unavailable" if entry.character_name.casefold() in unavailable_names else "Unknown",
        )
    console.print(table)
    console.print(
        "[dim]Ordered by the current Mudae values you imported. This is a factual shortlist, "
        "not yet an expected-value recommendation.[/dim]"
    )


@catalog_app.command("keyprogress")
def catalog_keyprogress(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose key progress to show."),
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="Number of entries to display."),
) -> None:
    """Show each imported harem character's next universal key unlock."""
    progress = KeyProgressService().progress(server, account)
    if not progress:
        console.print("[yellow]No keyed harem entries imported for this server/account yet.[/yellow]")
        raise typer.Exit()
    table = Table(title=f"{account} - next key milestones")
    table.add_column("Character", style="green")
    table.add_column("Keys", justify="right", style="cyan")
    table.add_column("Tier")
    table.add_column("Next", justify="right")
    table.add_column("Away", justify="right")
    table.add_column("Next unlock")
    for entry in progress[:limit]:
        table.add_row(
            entry.character_name,
            str(entry.key_count),
            entry.current_tier,
            str(entry.next_milestone_key_count) if entry.next_milestone_key_count is not None else "-",
            str(entry.keys_until_next_milestone) if entry.keys_until_next_milestone is not None else "-",
            "\n".join(entry.next_effects),
        )
    console.print(table)
    console.print(
        "[dim]This explains the next key unlock only; it does not yet estimate how often each character rolls.[/dim]"
    )


@recommend_app.command("keyfarm")
def recommend_keyfarm(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose harem to prioritize."),
    limit: int = typer.Option(15, "--limit", "-n", min=1, help="Number of recommendations to show."),
) -> None:
    """Rank key-farm targets from imported value, wish bonuses, and key chance."""
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
            f"{entry.kakera_value:,}",
            f"{entry.key_type.title()} {entry.key_count}",
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


@catalog_app.command("bonus")
def catalog_bonus(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose bonus snapshot to show."),
) -> None:
    """Show the latest imported `$bonus` snapshot for one account."""
    bonus = CatalogService().player_bonus(server, account)
    if bonus is None:
        console.print("[yellow]No $bonus snapshot imported for this server/account yet.[/yellow]")
        raise typer.Exit()

    table = Table(title=f"{bonus.account_name} - player bonuses")
    table.add_column("Metric", style="green")
    table.add_column("Mudae value")
    for metric in bonus.metrics:
        table.add_row(metric.label, metric.detail)
    console.print(table)
    console.print(f"[dim]Observed: {bonus.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]")


@catalog_app.command("wishlist")
def catalog_wishlist(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose wishlist to show."),
) -> None:
    """Show the latest imported `$wl` snapshot for one account."""
    wishlist = CatalogService().wishlist(server, account)
    if wishlist is None:
        console.print("[yellow]No $wl snapshot imported for this server/account yet.[/yellow]")
        raise typer.Exit()
    table = Table(
        title=(
            f"{wishlist.account_name} - wishlist {wishlist.wishlist_count}/{wishlist.wishlist_capacity} · "
            f"Starwish {wishlist.starwish_count}/{wishlist.starwish_capacity}"
        )
    )
    table.add_column("Character", style="green")
    table.add_column("Status")
    for entry in wishlist.entries:
        table.add_row(entry.name, "Starwish" if entry.is_starwish else "Wish")
    console.print(table)


@catalog_app.command("disablelist")
def catalog_disablelist(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose disable list to show."),
) -> None:
    """Show the latest imported `$dl` snapshot for one account."""
    disablelist = CatalogService().disablelist(server, account)
    if disablelist is None:
        console.print("[yellow]No $dl snapshot imported for this server/account yet.[/yellow]")
        raise typer.Exit()
    console.print(
        f"[bold cyan]{disablelist.account_name} - disablelist[/bold cyan]\n"
        f"Slots: {disablelist.slots_used}/{disablelist.slots_capacity} · "
        f"Disabled: {disablelist.total_disabled:,}\n"
        f"$wa: {disablelist.disabled_wa:,} · $ha: {disablelist.disabled_ha:,} · "
        f"$wg: {disablelist.disabled_wg:,} · $hg: {disablelist.disabled_hg:,}\n"
        f"Western disabled: {disablelist.western_disabled} · IRL disabled: {disablelist.irl_disabled}"
    )
    table = Table()
    table.add_column("Disabled bundle", style="green")
    table.add_column("Characters", justify="right", style="cyan")
    for entry in disablelist.entries:
        table.add_row(entry.name, f"{entry.disabled_count:,}")
    console.print(table)


@catalog_app.command("unavailable")
def catalog_unavailable(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose roll pool to show."),
) -> None:
    """Show characters directly observed as unavailable by `$topx`."""
    observations = CatalogService().unavailable_characters(server, account)
    if not observations:
        console.print("[yellow]No unavailable-character observations imported yet.[/yellow]")
        raise typer.Exit()
    table = Table(title=f"{account} - directly observed unavailable characters")
    table.add_column("Claim rank", justify="right", style="cyan")
    table.add_column("Character", style="green")
    table.add_column("Series")
    table.add_column("Reason")
    for observation in observations:
        table.add_row(
            f"#{observation.claim_rank:,}",
            observation.character.name,
            observation.character.series,
            observation.reason or "Disabled bundle/pool",
        )
    console.print(table)


@catalog_app.command("kakera")
def catalog_kakera(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose Kakera state to show."),
) -> None:
    """Show the latest imported `$k` snapshot for one account."""
    state = CatalogService().kakera_state(server, account)
    if state is None:
        console.print("[yellow]No $k snapshot imported for this server/account yet.[/yellow]")
        raise typer.Exit()
    table = Table(title=f"{state.account_name} - Kakera balance: {state.kakera_balance:,}")
    table.add_column("Badge", style="green")
    table.add_column("Level", justify="right", style="cyan")
    table.add_column("Status")
    for badge in state.badges:
        table.add_row(badge.badge_name.title(), str(badge.level), "Max" if badge.max_reached else "In progress")
    console.print(table)


@catalog_app.command("towerstate")
def catalog_towerstate(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose tower state to show."),
) -> None:
    """Show the latest imported `$kt` snapshot for one account."""
    state = CatalogService().tower_state(server, account)
    if state is None:
        console.print("[yellow]No $kt snapshot imported for this server/account yet.[/yellow]")
        raise typer.Exit()
    gap = max(0, state.next_level_cost - state.kakera_balance)
    console.print(
        f"[bold cyan]{state.account_name} - Tower level {state.current_level}[/bold cyan]\n"
        f"Completed towers: {state.completed_towers} · Built perks: "
        f"{', '.join(str(perk) for perk in state.built_perk_ids) or 'none'}\n"
        f"Next floor: {state.next_level_cost:,} Kakera · Balance: {state.kakera_balance:,} Kakera · "
        f"Shortfall: {gap:,} Kakera"
    )


@catalog_app.command("timers")
def catalog_timers(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose latest $tu snapshot to show."),
) -> None:
    """Show the most recently imported `$tu` snapshot without treating it as live state."""
    observation = CatalogService().timer_state(server, account)
    if observation is None:
        console.print("[yellow]No $tu snapshot imported for this server/account yet.[/yellow]")
        raise typer.Exit()
    state = observation.snapshot
    table = Table(title=f"{observation.account_name} - $tu snapshot")
    table.add_column("Metric", style="green")
    table.add_column("Mudae value")
    if state.can_claim_now is not None:
        claim = "Ready now" if state.can_claim_now else f"Available in {state.claim_reset_minutes} min"
        table.add_row("Claim", claim)
    if state.rolls_left is not None:
        table.add_row("Rolls", f"{state.rolls_left} left; reset in {state.rolls_reset_minutes} min")
    if state.daily_kakera_ready is not None:
        table.add_row("$dk", "Ready" if state.daily_kakera_ready else "Not ready")
    if state.rt_available is not None:
        table.add_row("$rt", "Available" if state.rt_available else "Not available")
    if state.reaction_power_percent is not None:
        table.add_row("Kakera reaction power", f"{state.reaction_power_percent}%")
    if state.oh_remaining is not None:
        table.add_row("Ouro", f"$oh {state.oh_remaining}; $oc {state.oc_remaining}; $oq {state.oq_remaining}; $ot {state.ot_remaining}")
    console.print(table)
    console.print(f"[dim]Observed: {observation.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]")


@catalog_app.command("lootstate")
def catalog_lootstate(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose Kakeraloot state to show."),
) -> None:
    """Show the latest imported `$lk` snapshot for one account."""
    state = CatalogService().kakeraloot_state(server, account)
    if state is None:
        console.print("[yellow]No $lk snapshot imported for this server/account yet.[/yellow]")
        raise typer.Exit()
    if not state.has_kakeraloots:
        console.print(f"[yellow]{state.status_note}[/yellow]")
        console.print(f"[dim]Observed: {state.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]")
        return
    table = Table(title=f"{state.account_name} - Kakeraloot state")
    table.add_column("Metric", style="green")
    table.add_column("Value", justify="right", style="cyan")
    table.add_row("Kakera balance", f"{state.kakera_balance:,}")
    table.add_row("$kl usage", f"{state.usage_count:,}")
    table.add_row("Quantity / Quality", f"{state.quantity_level} / {state.quality_level}")
    table.add_row("Rolls stacked", str(state.rolls_stacked))
    table.add_row("Permanent rolls", f"+{state.permanent_roll_bonus}")
    table.add_row("Wishprotect", f"LVL {state.protected_wish_level} (1/{state.protected_wish_denominator:,})")
    table.add_row("$disable reduction", f"-{state.disable_wa_ha_reduction} $wa/$ha · -{state.disable_wg_hg_reduction} $wg/$hg")
    table.add_row("$rt cooldown", f"-{state.rt_cooldown_reduction_hours}h")
    table.add_row("Mudapins", str(state.mudapins))
    table.add_row("Star branches", f"{state.star_branches} (+{state.starwish_slots_from_branches} $sw)")
    console.print(table)
    console.print(f"[dim]Observed: {state.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]")


@catalog_app.command("infokl")
def catalog_infokl(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
) -> None:
    """Show the latest imported `$infokl` configuration for one server."""
    settings = CatalogService().kakeraloot_settings(server)
    if settings is None:
        console.print("[yellow]No $infokl configuration imported for this server yet.[/yellow]")
        raise typer.Exit()
    console.print(
        f"[bold cyan]{settings.server_name} - Kakeraloot configuration[/bold cyan]\n"
        f"Each $kl: {settings.loot_cost:,} Kakera\n"
        f"Quantity/Quality next-level cost: {settings.quantity_quality_base_cost:,} + "
        f"{settings.quantity_quality_level_increment:,} per current level\n"
        f"[dim]Observed: {settings.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]"
    )


@catalog_app.command("settings")
def catalog_settings(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
) -> None:
    """Show the latest imported `$settings` snapshot for one server."""
    settings = CatalogService().server_settings(server)
    if settings is None:
        console.print("[yellow]No $settings snapshot imported for this server yet.[/yellow]")
        raise typer.Exit()
    table = Table(title=f"{settings.server_name} - server settings")
    table.add_column("Setting", style="green")
    table.add_column("Mudae value")
    for metric in settings.metrics:
        table.add_row(metric.label, metric.value)
    console.print(table)
    console.print(
        f"[dim]Core: Gamemode {settings.game_mode} | {settings.rolls_per_hour} rolls/hour | "
        f"claim reset {settings.claim_reset_minutes} min | observed "
        f"{settings.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]"
    )


@catalog_app.command("imports")
def catalog_imports(
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="Number of imports to display."),
) -> None:
    """Show recent raw Mudae imports and their server labels."""
    service = CatalogService()
    try:
        imports = service.recent_imports(limit)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if not imports:
        console.print("[yellow]No imports recorded yet.[/yellow]")
        raise typer.Exit()

    table = Table(title="Recent Mudae imports")
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


@catalog_app.command("reactions")
def catalog_reactions(
    server: str = typer.Option(..., "--server", "-s"),
    account: str = typer.Option(..., "--account", "-a"),
) -> None:
    """Show recent standalone Kakera payouts reported by Mudae."""
    reactions = CatalogService().kakera_reactions(server, account)
    if not reactions:
        console.print("[yellow]No reaction receipts imported for this server/account yet.[/yellow]")
        raise typer.Exit()
    table = Table(title=f"{account} - Kakera reaction payouts")
    table.add_column("Observed (UTC)")
    table.add_column("Reaction")
    table.add_column("Kakera", justify="right", style="cyan")
    for reaction in reactions:
        table.add_row(reaction.observed_at.strftime("%Y-%m-%d %H:%M"), reaction.reaction_label, f"+{reaction.kakera_earned:,}")
    console.print(table)


@catalog_app.command("reaction-summary")
def catalog_reaction_summary(
    server: str = typer.Option(..., "--server", "-s"),
    account: str = typer.Option(..., "--account", "-a"),
) -> None:
    """Summarize Kakera-reaction receipts stored for one account."""
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


@catalog_app.command("rank-history")
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
            _format_optional_rank(observation.claim_rank),
            _format_optional_rank(observation.like_rank),
        )
    console.print(table)
    console.print("[dim]Only ranks MOA imported from Mudae are shown; this is not a complete rank timeline.[/dim]")


@catalog_app.command("delete-import")
def catalog_delete_import(import_event_id: int) -> None:
    """Delete one mistaken import while preserving all other catalog data."""
    if not CatalogService().delete_import_event(import_event_id):
        console.print("[red]Import event not found.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Deleted import event {import_event_id}.[/green]")


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
