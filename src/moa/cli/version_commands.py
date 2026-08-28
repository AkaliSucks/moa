import typer
from rich.console import Console


def register_version_command(app: typer.Typer, console: Console) -> None:
    @app.command()
    def version() -> None:
        console.print("[cyan]MOA[/cyan] v0.1.0")
