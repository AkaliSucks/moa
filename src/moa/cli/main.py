from datetime import datetime
import logging
import shutil
from pathlib import Path

import typer
from rich.console import Console

from moa.cli.action_commands import build_action_app
from moa.cli.adl_commands import build_adl_app
from moa.cli.account_commands import build_account_app
from moa.core.config import ConfigService
from moa.cli.badge_commands import build_badge_app
from moa.cli.command_commands import build_command_app
from moa.cli.config_commands import build_config_app
from moa.cli.catalog_operational_commands import build_catalog_operational_app
from moa.cli.catalog_delete_import_commands import register_catalog_delete_import_command
from moa.cli.catalog_relocate_database_commands import (
    register_catalog_relocate_database_command,
)
from moa.cli.catalog_reset_commands import register_catalog_reset_command
from moa.cli.catalog_search_commands import build_catalog_search_app
from moa.cli.catalog_snapshot_commands import build_catalog_snapshot_app
from moa.cli.data_health_commands import build_data_health_app
from moa.cli.analyze_roll_commands import register_analyze_roll_command
from moa.cli.detect_commands import register_detect_command
from moa.cli.key_commands import build_key_app
from moa.cli.loot_commands import build_loot_app
from moa.cli.harem_commands import build_harem_app
from moa.cli.import_direct_commands import register_direct_import_commands
from moa.cli.import_workflow_commands import register_import_workflow_commands
from moa.cli.parse_commands import build_parse_app
from moa.cli.reaction_commands import build_reaction_app
from moa.cli.recommend_commands import build_recommend_app
from moa.cli.roll_commands import build_roll_app
from moa.cli.server_commands import build_server_app
from moa.cli.tower_commands import build_tower_app
from moa.database.sqlite import DEFAULT_DATABASE_PATH, default_database_path
from moa.repositories.catalog_repository import (
    CatalogRepository,
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
from moa.services.infokl_projection_coordinator import InfoklProjectionCoordinator
from moa.services.kakera_state_projection_coordinator import KakeraStateProjectionCoordinator
from moa.services.kakeraloot_state_projection_coordinator import KakeralootStateProjectionCoordinator
from moa.services.listener_process_guard import (
    ListenerAlreadyRunningError,
    ListenerProcessGuardResourceError,
)
from moa.services.profile_projection_coordinator import ProfileProjectionCoordinator
from moa.services.player_bonus_projection_coordinator import PlayerBonusProjectionCoordinator
from moa.services.roll_projection_coordinator import RollProjectionCoordinator
from moa.services.settings_projection_coordinator import SettingsProjectionCoordinator
from moa.services.sphere_result_projection_coordinator import SphereResultProjectionCoordinator
from moa.services.timer_projection_coordinator import TimerProjectionCoordinator
from moa.services.tower_state_projection_coordinator import TowerStateProjectionCoordinator
from moa.services.wishlist_projection_coordinator import WishlistProjectionCoordinator
app = typer.Typer(help="MOA - Mudae Optimization Assistant")
import_app = typer.Typer(help="Save parsed Mudae data to the local catalog")
catalog_app = typer.Typer(help="Browse MOA's local character catalog")
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
harem_app = build_harem_app(console)
adl_app = build_adl_app(console)
parse_app = build_parse_app(
    console,
    lambda path, clipboard: _read_message_source(path, clipboard),
)
data_health_app = build_data_health_app(
    console,
    lambda: DEFAULT_DATABASE_PATH,
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


register_direct_import_commands(
    import_app,
    console,
    lambda path, clipboard: _read_message_source(path, clipboard),
)
register_import_workflow_commands(
    import_app,
    console,
    lambda path, clipboard: _read_message_source(path, clipboard),
)


register_detect_command(
    app,
    console,
    lambda path, clipboard: _read_message_source(path, clipboard),
)
register_analyze_roll_command(
    app,
    console,
    lambda path, clipboard: _read_message_source(path, clipboard),
    lambda server, account: _resolve_account_context(server, account),
)


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


def _format_optional_rank(value: int | None) -> str:
    return "-" if value is None else f"#{value:,}"


catalog_search_app = build_catalog_search_app(
    console,
    lambda server, account: _resolve_account_context(server, account),
    lambda: ConfigService(),
    _format_optional_rank,
)
catalog_app.add_typer(catalog_search_app)

catalog_snapshot_app = build_catalog_snapshot_app(
    console,
    lambda server, account: _resolve_account_context(server, account),
    lambda server: _resolve_server_context(server),
)
catalog_app.add_typer(catalog_snapshot_app)

catalog_operational_app = build_catalog_operational_app(
    console,
    lambda server, account: _resolve_account_context(server, account),
    _format_optional_rank,
)
catalog_app.add_typer(catalog_operational_app)
register_catalog_delete_import_command(catalog_app, console)
register_catalog_reset_command(catalog_app, console, lambda: DEFAULT_DATABASE_PATH)
register_catalog_relocate_database_command(
    catalog_app,
    console,
    lambda: default_database_path(),
)


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
