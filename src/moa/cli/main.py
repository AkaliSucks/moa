from pathlib import Path

import typer
from rich.console import Console

from moa.cli.action_commands import build_action_app
from moa.cli.adl_commands import build_adl_app
from moa.cli.account_commands import build_account_app
from moa.core.config import ConfigService
from moa.cli.badge_commands import build_badge_app
from moa.cli.command_commands import build_command_app
from moa.cli.config_commands import build_config_app
from moa.cli.catalog_operational_commands import build_catalog_operational_app
from moa.cli.catalog_delete_import_commands import register_catalog_delete_import_command
from moa.cli.catalog_relocate_database_commands import (
    register_catalog_relocate_database_command,
)
from moa.cli.catalog_repair_bugged_data_commands import (
    register_catalog_repair_bugged_data_command,
)
from moa.cli.catalog_reset_commands import register_catalog_reset_command
from moa.cli.catalog_search_commands import build_catalog_search_app
from moa.cli.catalog_snapshot_commands import build_catalog_snapshot_app
from moa.cli.data_health_commands import build_data_health_app
from moa.cli.retained_source_reprojection_preflight_commands import (
    register_retained_source_reprojection_preflight_command,
)
from moa.cli.discord_commands import build_discord_app
from moa.cli.analyze_roll_commands import register_analyze_roll_command
from moa.cli.detect_commands import register_detect_command
from moa.cli.key_commands import build_key_app
from moa.cli.loot_commands import build_loot_app
from moa.cli.harem_commands import build_harem_app
from moa.cli.import_direct_commands import register_direct_import_commands
from moa.cli.import_workflow_commands import register_import_workflow_commands
from moa.cli.parse_commands import build_parse_app
from moa.cli.reaction_commands import build_reaction_app
from moa.cli.recommend_commands import build_recommend_app
from moa.cli.roll_commands import build_roll_app
from moa.cli.server_commands import build_server_app
from moa.cli.tower_commands import build_tower_app
from moa.cli.version_commands import register_version_command
from moa.database.sqlite import DEFAULT_DATABASE_PATH, default_database_path

app = typer.Typer(help="MOA - Mudae Optimization Assistant")
import_app = typer.Typer(help="Save parsed Mudae data to the local catalog")
catalog_app = typer.Typer(help="Browse MOA's local character catalog")
console = Console()


def _resolve_account_context(
    server: str | None,
    account: str | None,
) -> tuple[str, str]:
    """Resolve explicit or configured account context for read-only commands."""
    try:
        resolved_server, resolved_account = ConfigService().resolve_context(server, account)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    if not resolved_server or not resolved_account:
        console.print(
            "[red]No active server/account context. Configure one with `moa config use` "
            "or pass --server and --account.[/red]"
        )
        raise typer.Exit(1)
    return resolved_server, resolved_account


tower_app = build_tower_app(console)
config_app = build_config_app(console)
badge_app = build_badge_app(console)
key_app = build_key_app(console)
reaction_app = build_reaction_app(console)
command_app = build_command_app(console)
server_app = build_server_app(console)
action_app = build_action_app(console, _resolve_account_context)
recommend_app = build_recommend_app(console, _resolve_account_context)
loot_app = build_loot_app(console, _resolve_account_context)
roll_app = build_roll_app(console, _resolve_account_context)
account_app = build_account_app(console, _resolve_account_context)
harem_app = build_harem_app(console)
adl_app = build_adl_app(console)
parse_app = build_parse_app(
    console,
    lambda path, clipboard: _read_message_source(path, clipboard),
)
data_health_app = build_data_health_app(
    console,
    lambda: DEFAULT_DATABASE_PATH,
)
discord_app = build_discord_app(
    console,
    lambda: DEFAULT_DATABASE_PATH,
)

