from pathlib import Path

import pytest

from moa.core.config import ConfigService


def test_config_service_persists_profiles_accounts_and_active_context(tmp_path: Path) -> None:
    service = ConfigService(tmp_path / "config.json")

    service.add_account("Lake Arrowhead 2025", "ernieuuu", "primary")
    service.add_account("Lake Arrowhead 2025", "ernie_alt", "alt")
    service.add_account("Second Server", "solo_account", "primary")
    service.use_context("Lake Arrowhead 2025", "ernieuuu")

    assert service.path.exists()
    assert service.resolve_context(None, None) == ("Lake Arrowhead 2025", "ernieuuu")
    assert service.resolve_context("Explicit", "Account") == ("Explicit", "Account")
    assert service.owned_account_names("Lake Arrowhead 2025") == ("ernieuuu", "ernie_alt")
    assert service.owned_account_names("Second Server") == ("solo_account",)


def test_config_service_tracks_observed_users_without_treating_them_as_owned(
    tmp_path: Path,
) -> None:
    service = ConfigService(tmp_path / "config.json")
    service.add_account(
        "LEAGUE OF DRAVEN",
        "friend_account",
        "observed",
        discord_server_id="1402543612549398538",
        discord_user_id="999999999999999999",
    )

    identity = service.identity_for_discord_ids(
        "1402543612549398538",
        "999999999999999999",
    )

    assert identity is not None
    assert identity.role == "observed"
    assert service.owned_account_names("LEAGUE OF DRAVEN") == ()


def test_config_service_selects_active_context_by_discord_ids(tmp_path: Path) -> None:
    service = ConfigService(tmp_path / "config.json")
    service.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="1323181920397426763",
        discord_user_id="146851153412358144",
    )

    selected = service.use_identity_ids(
        "1323181920397426763",
        "146851153412358144",
    )

    assert selected.active_server == "Lake Arrowhead 2025"
    assert selected.active_account == "ernieuuu"
    assert selected.active_server_id == "1323181920397426763"
    assert selected.active_user_id == "146851153412358144"


def test_config_service_updates_existing_identity_with_discord_ids(tmp_path: Path) -> None:
    service = ConfigService(tmp_path / "config.json")
    service.add_account("Lake Arrowhead 2025", "ernieuuu")

    identity = service.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="1323181920397426763",
        discord_user_id="146851153412358144",
    )

    assert identity.discord_server_id == "1323181920397426763"
    assert identity.discord_user_id == "146851153412358144"


def test_config_service_resolves_identity_for_discord_listener(tmp_path: Path) -> None:
    service = ConfigService(tmp_path / "config.json")
    service.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="1323181920397426763",
        discord_user_id="146851153412358144",
    )

    identity = service.identity_for_discord_ids(
        "1323181920397426763", "146851153412358144"
    )

    assert identity is not None
    assert identity.server == "Lake Arrowhead 2025"
    assert identity.account == "ernieuuu"
    assert service.identity_for_discord_ids("1323181920397426763", "999") is None
    assert service.profile().accounts[0] == identity


def test_config_service_resolves_identity_by_server_and_account_name(tmp_path: Path) -> None:
    service = ConfigService(tmp_path / "config.json")
    service.add_account(
        "Lake Arrowhead 2025",
        "cute_beagle_91130",
        discord_server_id="1323181920397426763",
        discord_user_id="147839232239599616",
    )

    identity = service.identity_for_discord_server_account(
        "1323181920397426763", "CUTE_BEAGLE_91130"
    )

    assert identity is not None
    assert identity.account == "cute_beagle_91130"


def test_config_service_supports_named_profiles(tmp_path: Path) -> None:
    service = ConfigService(tmp_path / "config.json")
    service.add_profile("travel")
    service.add_account("Travel Server", "travel_account", profile_name="travel")
    service.use_context("Travel Server", "travel_account", profile_name="travel")

    profile = service.profile("travel")

    assert profile.active_server == "Travel Server"
    assert profile.active_account == "travel_account"
    assert profile.accounts[0].role == "primary"


def test_config_service_rejects_non_numeric_discord_ids(tmp_path: Path) -> None:
    service = ConfigService(tmp_path / "config.json")

    with pytest.raises(ValueError, match="Server ID must be a numeric Discord ID"):
        service.add_account(
            "New Server",
            "new_account",
            discord_server_id="PASTE_SERVER_ID_HERE",
            discord_user_id="123456789",
        )
    assert not service.path.exists()

    with pytest.raises(ValueError, match="User ID must be a numeric Discord ID"):
        service.add_account(
            "New Server",
            "new_account",
            discord_server_id="123456789",
            discord_user_id="PASTE_USER_ID_HERE",
        )

    with pytest.raises(ValueError, match="Server ID must be a numeric Discord ID"):
        service.use_identity_ids("YOUR_SERVER_ID", "123456789")
