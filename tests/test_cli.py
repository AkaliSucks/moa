from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import moa.services.account_comparison_service as account_comparison_service_module
import moa.services.account_overview_service as account_overview_service_module
import moa.services.action_service as action_service_module
import moa.services.catalog_service as catalog_service_module
import moa.services.kakeraloot_budget_service as kakeraloot_budget_service_module
import moa.services.keyfarm_service as keyfarm_service_module
import moa.services.loot_service as loot_service_module
import moa.services.progress_service as progress_service_module
import moa.services.server_comparison_service as server_comparison_module
from moa.cli import main
from moa.models.catalog import CatalogCharacter, CatalogTopSearchEntry
from moa.parser.mudae import MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.antidisable_page_projection_coordinator import (
    AntidisablePageProjectionCoordinator,
)
from moa.services.automatic_import_service import AutomaticImportService
from moa.services.catalog_service import CatalogService
from moa.services.claim_projection_coordinator import ClaimProjectionCoordinator
from moa.services.disablelist_projection_coordinator import DisableListProjectionCoordinator
from moa.services.infokl_projection_coordinator import InfoklProjectionCoordinator
from moa.services.kakera_state_projection_coordinator import KakeraStateProjectionCoordinator
from moa.services.kakeraloot_state_projection_coordinator import (
    KakeralootStateProjectionCoordinator,
)
from moa.services.player_bonus_projection_coordinator import PlayerBonusProjectionCoordinator
from moa.services.profile_projection_coordinator import ProfileProjectionCoordinator
from moa.services.roll_projection_coordinator import RollProjectionCoordinator
from moa.services.settings_projection_coordinator import SettingsProjectionCoordinator
from moa.services.sphere_result_projection_coordinator import SphereResultProjectionCoordinator
from moa.services.timer_projection_coordinator import TimerProjectionCoordinator
from moa.services.tower_state_projection_coordinator import TowerStateProjectionCoordinator
from moa.services.wishlist_projection_coordinator import WishlistProjectionCoordinator


def test_recommend_keyfarm_resolves_context_and_constructs_service_at_callback_time(
    tmp_path, monkeypatch
) -> None:
    server = "Lake Arrowhead 2025"
    account = "ernieuuu"
    database_path = tmp_path / "catalog.db"
    catalog = CatalogService(CatalogRepository(database_path))
    harem = (
        "Power · :silverkey: (5) 1,448 ka\n"
        "Emilia · :silverkey: (5) 1,295 ka\n"
        "Megumin · :silverkey: (5) 1,505 ka\n"
        "Page 1 / 1"
    )
    bonus = (
        "Spawn bonus for wishes: +210% ($k)\n"
        "Additional % spawn bonus for $starwish: +180% ($kt) (= 390%)\n"
        "Chance to get an additional key on wishes: +10% ($kt)"
    )
    wishlist = (
        "ernieuuu's Wishlist - 3/13 $wl, 2/2 $sw\n"
        "Power ⭐\n"
        "Emilia ⭐\n"
        "Megumin"
    )
    catalog.import_harem_key_page(
        MudaeTextParser().parse_harem_key_page(harem), server, account, harem, "test"
    )
    catalog.import_player_bonus(
        MudaeTextParser().parse_player_bonus(bonus), server, account, bonus, "test"
    )
    catalog.import_wishlist(
        MudaeTextParser().parse_wishlist(wishlist), server, account, wishlist, "test"
    )
    events: list[str] = []

    class RecordingConfigService:
        def resolve_context(self, requested_server, requested_account):
            events.append("resolve")
            return requested_server, requested_account

    def isolated_catalog() -> CatalogService:
        events.append("catalog")
        return catalog

    monkeypatch.setattr(main, "ConfigService", RecordingConfigService)
    monkeypatch.setattr(keyfarm_service_module, "CatalogService", isolated_catalog)
    runner = CliRunner()

    recommend_help = runner.invoke(main.app, ["recommend", "--help"])
    assert recommend_help.exit_code == 0
    assert "keyfarm" in recommend_help.stdout
    assert events == []

    keyfarm_help = runner.invoke(main.app, ["recommend", "keyfarm", "--help"])
    assert keyfarm_help.exit_code == 0
    assert "--server" in keyfarm_help.stdout
    assert "-s" in keyfarm_help.stdout
    assert "--account" in keyfarm_help.stdout
    assert "-a" in keyfarm_help.stdout
    assert "--limit" in keyfarm_help.stdout
    assert "15" in keyfarm_help.stdout
    assert events == []

    result = runner.invoke(
        main.app,
        [
            "recommend",
            "keyfarm",
            "--server",
            server,
            "--account",
            account,
            "--limit",
            "1",
        ],
    )
    assert result.exit_code == 0
    assert events == ["resolve", "catalog"]
    assert "key-farm recommendations" in result.stdout
    assert "Power" in result.stdout
    assert "Kakera" in result.stdout
    assert "Megumin" not in result.stdout

    missing_catalog = CatalogService(CatalogRepository(tmp_path / "missing.db"))
    monkeypatch.setattr(keyfarm_service_module, "CatalogService", lambda: missing_catalog)
    events.clear()
    missing = runner.invoke(
        main.app,
        ["recommend", "keyfarm", "--server", server, "--account", account],
    )
    assert missing.exit_code == 1
    assert events == ["resolve"]
    assert "Import a $bonus snapshot" in missing.stdout
    assert "Power" not in missing.stdout


def test_loot_cli_characterizes_registration_laziness_and_runtime_seams(
    monkeypatch,
) -> None:
    events: list[str] = []
    runner = CliRunner()
    real_loot_service = loot_service_module.KakeralootService
    real_budget_service = kakeraloot_budget_service_module.KakeralootBudgetService
    real_loot_init = real_loot_service.__init__
    real_budget_init = real_budget_service.__init__

    class UnexpectedConfigService:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Context resolution must not run during help")

    def unexpected_service_init(self, *args, **kwargs):
        raise AssertionError("Loot services must not construct during help")

    monkeypatch.setattr(real_loot_service, "__init__", unexpected_service_init)
    monkeypatch.setattr(real_budget_service, "__init__", unexpected_service_init)
    monkeypatch.setattr(main, "ConfigService", UnexpectedConfigService)

    for arguments in (
        ["loot", "--help"],
        ["loot", "list", "--help"],
        ["loot", "show", "--help"],
        ["loot", "next", "--help"],
    ):
        result = runner.invoke(main.app, arguments)
        assert result.exit_code == 0
        assert events == []

    help_result = runner.invoke(main.app, ["loot", "--help"])
    assert all(command in help_result.stdout for command in ("list", "show", "next"))

    def recording_loot_init(self, *args, **kwargs):
        events.append("loot_service_construct")
        real_loot_init(self, *args, **kwargs)

    monkeypatch.setattr(real_loot_service, "__init__", recording_loot_init)
    listed = runner.invoke(main.app, ["loot", "list"])
    assert listed.exit_code == 0
    assert events == ["loot_service_construct"]
    assert "$bku Reset Chance" in listed.stdout
    assert "complete known reward list" in listed.stdout

    events.clear()
    shown = runner.invoke(main.app, ["loot", "show", "bku_reset_chance"])
    assert shown.exit_code == 0
    assert events == ["loot_service_construct"]
    assert "$bku Reset Chance" in shown.stdout
    assert "Sapphire I" in shown.stdout

    events.clear()
    unknown = runner.invoke(main.app, ["loot", "show", "not-a-loot"])
    assert unknown.exit_code == 1
    assert events == ["loot_service_construct"]
    assert "Kakeraloot reward not found." in unknown.stdout
    assert "Traceback" not in unknown.stdout

    class RecordingConfigService:
        def resolve_context(self, requested_server, requested_account):
            events.append("resolve")
            return requested_server, requested_account

    class IsolatedBudgetCatalog:
        def __init__(self):
            events.append("budget_input_construct")

        def kakera_state(self, server_name, account_name):
            events.append("kakera_read")

        def kakeraloot_settings(self, server_name):
            events.append("settings_read")

        def kakeraloot_state(self, server_name, account_name):
            events.append("loot_state_read")
            raise AssertionError("Locked Kakeraloots must not read state")

    monkeypatch.setattr(main, "ConfigService", RecordingConfigService)

    def recording_budget_init(self, *args, **kwargs):
        events.append("budget_service_construct")
        real_budget_init(self, *args, **kwargs)

    monkeypatch.setattr(real_budget_service, "__init__", recording_budget_init)
    monkeypatch.setattr(
        kakeraloot_budget_service_module,
        "CatalogService",
        IsolatedBudgetCatalog,
    )
    events.clear()
    next_result = runner.invoke(
        main.app,
        ["loot", "next", "-s", "Lake", "-a", "ernieuuu"],
    )
    assert next_result.exit_code == 0
    assert events == [
        "resolve",
        "budget_service_construct",
        "budget_input_construct",
        "kakera_read",
        "settings_read",
    ]
    assert "Import $k before planning Kakeraloot spending." in next_result.stdout
    assert "affordable now" not in next_result.stdout


