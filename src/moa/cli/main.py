from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.services.badge_service import BadgeService
from moa.services.catalog_service import CatalogService
from moa.services.reaction_service import ReactionService
from moa.services.tower_service import TowerService

app = typer.Typer(help="MOA - Mudae Optimization Assistant")
tower_app = typer.Typer(help="Tower commands")
badge_app = typer.Typer(help="Kakera Badge commands")
reaction_app = typer.Typer(help="Kakera reaction commands")
parse_app = typer.Typer(help="Parse copied Mudae bot output")
import_app = typer.Typer(help="Save parsed Mudae data to the local catalog")
catalog_app = typer.Typer(help="Browse MOA's local character catalog")
harem_app = typer.Typer(help="Build complete keyed-harem snapshots safely")
console = Console()

app.add_typer(tower_app, name="tower")
app.add_typer(badge_app, name="badge")
app.add_typer(reaction_app, name="reaction")
app.add_typer(parse_app, name="parse")
app.add_typer(import_app, name="import")
app.add_typer(catalog_app, name="catalog")
app.add_typer(harem_app, name="harem")


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
        )
    console.print(table)
    console.print(
        "[dim]Ordered by the current Mudae values you imported. This is a factual shortlist, "
        "not yet an expected-value recommendation.[/dim]"
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
