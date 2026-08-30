"""Typer commands for searching and analyzing the imported character catalog."""

from collections.abc import Callable

import typer
from rich.console import Console
from rich.table import Table

from moa.services.catalog_service import CatalogService
from moa.services.harem_search_service import HaremSearchService
from moa.services.key_progress_service import KeyProgressService
from moa.services.top_search_service import TopSearchService
from moa.utils.display import (
    format_mudae_gender,
    format_mudae_kakera,
    format_mudae_key_marker,
    format_mudae_roulette_types,
)


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


def build_catalog_search_app(
    console: Console,
    resolve_account_context: Callable[[str | None, str | None], tuple[str, str]],
    config_service_factory: Callable[[], object],
    format_optional_rank: Callable[[int | None], str],
) -> typer.Typer:
    """Build the flat catalog search command subtree."""
    catalog_search_app = typer.Typer()

    @catalog_search_app.command("top")
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
        config_service = config_service_factory()
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
        table.add_column("$top observed (UTC)")
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
        console.print(
            "[dim]The `$top observed (UTC)` timestamp is the latest local `$top` observation; "
            "account-scoped evidence has independent timestamps. MOA applies no age threshold to "
            "classify evidence as fresh or stale.[/dim]"
        )
        if server and account:
            console.print(
                "[dim]Missing owned evidence does not prove unowned; one $mm page is not a complete harem snapshot. "
                "A dash in Keys means no imported key row for that character. Unknown means no explicit rollability evidence; "
                "import fresh $topx/$adl data for stronger rollability evidence.[/dim]"
            )

    @catalog_search_app.command("show")
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
        console.print(f"[bold]Claim rank:[/bold] {format_optional_rank(profile.claim_rank)}")
        console.print(f"[bold]Like rank:[/bold] {format_optional_rank(profile.like_rank)}")

        if not profile.server_observations:
            console.print("[yellow]No server-specific observations imported yet.[/yellow]")
            console.print(
                "[dim]Claim and like ranks are the latest locally imported global snapshots; "
                "server Kakera rows are the latest locally imported observation per server. "
                "Displayed timestamps do not establish current, fresh, or stale state.[/dim]"
            )
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
        console.print(
            "[dim]Claim and like ranks are the latest locally imported global snapshots; "
            "server Kakera rows are the latest locally imported observation per server. "
            "Displayed timestamps do not establish current, fresh, or stale state.[/dim]"
        )

    @catalog_search_app.command("harem")
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
        server, account = resolve_account_context(server, account)
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
        console.print(
            "[dim]Each Observed value is the latest local key observation for that row; it does not "
            "establish current state and has no fresh/stale age classification. `Needs $im` means "
            "unresolved identity evidence; scan completeness is not shown.[/dim]"
        )
        if any(entry.character is None for entry in entries):
            console.print(
                "[dim]Unresolved entries cannot be matched by series until a matching $im import provides identity data.[/dim]"
            )

    @catalog_search_app.command("keyfarm")
    def catalog_keyfarm(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose harem to shortlist."),
        limit: int = typer.Option(15, "--limit", "-n", min=1, help="Number of entries to display."),
    ) -> None:
        """Show the highest-value imported keyed characters for a future key-farm plan."""
        server, account = resolve_account_context(server, account)
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

        table = Table(title=f"{account} - latest local key-farm shortlist")
        table.add_column("Character", style="green")
        table.add_column("Kakera", justify="right", style="magenta")
        table.add_column("Keys", justify="right", style="cyan")
        table.add_column("Wishlist")
        table.add_column("Rollability")
        table.add_column("Observed (UTC)")
        for entry in valued_entries:
            wishlist_entry = wishlist_by_name.get(entry.character_name.casefold())
            wishlist_status = (
                "Starwish" if wishlist_entry and wishlist_entry.is_starwish
                else "Wish" if wishlist_entry
                else "Not listed in observed wishlist" if wishlist is not None
                else "No imported $wl snapshot"
            )
            table.add_row(
                entry.character_name,
                format_mudae_kakera(entry.kakera_value),
                format_mudae_key_marker(entry.key_type, entry.key_count),
                wishlist_status,
                "Observed unavailable"
                if entry.character_name.casefold() in unavailable_names
                else "No matching unavailable evidence",
                entry.observed_at.strftime("%Y-%m-%d %H:%M"),
            )
        console.print(table)
        console.print(
            "[dim]Ordered by latest local harem-key observations. Observed timestamps have no "
            "freshness/staleness age classification; this does not claim a complete harem. "
            "This factual shortlist is not an expected-value recommendation.[/dim]"
        )

    @catalog_search_app.command("keyprogress")
    def catalog_keyprogress(
        server: str | None = typer.Option(None, "--server", "-s", help="Your label for the Mudae server."),
        account: str | None = typer.Option(None, "--account", "-a", help="Account whose key progress to show."),
        limit: int = typer.Option(20, "--limit", "-n", min=1, help="Number of entries to display."),
    ) -> None:
        """Show each imported harem character's next universal key unlock."""
        server, account = resolve_account_context(server, account)
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

    @catalog_search_app.command("key-gains")
    def catalog_key_gains(
        server: str | None = typer.Option(None, "--server", "-s"),
        account: str | None = typer.Option(None, "--account", "-a"),
        limit: int = typer.Option(20, "--limit", "-n", min=1),
    ) -> None:
        """Show recent key states directly observed on imported rolls."""
        server, account = resolve_account_context(server, account)
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

    return catalog_search_app
