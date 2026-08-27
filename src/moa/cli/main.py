from datetime import datetime
import logging
import shutil
import sqlite3
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from moa.cli.action_commands import build_action_app
from moa.cli.account_commands import build_account_app
from moa.core.config import ConfigService
from moa.cli.badge_commands import build_badge_app
from moa.cli.command_commands import build_command_app
from moa.cli.config_commands import build_config_app
from moa.cli.key_commands import build_key_app
from moa.cli.loot_commands import build_loot_app
from moa.cli.parse_commands import build_parse_app
from moa.cli.reaction_commands import build_reaction_app
from moa.cli.recommend_commands import build_recommend_app
from moa.cli.roll_commands import build_roll_app
from moa.cli.server_commands import build_server_app
from moa.cli.tower_commands import build_tower_app
from moa.database.legacy_database_relocation import (
    DatabaseRelocationError,
    relocate_database,
)
from moa.database.sqlite import DEFAULT_DATABASE_PATH, default_database_path
from moa.models.retention import RetentionExpiryResult
from moa.repositories.data_health_repository import DataHealthSchemaError
from moa.repositories.retention_eligibility_repository import RetentionEligibilityDataError
from moa.repositories.retention_expiry_repository import RetentionExpiryError
from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.parser.message_router import MudaeMessageRouter
from moa.repositories.catalog_repository import (
    CatalogRepository,
    ImportEventDeletionBlockedError,
)
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.antidisable_page_projection_coordinator import AntidisablePageProjectionCoordinator
from moa.services.automatic_import_service import AutomaticImportService
from moa.services.catalog_service import CatalogService
from moa.services.claim_projection_coordinator import ClaimProjectionCoordinator
from moa.services.discord_listener_service import (
    DiscordEventCaptureConfig,
    DiscordEventCaptureError,
    DiscordEventCaptureService,
    DiscordListenerService,
)
from moa.services.disablelist_projection_coordinator import DisableListProjectionCoordinator
from moa.services.data_health_service import DataHealthService
from moa.services.retention_eligibility_service import RetentionEligibilityService
from moa.services.retention_expiry_service import RetentionExpiryService
from moa.services.infokl_projection_coordinator import InfoklProjectionCoordinator
from moa.services.kakera_state_projection_coordinator import KakeraStateProjectionCoordinator
from moa.services.kakeraloot_state_projection_coordinator import KakeralootStateProjectionCoordinator
from moa.services.listener_process_guard import (
    ListenerAlreadyRunningError,
    ListenerProcessGuardResourceError,
)
from moa.services.key_progress_service import KeyProgressService
from moa.services.harem_search_service import HaremSearchService
from moa.services.profile_projection_coordinator import ProfileProjectionCoordinator
from moa.services.player_bonus_projection_coordinator import PlayerBonusProjectionCoordinator
from moa.services.roll_analysis_service import RollAnalysisService
from moa.services.roll_projection_coordinator import RollProjectionCoordinator
from moa.services.settings_projection_coordinator import SettingsProjectionCoordinator
from moa.services.sphere_result_projection_coordinator import SphereResultProjectionCoordinator
from moa.services.timer_projection_coordinator import TimerProjectionCoordinator
from moa.services.top_search_service import TopSearchService
from moa.services.tower_state_projection_coordinator import TowerStateProjectionCoordinator
from moa.services.wishlist_projection_coordinator import WishlistProjectionCoordinator
from moa.utils.display import (
    format_mudae_gender,
    format_mudae_kakera,
    format_mudae_key_marker,
    format_mudae_roulette_types,
)

app = typer.Typer(help="MOA - Mudae Optimization Assistant")
import_app = typer.Typer(help="Save parsed Mudae data to the local catalog")
catalog_app = typer.Typer(help="Browse MOA's local character catalog")
data_health_app = typer.Typer(help="Report read-only local catalog health findings")
harem_app = typer.Typer(help="Build complete keyed-harem snapshots safely")
adl_app = typer.Typer(help="Build complete antidisable series snapshots safely")
discord_app = typer.Typer(help="Listen for Mudae messages through a Discord bot")
console = Console()


def _resolve_account_context(
    server: str | None,
    account: str | None,
) -> tuple[str, str]:
    """Resolve explicit or configured account context for read-only commands."""
    try:
        resolved_server, resolved_account = ConfigService().resolve_context(server, account)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    if not resolved_server or not resolved_account:
        console.print(
            "[red]No active server/account context. Configure one with `moa config use` "
            "or pass --server and --account.[/red]"
        )
        raise typer.Exit(1)
    return resolved_server, resolved_account


tower_app = build_tower_app(console)
config_app = build_config_app(console)
badge_app = build_badge_app(console)
key_app = build_key_app(console)
reaction_app = build_reaction_app(console)
command_app = build_command_app(console)
server_app = build_server_app(console)
action_app = build_action_app(console, _resolve_account_context)
recommend_app = build_recommend_app(console, _resolve_account_context)
loot_app = build_loot_app(console, _resolve_account_context)
roll_app = build_roll_app(console, _resolve_account_context)
account_app = build_account_app(console, _resolve_account_context)
parse_app = build_parse_app(
    console,
    lambda path, clipboard: _read_message_source(path, clipboard),
)

app.add_typer(tower_app, name="tower")
app.add_typer(command_app, name="command")
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
app.add_typer(adl_app, name="adl")
app.add_typer(recommend_app, name="recommend")
app.add_typer(server_app, name="server")
app.add_typer(config_app, name="config")
app.add_typer(discord_app, name="discord")
catalog_app.add_typer(data_health_app, name="data-health")


@app.command()
def version():
    console.print("[cyan]MOA[/cyan] v0.1.0")


