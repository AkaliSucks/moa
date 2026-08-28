from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console

from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.services.catalog_service import CatalogService
from moa.utils.display import format_mudae_kakera


def register_direct_import_commands(
    import_app: typer.Typer,
    console: Console,
    read_message_source: Callable[[Path | None, bool], str],
) -> None:
    @import_app.command("reaction")
    def import_reaction(
        server: str = typer.Option(..., "--server", "-s"),
        path: Path | None = typer.Argument(None),
        clipboard: bool = typer.Option(False, "--clipboard", "-c"),
    ) -> None:
        """Parse and persist one standalone Mudae Kakera-reaction receipt."""
        raw_message = read_message_source(path, clipboard)
        try:
            receipt = MudaeTextParser().parse_kakera_reaction_receipt(raw_message)
        except MudaeParseError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        result = CatalogService().import_kakera_reaction(
            receipt, server, raw_message, "clipboard" if clipboard else f"file:{path}"
        )
        console.print(f"[green]Imported +{receipt.kakera_earned:,} Kakera for {result.account_name}.[/green]")

    @import_app.command("im")
    def import_im(
        server: str = typer.Option(..., "--server", "-s", help="Your label for the server this $im came from."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account that ran `$im`, if key evidence should be account-scoped."),
        path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $im response."),
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Parse and persist one `$im` response with its server-specific Kakera value."""
        raw_message = read_message_source(path, clipboard)
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

    @import_app.command("bonus")
    def import_bonus(
        server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
        account: str = typer.Option(..., "--account", "-a", help="Account whose bonuses are shown."),
        path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $bonus response."),
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Parse and persist one `$bonus` response as account-scoped player state."""
        raw_message = read_message_source(path, clipboard)
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
        raw_message = read_message_source(path, clipboard)
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
        raw_message = read_message_source(path, clipboard)
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
        raw_message = read_message_source(path, clipboard)
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
        raw_message = read_message_source(path, clipboard)
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
        raw_message = read_message_source(path, clipboard)
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
        raw_message = read_message_source(path, clipboard)
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
        raw_message = read_message_source(path, clipboard)
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
        raw_message = read_message_source(path, clipboard)
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
        raw_message = read_message_source(path, clipboard)
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
        raw_message = read_message_source(path, clipboard)
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
