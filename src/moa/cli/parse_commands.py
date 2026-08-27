from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.utils.display import (
    format_mudae_gender,
    format_mudae_kakera,
    format_mudae_key_marker,
    format_mudae_reaction_kakera,
    format_mudae_roulette_types,
)


def build_parse_app(
    console: Console,
    read_message_source,
) -> typer.Typer:
    parse_app = typer.Typer(help="Parse copied Mudae bot output")

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
            page = MudaeTextParser().parse_top_page(read_message_source(path, clipboard))
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
            character = MudaeTextParser().parse_character_details(read_message_source(path, clipboard))
        except MudaeParseError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error

        console.print(f"[bold cyan]{character.name}[/bold cyan] — {character.series}")
        console.print(f"[bold]Claim rank:[/bold] {_format_optional_rank(character.claim_rank)}")
        console.print(f"[bold]Like rank:[/bold] {_format_optional_rank(character.like_rank)}")
        console.print(f"[bold]Kakera value:[/bold] {format_mudae_kakera(character.kakera_value)}")
        console.print(f"[bold]Gender:[/bold] {format_mudae_gender(character.gender)}")
        console.print(f"[bold]Key:[/bold] {format_mudae_key_marker(character.key_type, character.key_count)}")


    @parse_app.command("roll")
    def parse_roll(
        path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae roll card."),
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Parse one copied Mudae roll card from a file or the clipboard."""
        try:
            roll = MudaeTextParser().parse_roll(read_message_source(path, clipboard))
        except MudaeParseError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error

        console.print(f"[bold cyan]{roll.name}[/bold cyan] — {roll.series}")
        console.print(f"[bold]Claim rank:[/bold] {_format_optional_rank(roll.claim_rank)}")
        console.print(f"[bold]Kakera value:[/bold] {format_mudae_kakera(roll.kakera_value)}")
        if roll.displayed_key_count is not None:
            console.print(
                f"[bold]Displayed keys:[/bold] "
                f"{format_mudae_key_marker(roll.displayed_key_type, roll.displayed_key_count)}"
            )


    @parse_app.command("reaction")
    def parse_kakera_reaction(
        path: Path | None = typer.Argument(None, help="Text file containing one Mudae reaction receipt."),
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Parse a standalone Mudae Kakera-reaction receipt."""
        try:
            receipt = MudaeTextParser().parse_kakera_reaction_receipt(read_message_source(path, clipboard))
        except MudaeParseError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        console.print(
            f"[bold cyan]{receipt.account_name}[/bold cyan] received "
            f"[green]{format_mudae_reaction_kakera(receipt.kakera_earned, receipt.reaction_label)}[/green]"
        )


    @parse_app.command("mm")
    def parse_mm(
        path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $mmy= page."),
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Parse one copied Mudae `$mmy=`/`$mmyk=` keyed-harem page."""
        try:
            page = MudaeTextParser().parse_harem_key_page(read_message_source(path, clipboard))
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
        table.add_column("Keys", justify="right", style="cyan")
        table.add_column("Kakera", justify="right", style="magenta")
        for entry in page.entries:
            table.add_row(
                entry.name,
                format_mudae_key_marker(entry.key_type, entry.key_count),
                format_mudae_kakera(entry.kakera_value),
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
            bonus = MudaeTextParser().parse_player_bonus(read_message_source(path, clipboard))
        except MudaeParseError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error

        table = Table(title="Parsed player bonuses")
        table.add_column("Metric", style="green")
        table.add_column("Mudae value")
        for metric in bonus.metrics:
            table.add_row(metric.label, metric.detail)
        console.print(table)


    @parse_app.command("mmr")
    def parse_mmr(
        path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $mmr/$mmrk/$mmrt page."),
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Parse one copied ranked `$mmr`/`$mmrk`/`$mmrt` owned-harem page."""
        try:
            page = MudaeTextParser().parse_ranked_harem_page(read_message_source(path, clipboard))
        except MudaeParseError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error

        page_label = (
            f"Page {page.page_number}/{page.page_count}"
            if page.page_number is not None and page.page_count is not None
            else "Partial import"
        )
        console.print(f"[bold cyan]Owned harem - {page_label}[/bold cyan]")
        table = Table()
        table.add_column("Claim rank", justify="right", style="cyan")
        table.add_column("Character", style="green")
        table.add_column("Roulette")
        table.add_column("Kakera", justify="right", style="magenta")
        for entry in page.entries:
            table.add_row(
                f"#{entry.claim_rank:,}",
                entry.name,
                format_mudae_roulette_types(entry.roulette_types),
                format_mudae_kakera(entry.kakera_value),
            )
        console.print(table)


    @parse_app.command("wishlist")
    def parse_wishlist(
        path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $wl response."),
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Parse one copied Mudae `$wl` response."""
        try:
            wishlist = MudaeTextParser().parse_wishlist(read_message_source(path, clipboard))
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
            disablelist = MudaeTextParser().parse_disablelist(read_message_source(path, clipboard))
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
            page = MudaeTextParser().parse_unavailable_characters(read_message_source(path, clipboard))
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
            state = MudaeTextParser().parse_kakera_state(read_message_source(path, clipboard))
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
            state = MudaeTextParser().parse_personal_rare(read_message_source(path, clipboard))
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
            state = MudaeTextParser().parse_timer_state(read_message_source(path, clipboard))
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
            state = MudaeTextParser().parse_tower_state(read_message_source(path, clipboard))
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
            state = MudaeTextParser().parse_kakeraloot_state(read_message_source(path, clipboard))
        except MudaeParseError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        if not state.has_kakeraloots:
            console.print(f"[yellow]{state.status_note}[/yellow]")
            return
        wishprotect = (
            "-"
            if state.protected_wish_level is None or state.protected_wish_denominator is None
            else f"LVL {state.protected_wish_level} (1/{state.protected_wish_denominator:,})"
        )
        permanent_rolls = (
            "-" if state.permanent_roll_bonus is None else f"+{state.permanent_roll_bonus}"
        )
        console.print(
            f"[bold cyan]Kakeraloots[/bold cyan] · Quantity {_format_optional_number(state.quantity_level)} · "
            f"Quality {_format_optional_number(state.quality_level)}\n"
            f"Usage: {_format_optional_number(state.usage_count)} · "
            f"Balance: {_format_optional_number(state.kakera_balance)} Kakera · "
            f"Rolls stacked: {_format_optional_number(state.rolls_stacked)}\n"
            f"Wishprotect: {wishprotect} · Permanent rolls: {permanent_rolls}"
        )


    @parse_app.command("infokl")
    def parse_infokl(
        path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae $infokl response."),
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Parse server-specific Kakeraloot prices from `$infokl`."""
        try:
            settings = MudaeTextParser().parse_kakeraloot_settings(read_message_source(path, clipboard))
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
            settings = MudaeTextParser().parse_server_settings(read_message_source(path, clipboard))
        except MudaeParseError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        console.print(
            f"[bold cyan]Server settings[/bold cyan] | Gamemode {settings.game_mode} | "
            f"{settings.rolls_per_hour} rolls/hour | claim reset {settings.claim_reset_minutes} min\n"
            f"Claim timer: {settings.claim_reaction_expiry_seconds}s | rare multiplier: "
            f"{settings.claimed_character_rarity_multiplier} | premium: {settings.server_premium}"
        )


    return parse_app
