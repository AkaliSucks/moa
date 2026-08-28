from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.services.roll_analysis_service import RollAnalysisService
from moa.utils.display import format_mudae_kakera, format_mudae_key_marker


def _format_optional_rank(value: int | None) -> str:
    return "-" if value is None else f"#{value:,}"


def register_analyze_roll_command(
    app: typer.Typer,
    console: Console,
    read_message_source: Callable[[Path | None, bool], str],
    resolve_account_context: Callable[[str | None, str | None], tuple[str, str]],
) -> None:
    @app.command("analyze-roll")
    def analyze_roll(
        server: str | None = typer.Option(
            None, "--server", "-s", help="Your label for the Mudae server."
        ),
        account: str | None = typer.Option(
            None, "--account", "-a", help="Account deciding what to do with this roll."
        ),
        path: Path | None = typer.Argument(
            None, help="Text file containing one copied Mudae roll card."
        ),
        clipboard: bool = typer.Option(
            False, "--clipboard", "-c", help="Read copied Discord text."
        ),
    ) -> None:
        """Explain a copied roll using directly imported account context."""
        server, account = resolve_account_context(server, account)
        try:
            roll = MudaeTextParser().parse_roll(read_message_source(path, clipboard))
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
