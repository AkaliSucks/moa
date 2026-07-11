import typer
from rich import print

app = typer.Typer(
    help="MOA - Mudae Optimization Assistant"
)


@app.command()
def version():
    """Display MOA version."""
    print("[green]MOA v0.1.0[/green]")


if __name__ == "__main__":
    app()