def test_command_cli_registration_rendering_and_validation() -> None:
    runner = CliRunner()

    help_result = runner.invoke(main.app, ["command", "--help"])
    assert help_result.exit_code == 0
    assert "explain" in help_result.stdout
    assert "flags" in help_result.stdout

    explain_result = runner.invoke(
        main.app,
        ["command", "explain", "$mmwy=a+ Re:Zero$--Some bundle"],
    )
    assert explain_result.exit_code == 0
    assert "Command:" in explain_result.stdout
    assert "$mm" in explain_result.stdout
    assert "Include:" in explain_result.stdout
    assert "Re:Zero" in explain_result.stdout
    assert "Exclude:" in explain_result.stdout
    assert "Some bundle" in explain_result.stdout
    assert "Flag" in explain_result.stdout
    assert "Category" in explain_result.stdout
    assert "w" in explain_result.stdout
    assert "gender" in explain_result.stdout
    assert "Waifu characters." in explain_result.stdout

    no_flags_result = runner.invoke(main.app, ["command", "explain", "$mm"])
    assert no_flags_result.exit_code == 0
    assert "Command:" in no_flags_result.stdout
    assert "$mm" in no_flags_result.stdout
    assert "No flags supplied." in no_flags_result.stdout

    flags_result = runner.invoke(main.app, ["command", "flags", "-c", "spheres"])
    assert flags_result.exit_code == 0
    assert "Mudae command flags" in flags_result.stdout
    assert "Flag" in flags_result.stdout
    assert "Category" in flags_result.stdout
    assert "spheres" in flags_result.stdout
    assert "z" in flags_result.stdout
    assert "Characters with spheres." in flags_result.stdout

    invalid_result = runner.invoke(main.app, ["command", "explain", "$mm?"])
    assert invalid_result.exit_code == 1
    assert "Unknown Mudae flag near" in invalid_result.stdout

    unmatched_result = runner.invoke(
        main.app,
        ["command", "flags", "-c", "not-a-category"],
    )
    assert unmatched_result.exit_code == 1
    assert "No Mudae flags matched that category." in unmatched_result.stdout


def test_tower_cli_registration_and_rendering() -> None:
    runner = CliRunner()

    help_result = runner.invoke(main.app, ["tower", "--help"])
    assert help_result.exit_code == 0
    assert "list" in help_result.stdout
    assert "show" in help_result.stdout

    list_result = runner.invoke(main.app, ["tower", "list"])
    assert list_result.exit_code == 0
    assert "Kakera Tower Floors" in list_result.stdout
    assert "Additional Rolls" in list_result.stdout

    show_result = runner.invoke(main.app, ["tower", "show", "11"])
    assert show_result.exit_code == 0
    assert "Floor 11: Additional Rolls" in show_result.stdout
    assert "Category: rolling" in show_result.stdout
    assert "Description: Adds one roll per hour." in show_result.stdout
    assert "First tower: +1 roll per hour" in show_result.stdout
    assert "Progression:" in show_result.stdout
    assert "Caps at +10 rolls/hour" in show_result.stdout
    assert "Initial cap: 10" in show_result.stdout

    unknown_result = runner.invoke(main.app, ["tower", "show", "13"])
    assert unknown_result.exit_code == 1
    assert "Tower floor not found." in unknown_result.stdout


