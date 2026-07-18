"""User-local MOA profiles for server and account context selection."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from moa.models.base import MOAModel


ConfigRole = Literal["primary", "alt", "observed"]


class ConfigAccount(MOAModel):
    """One tracked Mudae account identity attached to a server."""

    server: str
    account: str
    role: ConfigRole = "primary"
    discord_server_id: str | None = None
    discord_user_id: str | None = None


class ConfigProfile(MOAModel):
    """A named collection of server/account identities and one active context."""

    name: str
    active_server: str | None = None
    active_account: str | None = None
    active_server_id: str | None = None
    active_user_id: str | None = None
    accounts: tuple[ConfigAccount, ...] = ()


class MOAConfig(MOAModel):
    """The complete user-local MOA configuration document."""

    active_profile: str = "default"
    profiles: tuple[ConfigProfile, ...] = (ConfigProfile(name="default"),)


class ConfigService:
    """Read and update the user-local profile file without touching the catalog DB."""

    def __init__(self, path: Path | None = None) -> None:
        configured_path = os.environ.get("MOA_CONFIG_PATH")
        self._path = path or Path(configured_path) if configured_path else path
        if self._path is None:
            self._path = Path.home() / ".moa" / "config.json"

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> MOAConfig:
        if not self._path.exists():
            return MOAConfig()
        try:
            return MOAConfig.model_validate_json(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"Could not read MOA config at {self._path}: {error}") from error

    def save(self, config: MOAConfig) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(config.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )

    def profile(self, name: str | None = None) -> ConfigProfile:
        config = self.load()
        profile_name = self._clean(name or config.active_profile, "profile")
        for profile in config.profiles:
            if profile.name.casefold() == profile_name.casefold():
                return profile
        raise ValueError(f"MOA profile `{profile_name}` does not exist.")

    def add_profile(self, name: str) -> ConfigProfile:
        config = self.load()
        profile_name = self._clean(name, "profile")
        if any(profile.name.casefold() == profile_name.casefold() for profile in config.profiles):
            raise ValueError(f"MOA profile `{profile_name}` already exists.")
        profile = ConfigProfile(name=profile_name)
        updated = config.model_copy(update={"profiles": (*config.profiles, profile)})
        self.save(updated)
        return profile

    def add_account(
        self,
        server: str,
        account: str,
        role: ConfigRole = "primary",
        profile_name: str | None = None,
        discord_server_id: str | None = None,
        discord_user_id: str | None = None,
    ) -> ConfigAccount:
        config = self.load()
        profile = self.profile(profile_name)
        identity = ConfigAccount(
            server=self._clean(server, "server"),
            account=self._clean(account, "account"),
            role=role,
            discord_server_id=self._clean_optional_discord_id(discord_server_id, "Server ID"),
            discord_user_id=self._clean_optional_discord_id(discord_user_id, "User ID"),
        )
        existing_index = next(
            (
                index
                for index, item in enumerate(profile.accounts)
                if item.server.casefold() == identity.server.casefold()
                and item.account.casefold() == identity.account.casefold()
            ),
            None,
        )
        if existing_index is not None:
            existing = profile.accounts[existing_index]
            if identity.discord_server_id is None and identity.discord_user_id is None:
                raise ValueError(
                    f"Account `{identity.account}` is already configured for server `{identity.server}`."
                )
            updated_accounts = list(profile.accounts)
            updated_accounts[existing_index] = existing.model_copy(
                update={
                    "role": identity.role,
                    "discord_server_id": identity.discord_server_id or existing.discord_server_id,
                    "discord_user_id": identity.discord_user_id or existing.discord_user_id,
                }
            )
            identity = updated_accounts[existing_index]
        else:
            updated_accounts = [*profile.accounts, identity]
        updated_profile = profile.model_copy(update={"accounts": tuple(updated_accounts)})
        self._save_profile(config, updated_profile)
        return identity

    def use_context(
        self,
        server: str,
        account: str,
        profile_name: str | None = None,
    ) -> ConfigProfile:
        config = self.load()
        profile = self.profile(profile_name)
        normalized_server = self._clean(server, "server")
        normalized_account = self._clean(account, "account")
        if not any(
            item.server.casefold() == normalized_server.casefold()
            and item.account.casefold() == normalized_account.casefold()
            for item in profile.accounts
        ):
            raise ValueError(
                f"Add `{normalized_account}` on `{normalized_server}` to profile `{profile.name}` first."
            )
        updated_profile = profile.model_copy(
            update={
                "active_server": normalized_server,
                "active_account": normalized_account,
                "active_server_id": next(
                    (
                        item.discord_server_id
                        for item in profile.accounts
                        if item.server.casefold() == normalized_server.casefold()
                        and item.account.casefold() == normalized_account.casefold()
                    ),
                    None,
                ),
                "active_user_id": next(
                    (
                        item.discord_user_id
                        for item in profile.accounts
                        if item.server.casefold() == normalized_server.casefold()
                        and item.account.casefold() == normalized_account.casefold()
                    ),
                    None,
                ),
            }
        )
        self._save_profile(config, updated_profile)
        return updated_profile

    def use_identity_ids(
        self,
        server_id: str,
        user_id: str,
        profile_name: str | None = None,
    ) -> ConfigProfile:
        """Select an active context by stable Discord IDs."""
        config = self.load()
        profile = self.profile(profile_name)
        normalized_server_id = self._clean_discord_id(server_id, "Server ID")
        normalized_user_id = self._clean_discord_id(user_id, "User ID")
        identity = next(
            (
                item
                for item in profile.accounts
                if item.discord_server_id == normalized_server_id
                and item.discord_user_id == normalized_user_id
            ),
            None,
        )
        if identity is None:
            raise ValueError(
                f"No configured account matches server ID `{normalized_server_id}` and "
                f"user ID `{normalized_user_id}` in profile `{profile.name}`."
            )
        updated_profile = profile.model_copy(
            update={
                "active_server": identity.server,
                "active_account": identity.account,
                "active_server_id": normalized_server_id,
                "active_user_id": normalized_user_id,
            }
        )
        self._save_profile(config, updated_profile)
        return updated_profile

    def resolve_context(
        self,
        server: str | None,
        account: str | None,
        profile_name: str | None = None,
    ) -> tuple[str | None, str | None]:
        if bool(server) != bool(account):
            raise ValueError("--server and --account must be supplied together.")
        if server and account:
            return server.strip(), account.strip()
        profile = self.profile(profile_name)
        if profile.active_server is None or profile.active_account is None:
            if profile.active_server_id and profile.active_user_id:
                identity = next(
                    (
                        item
                        for item in profile.accounts
                        if item.discord_server_id == profile.active_server_id
                        and item.discord_user_id == profile.active_user_id
                    ),
                    None,
                )
                if identity is not None:
                    return identity.server, identity.account
        return profile.active_server, profile.active_account

    def owned_account_names(self, server: str, profile_name: str | None = None) -> tuple[str, ...]:
        """Return only the user's primary/alt identities for one server.

        Observed identities are intentionally excluded so another player's
        ownership does not make a character appear self-owned in rollability
        searches.
        """
        profile = self.profile(profile_name)
        seen: set[str] = set()
        names: list[str] = []
        for identity in profile.accounts:
            if (
                identity.server.casefold() != server.casefold()
                or identity.role not in {"primary", "alt"}
            ):
                continue
            key = identity.account.casefold()
            if key not in seen:
                seen.add(key)
                names.append(identity.account)
        return tuple(names)

    def identity_for_discord_ids(
        self,
        server_id: str,
        user_id: str,
        profile_name: str | None = None,
    ) -> ConfigAccount | None:
        """Return the configured MOA identity matching a Discord guild/user pair."""
        normalized_server_id = self._clean_discord_id(server_id, "Server ID")
        normalized_user_id = self._clean_discord_id(user_id, "User ID")
        profile = self.profile(profile_name)
        return next(
            (
                identity
                for identity in profile.accounts
                if identity.discord_server_id == normalized_server_id
                and identity.discord_user_id == normalized_user_id
            ),
            None,
        )

    def identity_for_discord_server_account(
        self,
        server_id: str,
        account_name: str,
        profile_name: str | None = None,
    ) -> ConfigAccount | None:
        """Resolve a configured account name within one Discord server."""
        normalized_server_id = self._clean_discord_id(server_id, "Server ID")
        normalized_account = self._clean(account_name, "account")
        profile = self.profile(profile_name)
        return next(
            (
                identity
                for identity in profile.accounts
                if identity.discord_server_id == normalized_server_id
                and identity.account.casefold() == normalized_account.casefold()
            ),
            None,
        )

    def active_identity_for_discord_server(
        self,
        server_id: str,
        profile_name: str | None = None,
    ) -> ConfigAccount | None:
        """Return the active configured account when it belongs to one guild.

        Discord slash responses from another bot do not always retain the
        original interaction metadata. The listener may use this explicit
        active context as a conservative fallback, but only for the selected
        Discord server and account.
        """
        normalized_server_id = self._clean_discord_id(server_id, "Server ID")
        profile = self.profile(profile_name)
        if profile.active_server_id != normalized_server_id:
            return None
        configured_user_ids = {
            identity.discord_user_id
            for identity in profile.accounts
            if identity.discord_server_id == normalized_server_id
            and identity.role in {"primary", "alt"}
            and identity.discord_user_id is not None
        }
        if len(configured_user_ids) > 1:
            return None
        if profile.active_user_id:
            identity = self.identity_for_discord_ids(
                normalized_server_id,
                profile.active_user_id,
                profile_name,
            )
            if identity is not None:
                return identity
        if profile.active_account:
            return self.identity_for_discord_server_account(
                normalized_server_id,
                profile.active_account,
                profile_name,
            )
        return None

    def _save_profile(self, config: MOAConfig, updated_profile: ConfigProfile) -> None:
        profiles = tuple(
            updated_profile if profile.name.casefold() == updated_profile.name.casefold() else profile
            for profile in config.profiles
        )
        self.save(config.model_copy(update={"profiles": profiles}))

    @staticmethod
    def _clean(value: str, label: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{label.title()} cannot be empty.")
        return cleaned

    @staticmethod
    def _clean_discord_id(value: str, label: str) -> str:
        cleaned = ConfigService._clean(value, label)
        if not cleaned.isascii() or not cleaned.isdecimal():
            raise ValueError(f"{label} must be a numeric Discord ID.")
        return cleaned

    @staticmethod
    def _clean_optional_discord_id(value: str | None, label: str) -> str | None:
        if value is None:
            return None
        return ConfigService._clean_discord_id(value, label)
