import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from rich.console import Console
from typer.main import get_command
from typer.testing import CliRunner

import moa.cli.import_workflow_commands as import_workflow_commands_module
import moa.cli.catalog_delete_import_commands as catalog_delete_import_commands_module
import moa.cli.catalog_operational_commands as catalog_operational_commands_module
import moa.cli.catalog_relocate_database_commands as catalog_relocate_database_commands_module
import moa.cli.catalog_repair_bugged_data_commands as catalog_repair_bugged_data_commands_module
import moa.cli.catalog_reset_commands as catalog_reset_commands_module
import moa.cli.catalog_search_commands as catalog_search_commands_module
import moa.cli.catalog_snapshot_commands as catalog_snapshot_commands_module
import moa.cli.discord_commands as discord_commands_module
import moa.parser.message_router as message_router_module
import moa.parser.mudae as mudae_parser_module
import moa.services.account_comparison_service as account_comparison_service_module
import moa.services.account_overview_service as account_overview_service_module
import moa.services.action_service as action_service_module
import moa.services.catalog_service as catalog_service_module
import moa.services.data_health_service as data_health_service_module
import moa.services.kakeraloot_budget_service as kakeraloot_budget_service_module
import moa.services.keyfarm_service as keyfarm_service_module
import moa.services.loot_service as loot_service_module
import moa.services.progress_service as progress_service_module
import moa.services.retention_eligibility_service as retention_eligibility_service_module
import moa.services.retention_expiry_service as retention_expiry_service_module
import moa.services.roll_analysis_service as roll_analysis_service_module
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


def test_data_health_cli_characterizes_schema_laziness_and_late_bound_path(
    tmp_path, monkeypatch
) -> None:
    root_command = get_command(main.app)
    data_health_command = root_command.commands["catalog"].commands["data-health"]
    assert set(data_health_command.commands) == {
        "orphans",
        "impossible-identities",
        "duplicates",
        "projection-gaps",
        "retention",
    }
    assert all(
        not data_health_command.commands[name].params
        for name in ("orphans", "impossible-identities", "duplicates", "projection-gaps")
    )
    retention_params = data_health_command.commands["retention"].params
    assert len(retention_params) == 1
    apply_option = retention_params[0]
    assert apply_option.name == "apply"
    assert tuple(apply_option.opts) == ("--apply",)
    assert apply_option.default is False
    assert not apply_option.required

    events: list[str] = []

    class UnexpectedDatabasePath:
        def __fspath__(self) -> str:
            events.append("database-path")
            raise AssertionError("Data Health help must not resolve a database path")

    def unexpected_constructor(name):
        def constructor(self, *args, **kwargs):
            events.append(name)
            raise AssertionError(f"Data Health help must not construct {name}")

        return constructor

    runner = CliRunner()
    with monkeypatch.context() as help_monkeypatch:
        help_monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", UnexpectedDatabasePath())
        help_monkeypatch.setattr(
            data_health_service_module.DataHealthService,
            "__init__",
            unexpected_constructor("data-health"),
        )
        help_monkeypatch.setattr(
            retention_eligibility_service_module.RetentionEligibilityService,
            "__init__",
            unexpected_constructor("retention-eligibility"),
        )
        help_monkeypatch.setattr(
            retention_expiry_service_module.RetentionExpiryService,
            "__init__",
            unexpected_constructor("retention-expiry"),
        )
        for arguments in (
            ["catalog", "data-health", "--help"],
            ["catalog", "data-health", "orphans", "--help"],
            ["catalog", "data-health", "retention", "--help"],
        ):
            result = runner.invoke(main.app, arguments)
            assert result.exit_code == 0
    assert events == []

    original_path = tmp_path / "original" / "catalog.db"
    original_path.parent.mkdir()
    original_path.write_text("not a catalog database", encoding="utf-8")
    patched_path = tmp_path / "patched" / "catalog.db"
    CatalogRepository(patched_path)
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", original_path)
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", patched_path)

    result = runner.invoke(main.app, ["catalog", "data-health", "orphans"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "No audited orphan findings in this read-only scan."


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


def test_detect_cli_registration_schema_and_help_are_lazy(monkeypatch) -> None:
    root_command = get_command(main.app)
    detect_command = root_command.commands["detect"]

    assert [parameter.name for parameter in detect_command.params] == ["path", "clipboard"]
    path_parameter, clipboard_parameter = detect_command.params
    assert path_parameter.required is False
    assert path_parameter.default is None
    assert path_parameter.type.name == "path"
    assert tuple(clipboard_parameter.opts) == ("--clipboard", "-c")
    assert clipboard_parameter.default is False
    assert clipboard_parameter.required is False

    events: list[str] = []

    def unexpected_source(path, clipboard):
        events.append("source")
        raise AssertionError("Detect help must not read a source")

    def unexpected_router_init(self, *args, **kwargs):
        events.append("router")
        raise AssertionError("Detect help must not construct a router")

    monkeypatch.setattr(main, "_read_message_source", unexpected_source)
    monkeypatch.setattr(message_router_module.MudaeMessageRouter, "__init__", unexpected_router_init)

    result = CliRunner().invoke(main.app, ["detect", "--help"])

    assert result.exit_code == 0
    assert "Text file containing one copied Mudae response." in result.stdout
    assert "--clipboard" in result.stdout
    assert "-c" in result.stdout
    assert events == []


def test_version_cli_help_is_lazy(monkeypatch) -> None:
    events: list[object] = []

    def unexpected_print(*args, **kwargs):
        events.append((args, kwargs))
        raise AssertionError("Version help must not render the version output")

    monkeypatch.setattr(main.console, "print", unexpected_print)

    result = CliRunner().invoke(main.app, ["version", "--help"])

    assert result.exit_code == 0
    assert "Usage: root version" in result.stdout
    assert events == []


def test_version_cli_renders_exact_output_and_exits_zero() -> None:
    result = CliRunner().invoke(main.app, ["version"])

    assert result.exit_code == 0
    assert result.stdout == "MOA v0.1.0\n"


def test_detect_cli_source_routing_late_bound_router_and_rendering(monkeypatch, tmp_path) -> None:
    events: list[object] = []
    original_source = main._read_message_source

    def patched_source(path, clipboard):
        events.append(("source", path, clipboard))
        return "unknown message" if clipboard else "known message"

    def recording_router_init(self, *args, **kwargs):
        events.append("router")

    def recording_detect(self, text):
        events.append(("detect", text))
        if text == "known message":
            return SimpleNamespace(kind="timers", reason="timer reason")
        return SimpleNamespace(kind="unknown", reason="unknown reason")

    # main.app already exists before this post-construction patch.
    monkeypatch.setattr(main, "_read_message_source", patched_source)
    monkeypatch.setattr(message_router_module.MudaeMessageRouter, "__init__", recording_router_init)
    monkeypatch.setattr(message_router_module.MudaeMessageRouter, "detect", recording_detect)

    path = tmp_path / "response.txt"
    file_result = CliRunner().invoke(main.app, ["detect", str(path)])
    clipboard_result = CliRunner().invoke(main.app, ["detect", "-c"])

    assert file_result.exit_code == 0
    assert clipboard_result.exit_code == 0
    assert events == [
        "router",
        ("source", path, False),
        ("detect", "known message"),
        "router",
        ("source", None, True),
        ("detect", "unknown message"),
    ]
    assert "Detected: timers" in file_result.stdout
    assert "timer reason" in file_result.stdout
    assert "Detected: unknown" in clipboard_result.stdout
    assert "unknown reason" in clipboard_result.stdout

    events.clear()
    monkeypatch.setattr(main, "_read_message_source", original_source)
    conflict = CliRunner().invoke(main.app, ["detect", str(path), "--clipboard"])
    missing = CliRunner().invoke(main.app, ["detect"])

    assert conflict.exit_code == 1
    assert "either a file path or --clipboard" in conflict.stdout
    assert missing.exit_code == 1
    assert "Provide a text-file path or use --clipboard" in missing.stdout
    assert events == ["router", "router"]


def test_analyze_roll_cli_registration_schema_and_help_are_lazy(monkeypatch) -> None:
    root_command = get_command(main.app)
    analyze_roll_command = root_command.commands["analyze-roll"]

    assert [parameter.name for parameter in analyze_roll_command.params] == [
        "server",
        "account",
        "path",
        "clipboard",
    ]
    server_parameter, account_parameter, path_parameter, clipboard_parameter = (
        analyze_roll_command.params
    )
    for parameter, options in (
        (server_parameter, ("--server", "-s")),
        (account_parameter, ("--account", "-a")),
    ):
        assert parameter.type.name == "str"
        assert parameter.default is None
        assert parameter.required is False
        assert tuple(parameter.opts) == options
    assert path_parameter.type.name == "path"
    assert path_parameter.default is None
    assert path_parameter.required is False
    assert tuple(path_parameter.opts) == ("path",)
    assert clipboard_parameter.type.name == "boolean"
    assert clipboard_parameter.default is False
    assert clipboard_parameter.required is False
    assert tuple(clipboard_parameter.opts) == ("--clipboard", "-c")
    assert clipboard_parameter.is_flag is True

    events: list[str] = []

    def unexpected_resolver(server, account):
        events.append("resolver")
        raise AssertionError("Analyze-roll help must not resolve account context")

    def unexpected_source(path, clipboard):
        events.append("source")
        raise AssertionError("Analyze-roll help must not read a source")

    def unexpected_parser_init(self, *args, **kwargs):
        events.append("parser")
        raise AssertionError("Analyze-roll help must not construct a parser")

    def unexpected_parse(self, text):
        events.append("parse")
        raise AssertionError("Analyze-roll help must not parse a roll")

    def unexpected_service_init(self, *args, **kwargs):
        events.append("service")
        raise AssertionError("Analyze-roll help must not construct an analysis service")

    def unexpected_analyze(self, *args, **kwargs):
        events.append("analyze")
        raise AssertionError("Analyze-roll help must not analyze a roll")

    monkeypatch.setattr(main, "_resolve_account_context", unexpected_resolver)
    monkeypatch.setattr(main, "_read_message_source", unexpected_source)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "__init__", unexpected_parser_init)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "parse_roll", unexpected_parse)
    monkeypatch.setattr(
        roll_analysis_service_module.RollAnalysisService,
        "__init__",
        unexpected_service_init,
    )
    monkeypatch.setattr(
        roll_analysis_service_module.RollAnalysisService,
        "analyze",
        unexpected_analyze,
    )

    result = CliRunner().invoke(main.app, ["analyze-roll", "--help"])

    assert result.exit_code == 0
    assert "analyze-roll" in result.stdout
    assert "Text file containing one copied Mudae roll card." in result.stdout
    assert "--server" in result.stdout
    assert "-s" in result.stdout
    assert "--account" in result.stdout
    assert "-a" in result.stdout
    assert "--clipboard" in result.stdout
    assert "-c" in result.stdout
    assert events == []


def test_analyze_roll_cli_success_preserves_late_bound_seams_and_order(monkeypatch) -> None:
    events: list[object] = []
    parsed_roll = SimpleNamespace(name="Power", series="Chainsaw Man")
    analysis = SimpleNamespace(
        character_name="Power",
        series="Chainsaw Man",
        claim_rank=7,
        kakera_value=1448,
        displayed_key_type=None,
        displayed_key_count=None,
        wishlist_state="Not wished",
        keyed_harem_state="No saved key record imported",
        rollability_state="Observed rolling now (available at import time)",
        claim_window_state="No imported claim-window state",
    )

    def patched_resolver(server, account):
        events.append(("resolver", server, account))
        return "Lake", "ernieuuu"

    def patched_source(path, clipboard):
        events.append(("source", path, clipboard))
        return "copied roll"

    def recording_parser_init(self, *args, **kwargs):
        events.append("parser")

    def recording_parse(self, text):
        events.append(("parse", text))
        return parsed_roll

    def recording_service_init(self, *args, **kwargs):
        events.append("service")

    def recording_analyze(self, roll, server, account):
        events.append(("analyze", roll, server, account))
        return analysis

    # main.app already exists before these post-construction patches.
    monkeypatch.setattr(main, "_resolve_account_context", patched_resolver)
    monkeypatch.setattr(main, "_read_message_source", patched_source)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "__init__", recording_parser_init)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "parse_roll", recording_parse)
    monkeypatch.setattr(
        roll_analysis_service_module.RollAnalysisService,
        "__init__",
        recording_service_init,
    )
    monkeypatch.setattr(
        roll_analysis_service_module.RollAnalysisService,
        "analyze",
        recording_analyze,
    )

    result = CliRunner().invoke(
        main.app,
        ["analyze-roll", "--server", "ignored", "--account", "ignored", "--clipboard"],
    )

    assert result.exit_code == 0
    assert events == [
        ("resolver", "ignored", "ignored"),
        "parser",
        ("source", None, True),
        ("parse", "copied roll"),
        "service",
        ("analyze", parsed_roll, "Lake", "ernieuuu"),
    ]
    assert "Power - roll context" in result.stdout
    assert "Series" in result.stdout
    assert "Chainsaw Man" in result.stdout
    assert "Wishlist" in result.stdout
    assert "Not wished" in result.stdout
    assert "Saved key state" in result.stdout
    assert "No saved key record imported" in result.stdout
    assert "Claim window" in result.stdout
    assert "No imported claim-window state" in result.stdout
    assert "This is factual roll context, not a claim/skip recommendation." in result.stdout
    assert result.stdout.lower().count("recommendation") == 1


def test_analyze_roll_cli_failures_preserve_sequencing_and_errors(monkeypatch) -> None:
    events: list[object] = []

    def failing_resolver(self, server, account):
        events.append(("resolver", server, account))
        raise ValueError("invalid account context")

    def recording_resolver(server, account):
        events.append(("resolver", server, account))
        return "Lake", "ernieuuu"

    def recording_parser_init(self, *args, **kwargs):
        events.append("parser")

    def unexpected_parse(self, text):
        events.append(("parse", text))
        raise AssertionError("parse_roll must not run after source selection failure")

    def unexpected_service_init(self, *args, **kwargs):
        events.append("service")
        raise AssertionError("RollAnalysisService must not construct after an earlier failure")

    original_resolve_context = main.ConfigService.resolve_context
    monkeypatch.setattr(main.ConfigService, "resolve_context", failing_resolver)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "__init__", recording_parser_init)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "parse_roll", unexpected_parse)
    monkeypatch.setattr(
        roll_analysis_service_module.RollAnalysisService,
        "__init__",
        unexpected_service_init,
    )

    resolver_failure = CliRunner().invoke(
        main.app,
        ["analyze-roll", "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert resolver_failure.exit_code == 1
    assert "invalid account context" in resolver_failure.stdout
    assert "Traceback" not in resolver_failure.stdout
    assert events == [("resolver", "Lake", "ernieuuu")]

    monkeypatch.setattr(main.ConfigService, "resolve_context", original_resolve_context)
    monkeypatch.setattr(main, "_resolve_account_context", recording_resolver)

    events.clear()
    conflict = CliRunner().invoke(
        main.app,
        ["analyze-roll", "response.txt", "--clipboard"],
    )
    missing = CliRunner().invoke(main.app, ["analyze-roll"])

    assert conflict.exit_code == 1
    assert "Use either a file path or --clipboard, not both." in conflict.stdout
    assert "Traceback" not in conflict.stdout
    assert missing.exit_code == 1
    assert "Provide a text-file path or use --clipboard." in missing.stdout
    assert "Traceback" not in missing.stdout
    assert events == [
        ("resolver", None, None),
        "parser",
        ("resolver", None, None),
        "parser",
    ]

    events.clear()

    def patched_source(path, clipboard):
        events.append(("source", path, clipboard))
        return "malformed roll"

    def failing_parse(self, text):
        events.append(("parse", text))
        raise mudae_parser_module.MudaeParseError("malformed roll")

    monkeypatch.setattr(main, "_read_message_source", patched_source)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "parse_roll", failing_parse)

    parse_failure = CliRunner().invoke(main.app, ["analyze-roll", "--clipboard"])

    assert parse_failure.exit_code == 1
    assert parse_failure.stdout.count("malformed roll") == 1
    assert "Traceback" not in parse_failure.stdout
    assert "roll context" not in parse_failure.stdout
    assert events == [
        ("resolver", None, None),
        "parser",
        ("source", None, True),
        ("parse", "malformed roll"),
    ]


def test_parse_cli_registration_and_help_are_lazy(monkeypatch) -> None:
    expected_commands = {
        "top",
        "im",
        "roll",
        "reaction",
        "mm",
        "bonus",
        "mmr",
        "wishlist",
        "disablelist",
        "topx",
        "kakera",
        "personalrare",
        "timers",
        "towerstate",
        "lootstate",
        "infokl",
        "settings",
    }
    events: list[str] = []

    def unexpected_source(path, clipboard):
        events.append("source")
        raise AssertionError("Parse help must not read a source")

    def unexpected_parser(*args, **kwargs):
        events.append("parser")
        raise AssertionError("Parse help must not invoke a parser")

    monkeypatch.setattr(main, "_read_message_source", unexpected_source)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "__init__", unexpected_parser)
    runner = CliRunner()

    result = runner.invoke(main.app, ["parse", "--help"])

    assert result.exit_code == 0
    registered = {command.name for command in main.parse_app.registered_commands}
    assert registered == expected_commands
    assert all(command in result.stdout for command in expected_commands)
    assert events == []

    for command in ("top", "reaction", "lootstate"):
        command_help = runner.invoke(main.app, ["parse", command, "--help"])
        assert command_help.exit_code == 0
        assert "--clipboard" in command_help.stdout
        assert "-c" in command_help.stdout
        assert "Text file containing" in command_help.stdout
    assert events == []