def test_server_cli_registration_rendering_and_validation(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    lake = (
        "(Server not premium)\n"
        "· Prefix: $ ($prefix)\n"
        "· Lang: en ($lang)\n"
        "· Claim reset: every 180 min. ($setclaim)\n"
        "· Exact minute of the reset: xx:14 ($setinterval)\n"
        "· Reset shifted: by +0 min. ($shifthour)\n"
        "· Rolls per hour: 10 ($setrolls)\n"
        "· Time before the claim reaction expires: 45 sec. ($settimer)\n"
        "· Spawn rarity multiplier for already claimed characters: 4 ($setrare)\n"
        "· % kakera bonus: +0 ($setkakerabonus)\n"
        "· % sphere bonus: +0 ($setspherebonus)\n"
        "· Game mode: 1 ($gamemode)\n"
        "· This channel instance: 1 ($channelinstance)"
    )
    comparison = lake.replace("· Rolls per hour: 10", "· Rolls per hour: 12")
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    parser = MudaeTextParser()
    catalog.import_server_settings(parser.parse_server_settings(lake), "Lake", lake, "test")
    catalog.import_server_settings(parser.parse_server_settings(comparison), "Fresh", comparison, "test")
    constructions = 0

    def isolated_catalog() -> CatalogService:
        nonlocal constructions
        constructions += 1
        return catalog

    monkeypatch.setattr(server_comparison_module, "CatalogService", isolated_catalog)

    help_result = runner.invoke(main.app, ["server", "--help"])
    assert help_result.exit_code == 0
    assert "compare" in help_result.stdout
    assert constructions == 0

    compare_help_result = runner.invoke(main.app, ["server", "compare", "--help"])
    assert compare_help_result.exit_code == 0
    assert "--left" in compare_help_result.stdout
    assert "--right" in compare_help_result.stdout
    assert constructions == 0

    success_result = runner.invoke(
        main.app,
        ["server", "compare", "--left", "Lake", "--right", "Fresh"],
    )
    assert success_result.exit_code == 0
    assert "Lake vs Fresh" in success_result.stdout
    assert "Rolls per hour" in success_result.stdout
    assert "10" in success_result.stdout
    assert "12" in success_result.stdout
    assert "This compares imported server configuration only" in success_result.stdout
    assert constructions == 1

    missing_result = runner.invoke(
        main.app,
        ["server", "compare", "--left", "Lake", "--right", "Missing"],
    )
    assert missing_result.exit_code == 1
    assert "No $settings snapshot imported for 'Missing'." in missing_result.stdout
    assert constructions == 2


def test_reaction_cli_registration_and_rendering() -> None:
    runner = CliRunner()

    help_result = runner.invoke(main.app, ["reaction", "--help"])
    assert help_result.exit_code == 0
    assert "list" in help_result.stdout
    assert "show" in help_result.stdout

    list_result = runner.invoke(main.app, ["reaction", "list"])
    assert list_result.exit_code == 0
    assert "Kakera Reactions" in list_result.stdout
    assert "Red Kakera" in list_result.stdout
    assert "1,401-1,500" in list_result.stdout
    assert "1,450.5" in list_result.stdout
    assert "Standard" in list_result.stdout

    show_result = runner.invoke(main.app, ["reaction", "show", "RED"])
    assert show_result.exit_code == 0
    assert "Red Kakera" in show_result.stdout
    assert "Type: range" in show_result.stdout
    assert "Reaction power: standard" in show_result.stdout
    assert "Base average: 1,450.5000 Kakera" in show_result.stdout
    assert "Details: A high-value standard reaction whose value is within its listed range." in show_result.stdout

    unknown_result = runner.invoke(main.app, ["reaction", "show", "not-a-reaction"])
    assert unknown_result.exit_code == 1
    assert "Kakera reaction not found." in unknown_result.stdout


def test_key_cli_registration_and_rendering() -> None:
    runner = CliRunner()

    help_result = runner.invoke(main.app, ["key", "--help"])
    assert help_result.exit_code == 0
    assert "list" in help_result.stdout
    assert "show" in help_result.stdout

    list_result = runner.invoke(main.app, ["key", "list"])
    assert list_result.exit_code == 0
    assert "Character Key Tiers" in list_result.stdout
    assert "This is universal key knowledge." in list_result.stdout
    assert "Bronze Key" in list_result.stdout
    assert "1-2" in list_result.stdout

    show_result = runner.invoke(main.app, ["key", "show", "chaos"])
    assert show_result.exit_code == 0
    assert "Chaos Key - Keys 10+" in show_result.stdout
    assert "Keys ten and above on a character" in show_result.stdout
    assert "Kakera reactions" in show_result.stdout
    assert "half power" in show_result.stdout
    assert "Character becomes a Soulmate." in show_result.stdout

    unknown_result = runner.invoke(main.app, ["key", "show", "not-a-tier"])
    assert unknown_result.exit_code == 1
    assert "Character key tier not found." in unknown_result.stdout


def test_parse_lootstate_renders_missing_optional_values_without_zero_or_crash(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        main,
        "_read_message_source",
        lambda path, clipboard: (
            "sample - Kakeraloots\nQuantity LVL 5\nQuality LVL 0\n$kl usage: 1\n31,271:kakera:"
        ),
    )

    result = CliRunner().invoke(main.app, ["parse", "lootstate", "--clipboard"])

    assert result.exit_code == 0
    assert "Quality 0" in result.stdout
    assert "Rolls stacked: -" in result.stdout
    assert "Wishprotect: -" in result.stdout
    assert "Permanent rolls: -" in result.stdout


def test_catalog_lootstate_renders_unknown_values_without_integer_formatting(
    monkeypatch,
) -> None:
    state = SimpleNamespace(
        account_name="Account",
        has_kakeraloots=True,
        status_note=None,
        kakera_balance=None,
        usage_count=None,
        quantity_level=0,
        quality_level=None,
        rolls_stacked=None,
        permanent_roll_bonus=None,
        protected_wish_level=None,
        protected_wish_denominator=None,
        disable_wa_ha_reduction=None,
        disable_wg_hg_reduction=None,
        rt_cooldown_reduction_hours=None,
        mudapins=None,
        star_branches=None,
        starwish_slots_from_branches=None,
        observed_at=datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        main,
        "CatalogService",
        lambda: SimpleNamespace(kakeraloot_state=lambda *_: state),
    )

    result = CliRunner().invoke(
        main.app,
        ["catalog", "lootstate", "--server", "Lake", "--account", "Account"],
    )

    assert result.exit_code == 0
    assert "Quantity / Quality" in result.stdout
    assert "0 /" not in result.stdout
    assert "Rolls stacked" in result.stdout
    assert "Wishprotect" in result.stdout


@pytest.mark.parametrize(
    ("value", "rendered"),
    ((True, "True"), (False, "False"), (None, "Unknown")),
)
def test_catalog_disablelist_renders_toggle_presence_without_inventing_false(
    monkeypatch, value, rendered
) -> None:
    disablelist = SimpleNamespace(
        account_name="Account",
        slots_used=0,
        slots_capacity=16,
        total_disabled=0,
        disabled_wa=0,
        disabled_ha=0,
        disabled_wg=0,
        disabled_hg=0,
        western_disabled=value,
        irl_disabled=value,
        entries=(),
    )
    monkeypatch.setattr(
        main,
        "CatalogService",
        lambda: SimpleNamespace(disablelist=lambda *_: disablelist),
    )

    result = CliRunner().invoke(
        main.app,
        ["catalog", "disablelist", "--server", "Lake", "--account", "Account"],
    )

    assert result.exit_code == 0
    assert f"Western disabled: {rendered}" in result.stdout
    assert f"IRL disabled: {rendered}" in result.stdout
    assert main._format_observed_toggle(value) == rendered


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

    monkeypatch.setattr(account_overview_service_module.AccountOverviewService, "__init__", lambda self: None)
    monkeypatch.setattr(
        account_overview_service_module.AccountOverviewService,
        "overview",
        lambda self, *_: overview,
    )
    monkeypatch.setattr(action_service_module.ActionService, "__init__", lambda self: None)
    monkeypatch.setattr(
        action_service_module.ActionService,
        "readiness",
        lambda self, *_: readiness,
    )
    monkeypatch.setattr(catalog_service_module.CatalogService, "__init__", lambda self: None)
    monkeypatch.setattr(catalog_service_module.CatalogService, "kakera_reaction_summary", FakeCatalogService.kakera_reaction_summary)
    monkeypatch.setattr(catalog_service_module.CatalogService, "kakera_reactions", FakeCatalogService.kakera_reactions)
    monkeypatch.setattr(catalog_service_module.CatalogService, "recent_rolls", FakeCatalogService.recent_rolls)
    monkeypatch.setattr(catalog_service_module.CatalogService, "roll_statistics", FakeCatalogService.roll_statistics)
    monkeypatch.setattr(catalog_service_module.CatalogService, "recent_key_gains", FakeCatalogService.recent_key_gains)
    monkeypatch.setattr(keyfarm_service_module.KeyFarmService, "__init__", lambda self: None)
    monkeypatch.setattr(
        keyfarm_service_module.KeyFarmService,
        "recommend",
        lambda self, *_: (_ for _ in ()).throw(ValueError("not ready")),
    )

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

    overview.quality_level = None
    partial_result = CliRunner().invoke(
        main.app,
        ["account", "activity", "--server", "Lake", "--account", "ernieuuu"],
    )

    assert partial_result.exit_code == 0
    assert "Not fully observed" in partial_result.stdout
    assert "Quantity 5; Quality 0" not in partial_result.stdout


def test_account_cli_registration_schema_and_help_are_lazy(monkeypatch) -> None:
    events: list[str] = []

    class UnexpectedConfigService:
        def __init__(self):
            events.append("config")
            raise AssertionError("account help must not resolve configured context")

    def unexpected_constructor(*args, **kwargs):
        events.append("service")
        raise AssertionError("account help must not construct services")

    monkeypatch.setattr(main, "ConfigService", UnexpectedConfigService)
    for service_class in (
        account_overview_service_module.AccountOverviewService,
        account_comparison_service_module.AccountComparisonService,
        progress_service_module.ProgressService,
        action_service_module.ActionService,
        keyfarm_service_module.KeyFarmService,
        catalog_service_module.CatalogService,
    ):
        monkeypatch.setattr(service_class, "__init__", unexpected_constructor)

    runner = CliRunner()
    help_result = runner.invoke(main.app, ["account", "--help"])
    assert help_result.exit_code == 0
    assert [name for name in ("activity", "overview", "compare", "progress") if name in help_result.stdout] == [
        "activity",
        "overview",
        "compare",
        "progress",
    ]
    assert events == []

    expected_help = {
        "activity": ("--server", "-s", "--account", "-a"),
        "overview": ("--server", "-s", "--account", "-a"),
        "compare": (
            "--left-server",
            "--left-account",
            "--right-server",
            "--right-account",
        ),
        "progress": ("--server", "-s", "--account", "-a"),
    }
    for command, fragments in expected_help.items():
        result = runner.invoke(main.app, ["account", command, "--help"])
        assert result.exit_code == 0
        for fragment in fragments:
            assert fragment in result.stdout
    assert events == []


def test_account_resolver_driven_commands_resolve_before_service_reads(monkeypatch) -> None:
    events: list[object] = []

    class RecordingConfigService:
        def resolve_context(self, server, account):
            events.append(("resolve", server, account))
            if server == "missing":
                raise ValueError("No configured account context")
            return server or "Lake", account or "ernieuuu"

    overview = SimpleNamespace(
        account_name="ernieuuu",
        kakera_balance=12000,
        kakera_balance_source="$k",
        personal_rare_multiplier=None,
        server_rare_multiplier=None,
        max_badge_count=3,
        badge_count=7,
        tower_level=None,
        completed_towers=None,
        next_tower_cost=None,
        tower_shortfall=None,
        kakeraloots_unlocked=True,
        missing_kakeraloot_prerequisites=(),
        has_kakeraloots=True,
        kakeraloot_status_note=None,
        quantity_level=5,
        quality_level=4,
        loot_usage_count=12,
        wishlist_count=4,
        wishlist_capacity=10,
        starwish_count=1,
        starwish_capacity=2,
        disable_slots_used=2,
        disable_slots_capacity=16,
        keyed_harem_count=25,
    )
    progress = SimpleNamespace(
        account_name="ernieuuu",
        observations=(
            SimpleNamespace(
                observed_at=datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc),
                kakera_balance=12000,
                max_badge_count=3,
            ),
        ),
        kakera_change=None,
        elapsed_seconds=None,
        kakera_per_day=None,
    )

    class RecordingOverviewService:
        def __init__(self):
            events.append("overview-construct")

        def overview(self, server, account):
            events.append(("overview-read", server, account))
            return overview

    class RecordingProgressService:
        def __init__(self):
            events.append("progress-construct")

        def kakera_progress(self, server, account):
            events.append(("progress-read", server, account))
            return progress

    monkeypatch.setattr(main, "ConfigService", RecordingConfigService)
    monkeypatch.setattr(account_overview_service_module.AccountOverviewService, "__init__", RecordingOverviewService.__init__)
    monkeypatch.setattr(account_overview_service_module.AccountOverviewService, "overview", RecordingOverviewService.overview)
    monkeypatch.setattr(progress_service_module.ProgressService, "__init__", RecordingProgressService.__init__)
    monkeypatch.setattr(progress_service_module.ProgressService, "kakera_progress", RecordingProgressService.kakera_progress)

    overview_result = CliRunner().invoke(main.app, ["account", "overview", "--server", "Lake", "--account", "ernieuuu"])
    assert overview_result.exit_code == 0
    assert "ernieuuu - account overview" in overview_result.stdout
    assert "12,000 Kakera ($k)" in overview_result.stdout
    assert events == [
        ("resolve", "Lake", "ernieuuu"),
        "overview-construct",
        ("overview-read", "Lake", "ernieuuu"),
    ]

    events.clear()
    progress_result = CliRunner().invoke(main.app, ["account", "progress", "--server", "Lake", "--account", "ernieuuu"])
    assert progress_result.exit_code == 0
    assert "ernieuuu - Kakera progression" in progress_result.stdout
    assert "12,000" in progress_result.stdout
    assert events == [
        ("resolve", "Lake", "ernieuuu"),
        "progress-construct",
        ("progress-read", "Lake", "ernieuuu"),
    ]

    events.clear()
    missing_result = CliRunner().invoke(main.app, ["account", "overview", "--server", "missing", "--account", "ernieuuu"])
    assert missing_result.exit_code == 1
    assert "No configured account context" in missing_result.stdout
    assert events == [("resolve", "missing", "ernieuuu")]


