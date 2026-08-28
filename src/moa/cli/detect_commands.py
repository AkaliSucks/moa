from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console

from moa.parser.message_router import MudaeMessageRouter


def register_detect_command(
    app: typer.Typer,
    console: Console,
    read_message_source: Callable[[Path | None, bool], str],
) -> None:
    @app.command("detect")
    def detect_mudae_message(
        path: Path | None = typer.Argument(None, help="Text file containing one copied Mudae response."),
        clipboard: bool = typer.Option(False, "--clipboard", "-c", help="Read copied Discord text."),
    ) -> None:
        """Identify which supported Mudae format one raw message uses."""
        detection = MudaeMessageRouter().detect(read_message_source(path, clipboard))
        style = "green" if detection.kind != "unknown" else "yellow"
        console.print(f"[{style}]Detected: {detection.kind}[/{style}]")
        console.print(f"[dim]{detection.reason}[/dim]")