@discord_app.command("listen")
def discord_listen(
    token: str | None = typer.Option(
        None,
        "--token",
        envvar="MOA_DISCORD_BOT_TOKEN",
        help="Discord bot token; prefer MOA_DISCORD_BOT_TOKEN instead of shell history.",
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="MOA profile containing Discord server/user IDs."
    ),
    mudae_user_id: str | None = typer.Option(
        None,
        "--mudae-user-id",
        envvar="MOA_MUDAE_BOT_ID",
        help="Optional Mudae Discord bot user ID used to filter responses.",
    ),
    status: str = typer.Option(
        "bugs are cracking me",                                               
        "--status",
        help="Text shown in the bot's Watching presence while the listener runs.",
    ),
    capture_discord_events: str | None = typer.Option(
        None,
        "--capture-discord-events",
        help="Diagnostic-only JSONL output path outside the repository.",
    ),
    capture_guild_id: str | None = typer.Option(
        None, "--capture-guild-id", help="Diagnostic capture guild ID."
    ),
    capture_channel_id: str | None = typer.Option(
        None, "--capture-channel-id", help="Diagnostic capture channel ID."
    ),
    capture_user_id: list[str] = typer.Option(
        [], "--capture-user-id", help="Diagnostic capture invoking-user ID; repeat as needed."
    ),
    capture_only: bool = typer.Option(
        False,
        "--capture-only",
        help="Run diagnostic Gateway capture only; do not initialize MOA imports or its database.",
    ),
    capture_include_message_text: bool = typer.Option(
        False,
        "--capture-include-message-text",
        help=(
            "Optional text capture. best-effort sanitization only; sensitive diagnostic data. "
            "manually inspect and deterministically sanitize before fixture use.\n\n"
            "never commit capture files directly."
        ),
    ),
) -> None:
    """Listen for Mudae responses and import them without clipboard copying."""
    if not token or not token.strip():
        console.print(
            "[red]Discord bot token missing.[/red] Set MOA_DISCORD_BOT_TOKEN or pass --token."
        )
        raise typer.Exit(1)
    capture_requested = capture_only or any(
        (
            capture_discord_events is not None,
            capture_guild_id is not None,
            capture_channel_id is not None,
            bool(capture_user_id),
            capture_include_message_text,
        )
    )
    if capture_requested and not capture_only:
        console.print("[red]Diagnostic capture options require --capture-only.[/red]")
        raise typer.Exit(1)
    if capture_only:
        missing_filters = [
            name
            for name, value in (
                ("--capture-discord-events", capture_discord_events),
                ("--capture-guild-id", capture_guild_id),
                ("--capture-channel-id", capture_channel_id),
                ("--mudae-user-id", mudae_user_id),
            )
            if value is None or not str(value).strip()
        ]
        if not capture_user_id:
            missing_filters.append("--capture-user-id")
        if missing_filters:
            console.print(
                "[red]--capture-only requires " + ", ".join(missing_filters) + ".[/red]"
            )
            raise typer.Exit(1)
        capture_path = Path(capture_discord_events or "").expanduser()
        repository_root = Path(__file__).resolve().parents[3]
        if not capture_path.is_absolute():
            console.print("[red]--capture-discord-events must be an absolute path.[/red]")
            raise typer.Exit(1)
        resolved_capture_path = capture_path.resolve(strict=False)
        if resolved_capture_path.is_dir():
            console.print("[red]--capture-discord-events must name a file, not a directory.[/red]")
            raise typer.Exit(1)
        if resolved_capture_path.is_relative_to(repository_root):
            console.print("[red]--capture-discord-events must be outside the repository.[/red]")
            raise typer.Exit(1)
        if resolved_capture_path.exists():
            console.print("[red]Diagnostic capture output already exists; refusing to overwrite it.[/red]")
            raise typer.Exit(1)
        if not resolved_capture_path.parent.is_dir():
            console.print(
                "[red]--capture-discord-events parent directory does not exist.[/red]"
            )
            raise typer.Exit(1)
        capture_ids = [capture_guild_id, capture_channel_id, mudae_user_id, *capture_user_id]
        if any(
            not value or not str(value).strip().isdigit() or int(str(value).strip()) <= 0
            for value in capture_ids
        ):
            console.print("[red]Diagnostic capture IDs must be positive numeric Discord IDs.[/red]")
            raise typer.Exit(1)
        console.print("[green]Starting database-free Discord diagnostic capture.[/green]")
        try:
            DiscordEventCaptureService(
                DiscordEventCaptureConfig(
                    output_path=resolved_capture_path,
                    guild_id=str(capture_guild_id),
                    channel_id=str(capture_channel_id),
                    mudae_user_id=str(mudae_user_id),
                    user_ids=frozenset(str(value) for value in capture_user_id),
                    enabled=True,
                    include_message_text=capture_include_message_text,
                )
            ).run(token)
        except (DiscordEventCaptureError, ValueError) as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        return
    parsed_mudae_user_id: int | None = None
    if mudae_user_id:
        try:
            parsed_mudae_user_id = int(mudae_user_id)
        except ValueError as error:
            console.print("[red]--mudae-user-id must be a numeric Discord user ID.[/red]")
            raise typer.Exit(1) from error
    console.print(
        "[green]Starting Discord listener.[/green] It requires Message Content Intent, "
        "View Channel, Read Message History, and reaction events."
    )
    logging.getLogger("moa.discord").setLevel(logging.INFO)
    try:
        database_path = Path(DEFAULT_DATABASE_PATH)
        catalog_repository = CatalogRepository(database_path)
        catalog_service = CatalogService(catalog_repository)
        discord_message_repository = DiscordMessageRepository(database_path)
        roll_projection_coordinator = RollProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        profile_projection_coordinator = ProfileProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        claim_projection_coordinator = ClaimProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        settings_projection_coordinator = SettingsProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        infokl_projection_coordinator = InfoklProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        timer_projection_coordinator = TimerProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        kakera_state_projection_coordinator = KakeraStateProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        kakeraloot_state_projection_coordinator = KakeralootStateProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        tower_state_projection_coordinator = TowerStateProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        sphere_result_projection_coordinator = SphereResultProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        player_bonus_projection_coordinator = PlayerBonusProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        disablelist_projection_coordinator = DisableListProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        wishlist_projection_coordinator = WishlistProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        antidisable_page_projection_coordinator = AntidisablePageProjectionCoordinator(
            catalog_repository,
            discord_message_repository,
        )
        importer = AutomaticImportService(
            catalog_service,
            roll_projection_coordinator=roll_projection_coordinator,
            profile_projection_coordinator=profile_projection_coordinator,
            claim_projection_coordinator=claim_projection_coordinator,
            settings_projection_coordinator=settings_projection_coordinator,
            infokl_projection_coordinator=infokl_projection_coordinator,
            timer_projection_coordinator=timer_projection_coordinator,
            kakera_state_projection_coordinator=kakera_state_projection_coordinator,
            kakeraloot_state_projection_coordinator=kakeraloot_state_projection_coordinator,
            tower_state_projection_coordinator=tower_state_projection_coordinator,
            sphere_result_projection_coordinator=sphere_result_projection_coordinator,
            player_bonus_projection_coordinator=player_bonus_projection_coordinator,
            disablelist_projection_coordinator=disablelist_projection_coordinator,
            wishlist_projection_coordinator=wishlist_projection_coordinator,
            antidisable_page_projection_coordinator=antidisable_page_projection_coordinator,
        )
        DiscordListenerService(
            catalog_service=catalog_service,
            importer=importer,
            database_path=database_path,
            profile_name=profile,
            status_text=status,
            discord_message_repository=discord_message_repository,
        ).run(token, parsed_mudae_user_id)
    except ListenerAlreadyRunningError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    except ListenerProcessGuardResourceError as error:
        console.print(f"[red]Listener ownership unavailable: {error}[/red]")
        raise typer.Exit(1) from error
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error


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