def test_account_progress_no_snapshots_is_informational(monkeypatch) -> None:
    class RecordingConfigService:
        def resolve_context(self, server, account):
            return server or "Lake", account or "ernieuuu"

    class EmptyProgressService:
        def kakera_progress(self, server, account):
            return SimpleNamespace(observations=())

    monkeypatch.setattr(main, "ConfigService", RecordingConfigService)
    monkeypatch.setattr(progress_service_module.ProgressService, "__init__", lambda self: None)
    monkeypatch.setattr(progress_service_module.ProgressService, "kakera_progress", lambda self, server, account: EmptyProgressService().kakera_progress(server, account))
    result = CliRunner().invoke(main.app, ["account", "progress", "--server", "Lake", "--account", "ernieuuu"])
    assert result.exit_code == 0
    assert "No $k snapshots imported for this server/account yet." in result.stdout
    assert "Observed (UTC)" not in result.stdout


def test_account_compare_uses_explicit_context_without_configured_resolution(monkeypatch) -> None:
    events: list[object] = []

    class UnexpectedConfigService:
        def resolve_context(self, server, account):
            raise AssertionError("account compare must not resolve configured context")

    comparison = SimpleNamespace(
        left_account_name="alpha",
        left_server_name="Lake",
        right_account_name="beta",
        right_server_name="Hill",
        rows=(
            SimpleNamespace(label="Kakera balance", left_value="9,283 ($k)", right_value="Not imported"),
            SimpleNamespace(label="Kakeraloots", left_value="Not imported", right_value="Not imported"),
        ),
    )

    class RecordingComparisonService:
        def __init__(self):
            events.append("construct")

        def compare(self, left_server, left_account, right_server, right_account):
            events.append(("compare", left_server, left_account, right_server, right_account))
            return comparison

    monkeypatch.setattr(main, "ConfigService", UnexpectedConfigService)
    monkeypatch.setattr(account_comparison_service_module.AccountComparisonService, "__init__", RecordingComparisonService.__init__)
    monkeypatch.setattr(account_comparison_service_module.AccountComparisonService, "compare", RecordingComparisonService.compare)
    result = CliRunner().invoke(
        main.app,
        [
            "account",
            "compare",
            "--left-server",
            "Lake",
            "--left-account",
            "alpha",
            "--right-server",
            "Hill",
            "--right-account",
            "beta",
        ],
    )
    assert result.exit_code == 0
    assert "alpha (Lake) vs beta (Hill)" in result.stdout
    assert "9,283 ($k)" in result.stdout
    assert "Not imported" in result.stdout
    assert "Only imported state is compared" in result.stdout
    assert events == ["construct", ("compare", "Lake", "alpha", "Hill", "beta")]


def test_roll_cli_characterization_preserves_help_resolution_and_empty_paths(monkeypatch) -> None:
    events: list[object] = []
    observed_at = datetime.fromisoformat("2026-07-12T23:45:00+00:00")
    roll = SimpleNamespace(
        observed_at=observed_at,
        character=SimpleNamespace(name="Chisato Nishikigi", series="Lycoris Recoil"),
        claim_rank=484,
        kakera_value=209,
    )
    statistics = SimpleNamespace(
        roll_count=2,
        best_claim_rank=484,
        average_claim_rank=512.5,
        average_kakera_value=119.5,
        highest_kakera_value=209,
    )
    empty = {"recent": False, "stats": False}

    class RecordingConfigService:
        def resolve_context(self, server, account):
            events.append(("resolve", server, account))
            return "Lake", "ernieuuu"

    def recording_init(self) -> None:
        events.append("construct")

    def recent_rolls(self, server, account, limit):
        events.append(("recent", server, account, limit))
        return () if empty["recent"] else (roll,)

    def roll_statistics(self, server, account):
        events.append(("stats", server, account))
        return SimpleNamespace(
            roll_count=0,
            best_claim_rank=None,
            average_claim_rank=None,
            average_kakera_value=None,
            highest_kakera_value=None,
        ) if empty["stats"] else statistics

    monkeypatch.setattr(main, "ConfigService", RecordingConfigService)
    monkeypatch.setattr(CatalogService, "__init__", recording_init)
    monkeypatch.setattr(CatalogService, "recent_rolls", recent_rolls)
    monkeypatch.setattr(CatalogService, "roll_statistics", roll_statistics)
    monkeypatch.setattr(main.console, "width", 240)
    runner = CliRunner()

    for arguments, expected in (
        (["roll", "--help"], ("recent", "stats", "compare")),
        (["roll", "recent", "--help"], ("--server", "-s", "--account", "-a", "--limit", "-n")),
        (["roll", "stats", "--help"], ("--server", "-s", "--account", "-a")),
        (
            ["roll", "compare", "--help"],
            ("--left-server", "--left-account", "--right-server", "--right-account"),
        ),
    ):
        result = runner.invoke(main.app, arguments)
        assert result.exit_code == 0
        for fragment in expected:
            assert fragment in result.stdout
    assert events == []

    recent = runner.invoke(main.app, ["roll", "recent", "--limit", "1"])
    assert recent.exit_code == 0
    assert "Chisato Nishikigi" in recent.stdout
    assert "209:kakera:" in recent.stdout
    assert "2026-07-12 23:45" in recent.stdout
    assert events == [
        ("resolve", None, None),
        "construct",
        ("recent", "Lake", "ernieuuu", 1),
    ]

    events.clear()
    stats = runner.invoke(main.app, ["roll", "stats", "--server", "Lake", "--account", "ernieuuu"])
    assert stats.exit_code == 0
    assert "Imported rolls" in stats.stdout
    assert "2" in stats.stdout
    assert "These are descriptive results from stored rolls only" in stats.stdout
    assert events == [
        ("resolve", "Lake", "ernieuuu"),
        "construct",
        ("stats", "Lake", "ernieuuu"),
    ]

    empty["recent"] = True
    empty["stats"] = True
    events.clear()
    empty_recent = runner.invoke(main.app, ["roll", "recent"])
    empty_stats = runner.invoke(main.app, ["roll", "stats"])
    assert empty_recent.exit_code == 0
    assert empty_stats.exit_code == 0
    assert "No rolls imported for this server/account yet." in empty_recent.stdout
    assert "No rolls imported for this server/account yet." in empty_stats.stdout


def test_roll_compare_cli_characterization_uses_one_explicit_input_service(monkeypatch) -> None:
    events: list[object] = []
    left = SimpleNamespace(
        account_name="alpha",
        server_name="Lake",
        roll_count=2,
        best_claim_rank=10,
        average_claim_rank=15.5,
        average_kakera_value=100.0,
        highest_kakera_value=150,
    )
    right = SimpleNamespace(
        account_name="beta",
        server_name="Hill",
        roll_count=1,
        best_claim_rank=20,
        average_claim_rank=None,
        average_kakera_value=75.0,
        highest_kakera_value=75,
    )
    missing = {"enabled": False}

    class UnexpectedConfigService:
        def resolve_context(self, server, account):
            raise AssertionError("roll compare must not resolve configured account context")

    def recording_init(self) -> None:
        events.append("construct")

    def roll_statistics(self, server, account):
        events.append(("stats", server, account))
        if missing["enabled"] and account == "alpha":
            return SimpleNamespace(
                account_name=account,
                server_name=server,
                roll_count=0,
                best_claim_rank=None,
                average_claim_rank=None,
                average_kakera_value=None,
                highest_kakera_value=None,
            )
        return left if account == "alpha" else right

    monkeypatch.setattr(main, "ConfigService", UnexpectedConfigService)
    monkeypatch.setattr(CatalogService, "__init__", recording_init)
    monkeypatch.setattr(CatalogService, "roll_statistics", roll_statistics)
    runner = CliRunner()

    result = runner.invoke(
        main.app,
        [
            "roll",
            "compare",
            "--left-server",
            "Lake",
            "--left-account",
            "alpha",
            "--right-server",
            "Hill",
            "--right-account",
            "beta",
        ],
    )
    assert result.exit_code == 0
    assert "alpha (Lake)" in result.stdout
    assert "beta (Hill)" in result.stdout
    assert "Imported rolls" in result.stdout
    assert "This compares imported observations only" in result.stdout
    assert events == ["construct", ("stats", "Lake", "alpha"), ("stats", "Hill", "beta")]

    missing["enabled"] = True
    events.clear()
    empty = runner.invoke(
        main.app,
        [
            "roll",
            "compare",
            "--left-server",
            "Lake",
            "--left-account",
            "alpha",
            "--right-server",
            "Hill",
            "--right-account",
            "beta",
        ],
    )
    assert empty.exit_code == 0
    assert "No rolls imported for alpha in the selected server/account context yet." in empty.stdout
    assert events == ["construct", ("stats", "Lake", "alpha"), ("stats", "Hill", "beta")]


