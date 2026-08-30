from collections.abc import Callable

import typer
from rich.console import Console
from rich.table import Table

from moa.services.catalog_service import CatalogService


def _format_optional_number(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _format_observed_toggle(value: bool | None) -> str:
    return str(value) if value is not None else "Unknown"


def build_catalog_snapshot_app(
    console: Console,
    resolve_account_context: Callable[[str | None, str | None], tuple[str, str]],
    resolve_server_context: Callable[[str | None], str],
) -> typer.Typer:
    """Build the flat catalog snapshot-state command subtree."""
    catalog_snapshot_app = typer.Typer()

    @catalog_snapshot_app.command("bonus")
    def catalog_bonus(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose bonus snapshot to show."),
    ) -> None:
        """Show the latest imported `$bonus` snapshot for one account."""
        server, account = resolve_account_context(server, account)
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
        console.print(
            "[dim]Latest locally imported `$bonus` capture; displayed values are observed, "
            "and the capture may be partial.[/dim]"
        )
        console.print(f"[dim]Observed: {bonus.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]")

    @catalog_snapshot_app.command("wishlist")
    def catalog_wishlist(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose wishlist to show."),
    ) -> None:
        """Show the latest imported `$wl` snapshot for one account."""
        server, account = resolve_account_context(server, account)
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
        console.print(f"[dim]Observed: {wishlist.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]")
        console.print(
            "[dim]Provenance: counts, rows, and Starwish/Wish markers are captured evidence only; "
            "they do not establish current, fresh, or stale state, and snapshot completeness is not established.[/dim]"
        )

    @catalog_snapshot_app.command("disablelist")
    def catalog_disablelist(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose disable list to show."),
    ) -> None:
        """Show the latest imported `$dl` snapshot for one account."""
        server, account = resolve_account_context(server, account)
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
        console.print(f"[dim]Observed: {disablelist.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]")
        console.print(
            "[dim]Provenance: counts, rows, and toggles are captured `$dl` evidence only; "
            "they do not establish current, fresh, or stale state, and snapshot completeness is not established.[/dim]"
        )

    @catalog_snapshot_app.command("unavailable")
    def catalog_unavailable(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose roll pool to show."),
    ) -> None:
        """Show characters directly observed as unavailable by `$topx`."""
        server, account = resolve_account_context(server, account)
        observations = CatalogService().unavailable_characters(server, account)
        if not observations:
            console.print("[yellow]No unavailable-character observations imported yet.[/yellow]")
            raise typer.Exit()
        table = Table(title=f"{account} - directly observed unavailable characters")
        table.add_column("Claim rank", justify="right", style="cyan")
        table.add_column("Character", style="green")
        table.add_column("Series")
        table.add_column("Reason", overflow="fold")
        table.add_column("Observed", overflow="fold", no_wrap=True)
        for observation in observations:
            table.add_row(
                f"#{observation.claim_rank:,}",
                observation.character.name,
                observation.character.series,
                observation.reason or "Not specified in observed $topx row",
                observation.observed_at.strftime("%Y-%m-%d %H:%M UTC"),
            )
        console.print(table)
        console.print(
            "[dim]Provenance: rows are latest locally retained positive `$topx` evidence for the "
            "selected server/account; no row establishes that a character is available or rollable, "
            "and freshness/currentness is not classified.[/dim]"
        )

    @catalog_snapshot_app.command("kakera")
    def catalog_kakera(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose Kakera state to show."),
    ) -> None:
        """Show the latest imported `$k` snapshot for one account."""
        server, account = resolve_account_context(server, account)
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
        console.print(f"[dim]Observed: {state.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]")
        console.print(
            "[dim]Provenance: Kakera balance and badges are from the latest locally imported `$k` "
            "capture; they do not establish current, fresh, or stale state, and snapshot "
            "completeness is not established.[/dim]"
        )

    @catalog_snapshot_app.command("towerstate")
    def catalog_towerstate(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose tower state to show."),
    ) -> None:
        """Show the latest imported `$kt` snapshot for one account."""
        server, account = resolve_account_context(server, account)
        state = CatalogService().tower_state(server, account)
        if state is None:
            console.print("[yellow]No $kt snapshot imported for this server/account yet.[/yellow]")
            raise typer.Exit()
        gap = max(0, state.next_level_cost - state.kakera_balance)
        completed_towers = (
            "Not reported in this capture" if state.completed_towers is None else str(state.completed_towers)
        )
        built_perks = ", ".join(str(perk) for perk in state.built_perk_ids) or "no checked perk IDs parsed"
        console.print(
            f"[bold cyan]{state.account_name} - Tower level {state.current_level}[/bold cyan]\n"
            f"Completed towers: {completed_towers} · Built perks: {built_perks}\n"
            f"Next floor: {state.next_level_cost:,} Kakera · Balance: {state.kakera_balance:,} Kakera · "
            f"Shortfall: {gap:,} Kakera"
        )
        console.print(f"[dim]Observed: {state.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]")
        console.print(
            "[dim]Provenance: values are from the latest locally imported `$kt`/`$tower` capture; "
            "they do not establish current, fresh, stale, or complete state.[/dim]"
        )

    @catalog_snapshot_app.command("timers")
    def catalog_timers(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose latest $tu snapshot to show."),
    ) -> None:
        """Show the most recently imported `$tu` snapshot without treating it as live state."""
        server, account = resolve_account_context(server, account)
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
        console.print(
            "[dim]Provenance: values are from the latest locally imported timer snapshot for the "
            "selected server/account; displayed countdowns are captured evidence, not current "
            "deadlines, and do not establish fresh, stale, expired, or complete state.[/dim]"
        )

    @catalog_snapshot_app.command("lootstate")
    def catalog_lootstate(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose Kakeraloot state to show."),
    ) -> None:
        """Show the latest imported `$lk` snapshot for one account."""
        server, account = resolve_account_context(server, account)
        state = CatalogService().kakeraloot_state(server, account)
        if state is None:
            console.print("[yellow]No $lk snapshot imported for this server/account yet.[/yellow]")
            raise typer.Exit()
        if not state.has_kakeraloots:
            console.print(f"[yellow]{state.status_note}[/yellow]")
            console.print(f"[dim]Observed: {state.observed_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]")
            console.print(
                "[dim]Provenance: this is the latest locally imported `$lk` evidence for the "
                "selected server/account; values and status do not establish live/current, "
                "fresh/stale, available, or complete state.[/dim]"
            )
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
        console.print(
            "[dim]Provenance: this is the latest locally imported `$lk` evidence for the "
            "selected server/account; values and status do not establish live/current, "
            "fresh/stale, available, or complete state.[/dim]"
        )

    @catalog_snapshot_app.command("infokl")
    def catalog_infokl(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    ) -> None:
        """Show the latest imported `$infokl` configuration for one server."""
        server = resolve_server_context(server)
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
        console.print(
            "[dim]Provenance: values are the latest locally imported `$infokl` capture for the "
            "selected server; they are observed price details only and do not establish "
            "live/current availability or entitlement, fresh/stale status, or complete loot "
            "state.[/dim]"
        )

    @catalog_snapshot_app.command("settings")
    def catalog_settings(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
    ) -> None:
        """Show the latest imported `$settings` snapshot for one server."""
        server = resolve_server_context(server)
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

    return catalog_snapshot_app
