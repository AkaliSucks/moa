from pathlib import Path

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


def test_config_service_supports_named_profiles(tmp_path: Path) -> None:
    service = ConfigService(tmp_path / "config.json")
    service.add_profile("travel")
    service.add_account("Travel Server", "travel_account", profile_name="travel")
    service.use_context("Travel Server", "travel_account", profile_name="travel")

    profile = service.profile("travel")

    assert profile.active_server == "Travel Server"
    assert profile.active_account == "travel_account"
    assert profile.accounts[0].role == "primary"
