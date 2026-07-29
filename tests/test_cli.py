from datetime import datetime, timezone
from types import SimpleNamespace

from typer.testing import CliRunner

from moa.cli import main
from moa.models.catalog import CatalogCharacter, CatalogTopSearchEntry
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.automatic_import_service import AutomaticImportService
from moa.services.catalog_service import CatalogService
from moa.services.claim_projection_coordinator import ClaimProjectionCoordinator
from moa.services.infokl_projection_coordinator import InfoklProjectionCoordinator
from moa.services.kakera_state_projection_coordinator import KakeraStateProjectionCoordinator
from moa.services.profile_projection_coordinator import ProfileProjectionCoordinator
from moa.services.roll_projection_coordinator import RollProjectionCoordinator
from moa.services.settings_projection_coordinator import SettingsProjectionCoordinator
from moa.services.timer_projection_coordinator import TimerProjectionCoordinator
from moa.services.tower_state_projection_coordinator import TowerStateProjectionCoordinator


def test_account_activity_shows_latest_imported_activity_with_utc_timestamps(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    overview = SimpleNamespace(
        kakera_balance=12000,
        max_badge_count=3,
        tower_level=2,
        tower_shortfall=63000,
        wishlist_count=4,
        wishlist_capacity=10,
        starwish_count=1,
        starwish_capacity=2,
        quantity_level=5,
        quality_level=4,
        loot_usage_count=12,
        disable_slots_used=2,
        disable_slots_capacity=16,
        keyed_harem_count=25,
    )
    readiness = SimpleNamespace(
        status="Ready",
        observed_at=observed_at,
        snapshot_age_seconds=125,
        available_actions=("$rolls",),
        upcoming_events=(),
    )
    reactions = SimpleNamespace(receipt_count=2, total_kakera_earned=600)
    latest_reaction = SimpleNamespace(
        kakera_earned=350,
        reaction_label=":kakeraY:",
        observed_at=observed_at,
    )
    latest_roll = SimpleNamespace(
        character=SimpleNamespace(name="Chisato Nishikigi"),
        kakera_value=209,
        claim_rank=484,
        observed_at=observed_at,
    )
    roll_stats = SimpleNamespace(
        roll_count=8,
        average_kakera_value=119.5,
        best_claim_rank=484,
    )
    latest_key = SimpleNamespace(
        character_name="Mai Sakurajima",
        key_count=7,
        key_type="gold",
        observed_at=observed_at,
    )

    class FakeCatalogService:
        def kakera_reaction_summary(self, server: str, account: str):
            return reactions

        def kakera_reactions(self, server: str, account: str, limit: int):
            return (latest_reaction,)

        def recent_rolls(self, server: str, account: str, limit: int):
            return (latest_roll,)

        def roll_statistics(self, server: str, account: str):
            return roll_stats

        def recent_key_gains(self, server: str, account: str, limit: int):
            return (latest_key,)

    monkeypatch.setattr(main, "AccountOverviewService", lambda: SimpleNamespace(overview=lambda *_: overview))
    monkeypatch.setattr(main, "ActionService", lambda: SimpleNamespace(readiness=lambda *_: readiness))
    monkeypatch.setattr(main, "CatalogService", FakeCatalogService)
    monkeypatch.setattr(main, "KeyFarmService", lambda: SimpleNamespace(recommend=lambda *_: ()))

    result = CliRunner().invoke(
        main.app,
        ["account", "activity", "--server", "Lake", "--account", "ernieuuu"],
    )

    assert result.exit_code == 0
    assert "Timer snapshot" in result.stdout
    assert "2m 5s old | 2026-07-12 23:45 UTC" in result.stdout
    assert "Latest reaction" in result.stdout
    assert "+350:kakeraY: | 2026-07-12 23:45 UTC" in result.stdout
    assert "Latest roll" in result.stdout
    assert "Chisato Nishikigi" in result.stdout
    assert "209:kakera:" in result.stdout
    assert "#484" in result.stdout
    assert "2026-07-12 23:45 UTC" in result.stdout
    assert "Mai Sakurajima" in result.stdout
    assert ":goldkey: (7)" in result.stdout
    assert "2026-07-12 23:45 UTC" in result.stdout


def test_catalog_top_displays_unavailable_reasons() -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    entries = (
        CatalogTopSearchEntry(
            character=CatalogCharacter(
                id=1, name="Venom", series="Marvel", gender=None, roulette=None
            ),
            claim_rank=87,
            like_rank=None,
            observed_at=observed_at,
            owned=None,
            keyed=None,
            unavailable=True,
            unavailable_reason="$togglewestern",
        ),
        CatalogTopSearchEntry(
            character=CatalogCharacter(
                id=2, name="2B", series="NieR: Automata", gender=None, roulette=None
            ),
            claim_rank=10,
            like_rank=None,
            observed_at=observed_at,
            owned=None,
            keyed=None,
            unavailable=True,
            unavailable_reason=None,
        ),
    )

    assert main._format_rollability(entries[0].unavailable, entries[0].unavailable_reason) == (
        "Unavailable ($togglewestern)"
    )
    assert main._format_rollability(entries[1].unavailable, entries[1].unavailable_reason) == (
        "Unavailable (disabled)"
    )
    assert main._format_rollability(False, None, "ernieuuu", True) == "Claimed"
    assert main._format_rollability(False, None, status="Wishlist") == "Wishlist"


def test_discord_listener_requires_a_bot_token(monkeypatch) -> None:
    monkeypatch.delenv("MOA_DISCORD_BOT_TOKEN", raising=False)

    result = CliRunner().invoke(main.app, ["discord", "listen"])

    assert result.exit_code == 1
    assert "Discord bot token missing" in result.stdout


def test_discord_listener_rejects_example_bot_token() -> None:
    result = CliRunner().invoke(
        main.app,
        ["discord", "listen", "--token", "YOUR_DISCORD_BOT_TOKEN"],
    )

    assert result.exit_code == 1
    assert "Replace YOUR_DISCORD_BOT_TOKEN" in result.stdout


def test_discord_listener_wires_shared_database_and_roll_coordinator(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "moa.db"
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)
    captured: dict[str, object] = {}
    kakera_coordinators: list[KakeraStateProjectionCoordinator] = []
    timer_coordinators: list[TimerProjectionCoordinator] = []
    tower_coordinators: list[TowerStateProjectionCoordinator] = []

    class RecordingKakeraStateProjectionCoordinator(KakeraStateProjectionCoordinator):
        def __init__(self, *repositories):
            kakera_coordinators.append(self)
            super().__init__(*repositories)

    class RecordingTimerProjectionCoordinator(TimerProjectionCoordinator):
        def __init__(self, *repositories):
            timer_coordinators.append(self)
            super().__init__(*repositories)

    class RecordingTowerStateProjectionCoordinator(TowerStateProjectionCoordinator):
        def __init__(self, *repositories):
            tower_coordinators.append(self)
            super().__init__(*repositories)

    class FakeListener:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, token, mudae_user_id):
            captured["token"] = token
            captured["mudae_user_id"] = mudae_user_id

    monkeypatch.setattr(main, "DiscordListenerService", FakeListener)
    monkeypatch.setattr(
        main,
        "KakeraStateProjectionCoordinator",
        RecordingKakeraStateProjectionCoordinator,
    )
    monkeypatch.setattr(main, "TimerProjectionCoordinator", RecordingTimerProjectionCoordinator)
    monkeypatch.setattr(
        main,
        "TowerStateProjectionCoordinator",
        RecordingTowerStateProjectionCoordinator,
    )

    result = CliRunner().invoke(
        main.app,
        ["discord", "listen", "--token", "test-token", "--mudae-user-id", "999"],
    )

    assert result.exit_code == 0
    catalog_service = captured["catalog_service"]
    discord_repository = captured["discord_message_repository"]
    importer = captured["importer"]
    coordinator = importer._roll_projection_coordinator
    profile_coordinator = importer._profile_projection_coordinator
    claim_coordinator = importer._claim_projection_coordinator
    settings_coordinator = importer._settings_projection_coordinator
    infokl_coordinator = importer._infokl_projection_coordinator
    timer_coordinator = importer._timer_projection_coordinator
    kakera_coordinator = importer._kakera_state_projection_coordinator
    tower_coordinator = importer._tower_state_projection_coordinator
    assert isinstance(catalog_service, CatalogService)
    assert isinstance(catalog_service._repository, CatalogRepository)
    assert isinstance(discord_repository, DiscordMessageRepository)
    assert isinstance(importer, AutomaticImportService)
    assert isinstance(coordinator, RollProjectionCoordinator)
    assert isinstance(profile_coordinator, ProfileProjectionCoordinator)
    assert isinstance(claim_coordinator, ClaimProjectionCoordinator)
    assert isinstance(settings_coordinator, SettingsProjectionCoordinator)
    assert isinstance(infokl_coordinator, InfoklProjectionCoordinator)
    assert isinstance(timer_coordinator, TimerProjectionCoordinator)
    assert isinstance(kakera_coordinator, KakeraStateProjectionCoordinator)
    assert isinstance(tower_coordinator, TowerStateProjectionCoordinator)
    assert kakera_coordinators == [kakera_coordinator]
    assert timer_coordinators == [timer_coordinator]
    assert tower_coordinators == [tower_coordinator]
    assert catalog_service._repository._database_path == database_path
    assert discord_repository._database_path == database_path
    assert coordinator._catalog is catalog_service._repository
    assert coordinator._discord is discord_repository
    assert coordinator._database_path == database_path
    assert profile_coordinator._catalog is catalog_service._repository
    assert profile_coordinator._discord is discord_repository
    assert profile_coordinator._database_path == database_path
    assert claim_coordinator._catalog is catalog_service._repository
    assert claim_coordinator._discord is discord_repository
    assert claim_coordinator._database_path == database_path
    assert settings_coordinator._catalog is catalog_service._repository
    assert settings_coordinator._discord is discord_repository
    assert settings_coordinator._database_path == database_path
    assert infokl_coordinator._catalog is catalog_service._repository
    assert infokl_coordinator._discord is discord_repository
    assert infokl_coordinator._database_path == database_path
    assert timer_coordinator._catalog is catalog_service._repository
    assert timer_coordinator._discord is discord_repository
    assert timer_coordinator._database_path == database_path
    assert kakera_coordinator._catalog is catalog_service._repository
    assert kakera_coordinator._discord is discord_repository
    assert kakera_coordinator._database_path == database_path
    assert tower_coordinator._catalog is catalog_service._repository
    assert tower_coordinator._discord is discord_repository
    assert tower_coordinator._database_path == database_path
    assert timer_coordinators[0]._catalog is catalog_service._repository
    assert timer_coordinators[0]._discord is discord_repository
    assert captured["catalog_service"] is importer._catalog
    assert captured["token"] == "test-token"
    assert captured["mudae_user_id"] == 999