def test_action_now_resolves_context_and_constructs_service_at_callback_time(monkeypatch) -> None:
    events: list[tuple[str, object]] = []

    class RecordingConfigService:
        def resolve_context(self, server, account):
            events.append(("resolve", (server, account)))
            return server or "Lake", account or "ernieuuu"

    class RecordingCatalogService:
        def __init__(self):
            events.append(("construct", None))
            self._state = SimpleNamespace(
                server_name="Lake",
                account_name="ernieuuu",
                observed_at=datetime.now(timezone.utc),
                snapshot=SimpleNamespace(
                    can_claim_now=True,
                    claim_reset_minutes=152,
                    rolls_left=0,
                    rolls_reset_minutes=32,
                    daily_kakera_ready=True,
                    rt_available=True,
                    can_react_kakera_now=True,
                    reaction_power_percent=72,
                    oq_stored=1,
                    daily_reset_minutes=496,
                    ouro_refill_minutes=918,
                    vote_reset_minutes=350,
                    gold_key_reset_minutes=152,
                ),
            )

        def timer_state(self, server, account):
            events.append(("read", (server, account)))
            return self._state

    monkeypatch.setattr(main, "ConfigService", RecordingConfigService)
    monkeypatch.setattr(action_service_module, "CatalogService", RecordingCatalogService)
    runner = CliRunner()

    action_help = runner.invoke(main.app, ["action", "--help"])
    assert action_help.exit_code == 0
    assert "now" in action_help.stdout
    assert events == []

    now_help = runner.invoke(main.app, ["action", "now", "--help"])
    assert now_help.exit_code == 0
    assert "--server" in now_help.stdout
    assert "-s" in now_help.stdout
    assert "--account" in now_help.stdout
    assert "-a" in now_help.stdout
    assert events == []

    result = runner.invoke(
        main.app,
        ["action", "now", "-s", "Lake", "-a", "ernieuuu"],
    )

    assert result.exit_code == 0
    assert events == [
        ("resolve", ("Lake", "ernieuuu")),
        ("construct", None),
        ("read", ("Lake", "ernieuuu")),
    ]
    assert "ernieuuu - action readiness" in result.stdout
    assert "Actions were available when this $tu snapshot was imported." in result.stdout
    assert "Claim" in result.stdout
    assert "$dk" in result.stdout
    assert "Upcoming timers from this snapshot" in result.stdout
    assert "Roll reset" in result.stdout
    assert "32 min" in result.stdout


def test_account_overview_does_not_render_partial_kakeraloot_state_as_factual(
    monkeypatch,
) -> None:
    overview = SimpleNamespace(
        account_name="ernieuuu",
        kakera_balance=None,
        kakera_balance_source=None,
        personal_rare_multiplier=None,
        server_rare_multiplier=None,
        max_badge_count=0,
        badge_count=0,
        tower_level=None,
        completed_towers=None,
        next_tower_cost=None,
        tower_shortfall=None,
        kakeraloots_unlocked=True,
        missing_kakeraloot_prerequisites=(),
        has_kakeraloots=True,
        kakeraloot_status_note=None,
        quantity_level=5,
        quality_level=None,
        loot_usage_count=12,
        wishlist_count=None,
        wishlist_capacity=None,
        starwish_count=None,
        starwish_capacity=None,
        disable_slots_used=None,
        disable_slots_capacity=None,
        keyed_harem_count=0,
    )
    monkeypatch.setattr(account_overview_service_module.AccountOverviewService, "__init__", lambda self: None)
    monkeypatch.setattr(account_overview_service_module.AccountOverviewService, "overview", lambda self, *_: overview)

    result = CliRunner().invoke(
        main.app,
        ["account", "overview", "--server", "Lake", "--account", "ernieuuu"],
    )

    assert result.exit_code == 0
    assert "Not fully observed" in result.stdout
    assert "Quantity 5" not in result.stdout


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
            roulette_types=None,
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
            roulette_types=None,
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
    assert main._format_rollability(False, None) == "Not observed unavailable"


def test_catalog_top_renders_unknown_roulette_distinct_from_observed_empty(
    monkeypatch,
) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    entries = (
        CatalogTopSearchEntry(
            character=CatalogCharacter(
                id=1, name="Unknown Roulette", series="Series", gender=None, roulette=None
            ),
            claim_rank=1,
            like_rank=None,
            observed_at=observed_at,
            owned=True,
            keyed=False,
            unavailable=False,
            unavailable_reason=None,
            roulette_types=None,
        ),
        CatalogTopSearchEntry(
            character=CatalogCharacter(
                id=2, name="Observed Empty", series="Series", gender=None, roulette=None
            ),
            claim_rank=2,
            like_rank=None,
            observed_at=observed_at,
            owned=True,
            keyed=False,
            unavailable=False,
            unavailable_reason=None,
            roulette_types=(),
        ),
    )
    monkeypatch.setattr(
        main,
        "ConfigService",
        lambda: SimpleNamespace(
            resolve_context=lambda server, account: (server, account),
            owned_account_names=lambda _server: (),
        ),
    )
    monkeypatch.setattr(
        main,
        "TopSearchService",
        lambda: SimpleNamespace(search=lambda **_kwargs: entries),
    )
    monkeypatch.setattr(main.console, "width", 240)

    result = CliRunner().invoke(main.app, ["catalog", "top", "--limit", "2"])

    assert result.exit_code == 0
    assert "Unknown Roulette" in result.stdout
    assert "Unknown" in result.stdout
    assert "Observed Empty" in result.stdout
    assert main.format_mudae_roulette_types(None) == "Unknown"
    assert main.format_mudae_roulette_types(()) == "-"


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


def _capture_only_arguments(output_path: str) -> list[str]:
    return [
        "discord",
        "listen",
        "--token",
        "test-token",
        "--capture-only",
        "--capture-discord-events",
        output_path,
        "--capture-guild-id",
        "100",
        "--capture-channel-id",
        "200",
        "--mudae-user-id",
        "300",
        "--capture-user-id",
        "400",
        "--capture-user-id",
        "401",
    ]


def test_discord_capture_arguments_require_capture_only(tmp_path) -> None:
    result = CliRunner().invoke(
        main.app,
        [
            "discord",
            "listen",
            "--token",
            "test-token",
            "--capture-discord-events",
            str(tmp_path / "capture.jsonl"),
        ],
    )

    assert result.exit_code == 1
    assert "require --capture-only" in result.stdout


def test_discord_capture_text_option_requires_capture_only() -> None:
    result = CliRunner().invoke(
        main.app,
        ["discord", "listen", "--token", "test-token", "--capture-include-message-text"],
    )

    assert result.exit_code == 1
    assert "require --capture-only" in result.stdout


def test_discord_capture_guild_option_requires_capture_only(monkeypatch) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("capture-only validation must precede service construction")

    monkeypatch.setattr(main, "DiscordEventCaptureService", unexpected)
    result = CliRunner().invoke(
        main.app,
        ["discord", "listen", "--token", "test-token", "--capture-guild-id", "100"],
    )

    assert result.exit_code == 1
    assert "require --capture-only" in result.stdout


def test_discord_capture_channel_option_requires_capture_only(monkeypatch) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("capture-only validation must precede service construction")

    monkeypatch.setattr(main, "DiscordEventCaptureService", unexpected)
    result = CliRunner().invoke(
        main.app,
        ["discord", "listen", "--token", "test-token", "--capture-channel-id", "200"],
    )

    assert result.exit_code == 1
    assert "require --capture-only" in result.stdout


def test_discord_capture_user_option_requires_capture_only(monkeypatch) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("capture-only validation must precede service construction")

    monkeypatch.setattr(main, "DiscordEventCaptureService", unexpected)
    result = CliRunner().invoke(
        main.app,
        ["discord", "listen", "--token", "test-token", "--capture-user-id", "400"],
    )

    assert result.exit_code == 1
    assert "require --capture-only" in result.stdout


@pytest.mark.parametrize(
    ("output_path", "expected"),
    [
        ("relative.jsonl", "must be an absolute path"),
        (str(Path(main.__file__).resolve().parents[3] / "capture.jsonl"), "outside the repository"),
    ],
)
def test_discord_capture_only_rejects_unsafe_output_paths(output_path, expected) -> None:
    result = CliRunner().invoke(main.app, _capture_only_arguments(output_path))

    assert result.exit_code == 1
    assert expected in result.stdout


