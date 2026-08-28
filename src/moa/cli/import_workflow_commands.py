from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console

from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.services.automatic_import_service import AutomaticImportService
from moa.services.catalog_service import CatalogService
from moa.services.harem_import_dispatch import HaremImportDispatcher, require_harem_import

_AUTO_PATH_ARGUMENT = typer.Argument(None, help="Text file containing one copied Mudae response.")
_TOP_PATH_ARGUMENT = typer.Argument(None, help="Text file containing one copied Mudae $top page.")
_MM_PATH_ARGUMENT = typer.Argument(None, help="Text file containing one copied Mudae $mmy= page.")
_MMR_PATH_ARGUMENT = typer.Argument(
    None, help="Text file containing one copied Mudae $mmr/$mmrk/$mmrt page."
)
_ADL_PATH_ARGUMENT = typer.Argument(None, help="Text file containing one copied Mudae $adl page.")


def register_import_workflow_commands(
    import_app: typer.Typer,
    console: Console,
    read_message_source: Callable[[Path | None, bool], str],
) -> None:
    @import_app.command("auto")
    def import_auto(
        server: str | None = typer.Option(
            None, "--server", "-s", help="Server label when the message needs one."
        ),
        account: str | None = typer.Option(
            None, "--account", "-a", help="Account name when the message needs one."
        ),
        scan: int | None = typer.Option(
            None, "--scan", help="Optional harem or antidisable scan ID for a multi-page import."
        ),
        path: Path | None = _AUTO_PATH_ARGUMENT,
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Detect and import one supported Mudae response using the existing import rules."""
        raw_message = read_message_source(path, clipboard)
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

    @import_app.command("top")
    def import_top(
        server: str | None = typer.Option(
            None,
            "--server",
            "-s",
            help="Server where `$topo` owner claims were observed.",
        ),
        path: Path | None = _TOP_PATH_ARGUMENT,
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Parse and persist a `$top` or `$topo` page as a timestamped local rank snapshot."""
        raw_message = read_message_source(path, clipboard)
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

    @import_app.command("mm")
    def import_mm(
        server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
        account: str = typer.Option(..., "--account", "-a", help="Account whose harem is shown."),
        scan: int | None = typer.Option(
            None, "--scan", help="Optional active harem scan ID created by `moa harem begin`."
        ),
        path: Path | None = _MM_PATH_ARGUMENT,
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Parse and persist one `$mmy=` or `$mmyk=` page for a server/account harem."""
        raw_message = read_message_source(path, clipboard)
        source = "clipboard" if clipboard else f"file:{path}"
        try:
            imported = HaremImportDispatcher().import_page(
                require_harem_import("$mmy"), raw_message, server, account, source, scan
            )
        except (MudaeParseError, ValueError) as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        result = imported.result
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
        path: Path | None = _MMR_PATH_ARGUMENT,
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Parse and persist one ranked `$mmr`/`$mmrk`/`$mmrt` owned-harem page."""
        raw_message = read_message_source(path, clipboard)
        try:
            imported = HaremImportDispatcher().import_page(
                require_harem_import("$mmr"),
                raw_message,
                server,
                account,
                "clipboard" if clipboard else f"file:{path}",
                scan,
            )
        except MudaeParseError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        result = imported.result
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

    @import_app.command("adl")
    def import_adl(
        server: str = typer.Option(..., "--server", "-s", help="Your label for the Mudae server."),
        account: str = typer.Option(..., "--account", "-a", help="Account whose antidisable list is shown."),
        scan: int | None = typer.Option(
            None, "--scan", help="Optional complete ADL scan ID created by `moa adl begin`."
        ),
        path: Path | None = _ADL_PATH_ARGUMENT,
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Parse and persist one `$adl` series-list page."""
        raw_message = read_message_source(path, clipboard)
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