def _resolve_server_context(server: str | None) -> str:
    """Resolve explicit or configured server context for read-only commands."""
    if server:
        return server.strip()
    try:
        resolved_server, _ = ConfigService().resolve_context(None, None)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    if not resolved_server:
        console.print(
            "[red]No active server context. Configure one with `moa config use` "
            "or pass --server.[/red]"
        )
        raise typer.Exit(1)
    return resolved_server


def _format_optional_number(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _format_observed_toggle(value: bool | None) -> str:
    return str(value) if value is not None else "Unknown"


def _format_rollability(
    unavailable: bool | None,
    reason: str | None,
    owner_name: str | None = None,
    owner_is_self: bool | None = None,
    status: str | None = None,
) -> str:
    if status:
        return status
    if owner_is_self is True and owner_name:
        return "Claimed"
    if unavailable is None:
        return "Not requested"
    if not unavailable:
        return "Not observed unavailable"
    return f"Unavailable ({reason or 'disabled'})"


def _format_catalog_ownership(
    owned: bool | None,
    owner_name: str | None,
    owner_is_self: bool | None,
    topo_observed: bool | None,
) -> str:
    """Show direct harem evidence separately from server-scoped `$topo` claims."""
    if owner_name:
        return f"Claimed 💞 => {owner_name}"
    if owned:
        return "Claimed"
    if topo_observed:
        return "Unclaimed"
    return "(no data)"


def _format_catalog_keys(
    keyed: bool | None,
    key_type: str | None,
    key_count: int | None,
) -> str:
    """Render imported harem key evidence using Mudae's key marker format."""
    if keyed is None:
        return "Not requested"
    if not keyed or key_count is None:
        return "-"
    return format_mudae_key_marker(key_type, key_count)


def _format_optional_rank(value: int | None) -> str:
    return "-" if value is None else f"#{value:,}"


@app.command("analyze-roll")
def analyze_roll(
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account deciding what to do with this roll."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae roll card."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Explain a copied roll using directly imported account context."""
    server, account = _resolve_account_context(server, account)
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
    table.add_row("This roll's Kakera", format_mudae_kakera(analysis.kakera_value))
    if analysis.displayed_key_count is not None:
        table.add_row(
            "Displayed keys",
            format_mudae_key_marker(
                analysis.displayed_key_type, analysis.displayed_key_count
            ),
        )
    table.add_row("Wishlist", analysis.wishlist_state)
    table.add_row("Saved key state", analysis.keyed_harem_state)
    table.add_row("Rollability", analysis.rollability_state)
    table.add_row("Claim window", analysis.claim_window_state)
    console.print(table)
    console.print(
        "[dim]This is factual roll context, not a claim/skip recommendation. "
        "A missing keyed entry does not prove the character is unowned, and $tu state is not live.[/dim]"
    )


@import_app.command("auto")
def import_auto(
    server: str | None = typer.Option(None, "--server", "-s", help="Server label when the message needs one."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account name when the message needs one."),
    scan: int | None = typer.Option(None, "--scan", help="Optional harem or antidisable scan ID for a multi-page import."),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae response."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Detect and import one supported Mudae response using the existing import rules."""
    raw_message = _read_message_source(path, clipboard)
    source = "clipboard" if clipboard else f"file:{path}"
    try:
        result = AutomaticImportService().import_message(
            raw_message, source, server, account, harem_scan_id=scan
        )
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
    server: str | None = typer.Option(
        None,
        "--server",
        "-s",
        help="Server where `$topo` owner claims were observed.",
    ),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $top page."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist a `$top` or `$topo` page as a timestamped local rank snapshot."""
    raw_message = _read_message_source(path, clipboard)
    try:
        page = MudaeTextParser().parse_top_page(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    source = "clipboard" if clipboard else f"file:{path}"
    try:
        result = CatalogService().import_top_page(page, raw_message, source, server)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    total = CatalogService().character_count()
    console.print(
        f"[green]Imported {result.characters_imported} ranked characters.[/green] "
        f"Catalog now contains [cyan]{total}[/cyan] characters."
    )


@import_app.command("im")
def import_im(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the server this $im came from."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account that ran `$im`, if key evidence should be account-scoped."),
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
    result = CatalogService().import_character_details(details, server, raw_message, source, account)
    console.print(
        f"[green]Imported {details.name} for {result.server_name}.[/green] "
        f"Recorded [cyan]{format_mudae_kakera(details.kakera_value)}[/cyan]."
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


@import_app.command("mmr")
def import_mmr(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose harem is shown."),
    scan: int | None = typer.Option(
        None, "--scan", help="Optional owned-harem scan ID created by `moa harem begin --kind owned`."
    ),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $mmr/$mmrk/$mmrt page."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist one ranked `$mmr`/`$mmrk`/`$mmrt` owned-harem page."""
    raw_message = _read_message_source(path, clipboard)
    try:
        page = MudaeTextParser().parse_ranked_harem_page(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    source = "clipboard" if clipboard else f"file:{path}"
    result = CatalogService().import_ranked_harem_page(
        page,
        server,
        account,
        raw_message,
        source,
        scan,
    )
    console.print(
        f"[green]Imported {result.entries_imported} owned harem entries for "
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


@import_app.command("adl")
def import_adl(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose antidisable list is shown."),
    scan: int | None = typer.Option(
        None, "--scan", help="Optional complete ADL scan ID created by `moa adl begin`."
    ),
    path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $adl page."),
    clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
) -> None:
    """Parse and persist one `$adl` series-list page."""
    raw_message = _read_message_source(path, clipboard)
    try:
        page = MudaeTextParser().parse_antidisable_page(raw_message)
    except MudaeParseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    source = "clipboard" if clipboard else f"file:{path}"
    try:
        result = CatalogService().import_antidisable_page(
            page, server, account, raw_message, source, scan
        )
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    count_message = (
        f"[cyan]{page.antidisabled_character_count:,}[/cyan] antidisabled characters reported."
        if page.antidisabled_character_count is not None
        else "[dim]Character total is not repeated on this page.[/dim]"
    )
    console.print(
        f"[green]Imported {result.series_imported} antidisable series for {result.account_name}.[/green] "
        f"{count_message}"
    )
    if result.scan_id is not None and result.page_number is not None and result.page_count is not None:
        console.print(
            f"[cyan]Scan {result.scan_id}:[/cyan] saved page {result.page_number}/{result.page_count}. "
            f"Keep using [bold]--scan {result.scan_id}[/bold] for every remaining page."
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
    scan_kind: str = typer.Option(
        "keys", "--kind", help="Scan `keys` with $mmyk or `owned` with $mmrkty+."
    ),
) -> None:
    """Start a new multi-page harem scan that activates only when complete."""
    try:
        scan = CatalogService().begin_harem_scan(server, account, scan_kind)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    import_command = "mm" if scan.scan_kind == "keys" else "mmr"
    mudae_command = "$mmyk" if scan.scan_kind == "keys" else "$mmrkty+"
    console.print(
        f"[green]Started {scan.scan_kind} harem scan {scan.id}[/green] for [cyan]{scan.account_name}[/cyan].\n"
        f"In Discord, run [bold]{mudae_command}[/bold] and copy each full page.\n"
        "Import each Mudae page with:\n"
        f"[bold]uv run moa import {import_command} --scan {scan.id} --server {scan.server_name!r} "
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


@adl_app.command("begin")
def adl_begin(
    server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
    account: str = typer.Option(..., "--account", "-a", help="Account whose `$adl` list is being scanned."),
) -> None:
    """Start a complete multi-page `$adl` scan."""
    try:
        scan = CatalogService().begin_antidisable_scan(server, account)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    console.print(
        f"[green]Started antidisable scan {scan.id}[/green] for [cyan]{scan.account_name}[/cyan].\n"
        "In Discord, run `$adl` and copy each full page. Import page 1 with:\n"
        f"[bold]moa import adl --scan {scan.id} --server {scan.server_name!r} "
        f"--account {scan.account_name!r} --clipboard[/bold]"
    )


@adl_app.command("status")
def adl_status(scan_id: int) -> None:
    """Show pages captured for an antidisable scan."""
    scan = CatalogService().harem_scan_progress(scan_id)
    if scan is None or scan.scan_kind != "antidisable":
        console.print("[red]Antidisable scan not found.[/red]")
        raise typer.Exit(1)
    expected = str(scan.expected_page_count) if scan.expected_page_count is not None else "unknown"
    captured = ", ".join(str(page) for page in scan.imported_pages) or "none"
    status = "complete" if scan.completed_at is not None else "in progress"
    console.print(
        f"[bold cyan]Antidisable scan {scan.id}[/bold cyan] — {scan.server_name} / {scan.account_name}\n"
        f"Pages: {captured} of {expected} · Status: {status}"
    )


@adl_app.command("complete")
def adl_complete(scan_id: int) -> None:
    """Validate and activate a fully imported `$adl` scan."""
    try:
        scan = CatalogService().complete_antidisable_scan(scan_id)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    console.print(
        f"[green]Antidisable scan {scan.id} is complete and active[/green] for "
        f"{scan.server_name} / {scan.account_name}."
    )


@data_health_app.command("orphans")
def catalog_data_health_orphans() -> None:
    """Report physical and audited logical orphan findings without repairs."""
    try:
        findings = DataHealthService(Path(DEFAULT_DATABASE_PATH)).find_orphans()
    except (DataHealthSchemaError, OSError, ValueError, sqlite3.Error) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if not findings:
        console.print("No data-health findings.")
        return

    table = Table(title="Data-health orphan findings")
    table.add_column("Check ID", style="cyan")
    table.add_column("Category")
    table.add_column("Entity", style="green")
    table.add_column("Local identifier")
    table.add_column("Reason")
    for finding in findings:
        table.add_row(
            finding.check_id,
            finding.category,
            finding.entity,
            str(finding.local_identifier),
            finding.reason,
        )
    console.print(table)
    console.print(f"Total findings: {len(findings)}")


@data_health_app.command("impossible-identities")
def catalog_data_health_impossible_identities() -> None:
    """Report impossible identity findings without repairs."""
    try:
        findings = DataHealthService(Path(DEFAULT_DATABASE_PATH)).find_impossible_identities()
    except (DataHealthSchemaError, OSError, ValueError, sqlite3.Error) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if not findings:
        console.print("No data-health findings.")
        return

    table = Table(title="Data-health impossible identity findings")
    table.add_column("Check ID", style="cyan")
    table.add_column("Category")
    table.add_column("Entity", style="green")
    table.add_column("Local identifier")
    table.add_column("Reason")
    for finding in findings:
        table.add_row(
            finding.check_id,
            finding.category,
            finding.entity,
            str(finding.local_identifier),
            finding.reason,
        )
    console.print(table)
    console.print(f"Total findings: {len(findings)}")


@data_health_app.command("duplicates")
def catalog_data_health_duplicates() -> None:
    """Report duplicate durable business identities without repairs."""
    try:
        findings = DataHealthService(Path(DEFAULT_DATABASE_PATH)).find_duplicates()
    except (DataHealthSchemaError, OSError, ValueError, sqlite3.Error) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if not findings:
        console.print("No data-health findings.")
        return

    table = Table(title="Data-health duplicate findings")
    table.add_column("Check ID", style="cyan")
    table.add_column("Category")
    table.add_column("Entity", style="green")
    table.add_column("Local identifier")
    table.add_column("Reason")
    for finding in findings:
        table.add_row(
            finding.check_id,
            finding.category,
            finding.entity,
            str(finding.local_identifier),
            finding.reason,
        )
    console.print(table)
    console.print(f"Total findings: {len(findings)}")


@data_health_app.command("projection-gaps")
def catalog_data_health_projection_gaps() -> None:
    """Report completed projection links owned by non-succeeded source events."""
    try:
        findings = DataHealthService(Path(DEFAULT_DATABASE_PATH)).find_projection_gaps()
    except (DataHealthSchemaError, OSError, ValueError, sqlite3.Error) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if not findings:
        console.print("No data-health findings.")
        return

    table = Table(title="Data-health projection-gap findings")
    table.add_column("Check ID", style="cyan")
    table.add_column("Category")
    table.add_column("Entity", style="green")
    table.add_column("Local identifier")
    table.add_column("Reason")
    for finding in findings:
        table.add_row(
            finding.check_id,
            finding.category,
            finding.entity,
            str(finding.local_identifier),
            finding.reason,
        )
    console.print(table)
    console.print(f"Total findings: {len(findings)}")


@data_health_app.command("retention")
def catalog_data_health_retention(
    apply: bool = typer.Option(
        False,
        "--apply",
        help=(
            "Intentionally remove eligible raw evidence. Historical raw repair/reparse may "
            "become unavailable; this does not guarantee forensic secure erasure."
        ),
    ),
) -> None:
    """Report retention eligibility, or explicitly apply logical evidence expiry."""
    if apply:
        try:
            result = RetentionExpiryService(Path(DEFAULT_DATABASE_PATH)).apply()
        except (
            DataHealthSchemaError,
            RetentionEligibilityDataError,
            RetentionExpiryError,
            OSError,
            ValueError,
            TypeError,
            sqlite3.Error,
        ):
            console.print(
                "[red]Unable to apply raw-evidence retention expiry; "
                "no success is claimed.[/red]"
            )
            raise typer.Exit(1) from None

        try:
            _render_retention_expiry_result(result)
        except Exception:
            typer.echo(
                "Retention expiry committed, but presentation failed; do not re-run automatically.",
                err=True,
            )
            raise typer.Exit(1) from None
        return

    try:
        report = RetentionEligibilityService(Path(DEFAULT_DATABASE_PATH)).report()
    except (
        DataHealthSchemaError,
        RetentionEligibilityDataError,
        OSError,
        ValueError,
        TypeError,
        sqlite3.Error,
    ):
        console.print("[red]Unable to generate the retention eligibility report.[/red]")
        raise typer.Exit(1) from None

    console.print(
        f"[bold cyan]Retention eligibility[/bold cyan] — as of {report.as_of.isoformat()} "
        f"(cutoff {report.cutoff.isoformat()})"
    )
    table = Table()
    table.add_column("Category", style="cyan")
    table.add_column("Eligible", justify="right")
    table.add_column("Retained/blocked", justify="right")
    table.add_column("Already expired", justify="right")
    table.add_column("Absent", justify="right")
    table.add_column("Oldest eligible anchor")
    table.add_column("Newest eligible anchor")
    table.add_column("Blocked reasons")
    for category in report.categories:
        reasons = ", ".join(f"{reason}={count}" for reason, count in category.blocked_reason_counts)
        table.add_row(
            category.category,
            str(category.eligible_count),
            str(category.retained_blocked_count),
            str(category.already_expired_count),
            str(category.absent_count),
            category.oldest_eligible_anchor.isoformat()
            if category.oldest_eligible_anchor is not None
            else "-",
            category.newest_eligible_anchor.isoformat()
            if category.newest_eligible_anchor is not None
            else "-",
            reasons or "-",
        )
    console.print(table)


def _render_retention_expiry_result(result: RetentionExpiryResult) -> None:
    console.print(
        f"[bold cyan]Retention expiry applied[/bold cyan] — committed at "
        f"{result.apply_as_of.isoformat()} (cutoff {result.cutoff.isoformat()})"
    )
    table = Table()
    table.add_column("Category", style="cyan")
    table.add_column("Recomputed eligible", justify="right")
    table.add_column("Expired", justify="right")
    table.add_column("Retained/blocked", justify="right")
    table.add_column("Already expired", justify="right")
    table.add_column("Absent", justify="right")
    table.add_column("Blocked reasons")
    for category in result.categories:
        reasons = ", ".join(
            f"{reason}={count}" for reason, count in category.blocked_reason_counts
        )
        table.add_row(
            category.category,
            str(category.recomputed_eligible_count),
            str(category.expired_count),
            str(category.retained_blocked_count),
            str(category.already_expired_count),
            str(category.absent_count),
            reasons or "-",
        )
    console.print(table)


@catalog_app.command("top")
def catalog_top(
    limit: int = typer.Option(15, "--limit", "-n", min=1, help="Number of characters to display."),
    server: str | None = typer.Option(None, "--server", "-s", help="Server for account evidence filters."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account for account evidence filters."),
    series: str | None = typer.Option(None, "--series", help="Case-insensitive series text filter."),
    exact_series: bool = typer.Option(False, "--exact-series", help="Require an exact series match."),
    owned_only: bool = typer.Option(False, "--owned-only", help="Only characters directly observed in the account's $mm harem."),
    unowned_only: bool = typer.Option(False, "--unowned-only", help="Only characters absent from a complete owned-harem scan."),
    keyed_only: bool = typer.Option(False, "--keyed-only", help="Only characters with imported key evidence."),
    unavailable_only: bool = typer.Option(False, "--unavailable-only", help="Only characters observed unavailable by $topx or claimed in imported $topo."),
    sort_by: str = typer.Option("rank", "--sort", help="Sort by rank or name."),
) -> None:
    """Search imported `$top` ranks with optional account evidence filters."""
    config_service = ConfigService()
    try:
        server, account = config_service.resolve_context(server, account)
        owned_account_names = (
            config_service.owned_account_names(server) if server and account else None
        )
        characters = TopSearchService().search(
            server_name=server,
            account_name=account,
            series=series,
            exact_series=exact_series,
            owned_only=owned_only,
            unowned_only=unowned_only,
            owned_account_names=owned_account_names,
            keyed_only=keyed_only,
            unavailable_only=unavailable_only,
            sort_by=sort_by,
            limit=limit,
        )
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if not characters:
        console.print("[yellow]No imported `$top` characters matched the requested filters.[/yellow]")
        raise typer.Exit()

    table = Table(title="Imported Character Catalog Search")
    table.add_column("Claim rank", justify="right", style="cyan")
    table.add_column("Character", style="green")
    table.add_column("Series")
    table.add_column("Ownership")
    table.add_column("Keys")
    table.add_column("Kakera value")
    table.add_column("Roulette")
    table.add_column("Gender")
    table.add_column("Rollability")
    table.add_column("Observed (UTC)")
    for character in characters:
        ownership = _format_catalog_ownership(
            character.owned,
            character.owner_name,
            character.owner_is_self,
            character.topo_observed,
        )
        key_state = _format_catalog_keys(
            character.keyed,
            character.key_type,
            character.key_count,
        )
        rollability = _format_rollability(
            character.unavailable,
            character.unavailable_reason,
            character.owner_name,
            character.owner_is_self,
            character.rollability_status,
        )
        table.add_row(
            f"#{character.claim_rank:,}",
            character.character.name,
            character.character.series,
            ownership,
            key_state,
            format_mudae_kakera(character.kakera_value),
            format_mudae_roulette_types(character.roulette_types),
            format_mudae_gender(character.character.gender),
            rollability,
            character.observed_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)
    if server and account:
        console.print(
            "[dim]Missing owned evidence does not prove unowned; one $mm page is not a complete harem snapshot. "
            "A dash in Keys means no imported key row for that character. Unknown means no explicit rollability evidence; "
            "import fresh $topx/$adl data for stronger rollability evidence.[/dim]"
        )


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
            format_mudae_kakera(observation.kakera_value),
            observation.observed_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@catalog_app.command("harem")
def catalog_harem(
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account whose harem to show."),
    series: str | None = typer.Option(None, "--series", help="Case-insensitive series text filter."),
    exact_series: bool = typer.Option(False, "--exact-series", help="Require an exact series match."),
    key_type: str | None = typer.Option(None, "--key-type", help="Filter by key tier, such as gold."),
    min_keys: int | None = typer.Option(None, "--min-keys", help="Minimum imported key count."),
    max_keys: int | None = typer.Option(None, "--max-keys", help="Maximum imported key count."),
    min_kakera: int | None = typer.Option(None, "--min-kakera", help="Minimum imported Kakera value."),
    unresolved_only: bool = typer.Option(False, "--unresolved-only", help="Only entries still needing $im identity data."),
    sort_by: str = typer.Option("kakera", "--sort", help="Sort by kakera, keys, name, or observed."),
    limit: int | None = typer.Option(None, "--limit", "-n", min=1, help="Maximum matching entries."),
) -> None:
    """Search imported keyed-harem observations for one server/account pair."""
    server, account = _resolve_account_context(server, account)
    try:
        entries = HaremSearchService().search(
            server,
            account,
            series=series,
            exact_series=exact_series,
            key_type=key_type,
            min_keys=min_keys,
            max_keys=max_keys,
            min_kakera=min_kakera,
            unresolved_only=unresolved_only,
            sort_by=sort_by,
            limit=limit,
        )
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    if not entries:
        console.print("[yellow]No keyed-harem entries matched the requested filters.[/yellow]")
        raise typer.Exit()

    table = Table(title=f"{account} - keyed harem search")
    table.add_column("Character", style="green")
    table.add_column("Series")
    table.add_column("Keys", justify="right", style="cyan")
    table.add_column("Kakera", justify="right", style="magenta")
    table.add_column("Catalog link")
    table.add_column("Observed (UTC)")
    for entry in entries:
        table.add_row(
            entry.character_name,
            entry.character.series if entry.character else "Needs $im",
            format_mudae_key_marker(entry.key_type, entry.key_count),
            format_mudae_kakera(entry.kakera_value),
            "Resolved" if entry.character else "Needs $im",
            entry.observed_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)
    if any(entry.character is None for entry in entries):
        console.print(
            "[dim]Unresolved entries cannot be matched by series until a matching $im import provides identity data.[/dim]"
        )


@catalog_app.command("keyfarm")
def catalog_keyfarm(
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account whose harem to shortlist."),
    limit: int = typer.Option(15, "--limit", "-n", min=1, help="Number of entries to display."),
) -> None:
    """Show the highest-value imported keyed characters for a future key-farm plan."""
    server, account = _resolve_account_context(server, account)
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
            format_mudae_kakera(entry.kakera_value),
            format_mudae_key_marker(entry.key_type, entry.key_count),
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
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account whose key progress to show."),
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="Number of entries to display."),
) -> None:
    """Show each imported harem character's next universal key unlock."""
    server, account = _resolve_account_context(server, account)
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
            format_mudae_key_marker(entry.current_tier, entry.key_count),
            entry.current_tier,
            str(entry.next_milestone_key_count) if entry.next_milestone_key_count is not None else "-",
            str(entry.keys_until_next_milestone) if entry.keys_until_next_milestone is not None else "-",
            "\n".join(entry.next_effects),
        )
    console.print(table)
    console.print(
        "[dim]This explains the next key unlock only; it does not yet estimate how often each character rolls.[/dim]"
    )


@catalog_app.command("key-gains")
def catalog_key_gains(
    server: str | None = typer.Option(None, "--server", "-s"),
    account: str | None = typer.Option(None, "--account", "-a"),
    limit: int = typer.Option(20, "--limit", "-n", min=1),
) -> None:
    """Show recent key states directly observed on imported rolls."""
    server, account = _resolve_account_context(server, account)
    observations = CatalogService().recent_key_gains(server, account, limit)
    if not observations:
        console.print("[yellow]No key gains imported from rolls for this server/account yet.[/yellow]")
        raise typer.Exit()
    table = Table(title=f"{account} - recent key gains")
    table.add_column("Observed (UTC)")
    table.add_column("Character", style="green")
    table.add_column("Keys", justify="right", style="cyan")
    table.add_column("Kakera", justify="right", style="magenta")
    for observation in observations:
        table.add_row(
            observation.observed_at.strftime("%Y-%m-%d %H:%M"),
            observation.character_name,
            format_mudae_key_marker(observation.key_type, observation.key_count),
            format_mudae_kakera(observation.kakera_value),
        )
    console.print(table)


@catalog_app.command("bonus")
def catalog_bonus(
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account whose bonus snapshot to show."),
) -> None:
    """Show the latest imported `$bonus` snapshot for one account."""
    server, account = _resolve_account_context(server, account)
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
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account whose wishlist to show."),
) -> None:
    """Show the latest imported `$wl` snapshot for one account."""
    server, account = _resolve_account_context(server, account)
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
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account whose disable list to show."),
) -> None:
    """Show the latest imported `$dl` snapshot for one account."""
    server, account = _resolve_account_context(server, account)
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
        f"Western disabled: {_format_observed_toggle(disablelist.western_disabled)} · "
        f"IRL disabled: {_format_observed_toggle(disablelist.irl_disabled)}"
    )
    table = Table()
    table.add_column("Disabled bundle", style="green")
    table.add_column("Characters", justify="right", style="cyan")
    for entry in disablelist.entries:
        table.add_row(entry.name, f"{entry.disabled_count:,}")
    console.print(table)


@catalog_app.command("unavailable")
def catalog_unavailable(
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account whose roll pool to show."),
) -> None:
    """Show characters directly observed as unavailable by `$topx`."""
    server, account = _resolve_account_context(server, account)
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
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account whose Kakera state to show."),
) -> None:
    """Show the latest imported `$k` snapshot for one account."""
    server, account = _resolve_account_context(server, account)
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
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account whose tower state to show."),
) -> None:
    """Show the latest imported `$kt` snapshot for one account."""
    server, account = _resolve_account_context(server, account)
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
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account whose latest $tu snapshot to show."),
) -> None:
    """Show the most recently imported `$tu` snapshot without treating it as live state."""
    server, account = _resolve_account_context(server, account)
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
    elif state.rolls_reset_status == "limited_timer":
        table.add_row(
            "Rolls",
            f"Limited to {state.rolls_per_hour_limit} per hour; reset in {state.rolls_reset_minutes} min",
        )
    elif state.rolls_reset_status == "vote_required":
        table.add_row("Rolls", "Vote required to reset")
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
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    account: str | None = typer.Option(None, "--account", "-a", help="Account whose Kakeraloot state to show."),
) -> None:
    """Show the latest imported `$lk` snapshot for one account."""
    server, account = _resolve_account_context(server, account)
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
    table.add_row("Kakera balance", _format_optional_number(state.kakera_balance))
    table.add_row("$kl usage", _format_optional_number(state.usage_count))
    table.add_row(
        "Quantity / Quality",
        "-"
        if state.quantity_level is None or state.quality_level is None
        else f"{state.quantity_level} / {state.quality_level}",
    )
    table.add_row("Rolls stacked", _format_optional_number(state.rolls_stacked))
    table.add_row(
        "Permanent rolls",
        "-" if state.permanent_roll_bonus is None else f"+{state.permanent_roll_bonus}",
    )
    table.add_row(
        "Wishprotect",
        "-"
        if state.protected_wish_level is None or state.protected_wish_denominator is None
        else f"LVL {state.protected_wish_level} (1/{state.protected_wish_denominator:,})",
    )
    table.add_row(
        "$disable reduction",
        "-"
        if state.disable_wa_ha_reduction is None or state.disable_wg_hg_reduction is None
        else f"-{state.disable_wa_ha_reduction} $wa/$ha · -{state.disable_wg_hg_reduction} $wg/$hg",
    )
    table.add_row(
        "$rt cooldown",
        "-"
        if state.rt_cooldown_reduction_hours is None
        else f"-{state.rt_cooldown_reduction_hours}h",
    )
    table.add_row("Mudapins", _format_optional_number(state.mudapins))
    table.add_row(
        "Star branches",
        "-"
        if state.star_branches is None or state.starwish_slots_from_branches is None
        else f"{state.star_branches} (+{state.starwish_slots_from_branches} $sw)",
    )
    console.print(table)
    console.print(f"[dim]Observed: {state.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]")