def test_discord_capture_only_rejects_existing_directory_without_construction(
    monkeypatch, tmp_path
) -> None:
    directory = tmp_path / "capture-directory"
    directory.mkdir()

    def unexpected(*_args, **_kwargs):
        raise AssertionError("invalid capture paths must not construct MOA services")

    monkeypatch.setattr(main, "DiscordEventCaptureService", unexpected)
    monkeypatch.setattr(main, "CatalogRepository", unexpected)
    monkeypatch.setattr(main, "DiscordMessageRepository", unexpected)
    monkeypatch.setattr(main, "AutomaticImportService", unexpected)
    monkeypatch.setattr(main, "DiscordListenerService", unexpected)

    result = CliRunner().invoke(main.app, _capture_only_arguments(str(directory)))

    assert result.exit_code == 1
    assert "must name a file, not a directory" in result.stdout
    assert "test-token" not in result.stdout


def test_discord_capture_only_rejects_nonexistent_parent_without_construction(
    monkeypatch, tmp_path
) -> None:
    output_path = tmp_path / "missing-parent" / "capture.jsonl"

    def unexpected(*_args, **_kwargs):
        raise AssertionError("invalid capture paths must not construct MOA services")

    monkeypatch.setattr(main, "DiscordEventCaptureService", unexpected)
    monkeypatch.setattr(main, "CatalogRepository", unexpected)
    monkeypatch.setattr(main, "DiscordMessageRepository", unexpected)
    monkeypatch.setattr(main, "AutomaticImportService", unexpected)
    monkeypatch.setattr(main, "DiscordListenerService", unexpected)

    result = CliRunner().invoke(main.app, _capture_only_arguments(str(output_path)))

    assert result.exit_code == 1
    assert "parent directory" in result.stdout
    assert not output_path.exists()
    assert not output_path.parent.exists()


def test_discord_capture_only_requires_output_path(tmp_path) -> None:
    arguments = _capture_only_arguments(str(tmp_path / "capture.jsonl"))
    index = arguments.index("--capture-discord-events")
    del arguments[index : index + 2]

    result = CliRunner().invoke(main.app, arguments)

    assert result.exit_code == 1
    assert "--capture-discord-events" in result.stdout


def test_discord_capture_only_requires_guild_id(tmp_path) -> None:
    arguments = _capture_only_arguments(str(tmp_path / "capture.jsonl"))
    index = arguments.index("--capture-guild-id")
    del arguments[index : index + 2]

    result = CliRunner().invoke(main.app, arguments)

    assert result.exit_code == 1
    assert "--capture-guild-id" in result.stdout


def test_discord_capture_only_requires_channel_id(tmp_path) -> None:
    arguments = _capture_only_arguments(str(tmp_path / "capture.jsonl"))
    index = arguments.index("--capture-channel-id")
    del arguments[index : index + 2]

    result = CliRunner().invoke(main.app, arguments)

    assert result.exit_code == 1
    assert "--capture-channel-id" in result.stdout


def test_discord_capture_only_requires_mudae_id(tmp_path) -> None:
    arguments = _capture_only_arguments(str(tmp_path / "capture.jsonl"))
    index = arguments.index("--mudae-user-id")
    del arguments[index : index + 2]

    result = CliRunner().invoke(main.app, arguments)

    assert result.exit_code == 1
    assert "--mudae-user-id" in result.stdout


def test_discord_capture_only_requires_capture_user(tmp_path) -> None:
    arguments = _capture_only_arguments(str(tmp_path / "capture.jsonl"))
    while "--capture-user-id" in arguments:
        index = arguments.index("--capture-user-id")
        del arguments[index : index + 2]

    result = CliRunner().invoke(main.app, arguments)

    assert result.exit_code == 1
    assert "--capture-user-id" in result.stdout


def test_discord_capture_only_refuses_existing_output(tmp_path) -> None:

    existing_path = tmp_path / "existing.jsonl"
    existing_path.write_text("existing diagnostic data\n", encoding="utf-8")
    existing = CliRunner().invoke(main.app, _capture_only_arguments(str(existing_path)))

    assert existing.exit_code == 1
    assert "refusing to overwrite" in existing.stdout


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--capture-guild-id", "0"),
        ("--capture-channel-id", "-1"),
        ("--mudae-user-id", "not-an-id"),
        ("--capture-user-id", "0"),
    ],
)
def test_discord_capture_only_rejects_nonpositive_or_nonnumeric_ids(tmp_path, flag, value) -> None:
    arguments = _capture_only_arguments(str(tmp_path / "capture.jsonl"))
    index = arguments.index(flag)
    arguments[index + 1] = value

    result = CliRunner().invoke(main.app, arguments)

    assert result.exit_code == 1
    assert "positive numeric Discord IDs" in result.stdout