def test_import_auto_keeps_direct_automatic_import_without_tower_coordinator(
    monkeypatch,
) -> None:
    constructed: list[tuple[tuple[object, ...], dict[str, object]]] = []
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class RecordingImporter:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))

        def import_message(self, *args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(
                kind="towerstate",
                imported_count=1,
                message="Imported Kakera Tower state.",
            )

    monkeypatch.setattr(main, "AutomaticImportService", RecordingImporter)
    monkeypatch.setattr(
        main,
        "_read_message_source",
        lambda path, clipboard: "tower response",
    )

    result = CliRunner().invoke(
        main.app,
        ["import", "auto", "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert result.exit_code == 0
    assert constructed == [((), {})]
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("tower response", "clipboard", "Lake", "ernieuuu")
    assert kwargs == {"harem_scan_id": None}


def test_catalog_keys_display_uses_mudae_key_marker_and_count() -> None:
    assert main._format_catalog_keys(True, "gold", 7) == ":goldkey: (7)"
    assert main._format_catalog_keys(True, "Gold Key", 7) == ":goldkey: (7)"
    assert main._format_catalog_keys(False, None, None) == "-"
    assert main._format_catalog_keys(None, None, None) == "Not requested"


def test_catalog_reset_requires_confirmation_and_backs_up_database(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    database_path.write_text("catalog", encoding="utf-8")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)
    runner = CliRunner()

    dry_run = runner.invoke(main.app, ["catalog", "reset"])
    applied = runner.invoke(main.app, ["catalog", "reset", "--confirm"])

    assert dry_run.exit_code == 0
    assert "No changes made" in dry_run.stdout
    assert applied.exit_code == 0
    assert not database_path.exists()
    assert len(list(tmp_path.glob("moa.db.bak-full-reset-*"))) == 1


def test_catalog_ownership_display_distinguishes_topo_claims_from_harem_evidence() -> None:
    assert main._format_catalog_ownership(None, "cute_beagle_91130", True, True) == (
        "Claimed 💞 => cute_beagle_91130"
    )
    assert main._format_catalog_ownership(None, "xuppii", False, True) == (
        "Claimed 💞 => xuppii"
    )
    assert main._format_catalog_ownership(True, None, None, False) == "Claimed"
    assert main._format_catalog_ownership(False, None, None, True) == "Unclaimed"
    assert main._format_catalog_ownership(None, None, None, False) == "(no data)"


def test_config_commands_manage_active_server_account_context(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MOA_CONFIG_PATH", str(tmp_path / "config.json"))
    runner = CliRunner()

    added = runner.invoke(
        main.app,
        [
            "config",
            "account",
            "add",
            "--server",
            "Lake Arrowhead 2025",
            "--account",
            "ernieuuu",
        ],
    )
    alt = runner.invoke(
        main.app,
        [
            "config",
            "account",
            "add",
            "--server",
            "Lake Arrowhead 2025",
            "--account",
            "ernie_alt",
            "--role",
            "alt",
        ],
    )
    used = runner.invoke(
        main.app,
        [
            "config",
            "use",
            "--server",
            "Lake Arrowhead 2025",
            "--account",
            "ernieuuu",
        ],
    )
    shown = runner.invoke(main.app, ["config", "show"])

    assert added.exit_code == 0
    assert alt.exit_code == 0
    assert used.exit_code == 0
    assert shown.exit_code == 0
    assert "ernie_alt" in shown.stdout
    assert "Active" in shown.stdout


def test_config_commands_allow_observed_users(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MOA_CONFIG_PATH", str(tmp_path / "config.json"))
    runner = CliRunner()

    result = runner.invoke(
        main.app,
        [
            "config",
            "account",
            "add",
            "--server",
            "LEAGUE OF DRAVEN",
            "--account",
            "friend_account",
            "--role",
            "observed",
            "--server-id",
            "1402543612549398538",
            "--user-id",
            "999999999999999999",
        ],
    )

    assert result.exit_code == 0
    assert "Added observed account friend_account" in result.stdout


def test_config_account_add_rejects_placeholder_discord_ids(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MOA_CONFIG_PATH", str(tmp_path / "config.json"))

    result = CliRunner().invoke(
        main.app,
        [
            "config",
            "account",
            "add",
            "--server",
            "NEW SERVER NAME",
            "--account",
            "new_account",
            "--server-id",
            "PASTE_SERVER_ID_HERE",
            "--user-id",
            "PASTE_USER_ID_HERE",
        ],
    )

    assert result.exit_code == 1
    assert "Server ID must be a numeric Discord ID" in result.stdout


def test_config_use_accepts_discord_ids(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MOA_CONFIG_PATH", str(tmp_path / "config.json"))
    runner = CliRunner()
    runner.invoke(
        main.app,
        [
            "config",
            "account",
            "add",
            "--server",
            "Lake Arrowhead 2025",
            "--account",
            "ernieuuu",
            "--server-id",
            "1323181920397426763",
            "--user-id",
            "146851153412358144",
        ],
    )

    result = runner.invoke(
        main.app,
        [
            "config",
            "use",
            "--server-id",
            "1323181920397426763",
            "--user-id",
            "146851153412358144",
        ],
    )

    assert result.exit_code == 0
    assert "Lake Arrowhead 2025 / ernieuuu" in result.stdout
