import logging
from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console

from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.antidisable_page_projection_coordinator import (
    AntidisablePageProjectionCoordinator,
)
from moa.services.automatic_import_service import AutomaticImportService
from moa.services.catalog_service import CatalogService
from moa.services.claim_projection_coordinator import ClaimProjectionCoordinator
from moa.services.disablelist_projection_coordinator import DisableListProjectionCoordinator
from moa.services.discord_listener_service import (
    DiscordEventCaptureConfig,
    DiscordEventCaptureError,
    DiscordEventCaptureService,
    DiscordListenerService,
)
from moa.services.infokl_projection_coordinator import InfoklProjectionCoordinator
from moa.services.kakera_state_projection_coordinator import KakeraStateProjectionCoordinator
from moa.services.kakeraloot_state_projection_coordinator import (
    KakeralootStateProjectionCoordinator,
)
from moa.services.listener_process_guard import (
    ListenerAlreadyRunningError,
    ListenerProcessGuardResourceError,
)
from moa.services.player_bonus_projection_coordinator import PlayerBonusProjectionCoordinator
from moa.services.profile_projection_coordinator import ProfileProjectionCoordinator
from moa.services.roll_projection_coordinator import RollProjectionCoordinator
from moa.services.settings_projection_coordinator import SettingsProjectionCoordinator
from moa.services.sphere_result_projection_coordinator import SphereResultProjectionCoordinator
from moa.services.timer_projection_coordinator import TimerProjectionCoordinator
from moa.services.tower_state_projection_coordinator import TowerStateProjectionCoordinator
from moa.services.wishlist_projection_coordinator import WishlistProjectionCoordinator


def build_discord_app(
    console: Console,
    database_path_provider: Callable[[], Path],
) -> typer.Typer:
    """Build the Discord listener command tree using shared CLI seams."""
    discord_app = typer.Typer(help="Listen for Mudae messages through a Discord bot")

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
            database_path = Path(database_path_provider())
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

    return discord_app