def test_parse_cli_source_schema_and_main_reader_are_late_bound(monkeypatch, tmp_path) -> None:
    calls: list[tuple[Path | None, bool]] = []
    parsed: list[str] = []

    def patched_source(path, clipboard):
        calls.append((path, clipboard))
        return "patched top response"

    def parse_top_page(self, text):
        parsed.append(text)
        return SimpleNamespace(limit=10, page_number=1, page_count=1, characters=())

    monkeypatch.setattr(main, "_read_message_source", patched_source)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "parse_top_page", parse_top_page)
    runner = CliRunner()
    path = tmp_path / "response.txt"

    path_result = runner.invoke(main.app, ["parse", "top", str(path)])
    clipboard_result = runner.invoke(main.app, ["parse", "top", "-c"])

    assert path_result.exit_code == 0
    assert clipboard_result.exit_code == 0
    assert calls == [(path, False), (None, True)]
    assert parsed == ["patched top response", "patched top response"]
    assert "TOP 10 - Page 1/1" in path_result.stdout


def test_parse_cli_source_conflict_and_missing_source_fail_before_parser(monkeypatch) -> None:
    parser_calls: list[str] = []

    def unexpected_parser(self, text):
        parser_calls.append(text)
        raise AssertionError("Parser must not run when source selection is invalid")

    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "parse_top_page", unexpected_parser)
    runner = CliRunner()

    conflict = runner.invoke(main.app, ["parse", "top", "response.txt", "--clipboard"])
    missing = runner.invoke(main.app, ["parse", "top"])

    assert conflict.exit_code == 1
    assert "either a file path or --clipboard" in conflict.stdout
    assert missing.exit_code == 1
    assert "Provide a text-file path or use --clipboard" in missing.stdout
    assert parser_calls == []


def test_parse_cli_parser_error_is_stable_and_callback_time(monkeypatch) -> None:
    parser_calls: list[str] = []

    def patched_source(path, clipboard):
        return "malformed response"

    def fail_parse(self, text):
        parser_calls.append(text)
        raise mudae_parser_module.MudaeParseError("malformed top response")

    monkeypatch.setattr(main, "_read_message_source", patched_source)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "parse_top_page", fail_parse)

    result = CliRunner().invoke(main.app, ["parse", "top", "--clipboard"])

    assert result.exit_code == 1
    assert "malformed top response" in result.stdout
    assert "Traceback" not in result.stdout
    assert parser_calls == ["malformed response"]


def test_catalog_snapshot_cli_boundary_and_late_bound_resolvers(monkeypatch) -> None:
    root_command = get_command(main.app)
    catalog_commands = root_command.commands["catalog"].commands
    assert {
        "bonus",
        "wishlist",
        "disablelist",
        "unavailable",
        "kakera",
        "towerstate",
        "timers",
        "lootstate",
        "infokl",
        "settings",
    } <= set(catalog_commands)

    events: list[object] = []

    def resolve_account(server, account):
        events.append(("account-resolve", server, account))
        return "Lake", "ernieuuu"

    def resolve_server(server):
        events.append(("server-resolve", server))
        return "Lake"

    class RecordingCatalogService:
        def __init__(self):
            events.append("catalog-construct")

        def player_bonus(self, server, account):
            events.append(("bonus-read", server, account))
            return SimpleNamespace(
                account_name=account,
                metrics=(),
                observed_at=datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc),
            )

        def server_settings(self, server):
            events.append(("settings-read", server))
            return SimpleNamespace(
                server_name=server,
                metrics=(),
                game_mode=1,
                rolls_per_hour=10,
                claim_reset_minutes=45,
                observed_at=datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc),
            )

    monkeypatch.setattr(main, "_resolve_account_context", resolve_account)
    monkeypatch.setattr(main, "_resolve_server_context", resolve_server)
    monkeypatch.setattr(catalog_snapshot_commands_module, "CatalogService", RecordingCatalogService)

    runner = CliRunner()
    bonus_result = runner.invoke(
        main.app,
        ["catalog", "bonus", "--server", "ignored", "--account", "ignored"],
    )
    settings_result = runner.invoke(main.app, ["catalog", "settings", "--server", "ignored"])

    assert bonus_result.exit_code == 0
    assert settings_result.exit_code == 0
    assert events == [
        ("account-resolve", "ignored", "ignored"),
        "catalog-construct",
        ("bonus-read", "Lake", "ernieuuu"),
        ("server-resolve", "ignored"),
        "catalog-construct",
        ("settings-read", "Lake"),
    ]
    bonus_output = " ".join(bonus_result.stdout.split())
    assert "2026-07-12 23:45 UTC" in bonus_output
    assert (
        "Latest locally imported `$bonus` capture; displayed values are observed, "
        "and the capture may be partial."
    ) in bonus_output
    assert "2026-07-12" in settings_result.stdout


def test_catalog_bonus_cli_preserves_duplicate_metric_rows_and_source_order(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    bonus = SimpleNamespace(
        account_name="ernieuuu",
        metrics=(
            SimpleNamespace(label="Repeated metric", detail="first observed detail"),
            SimpleNamespace(label="Repeated metric", detail="second observed detail"),
        ),
        observed_at=observed_at,
    )

    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(player_bonus=lambda *_: bonus),
    )
    monkeypatch.setattr(main, "_resolve_account_context", lambda *_: ("Lake", "ernieuuu"))

    result = CliRunner().invoke(main.app, ["catalog", "bonus"])

    assert result.exit_code == 0
    assert result.stdout.index("first observed detail") < result.stdout.index("second observed detail")
    assert "2026-07-12 23:45 UTC" in result.stdout
    assert "Latest locally imported `$bonus` capture" in result.stdout


def test_catalog_bonus_cli_distinguishes_empty_snapshot_from_no_matching_snapshot(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    snapshots = iter(
        (
            SimpleNamespace(account_name="ernieuuu", metrics=(), observed_at=observed_at),
            None,
        )
    )

    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(player_bonus=lambda *_: next(snapshots)),
    )
    monkeypatch.setattr(main, "_resolve_account_context", lambda *_: ("Lake", "ernieuuu"))

    empty_snapshot_result = CliRunner().invoke(main.app, ["catalog", "bonus"])
    no_snapshot_result = CliRunner().invoke(main.app, ["catalog", "bonus"])

    assert empty_snapshot_result.exit_code == 0
    empty_snapshot_output = " ".join(empty_snapshot_result.stdout.split())
    assert "ernieuuu - player bonuses" in empty_snapshot_output
    assert "2026-07-12 23:45 UTC" in empty_snapshot_output
    assert "Latest locally imported `$bonus` capture" in empty_snapshot_output
    assert no_snapshot_result.exit_code == 0
    assert "No $bonus snapshot imported for this server/account yet." in no_snapshot_result.stdout
    assert "player bonuses" not in no_snapshot_result.stdout


def test_catalog_infokl_cli_renders_zero_values_and_distinguishes_missing_snapshot(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    snapshots = iter(
        (
            SimpleNamespace(
                server_name="Lake",
                loot_cost=0,
                quantity_quality_base_cost=0,
                quantity_quality_level_increment=0,
                observed_at=observed_at,
            ),
            None,
        )
    )
    events: list[object] = []

    def resolve_server(server):
        events.append(("server-resolve", server))
        return "Lake"

    def read_settings(server):
        events.append(("settings-read", server))
        return next(snapshots)

    monkeypatch.setattr(main, "_resolve_server_context", resolve_server)
    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(kakeraloot_settings=read_settings),
    )

    runner = CliRunner()
    populated_result = runner.invoke(main.app, ["catalog", "infokl", "--server", "ignored"])
    missing_result = runner.invoke(main.app, ["catalog", "infokl", "--server", "ignored"])

    assert populated_result.exit_code == 0
    populated_output = " ".join(populated_result.stdout.split())
    assert "Lake - Kakeraloot configuration" in populated_output
    assert "Each $kl: 0 Kakera" in populated_output
    assert "Quantity/Quality next-level cost: 0 + 0 per current level" in populated_output
    assert "Observed: 2026-07-12 23:45 UTC" in populated_output
    assert (
        "Provenance: values are the latest locally imported `$infokl` capture for the selected "
        "server; they are observed price details only and do not establish live/current "
        "availability or entitlement, fresh/stale status, or complete loot state."
    ) in populated_output
    assert missing_result.exit_code == 0
    assert "No $infokl configuration imported for this server yet." in missing_result.stdout
    assert "Kakeraloot configuration" not in missing_result.stdout
    assert "Provenance:" not in missing_result.stdout
    assert events == [
        ("server-resolve", "ignored"),
        ("settings-read", "Lake"),
        ("server-resolve", "ignored"),
        ("settings-read", "Lake"),
    ]


def test_catalog_settings_cli_preserves_observed_rows_and_distinguishes_empty_snapshot(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    snapshots = iter(
        (
            SimpleNamespace(
                server_name="Lake",
                metrics=(
                    SimpleNamespace(label="Premium", value="False"),
                    SimpleNamespace(label="Zero setting", value="0"),
                    SimpleNamespace(label="Repeated metric", value="first observed value"),
                    SimpleNamespace(label="Repeated metric", value="second observed value"),
                ),
                game_mode=0,
                rolls_per_hour=0,
                claim_reset_minutes=0,
                observed_at=observed_at,
            ),
            SimpleNamespace(
                server_name="Lake",
                metrics=(),
                game_mode=0,
                rolls_per_hour=0,
                claim_reset_minutes=0,
                observed_at=observed_at,
            ),
            None,
        )
    )

    def read_settings(server):
        assert server == "Lake"
        return next(snapshots)

    monkeypatch.setattr(main, "_resolve_server_context", lambda server: "Lake")
    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(server_settings=read_settings),
    )

    runner = CliRunner()
    populated_result = runner.invoke(main.app, ["catalog", "settings"])
    empty_result = runner.invoke(main.app, ["catalog", "settings"])
    missing_result = runner.invoke(main.app, ["catalog", "settings"])

    assert populated_result.exit_code == 0
    populated_output = " ".join(populated_result.stdout.split())
    assert "Premium" in populated_output
    assert "False" in populated_output
    assert "Zero setting" in populated_output
    assert "0" in populated_output
    assert populated_output.index("first observed value") < populated_output.index("second observed value")
    assert "Core: Gamemode 0 | 0 rolls/hour | claim reset 0 min" in populated_output
    assert "observed 2026-07-12 23:45 UTC" in populated_output
    assert (
        "Provenance: values are the latest locally imported server-scoped `$settings` capture for the "
        "selected server; they are observed only and may not be current or fresh. This display does "
        "not claim to include every server setting."
    ) in populated_output
    assert "Current server settings" not in populated_output
    assert "Fresh server settings" not in populated_output
    assert "Complete server settings" not in populated_output

    assert empty_result.exit_code == 0
    empty_output = " ".join(empty_result.stdout.split())
    assert "Lake - server settings" in empty_output
    assert "Core: Gamemode 0 | 0 rolls/hour | claim reset 0 min" in empty_output
    assert "observed 2026-07-12 23:45 UTC" in empty_output
    assert "Provenance:" in empty_output
    assert "Repeated metric" not in empty_output

    assert missing_result.exit_code == 0
    assert "No $settings snapshot imported for this server yet." in missing_result.stdout
    assert "server settings" not in missing_result.stdout
    assert "Provenance:" not in missing_result.stdout


def test_catalog_kakera_cli_distinguishes_zero_empty_snapshot_from_missing_snapshot(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    snapshots = iter(
        (
            SimpleNamespace(
                account_name="ernieuuu",
                kakera_balance=0,
                badges=(),
                observed_at=observed_at,
            ),
            None,
        )
    )

    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(kakera_state=lambda *_: next(snapshots)),
    )
    monkeypatch.setattr(main, "_resolve_account_context", lambda *_: ("Lake", "ernieuuu"))

    empty_snapshot_result = CliRunner().invoke(main.app, ["catalog", "kakera"])
    missing_snapshot_result = CliRunner().invoke(main.app, ["catalog", "kakera"])

    assert empty_snapshot_result.exit_code == 0
    empty_snapshot_output = " ".join(empty_snapshot_result.stdout.split())
    assert "ernieuuu - Kakera balance: 0" in empty_snapshot_output
    assert "2026-07-12 23:45 UTC" in empty_snapshot_output
    assert (
        "Provenance: Kakera balance and badges are from the latest locally imported `$k` capture; "
        "they do not establish current, fresh, or stale state, and snapshot completeness is not "
        "established."
    ) in empty_snapshot_output
    assert missing_snapshot_result.exit_code == 0
    assert "No $k snapshot imported for this server/account yet." in missing_snapshot_result.stdout
    assert "Kakera balance" not in missing_snapshot_result.stdout
    assert "Provenance:" not in missing_snapshot_result.stdout


def test_catalog_wishlist_cli_preserves_duplicate_rows_zero_counts_and_evidence_wording(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    wishlist = SimpleNamespace(
        account_name="ernieuuu",
        wishlist_count=0,
        wishlist_capacity=0,
        starwish_count=0,
        starwish_capacity=0,
        entries=(
            SimpleNamespace(name="Repeated character", is_starwish=True),
            SimpleNamespace(name="Repeated character", is_starwish=False),
        ),
        observed_at=observed_at,
    )

    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(wishlist=lambda *_: wishlist),
    )
    monkeypatch.setattr(main, "_resolve_account_context", lambda *_: ("Lake", "ernieuuu"))

    result = CliRunner().invoke(main.app, ["catalog", "wishlist"])

    assert result.exit_code == 0
    output = " ".join(result.stdout.split())
    assert "ernieuuu - wishlist 0/0 · Starwish 0/0" in output
    assert output.count("Repeated character") == 2
    first_character = output.index("Repeated character")
    second_character = output.index("Repeated character", first_character + 1)
    assert output.index("Starwish", first_character, second_character) < output.index("Wish", second_character)
    assert "2026-07-12 23:45 UTC" in output
    assert (
        "Provenance: counts, rows, and Starwish/Wish markers are captured evidence only; "
        "they do not establish current, fresh, or stale state, and snapshot completeness is not established."
    ) in output


def test_catalog_wishlist_cli_distinguishes_empty_snapshot_from_missing_snapshot(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    snapshots = iter(
        (
            SimpleNamespace(
                account_name="ernieuuu",
                wishlist_count=0,
                wishlist_capacity=13,
                starwish_count=0,
                starwish_capacity=2,
                entries=(),
                observed_at=observed_at,
            ),
            None,
        )
    )

    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(wishlist=lambda *_: next(snapshots)),
    )
    monkeypatch.setattr(main, "_resolve_account_context", lambda *_: ("Lake", "ernieuuu"))

    empty_snapshot_result = CliRunner().invoke(main.app, ["catalog", "wishlist"])
    missing_snapshot_result = CliRunner().invoke(main.app, ["catalog", "wishlist"])

    assert empty_snapshot_result.exit_code == 0
    empty_snapshot_output = " ".join(empty_snapshot_result.stdout.split())
    assert "ernieuuu - wishlist 0/13 · Starwish 0/2" in empty_snapshot_output
    assert "2026-07-12 23:45 UTC" in empty_snapshot_output
    assert "Provenance: counts, rows, and Starwish/Wish markers are captured evidence only" in empty_snapshot_output
    assert missing_snapshot_result.exit_code == 0
    assert "No $wl snapshot imported for this server/account yet." in missing_snapshot_result.stdout
    assert "2026-07-12 23:45 UTC" not in missing_snapshot_result.stdout
    assert "Provenance:" not in missing_snapshot_result.stdout


def test_catalog_operational_cli_registration_schema_and_help_are_lazy(monkeypatch) -> None:
    child_command = get_command(main.catalog_operational_app)
    assert set(child_command.commands) == {
        "imports",
        "reactions",
        "spheres",
        "reaction-summary",
        "rank-history",
    }
    expected_parameters = {
        "imports": ("limit",),
        "reactions": ("server", "account"),
        "spheres": ("server", "account"),
        "reaction-summary": ("server", "account"),
        "rank-history": ("name", "series", "limit"),
    }
    for command, parameters in expected_parameters.items():
        assert [parameter.name for parameter in child_command.commands[command].params] == list(parameters)

    constructions: list[str] = []

    def unexpected_constructor(_self, *args, **kwargs):
        constructions.append("service")
        raise AssertionError("catalog operational help must not construct services")

    monkeypatch.setattr(
        catalog_operational_commands_module.CatalogService,
        "__init__",
        unexpected_constructor,
    )
    runner = CliRunner()
    for command in expected_parameters:
        result = runner.invoke(main.app, ["catalog", command, "--help"])
        assert result.exit_code == 0
    assert constructions == []


