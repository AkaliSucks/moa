from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.services.badge_service import BadgeService
from moa.services.account_overview_service import AccountOverviewService
from moa.services.catalog_service import CatalogService
from moa.services.keyfarm_service import KeyFarmService
from moa.services.key_service import KeyService
from moa.services.key_progress_service import KeyProgressService
from moa.services.loot_service import KakeralootService
from moa.services.reaction_service import ReactionService
from moa.services.tower_service import TowerService

app = typer.Typer(help="MOA - Mudae Optimization Assistant")
tower_app = typer.Typer(help="Tower commands")
badge_app = typer.Typer(help="Kakera Badge commands")
reaction_app = typer.Typer(help="Kakera reaction commands")
loot_app = typer.Typer(help="Kakeraloot reference commands")
key_app = typer.Typer(help="Character key reference commands")
account_app = typer.Typer(help="Imported account-state summary commands")
parse_app = typer.Typer(help="Parse copied Mudae bot output")
import_app = typer.Typer(help="Save parsed Mudae data to the local catalog")
catalog_app = typer.Typer(help="Browse MOA's local character catalog")
harem_app = typer.Typer(help="Build complete keyed-harem snapshots safely")
recommend_app = typer.Typer(help="Make transparent recommendations from imported Mudae state")
console = Console()

app.add_typer(tower_app, name="tower")
app.add_typer(badge_app, name="badge")
app.add_typer(reaction_app, name="reaction")
app.add_typer(loot_app, name="loot")
app.add_typer(key_app, name="key")
app.add_typer(account_app, name="account")
app.add_typer(parse_app, name="parse")
app.add_typer(import_app, name="import")
app.add_typer(catalog_app, name="catalog")
app.add_typer(harem_app, name="harem")
app.add_typer(recommend_app, name="recommend")


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