@catalog_app.command("infokl")
def catalog_infokl(
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
) -> None:
    """Show the latest imported `$infokl` configuration for one server."""
    server = _resolve_server_context(server)
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
    server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
) -> None:
    """Show the latest imported `$settings` snapshot for one server."""
    server = _resolve_server_context(server)
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
    server: str | None = typer.Option(None, "--server", "-s"),
    account: str | None = typer.Option(None, "--account", "-a"),
) -> None:
    """Show recent standalone Kakera payouts reported by Mudae."""
    server, account = _resolve_account_context(server, account)
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


@catalog_app.command("spheres")
def catalog_spheres(
    server: str | None = typer.Option(None, "--server", "-s"),
    account: str | None = typer.Option(None, "--account", "-a"),
) -> None:
    """Show the latest imported `$oq` sphere payout."""
    server, account = _resolve_account_context(server, account)
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
        f"Total gained: [cyan]+{snapshot.total_gained:,}[/cyan] spheres · Stock: [cyan]{stock}[/cyan] · "
        f"Observed: {observation.observed_at.strftime('%Y-%m-%d %H:%M UTC')}"
    )


@catalog_app.command("reaction-summary")
def catalog_reaction_summary(
    server: str | None = typer.Option(None, "--server", "-s"),
    account: str | None = typer.Option(None, "--account", "-a"),
) -> None:
    """Summarize Kakera-reaction receipts stored for one account."""
    server, account = _resolve_account_context(server, account)
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
    try:
        deleted = CatalogService().delete_import_event(import_event_id)
    except ImportEventDeletionBlockedError:
        console.print(
            "[red]Deletion blocked: this import belongs to durable/replayable source state.[/red]"
        )
        raise typer.Exit(1) from None
    if not deleted:
        console.print("[red]Import event not found.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Deleted import event {import_event_id}.[/green]")