def test_catalog_operational_commands_preserve_resolution_order_and_rendering(monkeypatch) -> None:
    events: list[object] = []
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)

    def resolve_account(server, account):
        events.append(("resolve", server, account))
        return "Lake", "ernieuuu"

    class RecordingCatalogService:
        def __init__(self):
            events.append("construct")

        def kakera_reactions(self, server, account):
            events.append(("reactions", server, account))
            return (SimpleNamespace(observed_at=observed_at, reaction_label=":kakeraY:", kakera_earned=350),)

        def sphere_result(self, server, account):
            events.append(("spheres", server, account))
            return SimpleNamespace(
                snapshot=SimpleNamespace(
                    gains=(
                        SimpleNamespace(sphere_type="white", amount=12, is_free=True),
                        SimpleNamespace(sphere_type="black", amount=3, is_free=False),
                    ),
                    total_gained=15,
                    stock=None,
                ),
                observed_at=observed_at,
            )

        def kakera_reaction_summary(self, server, account):
            events.append(("summary", server, account))
            return SimpleNamespace(
                receipt_count=2,
                total_kakera_earned=600,
                average_kakera_earned=300.0,
                highest_kakera_earned=350,
                by_reaction=((":kakeraY:", 2, 600),),
            )

        def rank_history(self, name, series, limit):
            events.append(("history", name, series, limit))
            return (
                SimpleNamespace(observed_at=observed_at, claim_rank=1200, like_rank=None),
            )

    monkeypatch.setattr(main, "_resolve_account_context", resolve_account)
    monkeypatch.setattr(catalog_operational_commands_module, "CatalogService", RecordingCatalogService)
    runner = CliRunner()

    reactions = runner.invoke(main.app, ["catalog", "reactions", "--server", "ignored", "--account", "ignored"])
    spheres = runner.invoke(main.app, ["catalog", "spheres", "--server", "ignored", "--account", "ignored"])
    summary = runner.invoke(main.app, ["catalog", "reaction-summary", "--server", "ignored", "--account", "ignored"])
    history = runner.invoke(main.app, ["catalog", "rank-history", "Power", "--series", "Chainsaw Man", "--limit", "3"])

    assert reactions.exit_code == spheres.exit_code == summary.exit_code == history.exit_code == 0
    assert events == [
        ("resolve", "ignored", "ignored"),
        "construct",
        ("reactions", "Lake", "ernieuuu"),
        ("resolve", "ignored", "ignored"),
        "construct",
        ("spheres", "Lake", "ernieuuu"),
        ("resolve", "ignored", "ignored"),
        "construct",
        ("summary", "Lake", "ernieuuu"),
        "construct",
        ("history", "Power", "Chainsaw Man", 3),
    ]
    assert "Local import time (UTC)" in reactions.stdout
    assert "Observed (UTC)" not in reactions.stdout
    assert "2026-07-12 23:45" in reactions.stdout
    assert (
        "Provenance: rows are up to 20 locally stored Mudae-reported receipt observations, newest stored first; "
        "they do not establish current reaction state, freshness/completeness, ownership, or successful causal action."
    ) in " ".join(reactions.stdout.split())
    assert "Yes" in spheres.stdout and "No" in spheres.stdout
    spheres_output = " ".join(spheres.stdout.split())
    assert "Mudae-reported stock: unknown" in spheres_output
    assert "Recorded observation: 2026-07-12 23:45 UTC" in spheres_output
    assert (
        "Provenance: this is the latest locally stored `$oq` observation for the selected server/account; it does "
        "not establish current stock/sphere state, freshness, availability/enabled state, ownership, completeness, "
        "successful action, or causality."
    ) in spheres_output
    assert "Receipts: 2" in summary.stdout
    summary_output = " ".join(summary.stdout.split())
    assert (
        "Provenance: values are descriptive aggregates of all currently stored Mudae-reported receipt rows for the "
        "selected server/account; no displayed time window or timestamps are provided. They do not establish "
        "current reaction state, freshness, complete history, ownership, successful action, causality, or "
        "dedup/replay assurance."
    ) in summary_output
    assert "Observed (UTC)" not in summary.stdout
    assert "Recorded observation:" not in summary.stdout
    assert "#1,200" in history.stdout
    assert "-" in history.stdout
    assert "2026-07-12 23:45" in history.stdout
    history_output = " ".join(history.stdout.split())
    assert (
        "Provenance: rows are up-to-limit local-import-time records, shown newest stored first; ranks are direct "
        "global-rank observations imported from Mudae. '-' means that rank was not observed. This output does not "
        "establish current, fresh, or stale rank, a complete timeline, server/account ownership or availability, "
        "trend, or causality."
    ) in history_output