app.add_typer(tower_app, name="tower")
app.add_typer(command_app, name="command")
app.add_typer(badge_app, name="badge")
app.add_typer(reaction_app, name="reaction")
app.add_typer(loot_app, name="loot")
app.add_typer(key_app, name="key")
app.add_typer(roll_app, name="roll")
app.add_typer(account_app, name="account")
app.add_typer(action_app, name="action")
app.add_typer(parse_app, name="parse")
app.add_typer(import_app, name="import")
app.add_typer(catalog_app, name="catalog")
app.add_typer(harem_app, name="harem")
app.add_typer(adl_app, name="adl")
app.add_typer(recommend_app, name="recommend")
app.add_typer(server_app, name="server")
app.add_typer(config_app, name="config")
app.add_typer(discord_app, name="discord")
catalog_app.add_typer(data_health_app, name="data-health")


register_version_command(app, console)


def _read_copied_message(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        console.print(f"[red]Could not read {path}: {error}[/red]")
        raise typer.Exit(1) from error


def _read_clipboard() -> str:
    """Read text the user has copied from Discord on the local desktop."""
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        try:
            text = root.clipboard_get()
        finally:
            root.destroy()
    except Exception as error:
        console.print(f"[red]Could not read text from the clipboard: {error}[/red]")
        raise typer.Exit(1) from error

    if not text.strip():
        console.print("[red]The clipboard does not contain text.[/red]")
        raise typer.Exit(1)
    return str(text)


def _read_message_source(path: Path | None, clipboard: bool) -> str:
    if clipboard:
        if path is not None:
            console.print("[red]Use either a file path or --clipboard, not both.[/red]")
            raise typer.Exit(1)
        return _read_clipboard()

    if path is None:
        console.print("[red]Provide a text-file path or use --clipboard.[/red]")
        raise typer.Exit(1)
    return _read_copied_message(path)


register_direct_import_commands(
    import_app,
    console,
    lambda path, clipboard: _read_message_source(path, clipboard),
)
register_import_workflow_commands(
    import_app,
    console,
    lambda path, clipboard: _read_message_source(path, clipboard),
)


register_detect_command(
    app,
    console,
    lambda path, clipboard: _read_message_source(path, clipboard),
)
register_analyze_roll_command(
    app,
    console,
    lambda path, clipboard: _read_message_source(path, clipboard),
    lambda server, account: _resolve_account_context(server, account),
)


def _resolve_server_context(server: str | None) -> str:
    """Resolve explicit or configured server context for read-only commands."""
    if server:
        return server.strip()
    try:
        resolved_server, _ = ConfigService().resolve_context(None, None)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    if not resolved_server:
        console.print(
            "[red]No active server context. Configure one with `moa config use` "
            "or pass --server.[/red]"
        )
        raise typer.Exit(1)
    return resolved_server


def _format_optional_rank(value: int | None) -> str:
    return "-" if value is None else f"#{value:,}"


catalog_search_app = build_catalog_search_app(
    console,
    lambda server, account: _resolve_account_context(server, account),
    lambda: ConfigService(),
    _format_optional_rank,
)
catalog_app.add_typer(catalog_search_app)

catalog_snapshot_app = build_catalog_snapshot_app(
    console,
    lambda server, account: _resolve_account_context(server, account),
    lambda server: _resolve_server_context(server),
)
catalog_app.add_typer(catalog_snapshot_app)

catalog_operational_app = build_catalog_operational_app(
    console,
    lambda server, account: _resolve_account_context(server, account),
    _format_optional_rank,
)
catalog_app.add_typer(catalog_operational_app)
register_catalog_delete_import_command(catalog_app, console)
register_catalog_reset_command(catalog_app, console, lambda: DEFAULT_DATABASE_PATH)
register_catalog_relocate_database_command(
    catalog_app,
    console,
    lambda: default_database_path(),
)
register_catalog_repair_bugged_data_command(
    catalog_app,
    console,
    lambda: DEFAULT_DATABASE_PATH,
)
register_retained_source_reprojection_preflight_command(catalog_app, console)


if __name__ == "__main__":
    app()