def test_discord_capture_only_bypasses_database_and_import_construction(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeCaptureService:
        def __init__(self, config):
            captured["config"] = config

        def run(self, token):
            captured["token"] = token

    def unexpected(*_args, **_kwargs):
        raise AssertionError("capture-only must not construct normal listener dependencies")

    monkeypatch.setattr(main, "DiscordEventCaptureService", FakeCaptureService)
    monkeypatch.setattr(main, "CatalogRepository", unexpected)
    monkeypatch.setattr(main, "DiscordMessageRepository", unexpected)
    monkeypatch.setattr(main, "AutomaticImportService", unexpected)
    monkeypatch.setattr(main, "DiscordListenerService", unexpected)

    output_path = tmp_path / "capture.jsonl"
    result = CliRunner().invoke(main.app, _capture_only_arguments(str(output_path)))

    assert result.exit_code == 0
    assert captured["token"] == "test-token"
    config = captured["config"]
    assert config.output_path == output_path.resolve()
    assert config.guild_id == "100"
    assert config.channel_id == "200"
    assert config.mudae_user_id == "300"
    assert config.user_ids == frozenset({"400", "401"})
    assert config.enabled
    assert not config.include_message_text
    assert not output_path.exists()


def test_discord_capture_only_passes_explicit_text_capture_opt_in(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeCaptureService:
        def __init__(self, config):
            captured["config"] = config

        def run(self, _token):
            return None

    monkeypatch.setattr(main, "DiscordEventCaptureService", FakeCaptureService)
    result = CliRunner().invoke(
        main.app,
        [*_capture_only_arguments(str(tmp_path / "capture.jsonl")), "--capture-include-message-text"],
    )

    assert result.exit_code == 0
    assert captured["config"].include_message_text


def test_discord_capture_help_warns_text_output_is_sensitive() -> None:
    result = CliRunner().invoke(main.app, ["discord", "listen", "--help"])

    assert result.exit_code == 0
    for warning in ("Optional", "best-effort", "sensitive diagnostic", "manually inspect", "never commit"):
        assert warning in result.stdout


def test_discord_listener_wires_shared_database_and_roll_coordinator(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "moa.db"
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)
    captured: dict[str, object] = {}
    catalog_repositories: list[CatalogRepository] = []
    discord_repositories: list[DiscordMessageRepository] = []
    importers: list[AutomaticImportService] = []
    listeners: list[object] = []
    kakera_coordinators: list[KakeraStateProjectionCoordinator] = []
    kakeraloot_coordinators: list[KakeralootStateProjectionCoordinator] = []
    timer_coordinators: list[TimerProjectionCoordinator] = []
    tower_coordinators: list[TowerStateProjectionCoordinator] = []
    sphere_coordinators: list[SphereResultProjectionCoordinator] = []
    player_bonus_coordinators: list[PlayerBonusProjectionCoordinator] = []
    disablelist_coordinators: list[DisableListProjectionCoordinator] = []
    wishlist_coordinators: list[WishlistProjectionCoordinator] = []
    antidisable_coordinators: list[AntidisablePageProjectionCoordinator] = []

    class RecordingCatalogRepository(CatalogRepository):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            catalog_repositories.append(self)

    class RecordingDiscordMessageRepository(DiscordMessageRepository):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            discord_repositories.append(self)

    class RecordingAutomaticImportService(AutomaticImportService):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            importers.append(self)

    class RecordingKakeraStateProjectionCoordinator(KakeraStateProjectionCoordinator):
        def __init__(self, *repositories):
            kakera_coordinators.append(self)
            super().__init__(*repositories)

    class RecordingKakeralootStateProjectionCoordinator(
        KakeralootStateProjectionCoordinator
    ):
        def __init__(self, *repositories):
            kakeraloot_coordinators.append(self)
            super().__init__(*repositories)

    class RecordingTimerProjectionCoordinator(TimerProjectionCoordinator):
        def __init__(self, *repositories):
            timer_coordinators.append(self)
            super().__init__(*repositories)

    class RecordingTowerStateProjectionCoordinator(TowerStateProjectionCoordinator):
        def __init__(self, *repositories):
            tower_coordinators.append(self)
            super().__init__(*repositories)

    class RecordingSphereResultProjectionCoordinator(SphereResultProjectionCoordinator):
        def __init__(self, *repositories):
            sphere_coordinators.append(self)
            super().__init__(*repositories)

    class RecordingPlayerBonusProjectionCoordinator(PlayerBonusProjectionCoordinator):
        def __init__(self, *repositories):
            player_bonus_coordinators.append(self)
            super().__init__(*repositories)

    class RecordingDisableListProjectionCoordinator(DisableListProjectionCoordinator):
        def __init__(self, *repositories):
            disablelist_coordinators.append(self)
            super().__init__(*repositories)

    class RecordingWishlistProjectionCoordinator(WishlistProjectionCoordinator):
        def __init__(self, *repositories):
            wishlist_coordinators.append(self)
            super().__init__(*repositories)

    class RecordingAntidisablePageProjectionCoordinator(
        AntidisablePageProjectionCoordinator
    ):
        def __init__(self, *repositories):
            antidisable_coordinators.append(self)
            super().__init__(*repositories)

    class FakeListener:
        def __init__(self, **kwargs):
            listeners.append(self)
            captured["listener"] = self
            captured.update(kwargs)

        def run(self, token, mudae_user_id):
            captured["token"] = token
            captured["mudae_user_id"] = mudae_user_id

    monkeypatch.setattr(main, "DiscordListenerService", FakeListener)
    monkeypatch.setattr(main, "CatalogRepository", RecordingCatalogRepository)
    monkeypatch.setattr(main, "DiscordMessageRepository", RecordingDiscordMessageRepository)
    monkeypatch.setattr(main, "AutomaticImportService", RecordingAutomaticImportService)
    monkeypatch.setattr(
        main,
        "KakeraStateProjectionCoordinator",
        RecordingKakeraStateProjectionCoordinator,
    )
    monkeypatch.setattr(
        main,
        "KakeralootStateProjectionCoordinator",
        RecordingKakeralootStateProjectionCoordinator,
    )
    monkeypatch.setattr(main, "TimerProjectionCoordinator", RecordingTimerProjectionCoordinator)
    monkeypatch.setattr(
        main,
        "TowerStateProjectionCoordinator",
        RecordingTowerStateProjectionCoordinator,
    )
    monkeypatch.setattr(
        main,
        "SphereResultProjectionCoordinator",
        RecordingSphereResultProjectionCoordinator,
    )
    monkeypatch.setattr(
        main,
        "PlayerBonusProjectionCoordinator",
        RecordingPlayerBonusProjectionCoordinator,
    )
    monkeypatch.setattr(
        main,
        "DisableListProjectionCoordinator",
        RecordingDisableListProjectionCoordinator,
    )
    monkeypatch.setattr(
        main,
        "WishlistProjectionCoordinator",
        RecordingWishlistProjectionCoordinator,
    )
    monkeypatch.setattr(
        main,
        "AntidisablePageProjectionCoordinator",
        RecordingAntidisablePageProjectionCoordinator,
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
    kakeraloot_coordinator = importer._kakeraloot_state_projection_coordinator
    tower_coordinator = importer._tower_state_projection_coordinator
    sphere_coordinator = importer._sphere_result_projection_coordinator
    player_bonus_coordinator = importer._player_bonus_projection_coordinator
    disablelist_coordinator = importer._disablelist_projection_coordinator
    wishlist_coordinator = importer._wishlist_projection_coordinator
    antidisable_coordinator = importer._antidisable_page_projection_coordinator
    assert isinstance(catalog_service, CatalogService)
    assert isinstance(catalog_service._repository, CatalogRepository)
    assert isinstance(discord_repository, DiscordMessageRepository)
    assert isinstance(importer, AutomaticImportService)
    assert catalog_repositories == [catalog_service._repository]
    assert discord_repositories == [discord_repository]
    assert importers == [importer]
    assert listeners == [captured["listener"]]
    assert isinstance(coordinator, RollProjectionCoordinator)
    assert isinstance(profile_coordinator, ProfileProjectionCoordinator)
    assert isinstance(claim_coordinator, ClaimProjectionCoordinator)
    assert isinstance(settings_coordinator, SettingsProjectionCoordinator)
    assert isinstance(infokl_coordinator, InfoklProjectionCoordinator)
    assert isinstance(timer_coordinator, TimerProjectionCoordinator)
    assert isinstance(kakera_coordinator, KakeraStateProjectionCoordinator)
    assert isinstance(kakeraloot_coordinator, KakeralootStateProjectionCoordinator)
    assert isinstance(tower_coordinator, TowerStateProjectionCoordinator)
    assert isinstance(sphere_coordinator, SphereResultProjectionCoordinator)
    assert isinstance(player_bonus_coordinator, PlayerBonusProjectionCoordinator)
    assert isinstance(disablelist_coordinator, DisableListProjectionCoordinator)
    assert isinstance(wishlist_coordinator, WishlistProjectionCoordinator)
    assert isinstance(antidisable_coordinator, AntidisablePageProjectionCoordinator)
    assert kakera_coordinators == [kakera_coordinator]
    assert kakeraloot_coordinators == [kakeraloot_coordinator]
    assert timer_coordinators == [timer_coordinator]
    assert tower_coordinators == [tower_coordinator]
    assert sphere_coordinators == [sphere_coordinator]
    assert player_bonus_coordinators == [player_bonus_coordinator]
    assert disablelist_coordinators == [disablelist_coordinator]
    assert wishlist_coordinators == [wishlist_coordinator]
    assert antidisable_coordinators == [antidisable_coordinator]
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
    assert kakeraloot_coordinator._catalog is catalog_service._repository
    assert kakeraloot_coordinator._discord is discord_repository
    assert kakeraloot_coordinator._database_path == database_path
    assert tower_coordinator._catalog is catalog_service._repository
    assert tower_coordinator._discord is discord_repository
    assert tower_coordinator._database_path == database_path
    assert sphere_coordinator._catalog is catalog_service._repository
    assert sphere_coordinator._discord is discord_repository
    assert sphere_coordinator._database_path == database_path
    assert importer._sphere_result_projection_coordinator is sphere_coordinator
    assert importer._player_bonus_projection_coordinator is player_bonus_coordinator
    assert importer._wishlist_projection_coordinator is wishlist_coordinator
    assert player_bonus_coordinator._catalog is catalog_service._repository
    assert player_bonus_coordinator._discord is discord_repository
    assert player_bonus_coordinator._database_path == database_path
    assert disablelist_coordinator._catalog is catalog_service._repository
    assert disablelist_coordinator._discord is discord_repository
    assert disablelist_coordinator._database_path == database_path
    assert antidisable_coordinator._catalog is catalog_service._repository
    assert antidisable_coordinator._discord is discord_repository
    assert antidisable_coordinator._database_path == database_path
    assert importer._antidisable_page_projection_coordinator is antidisable_coordinator
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


def test_import_auto_keeps_direct_kakeraloot_import_without_durable_coordinator(
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
                kind="kakeraloot_state",
                imported_count=1,
                message="Imported Kakeraloot state.",
            )

    monkeypatch.setattr(main, "AutomaticImportService", RecordingImporter)
    monkeypatch.setattr(
        main,
        "_read_message_source",
        lambda path, clipboard: "Kakera Loots: 0",
    )

    result = CliRunner().invoke(
        main.app,
        ["import", "auto", "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert result.exit_code == 0
    assert constructed == [((), {})]
    assert calls == [
        (
            ("Kakera Loots: 0", "clipboard", "Lake", "ernieuuu"),
            {"harem_scan_id": None},
        )
    ]


def test_import_auto_keeps_direct_sphere_import_without_durable_coordinator(
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
                kind="sphere_result",
                imported_count=1,
                message="Imported +158 spheres. Stock: 3,655.",
            )

    monkeypatch.setattr(main, "AutomaticImportService", RecordingImporter)
    monkeypatch.setattr(
        main,
        "_read_message_source",
        lambda path, clipboard: ":sp: +158\\n:spG: +43 (Stock: 3,655)",
    )

    result = CliRunner().invoke(
        main.app,
        ["import", "auto", "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert result.exit_code == 0
    assert constructed == [((), {})]
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (
        ":sp: +158\\n:spG: +43 (Stock: 3,655)",
        "clipboard",
        "Lake",
        "ernieuuu",
    )
    assert kwargs == {"harem_scan_id": None}


def test_import_auto_keeps_direct_player_bonus_import_without_durable_coordinator(
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
                kind="bonus",
                imported_count=10,
                message="Imported player bonuses.",
            )

    monkeypatch.setattr(main, "AutomaticImportService", RecordingImporter)
    monkeypatch.setattr(
        main,
        "_read_message_source",
        lambda path, clipboard: "Player Bonuses\nbonus response",
    )

    result = CliRunner().invoke(
        main.app,
        ["import", "auto", "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert result.exit_code == 0
    assert constructed == [((), {})]
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (
        "Player Bonuses\nbonus response",
        "clipboard",
        "Lake",
        "ernieuuu",
    )
    assert kwargs == {"harem_scan_id": None}


def test_import_auto_keeps_direct_wishlist_import_without_durable_coordinator(
    monkeypatch,
) -> None:
    constructed: list[tuple[tuple[object, ...], dict[str, object]]] = []
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    coordinators: list[object] = []

    class RecordingImporter:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))

        def import_message(self, *args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(
                kind="wishlist",
                imported_count=3,
                message="Imported wishlist.",
            )

    class RecordingWishlistProjectionCoordinator:
        def __init__(self, *args, **kwargs):
            coordinators.append((args, kwargs))

    monkeypatch.setattr(main, "AutomaticImportService", RecordingImporter)
    monkeypatch.setattr(main, "WishlistProjectionCoordinator", RecordingWishlistProjectionCoordinator)
    monkeypatch.setattr(
        main,
        "_read_message_source",
        lambda path, clipboard: "wishlist response",
    )

    result = CliRunner().invoke(
        main.app,
        ["import", "auto", "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert result.exit_code == 0
    assert constructed == [((), {})]
    assert coordinators == []
    assert calls == [
        (
            ("wishlist response", "clipboard", "Lake", "ernieuuu"),
            {"harem_scan_id": None},
        )
    ]


def test_import_auto_keeps_direct_disablelist_import_without_durable_coordinator(
    monkeypatch,
) -> None:
    constructed: list[tuple[tuple[object, ...], dict[str, object]]] = []
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    coordinators: list[object] = []

    class RecordingImporter:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))

        def import_message(self, *args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(
                kind="disablelist",
                imported_count=4,
                message="Imported disablelist.",
            )

    class RecordingDisableListProjectionCoordinator:
        def __init__(self, *args, **kwargs):
            coordinators.append((args, kwargs))

    monkeypatch.setattr(main, "AutomaticImportService", RecordingImporter)
    monkeypatch.setattr(
        main,
        "DisableListProjectionCoordinator",
        RecordingDisableListProjectionCoordinator,
    )
    monkeypatch.setattr(
        main,
        "_read_message_source",
        lambda path, clipboard: "disablelist response",
    )

    result = CliRunner().invoke(
        main.app,
        ["import", "auto", "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert result.exit_code == 0
    assert constructed == [((), {})]
    assert coordinators == []
    assert calls == [
        (
            ("disablelist response", "clipboard", "Lake", "ernieuuu"),
            {"harem_scan_id": None},
        )
    ]


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


def test_catalog_relocate_database_requires_explicit_source() -> None:
    result = CliRunner().invoke(main.app, ["catalog", "relocate-database", "--apply"])

    assert result.exit_code == 2
    assert "SOURCE" in result.stderr


def test_catalog_relocate_database_warns_and_requires_apply(monkeypatch, tmp_path) -> None:
    source = tmp_path / "legacy" / "moa.db"
    target = tmp_path / "user-data" / "moa.db"
    monkeypatch.setattr(main, "default_database_path", lambda: target)

    result = CliRunner().invoke(main.app, ["catalog", "relocate-database", str(source)])

    assert result.exit_code == 0
    unwrapped_output = result.stdout.replace("\n", "")
    assert str(source.resolve()) in unwrapped_output
    assert str(target.resolve()) in unwrapped_output
    assert "listener must be stopped" in result.stdout
    assert "old MOA checkouts" in result.stdout
    assert "No changes made" in result.stdout
    assert not target.exists()


def test_catalog_relocate_database_applies_without_traceback(monkeypatch, tmp_path) -> None:
    from moa.database import legacy_database_relocation, sqlite

    source = tmp_path / "legacy" / "moa.db"
    target = tmp_path / "user-data" / "moa.db"
    CatalogRepository(source)
    connection = sqlite.connect(source)
    connection.execute(
        "INSERT INTO import_events (kind, source, observed_at, raw_message) "
        "VALUES ('command_observation', 'cli-test', '2026-08-12T00:00:00+00:00', 'copied')"
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(main, "default_database_path", lambda: target)
    monkeypatch.setattr(
        legacy_database_relocation,
        "verified_legacy_database_path",
        lambda: None,
    )

    result = CliRunner().invoke(
        main.app,
        ["catalog", "relocate-database", str(source), "--apply"],
    )

    assert result.exit_code == 0
    assert "Database relocated" in result.stdout
    assert "Legacy source archived" in result.stdout
    assert "Traceback" not in result.stdout
    assert target.is_file()
    assert not source.exists()


def test_catalog_relocate_database_reports_existing_target_without_traceback(
    monkeypatch, tmp_path
) -> None:
    from moa.database import legacy_database_relocation

    source = tmp_path / "legacy" / "moa.db"
    source.parent.mkdir()
    source.write_bytes(b"source")
    target = tmp_path / "user-data" / "moa.db"
    target.parent.mkdir()
    target.write_bytes(b"target")
    monkeypatch.setattr(main, "default_database_path", lambda: target)
    monkeypatch.setattr(
        legacy_database_relocation,
        "verified_legacy_database_path",
        lambda: None,
    )

    result = CliRunner().invoke(
        main.app,
        ["catalog", "relocate-database", str(source), "--apply"],
    )

    assert result.exit_code == 1
    assert "will not be overwritten" in result.stdout
    assert "Traceback" not in result.stdout
    assert source.read_bytes() == b"source"
    assert target.read_bytes() == b"target"


def test_catalog_repair_uses_same_effective_default_for_backup(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "user-data" / "moa.db"
    database_path.parent.mkdir()
    database_path.write_bytes(b"catalog")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    class RecordingCatalogService:
        def inspect_bugged_imports(self):
            return 1, 0

        def repair_bugged_imports(self):
            return 1, 0

    monkeypatch.setattr(main, "CatalogService", RecordingCatalogService)

    result = CliRunner().invoke(main.app, ["catalog", "repair-bugged-data", "--apply"])

    assert result.exit_code == 0
    assert database_path.read_bytes() == b"catalog"
    assert len(list(database_path.parent.glob("moa.db.bak-*"))) == 1


def test_catalog_delete_import_reports_durable_source_refusal(monkeypatch) -> None:
    class BlockingCatalogService:
        def delete_import_event(self, import_event_id):
            raise main.ImportEventDeletionBlockedError(
                f"Import event {import_event_id} belongs to durable source state"
            )

    monkeypatch.setattr(main, "CatalogService", BlockingCatalogService)

    result = CliRunner().invoke(main.app, ["catalog", "delete-import", "42"])

    assert result.exit_code == 1
    assert "Deletion blocked" in result.stdout
    assert "durable/replayable source state" in result.stdout
    assert "FOREIGN KEY" not in result.stdout


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


def test_config_profile_add_and_show_selected_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MOA_CONFIG_PATH", str(tmp_path / "config.json"))
    runner = CliRunner()

    added = runner.invoke(main.app, ["config", "profile", "add", "seasonal"])
    shown = runner.invoke(main.app, ["config", "show", "--profile", "seasonal"])

    assert added.exit_code == 0
    assert "Created MOA profile `seasonal`." in added.stdout
    assert shown.exit_code == 0
    assert "MOA config" in shown.stdout
    assert "No server/account identities configured yet." in shown.stdout


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


def test_badge_cli_registration_rendering_and_validation() -> None:
    runner = CliRunner()

    help_result = runner.invoke(main.app, ["badge", "--help"])
    assert help_result.exit_code == 0
    assert "list" in help_result.stdout
    assert "cost" in help_result.stdout

    list_result = runner.invoke(main.app, ["badge", "list"])
    assert list_result.exit_code == 0
    assert "Kakera Badges" in list_result.stdout
    assert "Default base value" in list_result.stdout
    assert "Bronze" in list_result.stdout
    assert "1,000" in list_result.stdout

    cost_result = runner.invoke(
        main.app,
        [
            "badge",
            "cost",
            "GOLD",
            "4",
            "--base-value",
            "1000",
            "--ruby-iv",
        ],
    )
    assert cost_result.exit_code == 0
    assert "GOLD 4" in cost_result.stdout
    assert "3,000 Kakera" in cost_result.stdout
    assert "with Ruby IV" in cost_result.stdout

    invalid_result = runner.invoke(
        main.app,
        ["badge", "cost", "NOT_A_BADGE", "1", "--base-value", "1000"],
    )
    assert invalid_result.exit_code == 1
    assert "Unknown badge: NOT_A_BADGE" in invalid_result.stdout
