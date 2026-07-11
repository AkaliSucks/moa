import typer
from rich import print

app = typer.Typer(
    help="MOA - Mudae Optimization Assistant"
)


@app.command()
def version():
    """Show MOA version."""
    print("[cyan]MOA[/cyan] v0.1.0")


@app.command()
def hello():
    """Test command."""
    print("[green]Hello from MOA![/green]")


if __name__ == "__main__":
    app()                               