def test_catalog_spheres_distinguishes_empty_observation_from_no_history(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    observations = iter(
        (
            SimpleNamespace(
                snapshot=SimpleNamespace(gains=(), total_gained=0, stock=0),
                observed_at=observed_at,
            ),
            None,
        )
    )

    monkeypatch.setattr(main, "_resolve_account_context", lambda *_: ("Lake", "ernieuuu"))
    monkeypatch.setattr(
        catalog_operational_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(sphere_result=lambda *_args: next(observations)),
    )
    runner = CliRunner()

    empty_observation = runner.invoke(main.app, ["catalog", "spheres", "--server", "Lake", "--account", "ernieuuu"])
    no_history = runner.invoke(main.app, ["catalog", "spheres", "--server", "Lake", "--account", "ernieuuu"])

    assert empty_observation.exit_code == 0
    empty_output = " ".join(empty_observation.stdout.split())
    assert "Sphere" in empty_observation.stdout
    assert "Total gained: +0 spheres" in empty_output
    assert "Mudae-reported stock: 0" in empty_output
    assert "Recorded observation: 2026-07-12 23:45 UTC" in empty_output
    assert "Provenance: this is the latest locally stored `$oq` observation" in empty_output

    assert no_history.exit_code == 0
    assert "No $oq sphere result imported for this server/account yet." in no_history.stdout
    assert "Recorded observation:" not in no_history.stdout
    assert "Provenance:" not in no_history.stdout


def test_catalog_rank_history_empty_output_is_distinct_and_has_no_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        catalog_operational_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(rank_history=lambda *_args: ()),
    )

    result = CliRunner().invoke(main.app, ["catalog", "rank-history", "Power", "--series", "Chainsaw Man"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "No rank observations imported for that character/series yet."
    assert "imported rank history" not in result.stdout
    assert "Claim rank" not in result.stdout
    assert "Like rank" not in result.stdout
    assert "Provenance:" not in result.stdout


@pytest.mark.parametrize(
    ("command", "arguments", "method", "value", "message"),
    [
        ("imports", (), "recent_imports", (), "No local import events recorded yet."),
        ("reactions", ("--server", "Lake", "--account", "ernieuuu"), "kakera_reactions", (), "No reaction receipts imported for this server/account yet."),
        ("spheres", ("--server", "Lake", "--account", "ernieuuu"), "sphere_result", None, "No $oq sphere result imported for this server/account yet."),
        ("reaction-summary", ("--server", "Lake", "--account", "ernieuuu"), "kakera_reaction_summary", SimpleNamespace(receipt_count=0), "No reaction receipts imported for this server/account yet."),
        ("rank-history", ("Power", "--series", "Chainsaw Man"), "rank_history", (), "No rank observations imported for that character/series yet."),
    ],
)
def test_catalog_operational_commands_keep_command_specific_empty_exits(
    monkeypatch, command, arguments, method, value, message
) -> None:
    monkeypatch.setattr(main, "_resolve_account_context", lambda _server, _account: ("Lake", "ernieuuu"))
    monkeypatch.setattr(
        catalog_operational_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(**{method: lambda *_args: value}),
    )

    result = CliRunner().invoke(main.app, ["catalog", command, *arguments])

    assert result.exit_code == 0
    assert message in result.stdout
    if command == "reactions":
        assert "Provenance:" not in result.stdout
        assert "Kakera reaction payouts" not in result.stdout


def test_catalog_imports_preserves_red_value_error_boundary(monkeypatch) -> None:
    def recent_imports(_limit):
        raise ValueError("invalid import limit")

    monkeypatch.setattr(
        catalog_operational_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(recent_imports=recent_imports),
    )

    result = CliRunner().invoke(main.app, ["catalog", "imports"])

    assert result.exit_code == 1
    assert "invalid import limit" in result.stdout
    assert "Traceback" not in result.stdout


def test_catalog_imports_renders_local_history_and_forwards_limits(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    imports = (
        SimpleNamespace(
            id=18,
            kind="harem_key",
            source="clipboard",
            server_name=None,
            observed_at=observed_at,
        ),
        SimpleNamespace(
            id=17,
            kind="top_page",
            source="discord",
            server_name="Lake",
            observed_at=observed_at,
        ),
    )
    calls: list[int] = []

    def recent_imports(limit):
        calls.append(limit)
        return imports[:limit]

    monkeypatch.setattr(
        catalog_operational_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(recent_imports=recent_imports),
    )

    runner = CliRunner()
    help_result = runner.invoke(main.app, ["catalog", "imports", "--help"])
    default_result = runner.invoke(main.app, ["catalog", "imports"])
    explicit_result = runner.invoke(main.app, ["catalog", "imports", "--limit", "1"])

    assert help_result.exit_code == 0
    imports_command = get_command(main.app).commands["catalog"].commands["imports"]
    assert imports_command.help == "Show local import-event history."
    assert imports_command.params[0].help == "Number of local import-event summaries to display."
    assert default_result.exit_code == explicit_result.exit_code == 0
    assert calls == [20, 1]
    output = " ".join(default_result.stdout.split())
    assert "Recent local import-event history" in output
    assert "18" in output and "harem_key" in output and "clipboard" in output
    assert "17" in output and "top_page" in output and "discord" in output and "Lake" in output
    assert "2026-07-12 23:45" in output
    assert "-" in output
    assert (
        "Provenance: rows are limited local import-event summaries ordered by newest stored event ID; "
        "the server label is best-effort. Rows do not establish account scope, processing/replay/success "
        "status, freshness, completeness, or current state."
    ) in output
    assert "Account" not in output
    assert "Status" not in output
    assert "Fresh" not in output
    assert "Success" not in output

    explicit_output = " ".join(explicit_result.stdout.split())
    assert "Recent local import-event history" in explicit_output
    assert "18" in explicit_output and "harem_key" in explicit_output
    assert "top_page" not in explicit_output
    assert "No local import events recorded yet." not in explicit_output


def test_catalog_towerstate_renders_middle_dot_separators(monkeypatch) -> None:
    state = SimpleNamespace(
        account_name="Account",
        current_level=4,
        completed_towers=2,
        built_perk_ids=(3, 7),
        next_level_cost=100_000,
        kakera_balance=25_000,
        observed_at=datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(tower_state=lambda *_: state),
    )

    result = CliRunner().invoke(
        main.app,
        ["catalog", "towerstate", "--server", "Lake", "--account", "Account"],
    )

    assert result.exit_code == 0
    assert "Completed towers: 2 · Built perks: 3, 7" in result.stdout
    assert "Next floor: 100,000 Kakera · Balance: 25,000 Kakera" in result.stdout
    assert "Balance: 25,000 Kakera · Shortfall: 75,000 Kakera" in result.stdout
    assert "Observed: 2026-07-12 23:45 UTC" in result.stdout
    assert (
        "Provenance: values are from the latest locally imported `$kt`/`$tower` capture; "
        "they do not establish current, fresh, stale, or complete state."
    ) in " ".join(result.stdout.split())


def test_catalog_towerstate_preserves_missing_zero_and_empty_perks(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    snapshots = iter(
        (
            SimpleNamespace(
                account_name="Account",
                current_level=4,
                completed_towers=0,
                built_perk_ids=(),
                next_level_cost=100_000,
                kakera_balance=25_000,
                observed_at=observed_at,
            ),
            SimpleNamespace(
                account_name="Account",
                current_level=4,
                completed_towers=None,
                built_perk_ids=(3,),
                next_level_cost=100_000,
                kakera_balance=25_000,
                observed_at=observed_at,
            ),
            None,
        )
    )
    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(tower_state=lambda *_: next(snapshots)),
    )

    runner = CliRunner()
    zero_result = runner.invoke(main.app, ["catalog", "towerstate"])
    missing_result = runner.invoke(main.app, ["catalog", "towerstate"])
    no_snapshot_result = runner.invoke(main.app, ["catalog", "towerstate"])

    assert zero_result.exit_code == 0
    assert "Completed towers: 0" in zero_result.stdout
    assert "Built perks: no checked perk IDs parsed" in zero_result.stdout
    assert "Not reported in this capture" not in zero_result.stdout
    assert missing_result.exit_code == 0
    assert "Completed towers: Not reported in this capture" in missing_result.stdout
    assert "Built perks: 3" in missing_result.stdout
    assert no_snapshot_result.exit_code == 0
    assert "No $kt snapshot imported for this server/account yet." in no_snapshot_result.stdout
    assert "Provenance:" not in no_snapshot_result.stdout


def test_catalog_timers_preserves_explicit_zero_false_and_omitted_fields(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    observations = iter(
        (
            SimpleNamespace(
                account_name="Account",
                snapshot=SimpleNamespace(
                    can_claim_now=False,
                    claim_reset_minutes=0,
                    rolls_left=0,
                    rolls_reset_minutes=0,
                    rolls_reset_status=None,
                    daily_kakera_ready=False,
                    rt_available=False,
                    reaction_power_percent=0,
                    oh_remaining=0,
                    oc_remaining=0,
                    oq_remaining=0,
                    ot_remaining=0,
                ),
                observed_at=observed_at,
            ),
            SimpleNamespace(
                account_name="Account",
                snapshot=SimpleNamespace(
                    can_claim_now=None,
                    rolls_left=None,
                    rolls_reset_status=None,
                    daily_kakera_ready=None,
                    rt_available=None,
                    reaction_power_percent=None,
                    oh_remaining=None,
                ),
                observed_at=observed_at,
            ),
        )
    )
    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(timer_state=lambda *_: next(observations)),
    )
    monkeypatch.setattr(main, "_resolve_account_context", lambda *_: ("Lake", "Account"))

    runner = CliRunner()
    explicit_result = runner.invoke(main.app, ["catalog", "timers"])
    omitted_result = runner.invoke(main.app, ["catalog", "timers"])

    assert explicit_result.exit_code == 0
    assert "Available in 0 min" in explicit_result.stdout
    assert "0 left; reset in 0 min" in explicit_result.stdout
    assert "$dk" in explicit_result.stdout and "Not ready" in explicit_result.stdout
    assert "$rt" in explicit_result.stdout and "Not available" in explicit_result.stdout
    assert "Kakera reaction power" in explicit_result.stdout
    assert "Ouro" in explicit_result.stdout
    assert omitted_result.exit_code == 0
    assert "Claim" not in omitted_result.stdout
    assert "Rolls" not in omitted_result.stdout
    assert "$dk" not in omitted_result.stdout
    assert "$rt" not in omitted_result.stdout
    assert "Kakera reaction power" not in omitted_result.stdout
    assert "Ouro" not in omitted_result.stdout


def test_catalog_timers_distinguishes_observed_empty_from_no_snapshot(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    observations = iter(
        (
            SimpleNamespace(
                account_name="Account",
                snapshot=SimpleNamespace(
                    can_claim_now=None,
                    rolls_left=None,
                    rolls_reset_status=None,
                    daily_kakera_ready=None,
                    rt_available=None,
                    reaction_power_percent=None,
                    oh_remaining=None,
                ),
                observed_at=observed_at,
            ),
            None,
        )
    )
    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(timer_state=lambda *_: next(observations)),
    )
    monkeypatch.setattr(main, "_resolve_account_context", lambda *_: ("Lake", "Account"))

    runner = CliRunner()
    observed_empty_result = runner.invoke(main.app, ["catalog", "timers"])
    no_snapshot_result = runner.invoke(main.app, ["catalog", "timers"])

    assert observed_empty_result.exit_code == 0
    assert "$tu snapshot" in observed_empty_result.stdout
    assert "Observed: 2026-07-12 23:45 UTC" in observed_empty_result.stdout
    observed_output = " ".join(observed_empty_result.stdout.split())
    assert (
        "Provenance: values are from the latest locally imported timer snapshot for the selected "
        "server/account; displayed countdowns are captured evidence, not current deadlines, and "
        "do not establish fresh, stale, expired, or complete state."
    ) in observed_output
    assert "Source:" not in observed_empty_result.stdout
    assert "Expires at" not in observed_empty_result.stdout
    assert "Freshness:" not in observed_empty_result.stdout
    assert no_snapshot_result.exit_code == 0
    assert "No $tu snapshot imported for this server/account yet." in no_snapshot_result.stdout
    assert "Observed:" not in no_snapshot_result.stdout
    assert "Provenance:" not in no_snapshot_result.stdout


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
        catalog_snapshot_commands_module,
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
    assert (
        "Provenance: this is the latest locally imported `$lk` evidence for the selected "
        "server/account; values and status do not establish live/current, fresh/stale, "
        "available, or complete state."
    ) in " ".join(result.stdout.split())


def test_catalog_lootstate_renders_explicit_no_loots_with_provenance(monkeypatch) -> None:
    state = SimpleNamespace(
        has_kakeraloots=False,
        status_note="No Kakeraloots bought in this capture.",
        observed_at=datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(kakeraloot_state=lambda *_: state),
    )

    result = CliRunner().invoke(
        main.app,
        ["catalog", "lootstate", "--server", "Lake", "--account", "Account"],
    )

    output = " ".join(result.stdout.split())
    assert result.exit_code == 0
    assert "No Kakeraloots bought in this capture." in result.stdout
    assert "Observed: 2026-07-12 23:45 UTC" in result.stdout
    assert (
        "Provenance: this is the latest locally imported `$lk` evidence for the selected "
        "server/account; values and status do not establish live/current, fresh/stale, "
        "available, or complete state."
    ) in output
    assert "Kakeraloot state" not in result.stdout


def test_catalog_lootstate_distinguishes_no_snapshot_from_no_loots(monkeypatch) -> None:
    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(kakeraloot_state=lambda *_: None),
    )

    result = CliRunner().invoke(
        main.app,
        ["catalog", "lootstate", "--server", "Lake", "--account", "Account"],
    )

    assert result.exit_code == 0
    assert "No $lk snapshot imported for this server/account yet." in result.stdout
    assert "Observed:" not in result.stdout
    assert "Provenance:" not in result.stdout


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
        observed_at=datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        catalog_snapshot_commands_module,
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
    assert catalog_snapshot_commands_module._format_observed_toggle(value) == rendered


def test_catalog_disablelist_cli_preserves_zero_counts_duplicate_bundles_and_provenance(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    disablelist = SimpleNamespace(
        account_name="Account",
        slots_used=0,
        slots_capacity=16,
        total_disabled=0,
        disabled_wa=0,
        disabled_ha=0,
        disabled_wg=0,
        disabled_hg=0,
        western_disabled=True,
        irl_disabled=False,
        entries=(
            SimpleNamespace(name="Repeated bundle", disabled_count=0),
            SimpleNamespace(name="Other bundle", disabled_count=2),
            SimpleNamespace(name="Repeated bundle", disabled_count=0),
        ),
        observed_at=observed_at,
    )
    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(disablelist=lambda *_: disablelist),
    )

    result = CliRunner().invoke(
        main.app,
        ["catalog", "disablelist", "--server", "Lake", "--account", "Account"],
    )

    assert result.exit_code == 0
    output = " ".join(result.stdout.split())
    assert "Slots: 0/16 · Disabled: 0" in output
    assert "$wa: 0 · $ha: 0 · $wg: 0 · $hg: 0" in output
    assert output.count("Repeated bundle") == 2
    first_bundle = output.index("Repeated bundle")
    other_bundle = output.index("Other bundle")
    second_bundle = output.index("Repeated bundle", first_bundle + 1)
    assert first_bundle < other_bundle < second_bundle
    assert "2026-07-12 23:45 UTC" in output
    assert (
        "Provenance: counts, rows, and toggles are captured `$dl` evidence only; "
        "they do not establish current, fresh, or stale state, and snapshot completeness is not established."
    ) in output


def test_catalog_disablelist_cli_distinguishes_empty_snapshot_from_missing_snapshot(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    snapshots = iter(
        (
            SimpleNamespace(
                account_name="Account",
                slots_used=0,
                slots_capacity=0,
                total_disabled=0,
                disabled_wa=0,
                disabled_ha=0,
                disabled_wg=0,
                disabled_hg=0,
                western_disabled=None,
                irl_disabled=None,
                entries=(),
                observed_at=observed_at,
            ),
            None,
        )
    )
    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(disablelist=lambda *_: next(snapshots)),
    )

    empty_snapshot_result = CliRunner().invoke(
        main.app,
        ["catalog", "disablelist", "--server", "Lake", "--account", "Account"],
    )
    missing_snapshot_result = CliRunner().invoke(
        main.app,
        ["catalog", "disablelist", "--server", "Lake", "--account", "Account"],
    )

    assert empty_snapshot_result.exit_code == 0
    empty_snapshot_output = " ".join(empty_snapshot_result.stdout.split())
    assert "Account - disablelist" in empty_snapshot_output
    assert "Slots: 0/0 · Disabled: 0" in empty_snapshot_output
    assert "2026-07-12 23:45 UTC" in empty_snapshot_output
    assert "Provenance: counts, rows, and toggles are captured `$dl` evidence only" in empty_snapshot_output
    assert missing_snapshot_result.exit_code == 0
    assert "No $dl snapshot imported for this server/account yet." in missing_snapshot_result.stdout
    assert "2026-07-12 23:45 UTC" not in missing_snapshot_result.stdout
    assert "Provenance:" not in missing_snapshot_result.stdout


def test_catalog_unavailable_cli_renders_observation_timestamps_and_separates_rank_from_reason(
    monkeypatch,
) -> None:
    observations = (
        SimpleNamespace(
            character=SimpleNamespace(name="Power", series="Chainsaw Man"),
            claim_rank=42,
            reason="$togglewestern",
            observed_at=datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            character=SimpleNamespace(name="Albedo", series="Overlord"),
            claim_rank=7,
            reason=None,
            observed_at=datetime(2026, 7, 13, 0, 15, tzinfo=timezone.utc),
        ),
    )
    calls: list[tuple[str, str]] = []

    class RecordingCatalogService:
        def unavailable_characters(self, server, account):
            calls.append((server, account))
            return observations

    monkeypatch.setattr(catalog_snapshot_commands_module, "CatalogService", RecordingCatalogService)
    monkeypatch.setattr(main, "_resolve_account_context", lambda *_: ("Lake", "Account"))
    monkeypatch.setattr(main.console, "_width", 120)

    result = CliRunner().invoke(main.app, ["catalog", "unavailable"])

    assert result.exit_code == 0
    output = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", result.stdout)
    output = " ".join(output.split())
    assert calls == [("Lake", "Account")]
    assert "#42" in output
    assert "$togglewestern" in output
    assert "#7" in output
    assert "Not specified in observed $topx row" in output
    assert "2026-07-12 23:45 UTC" in output
    assert "2026-07-13 00:15 UTC" in output
    assert (
        "Provenance: rows are latest locally retained positive `$topx` evidence for the selected "
        "server/account; no row establishes that a character is available or rollable, and "
        "freshness/currentness is not classified."
    ) in output


def test_catalog_unavailable_cli_preserves_no_observations_message(monkeypatch) -> None:
    monkeypatch.setattr(
        catalog_snapshot_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(unavailable_characters=lambda *_: ()),
    )
    monkeypatch.setattr(main, "_resolve_account_context", lambda *_: ("Lake", "Account"))

    result = CliRunner().invoke(main.app, ["catalog", "unavailable"])

    assert result.exit_code == 0
    assert "No unavailable-character observations imported yet." in result.stdout
    assert "Provenance:" not in result.stdout


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


def test_catalog_search_cli_registration_schema_and_help_are_lazy(monkeypatch) -> None:
    root_command = get_command(main.app)
    catalog_command = root_command.commands["catalog"]
    expected_commands = {"top", "show", "harem", "keyfarm", "keyprogress", "key-gains"}
    assert expected_commands <= set(catalog_command.commands)

    expected_parameters = {
        "top": ("limit", "server", "account", "series", "exact_series", "owned_only", "unowned_only", "keyed_only", "unavailable_only", "sort_by"),
        "show": ("name", "series"),
        "harem": ("server", "account", "series", "exact_series", "key_type", "min_keys", "max_keys", "min_kakera", "unresolved_only", "sort_by", "limit"),
        "keyfarm": ("server", "account", "limit"),
        "keyprogress": ("server", "account", "limit"),
        "key-gains": ("server", "account", "limit"),
    }
    for command, parameters in expected_parameters.items():
        assert [parameter.name for parameter in catalog_command.commands[command].params] == list(parameters)

    constructions: list[str] = []

    def unexpected_constructor(_self, *args, **kwargs):
        constructions.append("service")
        raise AssertionError("catalog search help must not construct services")

    for service_class in (
        catalog_search_commands_module.CatalogService,
        catalog_search_commands_module.HaremSearchService,
        catalog_search_commands_module.KeyProgressService,
        catalog_search_commands_module.TopSearchService,
    ):
        monkeypatch.setattr(service_class, "__init__", unexpected_constructor)

    runner = CliRunner()
    for command in ("top", "show", "harem", "keyfarm", "keyprogress", "key-gains"):
        result = runner.invoke(main.app, ["catalog", command, "--help"])
        assert result.exit_code == 0
    assert constructions == []


def test_catalog_top_preserves_config_sequence_and_search_output(monkeypatch) -> None:
    events: list[object] = []
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    entry = SimpleNamespace(
        character=CatalogCharacter(id=1, name="Power", series="Chainsaw Man", gender=None, roulette=None),
        claim_rank=7,
        observed_at=observed_at,
        owned=None,
        owner_name=None,
        owner_is_self=None,
        topo_observed=None,
        keyed=True,
        key_type="gold",
        key_count=7,
        unavailable=False,
        unavailable_reason=None,
        rollability_status=None,
        kakera_value=1448,
        roulette_types=(),
    )

    class RecordingConfigService:
        def __init__(self):
            events.append("config")

        def resolve_context(self, server, account):
            events.append(("resolve", server, account))
            return "Lake", "ernieuuu"

        def owned_account_names(self, server):
            events.append(("owned-accounts", server))
            return ("ernieuuu",)

    class RecordingTopSearchService:
        def __init__(self):
            events.append("top-service")

        def search(self, **kwargs):
            events.append(("search", kwargs["server_name"], kwargs["account_name"], kwargs["owned_account_names"]))
            return (entry,)

    monkeypatch.setattr(main, "ConfigService", RecordingConfigService)
    monkeypatch.setattr(catalog_search_commands_module, "TopSearchService", RecordingTopSearchService)
    monkeypatch.setattr(main.console, "width", 240)

    result = CliRunner().invoke(main.app, ["catalog", "top", "--server", "ignored", "--account", "ignored"])

    assert result.exit_code == 0
    assert events == [
        "config",
        ("resolve", "ignored", "ignored"),
        ("owned-accounts", "Lake"),
        "top-service",
        ("search", "Lake", "ernieuuu", ("ernieuuu",)),
    ]
    assert "Imported Character Catalog Search" in result.stdout
    assert "Power" in result.stdout
    assert ":goldkey: (7)" in result.stdout
    assert "$top observed (UTC)" in result.stdout
    assert (
        "The `$top observed (UTC)` timestamp is the latest local `$top` observation; "
        "account-scoped evidence has independent timestamps. MOA applies no age threshold to "
        "classify evidence as fresh or stale."
    ) in result.stdout


def test_catalog_account_search_commands_use_late_bound_resolver(monkeypatch) -> None:
    events: list[object] = []

    def patched_resolver(server, account):
        events.append(("resolve", server, account))
        return "Lake", "ernieuuu"

    monkeypatch.setattr(main, "_resolve_account_context", patched_resolver)
    monkeypatch.setattr(catalog_search_commands_module.CatalogService, "__init__", lambda _self: None)
    monkeypatch.setattr(catalog_search_commands_module.HaremSearchService, "__init__", lambda _self: None)
    monkeypatch.setattr(catalog_search_commands_module.KeyProgressService, "__init__", lambda _self: None)
    monkeypatch.setattr(
        catalog_search_commands_module.HaremSearchService,
        "search",
        lambda _self, *_args, **_kwargs: events.append("harem-search") or (),
    )
    monkeypatch.setattr(
        catalog_search_commands_module.KeyProgressService,
        "progress",
        lambda _self, *_args: events.append("key-progress") or (),
    )
    monkeypatch.setattr(
        catalog_search_commands_module.CatalogService,
        "recent_key_gains",
        lambda _self, *_args: events.append("key-gains") or (),
    )

    runner = CliRunner()
    for command in ("harem", "keyprogress", "key-gains"):
        result = runner.invoke(main.app, ["catalog", command, "--server", "ignored", "--account", "ignored"])
        assert result.exit_code == 0
    assert events == [
        ("resolve", "ignored", "ignored"),
        "harem-search",
        ("resolve", "ignored", "ignored"),
        "key-progress",
        ("resolve", "ignored", "ignored"),
        "key-gains",
    ]


def test_catalog_keyprogress_cli_discloses_provenance_and_honors_limit(monkeypatch) -> None:
    entries = (
        SimpleNamespace(
            character_name="Power",
            current_tier="Gold Key",
            key_count=7,
            next_milestone_key_count=9,
            keys_until_next_milestone=2,
            next_effects=("Gold bonus.",),
        ),
        SimpleNamespace(
            character_name="Saber",
            current_tier="Silver Key",
            key_count=5,
            next_milestone_key_count=6,
            keys_until_next_milestone=1,
            next_effects=("Gold bonus.",),
        ),
        SimpleNamespace(
            character_name="Miku",
            current_tier="Chaos Key",
            key_count=24,
            next_milestone_key_count=25,
            keys_until_next_milestone=1,
            next_effects=("Second reaction.",),
        ),
    )
    monkeypatch.setattr(main, "_resolve_account_context", lambda _server, _account: ("Lake", "ernieuuu"))
    monkeypatch.setattr(
        catalog_search_commands_module,
        "KeyProgressService",
        lambda: SimpleNamespace(progress=lambda _server, _account: entries),
    )
    monkeypatch.setattr(main.console, "width", 240)

    result = CliRunner().invoke(
        main.app,
        [
            "catalog",
            "keyprogress",
            "--server",
            "Lake",
            "--account",
            "ernieuuu",
            "--limit",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert "Power" in result.stdout
    assert "Saber" in result.stdout
    assert "Miku" not in result.stdout
    assert (
        "Keys are latest local key observations; Tier, Next, Away, and Next unlock are derived from "
        "universal key rules. Observations do not establish current, fresh, or stale state; timestamps "
        "and scan completeness are not shown."
    ) in result.stdout


def test_catalog_key_gains_cli_discloses_observed_roll_key_states_and_honors_limit(
    monkeypatch,
) -> None:
    observed_at = datetime(2026, 7, 13, 0, 15, tzinfo=timezone.utc)
    observations = (
        SimpleNamespace(
            character_name="Power",
            key_type="gold",
            key_count=7,
            kakera_value=0,
            observed_at=observed_at,
        ),
        SimpleNamespace(
            character_name="Saber",
            key_type="silver",
            key_count=2,
            kakera_value=None,
            observed_at=observed_at,
        ),
        SimpleNamespace(
            character_name="Miku",
            key_type="bronze",
            key_count=1,
            kakera_value=125,
            observed_at=observed_at,
        ),
    )
    calls: list[tuple[str, str, int]] = []

    class RecordingCatalogService:
        def recent_key_gains(self, server, account, limit):
            calls.append((server, account, limit))
            return observations[:limit]

    monkeypatch.setattr(
        main,
        "_resolve_account_context",
        lambda _server, _account: ("Lake", "ernieuuu"),
    )
    monkeypatch.setattr(catalog_search_commands_module, "CatalogService", RecordingCatalogService)
    monkeypatch.setattr(main.console, "width", 240)

    result = CliRunner().invoke(
        main.app,
        [
            "catalog",
            "key-gains",
            "--server",
            "ignored",
            "--account",
            "ignored",
            "--limit",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("Lake", "ernieuuu", 2)]
    assert "ernieuuu - observed roll key states" in result.stdout
    assert "Power" in result.stdout
    assert "Saber" in result.stdout
    assert "Miku" not in result.stdout
    assert ":goldkey: (7)" in result.stdout
    assert ":silverkey: (2)" in result.stdout
    power_row = next(line for line in result.stdout.splitlines() if "Power" in line)
    saber_row = next(line for line in result.stdout.splitlines() if "Saber" in line)
    assert "0:kakera:" in power_row
    assert " - " in saber_row
    assert "2026-07-13 00:15" in result.stdout
    assert (
        "Each row is a directly displayed roll key marker/count and Kakera value, ordered by newest "
        "stored observation; it is not a calculated gain or a current/fresh/stale key state."
    ) in result.stdout


def test_catalog_key_gains_cli_empty_state_uses_observation_wording(monkeypatch) -> None:
    monkeypatch.setattr(main, "_resolve_account_context", lambda _server, _account: ("Lake", "ernieuuu"))
    monkeypatch.setattr(
        catalog_search_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(recent_key_gains=lambda _server, _account, _limit: ()),
    )

    result = CliRunner().invoke(
        main.app,
        ["catalog", "key-gains", "--server", "Lake", "--account", "ernieuuu"],
    )

    assert result.exit_code == 0
    assert "No observed roll key states imported from rolls for this server/account yet." in result.stdout
    assert "No key gains imported" not in result.stdout


def test_catalog_harem_cli_clarifies_key_observation_status(monkeypatch) -> None:
    first_observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    second_observed_at = datetime(2026, 7, 13, 0, 15, tzinfo=timezone.utc)
    entries = (
        SimpleNamespace(
            character_name="Power",
            character=CatalogCharacter(
                id=1, name="Power", series="Chainsaw Man", gender=None, roulette=None
            ),
            key_type="gold",
            key_count=7,
            kakera_value=1448,
            observed_at=first_observed_at,
        ),
        SimpleNamespace(
            character_name="Unresolved Name",
            character=None,
            key_type="silver",
            key_count=2,
            kakera_value=None,
            observed_at=second_observed_at,
        ),
    )
    monkeypatch.setattr(
        main,
        "_resolve_account_context",
        lambda server, account: (server, account),
    )
    monkeypatch.setattr(
        catalog_search_commands_module,
        "HaremSearchService",
        lambda: SimpleNamespace(search=lambda *_args, **_kwargs: entries),
    )
    monkeypatch.setattr(main.console, "width", 240)

    result = CliRunner().invoke(
        main.app,
        ["catalog", "harem", "--server", "Lake", "--account", "ernieuuu"],
    )

    assert result.exit_code == 0
    assert "Power" in result.stdout
    assert "Chainsaw Man" in result.stdout
    assert "Unresolved Name" in result.stdout
    assert "Needs $im" in result.stdout
    assert ":goldkey: (7)" in result.stdout
    assert ":silverkey: (2)" in result.stdout
    assert "1,448:kakera:" in result.stdout
    assert "-" in result.stdout
    assert "2026-07-12 23:45" in result.stdout
    assert "2026-07-13 00:15" in result.stdout
    assert (
        "Each Observed value is the latest local key observation for that row; it does not establish "
        "current state and has no fresh/stale age classification. `Needs $im` means unresolved "
        "identity evidence; scan completeness is not shown."
    ) in result.stdout


def test_catalog_keyfarm_cli_renders_latest_local_evidence_without_inference(monkeypatch) -> None:
    events: list[object] = []
    first_observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    second_observed_at = datetime(2026, 7, 13, 0, 15, tzinfo=timezone.utc)
    third_observed_at = datetime(2026, 7, 13, 0, 30, tzinfo=timezone.utc)
    entries = (
        SimpleNamespace(
            character_name="Zero Kakera",
            key_type="silver",
            key_count=0,
            kakera_value=0,
            observed_at=first_observed_at,
        ),
        SimpleNamespace(
            character_name="Missing Kakera",
            key_type="gold",
            key_count=7,
            kakera_value=None,
            observed_at=first_observed_at,
        ),
        SimpleNamespace(
            character_name="Starwish",
            key_type="gold",
            key_count=5,
            kakera_value=150,
            observed_at=second_observed_at,
        ),
        SimpleNamespace(
            character_name="Unlisted",
            key_type="silver",
            key_count=2,
            kakera_value=100,
            observed_at=third_observed_at,
        ),
    )
    wishlist = SimpleNamespace(
        entries=(SimpleNamespace(name="Starwish", is_starwish=True),)
    )
    unavailable = (
        SimpleNamespace(
            character=CatalogCharacter(
                id=1, name="Starwish", series="Series", gender=None, roulette=None
            )
        ),
    )

    class RecordingCatalogService:
        def __init__(self):
            events.append("construct")

        def harem_keys(self, server, account):
            events.append(("harem_keys", server, account))
            return entries

        def wishlist(self, server, account):
            events.append(("wishlist", server, account))
            return wishlist

        def unavailable_characters(self, server, account):
            events.append(("unavailable_characters", server, account))
            return unavailable

    monkeypatch.setattr(
        main,
        "_resolve_account_context",
        lambda server, account: events.append(("resolve", server, account)) or ("Lake", "ernieuuu"),
    )
    monkeypatch.setattr(catalog_search_commands_module, "CatalogService", RecordingCatalogService)
    monkeypatch.setattr(main.console, "width", 240)

    result = CliRunner().invoke(
        main.app,
        [
            "catalog",
            "keyfarm",
            "--server",
            "ignored",
            "--account",
            "ignored",
            "--limit",
            "3",
        ],
    )

    assert result.exit_code == 0
    assert events == [
        ("resolve", "ignored", "ignored"),
        "construct",
        ("harem_keys", "Lake", "ernieuuu"),
        ("wishlist", "Lake", "ernieuuu"),
        ("unavailable_characters", "Lake", "ernieuuu"),
    ]
    assert "latest local key-farm shortlist" in result.stdout
    assert "current key-farm" not in result.stdout
    assert "Observed (UTC)" in result.stdout
    assert "Zero Kakera" in result.stdout
    assert "Starwish" in result.stdout
    assert "Unlisted" in result.stdout
    assert "Missing Kakera" not in result.stdout
    assert result.stdout.index("Zero Kakera") < result.stdout.index("Starwish") < result.stdout.index("Unlisted")
    assert "0:kakera:" in result.stdout
    assert "No imported $wl snapshot" not in result.stdout
    assert "Not listed in observed wishlist" in result.stdout
    assert "Observed unavailable" in result.stdout
    assert "No matching unavailable evidence" in result.stdout
    assert "2026-07-12 23:45" in result.stdout
    assert "2026-07-13 00:15" in result.stdout
    assert "2026-07-13 00:30" in result.stdout
    assert (
        "Observed timestamps have no freshness/staleness age classification; this does not claim a "
        "complete harem. This factual shortlist is not an expected-value recommendation."
    ) in result.stdout


@pytest.mark.parametrize(
    ("wishlist", "expected_status"),
    (
        (None, "No imported $wl snapshot"),
        (SimpleNamespace(entries=()), "Not listed in observed wishlist"),
    ),
)
def test_catalog_keyfarm_cli_distinguishes_absent_and_observed_empty_wishlist(
    monkeypatch, wishlist, expected_status
) -> None:
    entry = SimpleNamespace(
        character_name="Power",
        key_type="silver",
        key_count=2,
        kakera_value=100,
        observed_at=datetime(2026, 7, 13, 0, 15, tzinfo=timezone.utc),
    )
    service = SimpleNamespace(
        harem_keys=lambda _server, _account: (entry,),
        wishlist=lambda _server, _account: wishlist,
        unavailable_characters=lambda _server, _account: (),
    )
    monkeypatch.setattr(main, "_resolve_account_context", lambda _server, _account: ("Lake", "ernieuuu"))
    monkeypatch.setattr(catalog_search_commands_module, "CatalogService", lambda: service)
    monkeypatch.setattr(main.console, "width", 240)

    result = CliRunner().invoke(
        main.app,
        ["catalog", "keyfarm", "--server", "Lake", "--account", "ernieuuu"],
    )

    assert result.exit_code == 0
    assert expected_status in result.stdout
    other_status = (
        "Not listed in observed wishlist"
        if wishlist is None
        else "No imported $wl snapshot"
    )
    assert other_status not in result.stdout


def test_catalog_show_cli_clarifies_global_and_server_evidence(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 13, 0, 15, tzinfo=timezone.utc)
    profile = SimpleNamespace(
        character=CatalogCharacter(
            id=1, name="Power", series="Chainsaw Man", gender=None, roulette=None
        ),
        claim_rank=7,
        like_rank=None,
        server_observations=(
            SimpleNamespace(server_name="Lake", kakera_value=1448, observed_at=observed_at),
            SimpleNamespace(server_name="Mountain", kakera_value=None, observed_at=observed_at),
        ),
    )
    monkeypatch.setattr(
        catalog_search_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(get_profile=lambda name, series: profile),
    )
    monkeypatch.setattr(main.console, "width", 240)

    result = CliRunner().invoke(main.app, ["catalog", "show", "Power", "--series", "Chainsaw Man"])

    assert result.exit_code == 0
    assert "Power - Chainsaw Man" in result.stdout
    assert "Claim rank:" in result.stdout
    assert "Like rank:" in result.stdout
    assert "Lake" in result.stdout
    assert "Mountain" in result.stdout
    assert "1,448:kakera:" in result.stdout
    assert "-" in result.stdout
    assert "2026-07-13 00:15" in result.stdout
    assert (
        "Claim and like ranks are the latest locally imported global snapshots; server Kakera rows "
        "are the latest locally imported observation per server. Displayed timestamps do not establish "
        "current, fresh, or stale state."
    ) in result.stdout


def test_catalog_show_cli_keeps_distinct_no_server_observations_message(monkeypatch) -> None:
    profile = SimpleNamespace(
        character=CatalogCharacter(
            id=1, name="Power", series="Chainsaw Man", gender=None, roulette=None
        ),
        claim_rank=None,
        like_rank=19,
        server_observations=(),
    )
    monkeypatch.setattr(
        catalog_search_commands_module,
        "CatalogService",
        lambda: SimpleNamespace(get_profile=lambda name, series: profile),
    )
    monkeypatch.setattr(main.console, "width", 240)

    result = CliRunner().invoke(main.app, ["catalog", "show", "Power", "--series", "Chainsaw Man"])

    assert result.exit_code == 0
    assert "No server-specific observations imported yet." in result.stdout
    assert (
        "Claim and like ranks are the latest locally imported global snapshots; server Kakera rows "
        "are the latest locally imported observation per server. Displayed timestamps do not establish "
        "current, fresh, or stale state."
    ) in result.stdout


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

    assert catalog_search_commands_module._format_rollability(entries[0].unavailable, entries[0].unavailable_reason) == (
        "Unavailable ($togglewestern)"
    )
    assert catalog_search_commands_module._format_rollability(entries[1].unavailable, entries[1].unavailable_reason) == (
        "Unavailable (disabled)"
    )
    assert catalog_search_commands_module._format_rollability(False, None, "ernieuuu", True) == "Claimed"
    assert catalog_search_commands_module._format_rollability(False, None, status="Wishlist") == "Wishlist"
    assert catalog_search_commands_module._format_rollability(False, None) == "Not observed unavailable"


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
        catalog_search_commands_module,
        "TopSearchService",
        lambda: SimpleNamespace(search=lambda **_kwargs: entries),
    )
    monkeypatch.setattr(main.console, "width", 240)

    result = CliRunner().invoke(main.app, ["catalog", "top", "--limit", "2"])

    assert result.exit_code == 0
    assert "Unknown Roulette" in result.stdout
    assert "Unknown" in result.stdout
    assert "Observed Empty" in result.stdout
    assert catalog_search_commands_module.format_mudae_roulette_types(None) == "Unknown"
    assert catalog_search_commands_module.format_mudae_roulette_types(()) == "-"


def test_catalog_top_cli_preserves_unknown_and_unavailable_precedence(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 12, 23, 45, tzinfo=timezone.utc)
    entries = (
        CatalogTopSearchEntry(
            character=CatalogCharacter(
                id=1, name="Unknown Evidence", series="Series", gender=None, roulette=None
            ),
            claim_rank=1,
            like_rank=None,
            observed_at=observed_at,
            owned=None,
            keyed=None,
            unavailable=None,
            unavailable_reason=None,
            roulette_types=None,
        ),
        CatalogTopSearchEntry(
            character=CatalogCharacter(
                id=2, name="Unavailable Evidence", series="Series", gender=None, roulette=None
            ),
            claim_rank=2,
            like_rank=None,
            observed_at=observed_at,
            owned=None,
            keyed=None,
            unavailable=True,
            unavailable_reason="$togglewestern",
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
        catalog_search_commands_module,
        "TopSearchService",
        lambda: SimpleNamespace(search=lambda **_kwargs: entries),
    )
    monkeypatch.setattr(main.console, "width", 240)

    result = CliRunner().invoke(
        main.app,
        ["catalog", "top", "--server", "Lake", "--account", "ernieuuu", "--limit", "2"],
    )

    assert result.exit_code == 0
    assert "Unknown Evidence" in result.stdout
    assert "Not requested" in result.stdout
    assert "Unavailable Evidence" in result.stdout
    assert "Unavailable ($togglewestern)" in result.stdout


def test_discord_listener_requires_a_bot_token(monkeypatch) -> None:
    monkeypatch.delenv("MOA_DISCORD_BOT_TOKEN", raising=False)

    result = CliRunner().invoke(main.app, ["discord", "listen"])

    assert result.exit_code == 1
    assert "Discord bot token missing" in result.stdout


def test_discord_listener_rejects_example_bot_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", tmp_path / "moa.db")

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

    monkeypatch.setattr(discord_commands_module, "DiscordEventCaptureService", unexpected)
    result = CliRunner().invoke(
        main.app,
        ["discord", "listen", "--token", "test-token", "--capture-guild-id", "100"],
    )

    assert result.exit_code == 1
    assert "require --capture-only" in result.stdout


def test_discord_capture_channel_option_requires_capture_only(monkeypatch) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("capture-only validation must precede service construction")

    monkeypatch.setattr(discord_commands_module, "DiscordEventCaptureService", unexpected)
    result = CliRunner().invoke(
        main.app,
        ["discord", "listen", "--token", "test-token", "--capture-channel-id", "200"],
    )

    assert result.exit_code == 1
    assert "require --capture-only" in result.stdout


def test_discord_capture_user_option_requires_capture_only(monkeypatch) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("capture-only validation must precede service construction")

    monkeypatch.setattr(discord_commands_module, "DiscordEventCaptureService", unexpected)
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
        (str(Path(discord_commands_module.__file__).resolve().parents[3] / "capture.jsonl"), "outside the repository"),
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

    monkeypatch.setattr(discord_commands_module, "DiscordEventCaptureService", unexpected)
    monkeypatch.setattr(discord_commands_module, "CatalogRepository", unexpected)
    monkeypatch.setattr(discord_commands_module, "DiscordMessageRepository", unexpected)
    monkeypatch.setattr(discord_commands_module, "AutomaticImportService", unexpected)
    monkeypatch.setattr(discord_commands_module, "DiscordListenerService", unexpected)

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

    monkeypatch.setattr(discord_commands_module, "DiscordEventCaptureService", unexpected)
    monkeypatch.setattr(discord_commands_module, "CatalogRepository", unexpected)
    monkeypatch.setattr(discord_commands_module, "DiscordMessageRepository", unexpected)
    monkeypatch.setattr(discord_commands_module, "AutomaticImportService", unexpected)
    monkeypatch.setattr(discord_commands_module, "DiscordListenerService", unexpected)

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

    monkeypatch.setattr(discord_commands_module, "DiscordEventCaptureService", FakeCaptureService)
    monkeypatch.setattr(discord_commands_module, "CatalogRepository", unexpected)
    monkeypatch.setattr(discord_commands_module, "DiscordMessageRepository", unexpected)
    monkeypatch.setattr(discord_commands_module, "AutomaticImportService", unexpected)
    monkeypatch.setattr(discord_commands_module, "DiscordListenerService", unexpected)

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

    monkeypatch.setattr(discord_commands_module, "DiscordEventCaptureService", FakeCaptureService)
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

    monkeypatch.setattr(discord_commands_module, "DiscordListenerService", FakeListener)
    monkeypatch.setattr(discord_commands_module, "CatalogRepository", RecordingCatalogRepository)
    monkeypatch.setattr(discord_commands_module, "DiscordMessageRepository", RecordingDiscordMessageRepository)
    monkeypatch.setattr(discord_commands_module, "AutomaticImportService", RecordingAutomaticImportService)
    monkeypatch.setattr(
        discord_commands_module,
        "KakeraStateProjectionCoordinator",
        RecordingKakeraStateProjectionCoordinator,
    )
    monkeypatch.setattr(
        discord_commands_module,
        "KakeralootStateProjectionCoordinator",
        RecordingKakeralootStateProjectionCoordinator,
    )
    monkeypatch.setattr(discord_commands_module, "TimerProjectionCoordinator", RecordingTimerProjectionCoordinator)
    monkeypatch.setattr(
        discord_commands_module,
        "TowerStateProjectionCoordinator",
        RecordingTowerStateProjectionCoordinator,
    )
    monkeypatch.setattr(
        discord_commands_module,
        "SphereResultProjectionCoordinator",
        RecordingSphereResultProjectionCoordinator,
    )
    monkeypatch.setattr(
        discord_commands_module,
        "PlayerBonusProjectionCoordinator",
        RecordingPlayerBonusProjectionCoordinator,
    )
    monkeypatch.setattr(
        discord_commands_module,
        "DisableListProjectionCoordinator",
        RecordingDisableListProjectionCoordinator,
    )
    monkeypatch.setattr(
        discord_commands_module,
        "WishlistProjectionCoordinator",
        RecordingWishlistProjectionCoordinator,
    )
    monkeypatch.setattr(
        discord_commands_module,
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

    monkeypatch.setattr(import_workflow_commands_module, "AutomaticImportService", RecordingImporter)
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

    monkeypatch.setattr(import_workflow_commands_module, "AutomaticImportService", RecordingImporter)
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

    monkeypatch.setattr(import_workflow_commands_module, "AutomaticImportService", RecordingImporter)
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

    monkeypatch.setattr(import_workflow_commands_module, "AutomaticImportService", RecordingImporter)
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

    monkeypatch.setattr(import_workflow_commands_module, "AutomaticImportService", RecordingImporter)
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

    monkeypatch.setattr(import_workflow_commands_module, "AutomaticImportService", RecordingImporter)
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


def test_import_cli_characterizes_registration_laziness_and_late_bound_reader(monkeypatch) -> None:
    root_command = get_command(main.app)
    import_command = root_command.commands["import"]
    assert set(import_command.commands) == {
        "auto",
        "reaction",
        "top",
        "im",
        "mm",
        "mmr",
        "bonus",
        "wishlist",
        "adl",
        "disablelist",
        "topx",
        "kakera",
        "personalrare",
        "timers",
        "towerstate",
        "lootstate",
        "infokl",
        "settings",
    }

    constructions: list[str] = []

    def unexpected(name):
        def constructor(*_args, **_kwargs):
            constructions.append(name)
            raise AssertionError(f"Import help must not construct {name}")

        return constructor

    with monkeypatch.context() as help_monkeypatch:
        help_monkeypatch.setattr(MudaeTextParser, "__init__", unexpected("parser"))
        help_monkeypatch.setattr(CatalogService, "__init__", unexpected("catalog"))
        help_monkeypatch.setattr(
            AutomaticImportService,
            "__init__",
            unexpected("automatic importer"),
        )
        help_result = CliRunner().invoke(main.app, ["import", "--help"])

    assert help_result.exit_code == 0
    assert constructions == []

    source_calls: list[tuple[Path | None, bool]] = []
    events: list[object] = []

    def patched_source(path: Path | None, clipboard: bool) -> str:
        source_calls.append((path, clipboard))
        events.append("source")
        return "player bonuses"

    def parser_init(_self) -> None:
        events.append("parser-init")

    def parse_bonus(_self, raw_message: str):
        events.append(("parse", raw_message))
        return SimpleNamespace(metrics={"bonus": 1})

    def catalog_init(_self) -> None:
        events.append("catalog-init")

    def import_bonus(_self, *args):
        events.append(("write", args))
        return SimpleNamespace(account_name="ernieuuu")

    monkeypatch.setattr(main, "_read_message_source", patched_source)
    monkeypatch.setattr(MudaeTextParser, "__init__", parser_init)
    monkeypatch.setattr(MudaeTextParser, "parse_player_bonus", parse_bonus)
    monkeypatch.setattr(CatalogService, "__init__", catalog_init)
    monkeypatch.setattr(CatalogService, "import_player_bonus", import_bonus)

    result = CliRunner().invoke(
        main.app,
        ["import", "bonus", "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert result.exit_code == 0
    assert source_calls == [(None, True)]
    assert events == [
        "source",
        "parser-init",
        ("parse", "player bonuses"),
        "catalog-init",
        (
            "write",
            (SimpleNamespace(metrics={"bonus": 1}), "Lake", "ernieuuu", "player bonuses", "clipboard"),
        ),
    ]


def test_import_direct_family_is_exact_and_flat() -> None:
    root_command = get_command(main.app)
    import_commands = root_command.commands["import"].commands
    direct_commands = {
        "reaction",
        "im",
        "bonus",
        "wishlist",
        "disablelist",
        "topx",
        "kakera",
        "personalrare",
        "timers",
        "towerstate",
        "lootstate",
        "infokl",
        "settings",
    }
    excluded_commands = {"auto", "top", "mm", "mmr", "adl"}

    assert set(import_commands) == direct_commands | excluded_commands
    assert set(import_commands) - direct_commands == excluded_commands
    assert "direct" not in import_commands


def test_import_workflow_family_is_exact_and_movable() -> None:
    root_command = get_command(main.app)
    import_commands = root_command.commands["import"].commands
    workflow_commands = {"auto", "top", "mm", "mmr", "adl"}
    direct_commands = {
        "reaction",
        "im",
        "bonus",
        "wishlist",
        "disablelist",
        "topx",
        "kakera",
        "personalrare",
        "timers",
        "towerstate",
        "lootstate",
        "infokl",
        "settings",
    }
    assert set(import_commands) == workflow_commands | direct_commands
    assert set(import_commands) - direct_commands == workflow_commands

    expected_names = {
        "auto": {"AutomaticImportService"},
        "top": {"MudaeTextParser", "CatalogService", "parse_top_page", "import_top_page", "character_count"},
        "mm": {"HaremImportDispatcher", "require_harem_import", "MudaeParseError"},
        "mmr": {"HaremImportDispatcher", "require_harem_import", "MudaeParseError"},
        "adl": {"MudaeTextParser", "CatalogService", "parse_antidisable_page", "import_antidisable_page"},
    }
    for command, names in expected_names.items():
        callback_names = import_commands[command].callback.__wrapped__.__code__.co_names
        assert names <= set(callback_names)
        assert not any("ProjectionCoordinator" in value for value in callback_names)


def test_import_mm_family_is_exact_flat_and_schema_stable() -> None:
    import_commands = get_command(main.app).commands["import"].commands
    family = {"mm", "mmr"}

    assert family <= set(import_commands)
    assert {"adl", "auto", "top"} <= set(import_commands) - family
    assert "mmfamily" not in import_commands
    assert "scan" not in import_commands

    expected_parameters = [
        ("server", ("--server", "-s"), None, True, "str"),
        ("account", ("--account", "-a"), None, True, "str"),
        ("scan", ("--scan",), None, False, "int"),
        ("path", ("path",), None, False, "path"),
        ("clipboard", ("--clipboard", "-c"), False, False, "boolean"),
    ]
    for command in family:
        parameters = import_commands[command].params
        assert [
            (parameter.name, tuple(parameter.opts), parameter.default, parameter.required, parameter.type.name)
            for parameter in parameters
        ] == expected_parameters

        callback_names = import_commands[command].callback.__wrapped__.__code__.co_names
        assert {"HaremImportDispatcher", "require_harem_import", "MudaeParseError"} <= set(callback_names)
        assert not {
            "parse_harem_key_page",
            "parse_ranked_harem_page",
            "import_harem_key_page",
            "import_ranked_harem_page",
        } & set(callback_names)
        assert not any(
            forbidden in callback_names
            for forbidden in (
                "HaremRepository",
                "run_write_transaction",
                "begin_harem_scan",
                "complete_harem_scan",
            )
        )


@pytest.mark.parametrize(
    ("command", "parser_method", "service_method", "rendered_label"),
    [
        ("mm", "parse_harem_key_page", "import_harem_key_page", "keyed harem entries"),
        ("mmr", "parse_ranked_harem_page", "import_ranked_harem_page", "owned harem entries"),
    ],
)
def test_import_mm_family_preserves_late_bound_source_orchestration_and_variant_rendering(
    monkeypatch, command, parser_method, service_method, rendered_label
) -> None:
    raw_message = f"{command} raw response"
    parsed = SimpleNamespace()
    events: list[object] = []
    parser_calls: list[str] = []
    writes: list[tuple[object, ...]] = []

    def patched_source(path, clipboard):
        events.append(("source", path, clipboard))
        return raw_message

    def parser_init(_self) -> None:
        events.append("parser-init")

    def parse_page(_self, text):
        events.append(("parse", text))
        parser_calls.append(text)
        return parsed

    def catalog_init(_self) -> None:
        events.append("catalog-init")

    def import_page(_self, *values):
        events.append(("write", values))
        writes.append(values)
        return SimpleNamespace(
            entries_imported=2,
            entries_linked=1,
            account_name="ernieuuu",
            scan_id=7 if values[-1] == 7 else None,
            page_number=1 if values[-1] == 7 else None,
            page_count=2 if values[-1] == 7 else None,
        )

    monkeypatch.setattr(main, "_read_message_source", patched_source)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "__init__", parser_init)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, parser_method, parse_page)
    monkeypatch.setattr(catalog_service_module.CatalogService, "__init__", catalog_init)
    monkeypatch.setattr(catalog_service_module.CatalogService, service_method, import_page)

    runner = CliRunner()
    with_scan = runner.invoke(
        main.app,
        ["import", command, "--server", "Lake", "--account", "ernieuuu", "--scan", "7", "--clipboard"],
    )
    without_scan = runner.invoke(
        main.app,
        ["import", command, "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert with_scan.exit_code == 0
    assert without_scan.exit_code == 0
    assert parser_calls == [raw_message, raw_message]
    assert writes == [
        (parsed, "Lake", "ernieuuu", raw_message, "clipboard", 7),
        (parsed, "Lake", "ernieuuu", raw_message, "clipboard", None),
    ]
    assert events[:5] == [
        ("source", None, True),
        "parser-init",
        ("parse", raw_message),
        "catalog-init",
        ("write", writes[0]),
    ]
    assert events[5:] == [
        ("source", None, True),
        "parser-init",
        ("parse", raw_message),
        "catalog-init",
        ("write", writes[1]),
    ]
    assert f"Imported 2 {rendered_label}" in with_scan.stdout
    assert "linked to the current catalog" in with_scan.stdout
    assert "Scan 7:" in with_scan.stdout
    assert "Keep using" in with_scan.stdout
    assert f"Imported 2 {rendered_label}" in without_scan.stdout
    assert "Scan 7:" not in without_scan.stdout
    assert "Keep using" not in without_scan.stdout


@pytest.mark.parametrize(
    ("command", "parser_method", "service_method"),
    [
        ("mm", "parse_harem_key_page", "import_harem_key_page"),
        ("mmr", "parse_ranked_harem_page", "import_ranked_harem_page"),
    ],
)
def test_import_mm_family_preserves_parser_and_service_error_boundaries(
    monkeypatch, command, parser_method, service_method
) -> None:
    parser_calls: list[str] = []
    raw_message = f"malformed {command} response"

    monkeypatch.setattr(main, "_read_message_source", lambda *_: raw_message)
    monkeypatch.setattr(mudae_parser_module.MudaeTextParser, "__init__", lambda _self: None)
    monkeypatch.setattr(
        mudae_parser_module.MudaeTextParser,
        parser_method,
        lambda _self, text: parser_calls.append(text)
        or (_ for _ in ()).throw(mudae_parser_module.MudaeParseError(raw_message)),
    )
    monkeypatch.setattr(
        catalog_service_module.CatalogService,
        "__init__",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("CatalogService must not construct after parser failure")
        ),
    )

    parser_failure = CliRunner().invoke(
        main.app,
        ["import", command, "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert parser_failure.exit_code == 1
    assert parser_calls == [raw_message]
    assert raw_message in parser_failure.stdout
    assert isinstance(parser_failure.exception, SystemExit)

    monkeypatch.setattr(catalog_service_module.CatalogService, "__init__", lambda _self: None)
    monkeypatch.setattr(
        mudae_parser_module.MudaeTextParser,
        parser_method,
        lambda _self, _text: SimpleNamespace(),
    )
    monkeypatch.setattr(
        catalog_service_module.CatalogService,
        service_method,
        lambda _self, *_args: (_ for _ in ()).throw(ValueError("invalid scan")),
    )

    service_failure = CliRunner().invoke(
        main.app,
        ["import", command, "--server", "Lake", "--account", "ernieuuu", "--scan", "7", "--clipboard"],
    )

    assert service_failure.exit_code == 1
    assert "Imported" not in service_failure.stdout
    if command == "mm":
        assert "invalid scan" in service_failure.stdout
        assert isinstance(service_failure.exception, SystemExit)
    else:
        assert "invalid scan" not in service_failure.stdout
        assert isinstance(service_failure.exception, ValueError)


@pytest.mark.parametrize(
    ("command", "parameters", "parser_method", "catalog_method"),
    [
        (
            "reaction",
            (
                ("server", ("--server", "-s"), None, True),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_kakera_reaction_receipt",
            "import_kakera_reaction",
        ),
        (
            "im",
            (
                ("server", ("--server", "-s"), None, True),
                ("account", ("--account", "-a"), None, False),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_character_details",
            "import_character_details",
        ),
        (
            "bonus",
            (
                ("server", ("--server", "-s"), None, True),
                ("account", ("--account", "-a"), None, True),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_player_bonus",
            "import_player_bonus",
        ),
        (
            "wishlist",
            (
                ("server", ("--server", "-s"), None, True),
                ("account", ("--account", "-a"), None, True),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_wishlist",
            "import_wishlist",
        ),
        (
            "disablelist",
            (
                ("server", ("--server", "-s"), None, True),
                ("account", ("--account", "-a"), None, True),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_disablelist",
            "import_disablelist",
        ),
        (
            "topx",
            (
                ("server", ("--server", "-s"), None, True),
                ("account", ("--account", "-a"), None, True),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_unavailable_characters",
            "import_unavailable_characters",
        ),
        (
            "kakera",
            (
                ("server", ("--server", "-s"), None, True),
                ("account", ("--account", "-a"), None, True),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_kakera_state",
            "import_kakera_state",
        ),
        (
            "personalrare",
            (
                ("server", ("--server", "-s"), None, True),
                ("account", ("--account", "-a"), None, True),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_personal_rare",
            "import_personal_rare",
        ),
        (
            "timers",
            (
                ("server", ("--server", "-s"), None, True),
                ("account", ("--account", "-a"), None, True),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_timer_state",
            "import_timer_state",
        ),
        (
            "towerstate",
            (
                ("server", ("--server", "-s"), None, True),
                ("account", ("--account", "-a"), None, True),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_tower_state",
            "import_tower_state",
        ),
        (
            "lootstate",
            (
                ("server", ("--server", "-s"), None, True),
                ("account", ("--account", "-a"), None, True),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_kakeraloot_state",
            "import_kakeraloot_state",
        ),
        (
            "infokl",
            (
                ("server", ("--server", "-s"), None, True),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_kakeraloot_settings",
            "import_kakeraloot_settings",
        ),
        (
            "settings",
            (
                ("server", ("--server", "-s"), None, True),
                ("path", ("path",), None, False),
                ("clipboard", ("--clipboard", "-c"), False, False),
            ),
            "parse_server_settings",
            "import_server_settings",
        ),
    ],
)
def test_import_direct_family_schema_and_movable_ownership(
    command, parameters, parser_method, catalog_method
) -> None:
    import_command = get_command(main.app).commands["import"].commands[command]

    assert [
        (parameter.name, tuple(parameter.opts), parameter.default, parameter.required)
        for parameter in import_command.params
    ] == list(parameters)

    callback_names = import_command.callback.__wrapped__.__code__.co_names
    assert parser_method in callback_names
    assert catalog_method in callback_names
    assert not any("ProjectionCoordinator" in value for value in callback_names)
    assert "AutomaticImportService" not in callback_names
    assert "DiscordListenerService" not in callback_names


def test_import_simple_direct_callbacks_use_movable_parser_catalog_seams_and_no_coordinators(
    monkeypatch,
) -> None:
    root_command = get_command(main.app)
    import_commands = root_command.commands["import"].commands
    direct_commands = {
        "reaction",
        "im",
        "bonus",
        "wishlist",
        "disablelist",
        "topx",
        "kakera",
        "personalrare",
        "timers",
        "towerstate",
        "lootstate",
        "infokl",
        "settings",
    }
    for name in direct_commands:
        names = import_commands[name].callback.__wrapped__.__code__.co_names
        assert not any("ProjectionCoordinator" in value for value in names)
        assert "DiscordListenerService" not in names

    events: list[str] = []

    monkeypatch.setattr(main, "_read_message_source", lambda *_: "bonus source")
    monkeypatch.setattr(MudaeTextParser, "__init__", lambda _self: events.append("parser-init"))
    monkeypatch.setattr(
        MudaeTextParser,
        "parse_player_bonus",
        lambda _self, raw_message: events.append(f"parse:{raw_message}")
        or SimpleNamespace(metrics={}),
    )
    monkeypatch.setattr(CatalogService, "__init__", lambda _self: events.append("catalog-init"))
    monkeypatch.setattr(
        CatalogService,
        "import_player_bonus",
        lambda _self, *_args: events.append("write") or SimpleNamespace(account_name="ernieuuu"),
    )

    result = CliRunner().invoke(
        main.app,
        ["import", "bonus", "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert result.exit_code == 0
    assert events == ["parser-init", "parse:bonus source", "catalog-init", "write"]
    assert "Imported 0 player bonus metrics" in result.stdout


def test_import_simple_direct_parser_failure_prevents_catalog_construction(monkeypatch) -> None:
    parser_calls: list[str] = []

    monkeypatch.setattr(main, "_read_message_source", lambda *_: "malformed bonus")
    monkeypatch.setattr(
        MudaeTextParser,
        "parse_player_bonus",
        lambda _self, raw_message: parser_calls.append(raw_message)
        or (_ for _ in ()).throw(mudae_parser_module.MudaeParseError("malformed bonus")),
    )
    monkeypatch.setattr(
        CatalogService,
        "__init__",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("CatalogService must not construct after parser failure")
        ),
    )

    result = CliRunner().invoke(
        main.app,
        ["import", "bonus", "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert result.exit_code == 1
    assert parser_calls == ["malformed bonus"]
    assert "malformed bonus" in result.stdout


def test_import_auto_uses_automatic_service_routing_without_cli_coordinators(monkeypatch) -> None:
    events: list[str] = []
    auto_callback = get_command(main.app).commands["import"].commands["auto"].callback.__wrapped__
    assert "AutomaticImportService" in auto_callback.__code__.co_names
    assert not any("ProjectionCoordinator" in value for value in auto_callback.__code__.co_names)

    monkeypatch.setattr(main, "_read_message_source", lambda *_: events.append("source") or "auto source")
    monkeypatch.setattr(
        AutomaticImportService,
        "__init__",
        lambda _self: events.append("automatic-init"),
    )
    monkeypatch.setattr(
        AutomaticImportService,
        "import_message",
        lambda _self, *args, **kwargs: events.append("automatic-import")
        or SimpleNamespace(kind="bonus", imported_count=1, message="Imported player bonuses."),
    )

    result = CliRunner().invoke(
        main.app,
        ["import", "auto", "--server", "Lake", "--account", "ernieuuu", "--clipboard"],
    )

    assert result.exit_code == 0
    assert events == ["source", "automatic-init", "automatic-import"]
    assert "Detected bonus and imported 1 item(s)." in result.stdout


def test_import_top_preserves_post_write_read_and_domain_error_boundary(monkeypatch) -> None:
    events: list[str] = []
    page = SimpleNamespace()

    monkeypatch.setattr(main, "_read_message_source", lambda *_: events.append("source") or "top source")
    monkeypatch.setattr(MudaeTextParser, "__init__", lambda _self: events.append("parser-init"))
    monkeypatch.setattr(
        MudaeTextParser,
        "parse_top_page",
        lambda _self, raw_message: events.append(f"parse:{raw_message}") or page,
    )
    monkeypatch.setattr(CatalogService, "__init__", lambda _self: events.append("catalog-init"))
    monkeypatch.setattr(
        CatalogService,
        "import_top_page",
        lambda _self, *_args: events.append("write") or SimpleNamespace(characters_imported=3),
    )
    monkeypatch.setattr(CatalogService, "character_count", lambda _self: events.append("count") or 42)

    success = CliRunner().invoke(main.app, ["import", "top", "--clipboard"])

    assert success.exit_code == 0
    assert events == ["source", "parser-init", "parse:top source", "catalog-init", "write", "catalog-init", "count"]
    assert "Imported 3 ranked characters." in success.stdout
    assert "Catalog now contains 42 characters." in success.stdout

    events.clear()
    monkeypatch.setattr(
        CatalogService,
        "import_top_page",
        lambda _self, *_args: (_ for _ in ()).throw(ValueError("invalid top context")),
    )
    monkeypatch.setattr(
        CatalogService,
        "character_count",
        lambda _self: (_ for _ in ()).throw(AssertionError("count must not run after failed write")),
    )

    failure = CliRunner().invoke(main.app, ["import", "top", "--clipboard"])

    assert failure.exit_code == 1
    assert events == ["source", "parser-init", "parse:top source", "catalog-init"]
    assert "invalid top context" in failure.stdout


@pytest.mark.parametrize(
    ("command", "parser_method", "service_method", "arguments", "parsed", "result"),
    [
        (
            "mm",
            "parse_harem_key_page",
            "import_harem_key_page",
            ["--server", "Lake", "--account", "ernieuuu", "--scan", "7", "--clipboard"],
            SimpleNamespace(),
            SimpleNamespace(
                entries_imported=2,
                entries_linked=1,
                account_name="ernieuuu",
                scan_id=7,
                page_number=1,
                page_count=2,
            ),
        ),
        (
            "mmr",
            "parse_ranked_harem_page",
            "import_ranked_harem_page",
            ["--server", "Lake", "--account", "ernieuuu", "--scan", "7", "--clipboard"],
            SimpleNamespace(),
            SimpleNamespace(
                entries_imported=2,
                entries_linked=1,
                account_name="ernieuuu",
                scan_id=7,
                page_number=1,
                page_count=2,
            ),
        ),
        (
            "adl",
            "parse_antidisable_page",
            "import_antidisable_page",
            ["--server", "Lake", "--account", "ernieuuu", "--scan", "7", "--clipboard"],
            SimpleNamespace(antidisabled_character_count=None),
            SimpleNamespace(
                series_imported=2,
                account_name="ernieuuu",
                scan_id=7,
                page_number=1,
                page_count=2,
            ),
        ),
    ],
)
def test_import_scan_linked_callbacks_preserve_parser_write_order_and_scan_forwarding(
    monkeypatch,
    command,
    parser_method,
    service_method,
    arguments,
    parsed,
    result,
) -> None:
    events: list[object] = []

    monkeypatch.setattr(main, "_read_message_source", lambda *_: events.append("source") or f"{command} source")
    monkeypatch.setattr(MudaeTextParser, "__init__", lambda _self: events.append("parser-init"))
    monkeypatch.setattr(
        MudaeTextParser,
        parser_method,
        lambda _self, raw_message: events.append(("parse", raw_message)) or parsed,
    )
    monkeypatch.setattr(CatalogService, "__init__", lambda _self: events.append("catalog-init"))
    monkeypatch.setattr(
        CatalogService,
        service_method,
        lambda _self, *values: events.append(("write", values)) or result,
    )

    invocation = CliRunner().invoke(main.app, ["import", command, *arguments])

    assert invocation.exit_code == 0
    assert events[:4] == ["source", "parser-init", ("parse", f"{command} source"), "catalog-init"]
    assert events[4][0] == "write"
    assert events[4][1][-1] == 7
    assert "Scan 7:" in invocation.stdout


@pytest.mark.parametrize(
    ("command", "parser_method", "service_method", "arguments", "caught"),
    [
        (
            "mm",
            "parse_harem_key_page",
            "import_harem_key_page",
            ["--server", "Lake", "--account", "ernieuuu", "--scan", "7", "--clipboard"],
            True,
        ),
        (
            "mmr",
            "parse_ranked_harem_page",
            "import_ranked_harem_page",
            ["--server", "Lake", "--account", "ernieuuu", "--scan", "7", "--clipboard"],
            False,
        ),
        (
            "adl",
            "parse_antidisable_page",
            "import_antidisable_page",
            ["--server", "Lake", "--account", "ernieuuu", "--scan", "7", "--clipboard"],
            True,
        ),
    ],
)
def test_import_scan_linked_callbacks_preserve_current_value_error_boundaries(
    monkeypatch,
    command,
    parser_method,
    service_method,
    arguments,
    caught,
) -> None:
    monkeypatch.setattr(main, "_read_message_source", lambda *_: f"{command} source")
    monkeypatch.setattr(MudaeTextParser, parser_method, lambda _self, _raw_message: SimpleNamespace())
    monkeypatch.setattr(CatalogService, "__init__", lambda _self: None)
    monkeypatch.setattr(
        CatalogService,
        service_method,
        lambda _self, *_args: (_ for _ in ()).throw(ValueError("invalid scan")),
    )

    result = CliRunner().invoke(main.app, ["import", command, *arguments])

    assert result.exit_code == 1
    if caught:
        assert "invalid scan" in result.stdout
        assert isinstance(result.exception, SystemExit)
    else:
        assert "invalid scan" not in result.stdout
        assert isinstance(result.exception, ValueError)


def test_catalog_keys_display_uses_mudae_key_marker_and_count() -> None:
    assert catalog_search_commands_module._format_catalog_keys(True, "gold", 7) == ":goldkey: (7)"
    assert catalog_search_commands_module._format_catalog_keys(True, "Gold Key", 7) == ":goldkey: (7)"
    assert catalog_search_commands_module._format_catalog_keys(False, None, None) == "-"
    assert catalog_search_commands_module._format_catalog_keys(None, None, None) == "Not requested"


def test_catalog_reset_registration_and_help_are_lazy(monkeypatch) -> None:
    calls: list[int] = []

    def database_path_provider():
        calls.append(1)
        return Path("unused.db")

    catalog_app = typer.Typer()
    console = Console()
    catalog_reset_commands_module.register_catalog_reset_command(
        catalog_app,
        console,
        database_path_provider,
    )

    result = CliRunner().invoke(catalog_app, ["reset", "--help"])

    assert result.exit_code == 0
    assert "Reset imported catalog data" in result.stdout
    assert calls == []


def test_catalog_reset_defaults_to_dry_run_without_mutation(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    database_path.write_bytes(b"catalog\x00bytes")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "reset"])

    assert result.exit_code == 0
    assert "No changes made" in result.stdout
    assert database_path.read_bytes() == b"catalog\x00bytes"
    assert list(tmp_path.glob("moa.db.bak-full-reset-*")) == []


def test_catalog_reset_confirmed_missing_path_does_not_create_or_backup(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "missing.db"
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "reset", "--confirm"])

    assert result.exit_code == 0
    assert "No catalog database exists" in result.stdout
    assert not database_path.exists()
    assert list(tmp_path.glob("missing.db.bak-full-reset-*")) == []


def test_catalog_reset_backs_up_bytes_before_unlink_and_uses_collision_suffix(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "moa.db"
    database_path.write_bytes(b"catalog\x00bytes")
    timestamp = "20260828-120000"
    first_backup = tmp_path / f"moa.db.bak-full-reset-{timestamp}"
    first_backup.write_bytes(b"existing-backup")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    class FixedNow:
        def strftime(self, _format):
            return timestamp

    class FixedDateTime:
        @staticmethod
        def now():
            return FixedNow()

    monkeypatch.setattr(catalog_reset_commands_module, "datetime", FixedDateTime)
    operations: list[str] = []
    original_copy2 = catalog_reset_commands_module.shutil.copy2

    def copy2(source, destination):
        operations.append("backup")
        result = original_copy2(source, destination)
        assert source.read_bytes() == b"catalog\x00bytes"
        return result

    original_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == database_path:
            operations.append("unlink")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(catalog_reset_commands_module.shutil, "copy2", copy2)
    monkeypatch.setattr(Path, "unlink", unlink)

    result = CliRunner().invoke(main.app, ["catalog", "reset", "--confirm"])

    second_backup = tmp_path / f"moa.db.bak-full-reset-{timestamp}-1"
    assert result.exit_code == 0
    assert operations == ["backup", "unlink"]
    assert not database_path.exists()
    assert first_backup.read_bytes() == b"existing-backup"
    assert second_backup.read_bytes() == b"catalog\x00bytes"


def test_catalog_reset_uses_main_database_path_at_callback_time(monkeypatch, tmp_path) -> None:
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    second_path.write_bytes(b"callback-time")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", first_path)

    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", second_path)
    result = CliRunner().invoke(main.app, ["catalog", "reset", "--confirm"])

    assert result.exit_code == 0
    assert not second_path.exists()
    assert first_path.exists() is False
    assert len(list(tmp_path.glob("second.db.bak-full-reset-*"))) == 1


def test_catalog_relocate_database_help_is_lazy_and_requires_source_and_apply() -> None:
    calls: list[int] = []

    def target_path_provider():
        calls.append(1)
        return Path("unused.db")

    catalog_app = typer.Typer()

    @catalog_app.command("sentinel")
    def sentinel() -> None:
        pass

    catalog_relocate_database_commands_module.register_catalog_relocate_database_command(
        catalog_app,
        Console(),
        target_path_provider,
    )

    help_result = CliRunner().invoke(catalog_app, ["relocate-database", "--help"])
    missing_source_result = CliRunner().invoke(catalog_app, ["relocate-database", "--apply"])

    assert help_result.exit_code == 0
    assert "SOURCE" in help_result.stdout
    assert "--apply" in help_result.stdout
    assert missing_source_result.exit_code == 2
    assert "SOURCE" in missing_source_result.stderr
    assert calls == []


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


def test_catalog_repair_registration_and_help_are_lazy(monkeypatch) -> None:
    calls: list[str] = []

    def database_path_provider():
        calls.append("database-path")
        return Path("unused.db")

    catalog_app = typer.Typer()
    catalog_repair_bugged_data_commands_module.register_catalog_repair_bugged_data_command(
        catalog_app,
        Console(),
        database_path_provider,
    )

    command = get_command(catalog_app)
    apply_option = next(param for param in command.params if param.name == "apply")
    assert apply_option.name == "apply"
    assert tuple(apply_option.opts) == ("--apply",)
    assert apply_option.default is False
    assert not apply_option.required

    result = CliRunner().invoke(catalog_app, ["repair-bugged-data", "--help"])

    assert result.exit_code == 0
    assert "Remove known timer-as-roll imports" in result.stdout
    assert calls == []


def test_catalog_repair_defaults_to_dry_run_and_constructs_service_before_inspection(
    monkeypatch,
) -> None:
    events: list[str] = []

    class RecordingCatalogService:
        def __init__(self):
            events.append("construct")

        def inspect_bugged_imports(self):
            events.append("inspect")
            return 2, 3

        def repair_bugged_imports(self):
            raise AssertionError("dry run must not repair")

    monkeypatch.setattr(
        catalog_repair_bugged_data_commands_module,
        "CatalogService",
        RecordingCatalogService,
    )
    monkeypatch.setattr(
        catalog_repair_bugged_data_commands_module,
        "shutil",
        SimpleNamespace(copy2=lambda *_args: (_ for _ in ()).throw(AssertionError("no backup"))),
    )

    result = CliRunner().invoke(main.app, ["catalog", "repair-bugged-data"])

    assert result.exit_code == 0
    assert events == ["construct", "inspect"]
    assert "Found 2 suspicious import event(s) and 3 suspicious character row(s)." in result.stdout
    assert "Dry run only; no database changes were made." in result.stdout


def test_catalog_repair_zero_candidates_does_not_resolve_path_or_repair(monkeypatch) -> None:
    events: list[str] = []

    class EmptyCatalogService:
        def inspect_bugged_imports(self):
            events.append("inspect")
            return 0, 0

        def repair_bugged_imports(self):
            raise AssertionError("zero candidates must not repair")

    monkeypatch.setattr(
        catalog_repair_bugged_data_commands_module,
        "CatalogService",
        EmptyCatalogService,
    )

    def database_path_provider():
        events.append("database-path")
        raise AssertionError("zero candidates must not resolve the database path")

    catalog_app = typer.Typer()
    catalog_repair_bugged_data_commands_module.register_catalog_repair_bugged_data_command(
        catalog_app,
        Console(),
        database_path_provider,
    )

    result = CliRunner().invoke(catalog_app, ["--apply"])

    assert result.exit_code == 0
    assert events == ["inspect"]
    assert "No targeted bugged data was found; nothing changed." in result.stdout


def test_catalog_repair_backs_up_before_repair_and_uses_collision_suffix(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "user-data" / "moa.db"
    database_path.parent.mkdir()
    database_path.write_bytes(b"catalog-bytes")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)
    timestamp = "20260828-120000"
    first_backup = database_path.with_name(f"moa.db.bak-{timestamp}")
    first_backup.write_bytes(b"existing-backup")

    class RecordingCatalogService:
        def inspect_bugged_imports(self):
            return 1, 0

        def repair_bugged_imports(self):
            assert first_backup.read_bytes() == b"existing-backup"
            assert (database_path.parent / f"moa.db.bak-{timestamp}-1").read_bytes() == b"catalog-bytes"
            return 1, 0

    class FixedNow:
        def strftime(self, _format):
            return timestamp

    class FixedDateTime:
        @staticmethod
        def now():
            return FixedNow()

    monkeypatch.setattr(catalog_repair_bugged_data_commands_module, "CatalogService", RecordingCatalogService)
    monkeypatch.setattr(catalog_repair_bugged_data_commands_module, "datetime", FixedDateTime)

    result = CliRunner().invoke(main.app, ["catalog", "repair-bugged-data", "--apply"])

    assert result.exit_code == 0
    assert database_path.read_bytes() == b"catalog-bytes"
    collision_backup = database_path.parent / f"moa.db.bak-{timestamp}-1"
    assert collision_backup.read_bytes() == b"catalog-bytes"
    assert "Cleaned 1 suspicious import event(s)" in result.stdout
    assert "Deleted 0 orphaned character row(s)." in result.stdout
    assert f"Backup saved to: {collision_backup}" in result.stdout.replace("\n", "")


def test_catalog_repair_uses_main_database_path_at_callback_time(monkeypatch, tmp_path) -> None:
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    second_path.write_bytes(b"callback-time")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", first_path)
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", second_path)

    class RecordingCatalogService:
        def inspect_bugged_imports(self):
            return 1, 0

        def repair_bugged_imports(self):
            return 1, 0

    monkeypatch.setattr(catalog_repair_bugged_data_commands_module, "CatalogService", RecordingCatalogService)

    result = CliRunner().invoke(main.app, ["catalog", "repair-bugged-data", "--apply"])

    assert result.exit_code == 0
    assert second_path.exists()
    assert len(list(tmp_path.glob("second.db.bak-*"))) == 1
    assert not first_path.exists()


def test_catalog_repair_propagates_copy_error(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    database_path.write_bytes(b"catalog")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    class RecordingCatalogService:
        def inspect_bugged_imports(self):
            return 1, 0

    monkeypatch.setattr(catalog_repair_bugged_data_commands_module, "CatalogService", RecordingCatalogService)
    copy_error = OSError("forced copy failure")
    monkeypatch.setattr(
        catalog_repair_bugged_data_commands_module.shutil,
        "copy2",
        lambda *_args: (_ for _ in ()).throw(copy_error),
    )

    result = CliRunner().invoke(
        main.app,
        ["catalog", "repair-bugged-data", "--apply"],
        catch_exceptions=True,
    )

    assert result.exit_code == 1
    assert result.exception is copy_error
    assert result.stdout == ""


def test_catalog_repair_propagates_repair_error(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    database_path.write_bytes(b"catalog")
    repair_error = RuntimeError("forced repair failure")

    class RecordingCatalogService:
        def inspect_bugged_imports(self):
            return 1, 0

        def repair_bugged_imports(self):
            raise repair_error

    monkeypatch.setattr(catalog_repair_bugged_data_commands_module, "CatalogService", RecordingCatalogService)
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(
        main.app,
        ["catalog", "repair-bugged-data", "--apply"],
        catch_exceptions=True,
    )

    assert result.exit_code == 1
    assert result.exception is repair_error
    assert list(tmp_path.glob("moa.db.bak-*"))


def test_catalog_delete_import_help_is_lazy_and_constructs_service_at_callback_time(monkeypatch) -> None:
    constructions: list[int] = []

    class RecordingCatalogService:
        def __init__(self):
            constructions.append(1)

        def delete_import_event(self, import_event_id):
            assert import_event_id == 42
            return True

    monkeypatch.setattr(catalog_delete_import_commands_module, "CatalogService", RecordingCatalogService)
    runner = CliRunner()

    help_result = runner.invoke(main.app, ["catalog", "delete-import", "--help"])

    assert help_result.exit_code == 0
    assert "Delete one mistaken import" in help_result.stdout
    assert "--confirm" in help_result.stdout
    assert constructions == []

    result = runner.invoke(main.app, ["catalog", "delete-import", "42", "--confirm"])

    assert result.exit_code == 0
    assert constructions == [1]
    assert "Deleted import event 42." in result.stdout


def test_catalog_delete_import_declines_without_confirmation_before_service_or_mutation(
    monkeypatch,
) -> None:
    constructions: list[int] = []
    deletions: list[int] = []

    class UnexpectedCatalogService:
        def __init__(self):
            constructions.append(1)

        def delete_import_event(self, import_event_id):
            deletions.append(import_event_id)
            return True

    monkeypatch.setattr(catalog_delete_import_commands_module, "CatalogService", UnexpectedCatalogService)

    result = CliRunner().invoke(main.app, ["catalog", "delete-import", "42"])

    assert result.exit_code == 1
    output = " ".join(result.stdout.split())
    assert "Declined unsafe invocation" in output
    assert "permanently removes the selected unlinked local import event and its import-derived observations" in output
    assert "may change derived/current reports" in output
    assert "not retention expiry or privacy erasure" in output
    assert "requires --confirm to proceed" in output
    assert constructions == []
    assert deletions == []


def test_catalog_delete_import_reports_durable_source_refusal(monkeypatch) -> None:
    class BlockingCatalogService:
        def delete_import_event(self, import_event_id):
            raise catalog_delete_import_commands_module.ImportEventDeletionBlockedError(
                f"Import event {import_event_id} belongs to durable source state"
            )

    monkeypatch.setattr(catalog_delete_import_commands_module, "CatalogService", BlockingCatalogService)

    result = CliRunner().invoke(main.app, ["catalog", "delete-import", "42", "--confirm"])

    assert result.exit_code == 1
    assert "Deletion blocked" in result.stdout
    assert "durable/replayable source state" in result.stdout
    assert "FOREIGN KEY" not in result.stdout


def test_catalog_delete_import_reports_missing_event(monkeypatch) -> None:
    class MissingCatalogService:
        def delete_import_event(self, import_event_id):
            assert import_event_id == 404
            return False

    monkeypatch.setattr(catalog_delete_import_commands_module, "CatalogService", MissingCatalogService)

    result = CliRunner().invoke(main.app, ["catalog", "delete-import", "404", "--confirm"])

    assert result.exit_code == 1
    assert "Import event not found." in result.stdout


def test_catalog_delete_import_renders_successful_deletion(monkeypatch) -> None:
    class SuccessfulCatalogService:
        def delete_import_event(self, import_event_id):
            assert import_event_id == 7
            return True

    monkeypatch.setattr(catalog_delete_import_commands_module, "CatalogService", SuccessfulCatalogService)

    result = CliRunner().invoke(main.app, ["catalog", "delete-import", "7", "--confirm"])

    assert result.exit_code == 0
    assert "Deleted import event 7." in result.stdout


def test_catalog_ownership_display_distinguishes_topo_claims_from_harem_evidence() -> None:
    assert catalog_search_commands_module._format_catalog_ownership(None, "cute_beagle_91130", True, True) == (
        "Claimed 💞 => cute_beagle_91130"
    )
    assert catalog_search_commands_module._format_catalog_ownership(None, "xuppii", False, True) == (
        "Claimed 💞 => xuppii"
    )
    assert catalog_search_commands_module._format_catalog_ownership(True, None, None, False) == "Claimed"
    assert catalog_search_commands_module._format_catalog_ownership(False, None, None, True) == "Unclaimed"
    assert catalog_search_commands_module._format_catalog_ownership(None, None, None, False) == "(no data)"


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


def test_harem_cli_registration_schema_and_help_are_lazy(monkeypatch) -> None:
    constructions: list[object] = []

    def recording_init(_self) -> None:
        constructions.append(object())

    monkeypatch.setattr(catalog_service_module.CatalogService, "__init__", recording_init)
    runner = CliRunner()

    parent_help = runner.invoke(main.app, ["harem", "--help"])
    assert parent_help.exit_code == 0
    assert {line.split()[1] for line in parent_help.stdout.splitlines() if "│" in line and line.split()[1] in {"begin", "status", "complete"}} == {
        "begin",
        "status",
        "complete",
    }
    assert constructions == []

    begin_help = runner.invoke(main.app, ["harem", "begin", "--help"])
    status_help = runner.invoke(main.app, ["harem", "status", "--help"])
    complete_help = runner.invoke(main.app, ["harem", "complete", "--help"])
    assert begin_help.exit_code == status_help.exit_code == complete_help.exit_code == 0
    assert "--server" in begin_help.stdout and "-s" in begin_help.stdout
    assert "--account" in begin_help.stdout and "-a" in begin_help.stdout
    assert "--kind" in begin_help.stdout and "keys" in begin_help.stdout
    assert "Usage: root harem status [OPTIONS]" in status_help.stdout
    assert "Usage: root harem complete [OPTIONS]" in complete_help.stdout
    assert constructions == []


def test_harem_begin_cli_constructs_service_once_and_preserves_mm_mmr_guidance(
    monkeypatch,
) -> None:
    events: list[tuple[str, object]] = []

    def recording_init(_self) -> None:
        events.append(("construct", None))

    def begin(_self, server: str, account: str, scan_kind: str = "keys"):
        events.append(("begin", (server, account, scan_kind)))
        return SimpleNamespace(
            id=7,
            scan_kind=scan_kind,
            account_name=account,
            server_name=server,
        )

    monkeypatch.setattr(catalog_service_module.CatalogService, "__init__", recording_init)
    monkeypatch.setattr(catalog_service_module.CatalogService, "begin_harem_scan", begin)
    runner = CliRunner()

    default_result = runner.invoke(
        main.app,
        ["harem", "begin", "--server", "Lake", "--account", "ernieuuu"],
    )
    assert default_result.exit_code == 0
    assert events == [("construct", None), ("begin", ("Lake", "ernieuuu", "keys"))]
    assert "Started keys harem scan 7" in default_result.stdout
    assert "$mmyk" in default_result.stdout and "import mm" in default_result.stdout

    events.clear()
    owned_result = runner.invoke(
        main.app,
        [
            "harem",
            "begin",
            "--server",
            "Lake",
            "--account",
            "ernieuuu",
            "--kind",
            "owned",
        ],
    )
    assert owned_result.exit_code == 0
    assert events == [("construct", None), ("begin", ("Lake", "ernieuuu", "owned"))]
    assert "Started owned harem scan 7" in owned_result.stdout
    assert "$mmrkty+" in owned_result.stdout and "import mmr" in owned_result.stdout


def test_harem_status_and_complete_cli_preserve_lifecycle_outputs_and_errors(
    monkeypatch,
) -> None:
    events: list[tuple[str, int]] = []

    def recording_init(_self) -> None:
        pass

    def status(_self, scan_id: int):
        events.append(("status", scan_id))
        if scan_id == 99:
            return None
        return SimpleNamespace(
            id=scan_id,
            server_name="Lake",
            account_name="ernieuuu",
            expected_page_count=None if scan_id == 1 else 3,
            imported_pages=() if scan_id == 1 else (1, 2),
            completed_at=None,
        )

    def complete(_self, scan_id: int):
        events.append(("complete", scan_id))
        if scan_id == 13:
            raise ValueError("scan is not complete")
        return SimpleNamespace(
            id=scan_id,
            server_name="Lake",
            account_name="ernieuuu",
        )

    monkeypatch.setattr(catalog_service_module.CatalogService, "__init__", recording_init)
    monkeypatch.setattr(catalog_service_module.CatalogService, "harem_scan_progress", status)
    monkeypatch.setattr(catalog_service_module.CatalogService, "complete_harem_scan", complete)
    runner = CliRunner()

    unknown_pages = runner.invoke(main.app, ["harem", "status", "1"])
    assert unknown_pages.exit_code == 0
    assert "Pages: none of unknown · Status: in progress" in unknown_pages.stdout

    partial = runner.invoke(main.app, ["harem", "status", "2"])
    assert partial.exit_code == 0
    assert "Pages: 1, 2 of 3 · Status: in progress" in partial.stdout

    missing = runner.invoke(main.app, ["harem", "status", "99"])
    assert missing.exit_code == 1
    assert "Harem scan not found." in missing.stdout

    completed = runner.invoke(main.app, ["harem", "complete", "12"])
    assert completed.exit_code == 0
    assert events[-1] == ("complete", 12)
    assert "Harem scan 12 is complete and active" in completed.stdout

    failed = runner.invoke(main.app, ["harem", "complete", "13"])
    assert failed.exit_code == 1
    assert "scan is not complete" in failed.stdout
    assert "Traceback" not in failed.stdout
    assert events[-1] == ("complete", 13)


def test_adl_cli_registration_schema_and_help_are_lazy(monkeypatch) -> None:
    events: list[str] = []

    def recording_init(_self) -> None:
        events.append("construct")

    monkeypatch.setattr(catalog_service_module.CatalogService, "__init__", recording_init)
    monkeypatch.setattr(
        catalog_service_module.CatalogService,
        "begin_antidisable_scan",
        lambda *_args: events.append("begin"),
    )
    monkeypatch.setattr(
        catalog_service_module.CatalogService,
        "harem_scan_progress",
        lambda *_args: events.append("status"),
    )
    monkeypatch.setattr(
        catalog_service_module.CatalogService,
        "complete_antidisable_scan",
        lambda *_args: events.append("complete"),
    )
    runner = CliRunner()

    parent_help = runner.invoke(main.app, ["adl", "--help"])
    assert parent_help.exit_code == 0
    assert {
        line.split()[1]
        for line in parent_help.stdout.splitlines()
        if "│" in line and line.split()[1] in {"begin", "status", "complete"}
    } == {"begin", "status", "complete"}

    begin_help = runner.invoke(main.app, ["adl", "begin", "--help"])
    status_help = runner.invoke(main.app, ["adl", "status", "--help"])
    complete_help = runner.invoke(main.app, ["adl", "complete", "--help"])
    assert begin_help.exit_code == status_help.exit_code == complete_help.exit_code == 0
    assert "--server" in begin_help.stdout and "-s" in begin_help.stdout
    assert "--account" in begin_help.stdout and "-a" in begin_help.stdout
    assert "Usage: root adl status [OPTIONS] {scan_id}" in status_help.stdout
    assert "Usage: root adl complete [OPTIONS] {scan_id}" in complete_help.stdout
    assert events == []


def test_adl_begin_cli_constructs_service_once_and_preserves_workflow_guidance(
    monkeypatch,
) -> None:
    events: list[tuple[str, object]] = []

    def recording_init(_self) -> None:
        events.append(("construct", None))

    def begin(_self, server: str, account: str):
        events.append(("begin", (server, account)))
        return SimpleNamespace(id=7, account_name=account, server_name=server)

    monkeypatch.setattr(catalog_service_module.CatalogService, "__init__", recording_init)
    monkeypatch.setattr(catalog_service_module.CatalogService, "begin_antidisable_scan", begin)
    runner = CliRunner()

    result = runner.invoke(
        main.app,
        ["adl", "begin", "--server", "Lake", "--account", "ernieuuu"],
    )
    assert result.exit_code == 0
    assert events == [("construct", None), ("begin", ("Lake", "ernieuuu"))]
    assert "Started antidisable scan 7" in result.stdout
    assert "$adl" in result.stdout
    assert "import adl --scan 7" in result.stdout
    assert "--server 'Lake'" in result.stdout
    assert "--account 'ernieuuu'" in result.stdout

    events.clear()

    def fail_begin(_self, _server: str, _account: str):
        events.append(("begin", "failed"))
        raise ValueError("scan already active")

    monkeypatch.setattr(catalog_service_module.CatalogService, "begin_antidisable_scan", fail_begin)
    failed = runner.invoke(
        main.app,
        ["adl", "begin", "--server", "Lake", "--account", "ernieuuu"],
    )
    assert failed.exit_code == 1
    assert "scan already active" in failed.stdout
    assert "Started antidisable scan" not in failed.stdout
    assert "Traceback" not in failed.stdout
    assert events == [("construct", None), ("begin", "failed")]


def test_adl_status_and_complete_cli_preserve_lifecycle_outputs_and_errors(
    monkeypatch,
) -> None:
    events: list[tuple[str, int]] = []

    def recording_init(_self) -> None:
        events.append(("construct", 0))

    def status(_self, scan_id: int):
        events.append(("status", scan_id))
        if scan_id == 99:
            return None
        if scan_id == 98:
            return SimpleNamespace(scan_kind="keys")
        return SimpleNamespace(
            id=scan_id,
            scan_kind="antidisable",
            server_name="Lake",
            account_name="ernieuuu",
            expected_page_count=None if scan_id == 1 else 3,
            imported_pages=() if scan_id == 1 else (1, 2),
            completed_at=None,
        )

    def complete(_self, scan_id: int):
        events.append(("complete", scan_id))
        if scan_id == 13:
            raise ValueError("scan is not complete")
        return SimpleNamespace(
            id=scan_id,
            server_name="Lake",
            account_name="ernieuuu",
        )

    monkeypatch.setattr(catalog_service_module.CatalogService, "__init__", recording_init)
    monkeypatch.setattr(catalog_service_module.CatalogService, "harem_scan_progress", status)
    monkeypatch.setattr(catalog_service_module.CatalogService, "complete_antidisable_scan", complete)
    runner = CliRunner()

    unknown_pages = runner.invoke(main.app, ["adl", "status", "1"])
    assert unknown_pages.exit_code == 0
    assert "Pages: none of unknown · Status: in progress" in unknown_pages.stdout
    assert events == [("construct", 0), ("status", 1)]

    events.clear()
    partial = runner.invoke(main.app, ["adl", "status", "2"])
    assert partial.exit_code == 0
    assert "Pages: 1, 2 of 3 · Status: in progress" in partial.stdout
    assert events == [("construct", 0), ("status", 2)]

    events.clear()
    wrong_kind = runner.invoke(main.app, ["adl", "status", "98"])
    assert wrong_kind.exit_code == 1
    assert "Antidisable scan not found." in wrong_kind.stdout
    assert events == [("construct", 0), ("status", 98)]

    events.clear()
    missing = runner.invoke(main.app, ["adl", "status", "99"])
    assert missing.exit_code == 1
    assert "Antidisable scan not found." in missing.stdout
    assert events == [("construct", 0), ("status", 99)]

    events.clear()
    completed = runner.invoke(main.app, ["adl", "complete", "12"])
    assert completed.exit_code == 0
    assert "Antidisable scan 12 is complete and active" in completed.stdout
    assert events == [("construct", 0), ("complete", 12)]

    events.clear()
    failed = runner.invoke(main.app, ["adl", "complete", "13"])
    assert failed.exit_code == 1
    assert "scan is not complete" in failed.stdout
    assert "Antidisable scan 13 is complete and active" not in failed.stdout
    assert "Traceback" not in failed.stdout
    assert events == [("construct", 0), ("complete", 13)]