@catalog_app.command("reset")
def catalog_reset(
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Delete the current catalog after making a timestamped backup.",
    ),
) -> None:
    """Reset imported catalog data while preserving the MOA configuration."""
    database_path = Path(DEFAULT_DATABASE_PATH)
    if not confirm:
        console.print(
            "[yellow]No changes made. This removes all imported catalog data but keeps your "
            "MOA config.[/yellow]"
        )
        console.print("Run `uv run moa catalog reset --confirm` after stopping the listener.")
        return

    if not database_path.exists():
        console.print("[green]No catalog database exists; it will be created on the next import.[/green]")
        return

    backup_path = database_path.with_name(
        f"{database_path.name}.bak-full-reset-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    suffix = 1
    while backup_path.exists():
        backup_path = database_path.with_name(
            f"{database_path.name}.bak-full-reset-"
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{suffix}"
        )
        suffix += 1
    shutil.copy2(database_path, backup_path)
    database_path.unlink()
    console.print("[green]Catalog database reset. MOA config was preserved.[/green]")
    console.print(f"Backup saved to: {backup_path}")


@catalog_app.command("relocate-database")
def catalog_relocate_database(
    source: Path = typer.Argument(..., help="Explicit legacy MOA database path."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Create the new database and retire the explicit source path.",
    ),
) -> None:
    """Move database authority to MOA's per-user application-data location."""
    target = default_database_path().resolve(strict=False)
    resolved_source = source.expanduser().resolve(strict=False)
    console.print(f"Source: {resolved_source}")
    console.print(f"Target: {target}")
    console.print("[yellow]The Discord listener must be stopped before relocation.[/yellow]")
    console.print(
        "[yellow]Do not resume old MOA checkouts that write the legacy database after "
        "relocation; no cross-version synchronization is provided.[/yellow]"
    )
    if not apply:
        console.print("[yellow]No changes made. Rerun with --apply after stopping the listener.[/yellow]")
        return
    try:
        result = relocate_database(resolved_source, target)
    except DatabaseRelocationError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    console.print(f"[green]Database relocated to: {result.target}[/green]")
    console.print(f"Legacy source archived at: {result.source_archive}")


@catalog_app.command("repair-bugged-data")
def catalog_repair_bugged_data(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the targeted cleanup. Without this flag, only a dry-run report is shown.",
    ),
) -> None:
    """Remove known timer-as-roll imports and orphaned malformed characters."""
    service = CatalogService()
    import_count, character_count = service.inspect_bugged_imports()
    if not apply:
        console.print(
            f"Found {import_count} suspicious import event(s) and "
            f"{character_count} suspicious character row(s)."
        )
        console.print(
            "[yellow]Dry run only; no database changes were made. "
            "Stop the Discord listener, then rerun with --apply to clean these candidates.[/yellow]"
        )
        return

    if import_count == 0 and character_count == 0:
        console.print("[green]No targeted bugged data was found; nothing changed.[/green]")
        return

    database_path = Path(DEFAULT_DATABASE_PATH)
    backup_path = database_path.with_name(
        f"{database_path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    suffix = 1
    while backup_path.exists():
        backup_path = database_path.with_name(
            f"{database_path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{suffix}"
        )
        suffix += 1
    shutil.copy2(database_path, backup_path)

    cleaned_imports, deleted_characters = service.repair_bugged_imports()
    console.print(
        f"[green]Cleaned {cleaned_imports} suspicious import event(s) "
        "(timer misimports removed; stale character links repaired).[/green]"
    )
    console.print(f"[green]Deleted {deleted_characters} orphaned character row(s).[/green]")
    console.print(f"Backup saved to: {backup_path}")


if __name__ == "__main__":
    app()
