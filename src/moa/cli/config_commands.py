"""Typer commands for managing user-local MOA configuration."""

import typer
from rich.console import Console
from rich.table import Table

from moa.core.config import ConfigService


def build_config_app(console: Console) -> typer.Typer:
    """Build the configuration command tree using the CLI's shared console."""
    config_app = typer.Typer(help="Manage user-local MOA profiles and account identities")
    config_profile_app = typer.Typer(help="Manage MOA profiles")
    config_account_app = typer.Typer(help="Manage server/account identities")
    config_app.add_typer(config_profile_app, name="profile")
    config_app.add_typer(config_account_app, name="account")

    @config_app.command("show")
    def config_show(
        profile: str | None = typer.Option(
            None, "--profile", help="Profile to display; defaults to the active profile."
        ),
    ) -> None:
        """Show the active profile and configured server/account identities."""
        service = ConfigService()
        try:
            config = service.load()
            selected = service.profile(profile)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error

        console.print(f"[bold cyan]MOA config[/bold cyan] — {service.path}")
        console.print(f"Active profile: [green]{config.active_profile}[/green]")
        table = Table(title=f"Profile: {selected.name}")
        table.add_column("Server", style="green")
        table.add_column("Account", style="cyan")
        table.add_column("Role")
        table.add_column("Server ID")
        table.add_column("Discord user ID")
        table.add_column("Active")
        if not selected.accounts:
            console.print("[yellow]No server/account identities configured yet.[/yellow]")
            return
        for identity in selected.accounts:
            active = (
                "Yes"
                if identity.server.casefold() == (selected.active_server or "").casefold()
                and identity.account.casefold() == (selected.active_account or "").casefold()
                else ""
            )
            table.add_row(
                identity.server,
                identity.account,
                identity.role,
                identity.discord_server_id or "-",
                identity.discord_user_id or "-",
                active,
            )
        console.print(table)

    @config_profile_app.command("add")
    def config_profile_add(name: str) -> None:
        """Create a named profile for one MOA setup."""
        try:
            profile = ConfigService().add_profile(name)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        console.print(f"[green]Created MOA profile `{profile.name}`.[/green]")

    @config_account_app.command("add")
    def config_account_add(
        server: str = typer.Option(..., "--server", "-s", help="Mudae server label."),
        account: str = typer.Option(..., "--account", "-a", help="Mudae account name."),
        role: str = typer.Option(
            "primary", "--role", help="Identity role: primary, alt, or observed."
        ),
        profile: str | None = typer.Option(None, "--profile", help="Profile to update."),
        server_id: str | None = typer.Option(
            None, "--server-id", help="Stable Discord server ID from `$myid`."
        ),
        user_id: str | None = typer.Option(
            None, "--user-id", help="Stable Discord user ID from `$myid`."
        ),
    ) -> None:
        """Add one owned or observed account identity to a profile."""
        if role not in {"primary", "alt", "observed"}:
            console.print("[red]--role must be `primary`, `alt`, or `observed`.[/red]")
            raise typer.Exit(1)
        try:
            identity = ConfigService().add_account(
                server,
                account,
                role,
                profile,
                discord_server_id=server_id,
                discord_user_id=user_id,
            )
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        console.print(
            f"[green]Added {identity.role} account {identity.account} on {identity.server}.[/green]"
        )

    @config_app.command("use")
    def config_use(
        server: str | None = typer.Option(
            None, "--server", "-s", help="Active Mudae server label."
        ),
        account: str | None = typer.Option(
            None, "--account", "-a", help="Active Mudae account name."
        ),
        profile: str | None = typer.Option(None, "--profile", help="Profile to update."),
        server_id: str | None = typer.Option(None, "--server-id", help="Stable Discord server ID."),
        user_id: str | None = typer.Option(None, "--user-id", help="Stable Discord user ID."),
    ) -> None:
        """Select the default server/account context for profile-aware commands."""
        service = ConfigService()
        try:
            if bool(server_id) != bool(user_id):
                raise ValueError("--server-id and --user-id must be supplied together.")
            if server_id and user_id:
                selected = service.use_identity_ids(server_id, user_id, profile)
            elif server and account:
                selected = service.use_context(server, account, profile)
            else:
                raise ValueError(
                    "Provide either --server/--account or --server-id/--user-id."
                )
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        console.print(
            f"[green]Active context:[/green] {selected.active_server} / {selected.active_account} "
            f"([cyan]{selected.name}[/cyan])"
        )

    return config_app
