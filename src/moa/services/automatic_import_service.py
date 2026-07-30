"""Route one recognized Mudae message into the correct existing importer."""

from dataclasses import dataclass
from datetime import datetime

from moa.models.catalog import AutomaticImportResult
from moa.parser.message_router import MudaeMessageRouter
from moa.parser.mudae import MudaeTextParser
from moa.services.catalog_service import CatalogService
from moa.services.claim_projection_coordinator import ClaimProjectionCoordinator
from moa.services.infokl_projection_coordinator import InfoklProjectionCoordinator
from moa.services.kakera_state_projection_coordinator import KakeraStateProjectionCoordinator
from moa.services.kakeraloot_state_projection_coordinator import (
    KakeralootStateProjectionCoordinator,
)
from moa.services.mudapins_projection_coordinator import MudapinsProjectionCoordinator
from moa.services.player_bonus_projection_coordinator import PlayerBonusProjectionCoordinator
from moa.services.profile_projection_coordinator import ProfileProjectionCoordinator
from moa.services.roll_projection_coordinator import RollProjectionCoordinator
from moa.services.settings_projection_coordinator import SettingsProjectionCoordinator
from moa.services.sphere_result_projection_coordinator import SphereResultProjectionCoordinator
from moa.services.timer_projection_coordinator import TimerProjectionCoordinator
from moa.services.tower_state_projection_coordinator import TowerStateProjectionCoordinator


@dataclass(frozen=True, slots=True)
class DurableRollImportContext:
    """Durable lifecycle identifiers and completion time for one roll import."""

    source_event_id: int
    attempt_id: int | None
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class DurableProfileImportContext:
    """Durable lifecycle identifiers and completion time for one profile import."""

    source_event_id: int
    attempt_id: int | None
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class DurableClaimImportContext:
    """Durable lifecycle identifiers and completion time for one claim import."""

    source_event_id: int
    attempt_id: int | None
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class DurableSettingsImportContext:
    """Durable lifecycle identifiers and completion time for one settings import."""

    source_event_id: int
    attempt_id: int | None
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class DurableInfoklImportContext:
    """Durable lifecycle identifiers and completion time for one Infokl import."""

    source_event_id: int
    attempt_id: int | None
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class DurableTimerImportContext:
    """Durable lifecycle, scope, and payload metadata for one timer import."""

    source_event_id: int
    attempt_id: int | None
    server: str
    account: str
    raw: str
    source: str
    observed_at: datetime
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class DurableKakeraImportContext:
    """Durable lifecycle, scope, and payload metadata for one Kakera import."""

    source_event_id: int
    attempt_id: int | None
    server: str
    account: str
    raw: str
    source: str
    observed_at: datetime
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class DurableMudapinsImportContext:
    """Durable lifecycle, scope, and payload metadata for one Mudapin import."""

    source_event_id: int
    attempt_id: int | None
    server: str
    account: str
    raw: str
    source: str
    observed_at: datetime
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class DurableTowerStateImportContext:
    """Durable lifecycle, scope, and payload metadata for one Tower-state import."""

    source_event_id: int
    attempt_id: int | None
    server: str
    account: str
    raw: str
    source: str
    observed_at: datetime
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class DurableKakeralootStateImportContext:
    """Durable lifecycle, scope, and payload metadata for one Kakeraloot-state import."""

    source_event_id: int
    attempt_id: int | None
    server: str
    account: str
    raw: str
    source: str
    observed_at: datetime
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class DurableSphereResultImportContext:
    """Durable lifecycle, scope, and payload metadata for one sphere-result import."""

    source_event_id: int
    attempt_id: int | None
    server: str
    account: str
    raw: str
    source: str
    observed_at: datetime
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class DurablePlayerBonusImportContext:
    """Durable lifecycle, scope, and payload metadata for one player-bonus import."""

    source_event_id: int
    attempt_id: int | None
    server: str
    account: str
    raw: str
    source: str
    observed_at: datetime
    finished_at: datetime


class AutomaticImportService:
    """Import one recognized message without duplicating parser or storage rules."""

    def __init__(
        self,
        catalog_service: CatalogService | None = None,
        parser: MudaeTextParser | None = None,
        router: MudaeMessageRouter | None = None,
        roll_projection_coordinator: RollProjectionCoordinator | None = None,
        profile_projection_coordinator: ProfileProjectionCoordinator | None = None,
        claim_projection_coordinator: ClaimProjectionCoordinator | None = None,
        settings_projection_coordinator: SettingsProjectionCoordinator | None = None,
        infokl_projection_coordinator: InfoklProjectionCoordinator | None = None,
        timer_projection_coordinator: TimerProjectionCoordinator | None = None,
        kakera_state_projection_coordinator: KakeraStateProjectionCoordinator | None = None,
        mudapins_projection_coordinator: MudapinsProjectionCoordinator | None = None,
        tower_state_projection_coordinator: TowerStateProjectionCoordinator | None = None,
        kakeraloot_state_projection_coordinator: KakeralootStateProjectionCoordinator | None = None,
        sphere_result_projection_coordinator: SphereResultProjectionCoordinator | None = None,
        player_bonus_projection_coordinator: PlayerBonusProjectionCoordinator | None = None,
    ) -> None:
        self._catalog = catalog_service or CatalogService()
        self._parser = parser or MudaeTextParser()
        self._router = router or MudaeMessageRouter(self._parser)
        self._roll_projection_coordinator = roll_projection_coordinator
        self._profile_projection_coordinator = profile_projection_coordinator
        self._claim_projection_coordinator = claim_projection_coordinator
        self._settings_projection_coordinator = settings_projection_coordinator
        self._infokl_projection_coordinator = infokl_projection_coordinator
        self._timer_projection_coordinator = timer_projection_coordinator
        self._kakera_state_projection_coordinator = kakera_state_projection_coordinator
        self._mudapins_projection_coordinator = mudapins_projection_coordinator
        self._tower_state_projection_coordinator = tower_state_projection_coordinator
        self._kakeraloot_state_projection_coordinator = kakeraloot_state_projection_coordinator
        self._sphere_result_projection_coordinator = sphere_result_projection_coordinator
        self._player_bonus_projection_coordinator = player_bonus_projection_coordinator

    def import_message(
        self,
        raw_message: str,
        source: str,
        server_name: str | None = None,
        account_name: str | None = None,
        harem_scan_id: int | None = None,
        detected_kind: str | None = None,
        *,
        observed_at: datetime | None = None,
        durable_roll_context: DurableRollImportContext | None = None,
        durable_profile_context: DurableProfileImportContext | None = None,
        durable_claim_context: DurableClaimImportContext | None = None,
        durable_settings_context: DurableSettingsImportContext | None = None,
        durable_infokl_context: DurableInfoklImportContext | None = None,
        durable_timer_context: DurableTimerImportContext | None = None,
        durable_kakera_context: DurableKakeraImportContext | None = None,
        durable_mudapins_context: DurableMudapinsImportContext | None = None,
        durable_tower_state_context: DurableTowerStateImportContext | None = None,
        durable_kakeraloot_state_context: DurableKakeralootStateImportContext | None = None,
        durable_sphere_result_context: DurableSphereResultImportContext | None = None,
        durable_player_bonus_context: DurablePlayerBonusImportContext | None = None,
    ) -> AutomaticImportResult:
        """Detect and import one supported message, or explain why it cannot be routed."""
        kind = detected_kind or self._router.detect(raw_message).kind
        if kind == "unknown":
            raise ValueError("This message is not a supported Mudae import format.")
        if kind in {"help", "tutorial"}:
            label = "tutorial progress" if kind == "tutorial" else "help"
            return AutomaticImportResult(
                kind=kind,
                imported_count=0,
                message=f"Observed Mudae {label}; no catalog data imported.",
            )
        if kind == "top":
            result = self._catalog.import_top_page(
                self._parser.parse_top_page(raw_message),
                raw_message,
                source,
                server_name,
            )
            return AutomaticImportResult(
                kind=kind,
                imported_count=result.characters_imported,
                message=f"Imported {result.characters_imported} ranked characters.",
            )

        server = (
            durable_timer_context.server
            if kind == "timers" and durable_timer_context is not None
            else (
                durable_kakera_context.server
                if kind == "kakera" and durable_kakera_context is not None
                else (
                    durable_mudapins_context.server
                    if kind == "mudapins" and durable_mudapins_context is not None
                    else (
                        durable_tower_state_context.server
                        if kind == "towerstate" and durable_tower_state_context is not None
                        else (
                            durable_kakeraloot_state_context.server
                            if kind == "lootstate" and durable_kakeraloot_state_context is not None
                            else (
                                durable_sphere_result_context.server
                                if kind == "sphere_result" and durable_sphere_result_context is not None
                                else (
                                    durable_player_bonus_context.server
                                    if kind == "bonus" and durable_player_bonus_context is not None
                                    else self._require(server_name, "server", kind)
                                )
                            )
                        )
                    )
                )
            )
        )
        transaction_commands = {
            "gift_kakera": "givek",
            "gift_spheres": "givesp",
            "gift_character": "give",
            "trade": "trade",
        }
        if kind in transaction_commands:
            self._parser.parse_transaction(raw_message, kind)
            self._catalog.import_command_observation(
                transaction_commands[kind], raw_message, source
            )
            return AutomaticImportResult(
                kind=kind,
                imported_count=0,
                message=f"Observed ${transaction_commands[kind]} transaction step; no catalog data imported.",
            )
        if kind == "antidisable":
            account = self._require(account_name, "account", kind)
            page = self._parser.parse_antidisable_page(raw_message)
            result = self._catalog.import_antidisable_page(
                page, server, account, raw_message, source, harem_scan_id
            )
            page_label = (
                f" page {page.page_number}/{page.page_count}"
                if page.page_number is not None and page.page_count is not None
                else ""
            )
            return AutomaticImportResult(
                kind=kind,
                imported_count=result.series_imported,
                message=f"Imported {result.series_imported} antidisable series{page_label}.",
            )
        if kind == "ranked_harem":
            account = self._require(account_name, "account", kind)
            page = self._parser.parse_ranked_harem_page(raw_message)
            result = self._catalog.import_ranked_harem_page(
                page, server, account, raw_message, source, harem_scan_id
            )
            page_label = (
                f" page {page.page_number}/{page.page_count}"
                if page.page_number is not None and page.page_count is not None
                else ""
            )
            return AutomaticImportResult(
                kind=kind,
                imported_count=result.entries_imported,
                message=(
                    f"Imported {result.entries_imported} owned harem entries{page_label}; "
                    f"{result.entries_linked} linked to the catalog."
                ),
            )
        if kind == "harem":
            account = self._require(account_name, "account", kind)
            page = self._parser.parse_harem_key_page(raw_message)
            result = self._catalog.import_harem_key_page(
                page, server, account, raw_message, source, harem_scan_id
            )
            page_label = (
                f" page {page.page_number}/{page.page_count}"
                if page.page_number is not None and page.page_count is not None
                else ""
            )
            return AutomaticImportResult(
                kind=kind,
                imported_count=result.entries_imported,
                message=(
                    f"Imported {result.entries_imported} keyed harem entries{page_label}; "
                    f"{result.entries_linked} linked to the catalog."
                ),
            )
        if kind == "reaction_receipt":
            receipt = self._parser.parse_kakera_reaction_receipt(raw_message)
            result = self._catalog.import_kakera_reaction(receipt, server, raw_message, source)
            return AutomaticImportResult(
                kind=kind,
                imported_count=1,
                message=f"Imported +{receipt.kakera_earned:,} Kakera for {result.account_name}.",
            )
        if kind == "reaction_blocked":
            blocked = self._parser.parse_kakera_reaction_blocked(raw_message)
            return AutomaticImportResult(
                kind=kind,
                imported_count=0,
                message=(
                    f"Observed Kakera reaction cooldown for {blocked.account_name}: "
                    f"{blocked.cooldown_minutes} min; no $ku snapshot imported."
                ),
            )
        if kind == "claim":
            account = self._require(account_name, "account", kind)
            claim = self._parser.parse_claim_confirmation(raw_message)
            if claim.account_name.casefold() != account.casefold():
                raise ValueError(
                    f"Claim confirmation is for {claim.account_name!r}, not configured account {account!r}."
                )
            if durable_claim_context is None:
                imported_count = 1
                import_event_id = None
                replay_skipped = False
                durable_success_recorded = False
                self._catalog.import_claim(claim, server, account, raw_message, source)
            else:
                coordinator = self._claim_projection_coordinator
                if coordinator is None:
                    raise RuntimeError(
                        "A ClaimProjectionCoordinator is required for a durable claim import."
                    )
                coordinated = coordinator.coordinate_claim(
                    source_event_id=durable_claim_context.source_event_id,
                    attempt_id=durable_claim_context.attempt_id,
                    claim=claim,
                    server=server,
                    account=account,
                    raw=raw_message,
                    source=source,
                    observed_at=observed_at or durable_claim_context.finished_at,
                    finished_at=durable_claim_context.finished_at,
                )
                imported_count = coordinated.imported_count
                import_event_id = coordinated.import_event_id
                replay_skipped = coordinated.replay_skipped
                durable_success_recorded = coordinated.durable_success_recorded
            return AutomaticImportResult(
                kind=kind,
                imported_count=imported_count,
                message=f"Imported claim: {claim.character_name} for {claim.account_name}.",
                import_event_id=import_event_id,
                replay_skipped=replay_skipped,
                durable_success_recorded=durable_success_recorded,
            )
        if kind == "divorce_prompt":
            prompt = self._parser.parse_divorce_prompt(raw_message)
            refund = (
                f" (+{prompt.kakera_refund:,} Kakera refund)"
                if prompt.kakera_refund is not None
                else ""
            )
            return AutomaticImportResult(
                kind=kind,
                imported_count=0,
                message=(
                    f"Observed divorce confirmation for {prompt.character_name}{refund}; "
                    "waiting for y/yes."
                ),
            )
        if kind == "divorce_declined":
            self._parser.parse_divorce_declined(raw_message)
            return AutomaticImportResult(
                kind=kind,
                imported_count=0,
                message="Observed declined divorce; no catalog data imported.",
            )
        if kind == "divorce_complete":
            account = self._require(account_name, "account", kind)
            divorce = self._parser.parse_divorce_confirmation(
                raw_message, expected_account=account
            )
            result = self._catalog.import_divorce(
                divorce, server, account, raw_message, source
            )
            refund = (
                f" (+{divorce.kakera_refund:,} Kakera)"
                if divorce.kakera_refund is not None
                else ""
            )
            return AutomaticImportResult(
                kind=kind,
                imported_count=1,
                message=f"Imported divorce: {divorce.character_name} for {account}{refund}.",
            )
        if kind == "roll":
            account = self._require(account_name, "account", kind)
            roll = self._parser.parse_roll(raw_message)
            if durable_roll_context is None:
                imported_count = 1
                import_event_id = None
                replay_skipped = False
                durable_success_recorded = False
                self._catalog.import_roll(roll, server, account, raw_message, source)
            else:
                coordinator = self._roll_projection_coordinator
                if coordinator is None:
                    raise RuntimeError(
                        "A RollProjectionCoordinator is required for a durable roll import."
                    )
                coordinated = coordinator.coordinate_roll(
                    source_event_id=durable_roll_context.source_event_id,
                    attempt_id=durable_roll_context.attempt_id,
                    roll=roll,
                    server=server,
                    account=account,
                    raw=raw_message,
                    source=source,
                    observed_at=observed_at or durable_roll_context.finished_at,
                    finished_at=durable_roll_context.finished_at,
                )
                imported_count = coordinated.imported_count
                import_event_id = coordinated.import_event_id
                replay_skipped = coordinated.replay_skipped
                durable_success_recorded = coordinated.durable_success_recorded
            key_note = ""
            if roll.displayed_key_type is not None and roll.displayed_key_count is not None:
                key_note = f" with :{roll.displayed_key_type}key: ({roll.displayed_key_count})"
            return AutomaticImportResult(
                kind=kind,
                imported_count=imported_count,
                message=f"Imported roll observation: {roll.name} / {roll.series}{key_note}.",
                import_event_id=import_event_id,
                replay_skipped=replay_skipped,
                durable_success_recorded=durable_success_recorded,
            )
        if kind == "im":
            account = account_name.strip() if account_name else None
            details = self._parser.parse_character_details(raw_message)
            result = self._catalog.import_character_details(
                details,
                server,
                raw_message,
                source,
                account,
            )
            return AutomaticImportResult(
                kind=kind,
                imported_count=1,
                message=f"Imported one character profile: {details.name} / {details.series}.",
            )
        if kind == "settings":
            settings = self._parser.parse_server_settings(raw_message)
            if durable_settings_context is None:
                self._catalog.import_server_settings(settings, server, raw_message, source)
                imported_count = len(settings.metrics)
                import_event_id = None
                replay_skipped = False
                durable_success_recorded = False
            else:
                coordinator = self._settings_projection_coordinator
                if coordinator is None:
                    raise RuntimeError(
                        "A SettingsProjectionCoordinator is required for a durable settings import."
                    )
                coordinated = coordinator.coordinate_settings(
                    source_event_id=durable_settings_context.source_event_id,
                    attempt_id=durable_settings_context.attempt_id,
                    settings=settings,
                    server=server,
                    raw=raw_message,
                    source=source,
                    observed_at=observed_at or durable_settings_context.finished_at,
                    finished_at=durable_settings_context.finished_at,
                )
                imported_count = coordinated.imported_count
                import_event_id = coordinated.import_event_id
                replay_skipped = coordinated.replay_skipped
                durable_success_recorded = coordinated.durable_success_recorded
            return AutomaticImportResult(
                kind=kind,
                imported_count=imported_count,
                message="Imported server settings.",
                import_event_id=import_event_id,
                replay_skipped=replay_skipped,
                durable_success_recorded=durable_success_recorded,
            )
        if kind == "infokl":
            settings = self._parser.parse_kakeraloot_settings(raw_message)
            if durable_infokl_context is None:
                self._catalog.import_kakeraloot_settings(settings, server, raw_message, source)
                imported_count = 1
                import_event_id = None
                replay_skipped = False
                durable_success_recorded = False
            else:
                coordinator = self._infokl_projection_coordinator
                if coordinator is None:
                    raise RuntimeError(
                        "An InfoklProjectionCoordinator is required for a durable infokl import."
                    )
                coordinated = coordinator.coordinate_infokl(
                    source_event_id=durable_infokl_context.source_event_id,
                    attempt_id=durable_infokl_context.attempt_id,
                    settings=settings,
                    server=server,
                    raw=raw_message,
                    source=source,
                    observed_at=observed_at or durable_infokl_context.finished_at,
                    finished_at=durable_infokl_context.finished_at,
                )
                imported_count = coordinated.imported_count
                import_event_id = coordinated.import_event_id
                replay_skipped = coordinated.replay_skipped
                durable_success_recorded = coordinated.durable_success_recorded
            return AutomaticImportResult(
                kind=kind,
                imported_count=imported_count,
                message="Imported Kakeraloot configuration.",
                import_event_id=import_event_id,
                replay_skipped=replay_skipped,
                durable_success_recorded=durable_success_recorded,
            )
        if kind == "profile":
            account = self._require(account_name, "account", kind)
            profile = self._parser.parse_profile(raw_message)
            if durable_profile_context is None:
                self._catalog.import_profile(profile, server, account, raw_message, source)
                imported_count = 1
                import_event_id = None
                replay_skipped = False
                durable_success_recorded = False
            else:
                coordinator = self._profile_projection_coordinator
                if coordinator is None:
                    raise RuntimeError(
                        "A ProfileProjectionCoordinator is required for a durable profile import."
                    )
                coordinated = coordinator.coordinate_profile(
                    source_event_id=durable_profile_context.source_event_id,
                    attempt_id=durable_profile_context.attempt_id,
                    profile=profile,
                    server=server,
                    account=account,
                    raw=raw_message,
                    source=source,
                    observed_at=observed_at or durable_profile_context.finished_at,
                    finished_at=durable_profile_context.finished_at,
                )
                imported_count = coordinated.imported_count
                import_event_id = coordinated.import_event_id
                replay_skipped = coordinated.replay_skipped
                durable_success_recorded = coordinated.durable_success_recorded
            return AutomaticImportResult(
                kind=kind,
                imported_count=imported_count,
                message=f"Imported profile snapshot for {profile.profile_name}.",
                import_event_id=import_event_id,
                replay_skipped=replay_skipped,
                durable_success_recorded=durable_success_recorded,
            )
        if kind == "mudapins":
            snapshot = self._parser.parse_mudapins(raw_message)
            if durable_mudapins_context is None:
                account = self._require(account_name, "account", kind)
                self._catalog.import_mudapins(snapshot, server, account, raw_message, source)
                imported_count = len(snapshot.pin_markers)
                import_event_id = None
                replay_skipped = False
                durable_success_recorded = False
            else:
                coordinator = self._mudapins_projection_coordinator
                if coordinator is None:
                    raise RuntimeError(
                        "A MudapinsProjectionCoordinator is required for a durable Mudapins import."
                    )
                coordinated = coordinator.coordinate_mudapins(
                    source_event_id=durable_mudapins_context.source_event_id,
                    attempt_id=durable_mudapins_context.attempt_id,
                    snapshot=snapshot,
                    server=durable_mudapins_context.server,
                    account=durable_mudapins_context.account,
                    raw=durable_mudapins_context.raw,
                    source=durable_mudapins_context.source,
                    observed_at=durable_mudapins_context.observed_at,
                    finished_at=durable_mudapins_context.finished_at,
                )
                imported_count = coordinated.imported_count
                import_event_id = coordinated.import_event_id
                replay_skipped = coordinated.replay_skipped
                durable_success_recorded = coordinated.durable_success_recorded
            count = len(snapshot.pin_markers)
            return AutomaticImportResult(
                kind=kind,
                imported_count=imported_count,
                message=(
                    "Imported no Mudapins."
                    if count == 0
                    else f"Imported {count} Mudapin markers."
                ),
                import_event_id=import_event_id,
                replay_skipped=replay_skipped,
                durable_success_recorded=durable_success_recorded,
            )

        account = (
            durable_timer_context.account
            if kind == "timers" and durable_timer_context is not None
            else (
                durable_kakera_context.account
                if kind == "kakera" and durable_kakera_context is not None
                else (
                    durable_tower_state_context.account
                    if kind == "towerstate" and durable_tower_state_context is not None
                    else (
                        durable_kakeraloot_state_context.account
                        if kind == "lootstate" and durable_kakeraloot_state_context is not None
                        else (
                            durable_sphere_result_context.account
                            if kind == "sphere_result" and durable_sphere_result_context is not None
                            else (
                                durable_player_bonus_context.account
                                if kind == "bonus" and durable_player_bonus_context is not None
                                else self._require(account_name, "account", kind)
                            )
                        )
                    )
                )
            )
        )
        if kind == "bonus":
            bonus = self._parser.parse_player_bonus(raw_message)
            if durable_player_bonus_context is None:
                self._catalog.import_player_bonus(bonus, server, account, raw_message, source)
                imported_count = len(bonus.metrics)
                import_event_id = None
                replay_skipped = False
                durable_success_recorded = False
            else:
                coordinator = self._player_bonus_projection_coordinator
                if coordinator is None:
                    raise RuntimeError(
                        "A PlayerBonusProjectionCoordinator is required for a durable player-bonus import."
                    )
                coordinated = coordinator.coordinate_player_bonus(
                    source_event_id=durable_player_bonus_context.source_event_id,
                    attempt_id=durable_player_bonus_context.attempt_id,
                    state=bonus,
                    server=durable_player_bonus_context.server,
                    account=durable_player_bonus_context.account,
                    raw=durable_player_bonus_context.raw,
                    source=durable_player_bonus_context.source,
                    observed_at=durable_player_bonus_context.observed_at,
                    finished_at=durable_player_bonus_context.finished_at,
                )
                imported_count = coordinated.imported_count
                import_event_id = coordinated.import_event_id
                replay_skipped = coordinated.replay_skipped
                durable_success_recorded = coordinated.durable_success_recorded
            return AutomaticImportResult(
                kind=kind,
                imported_count=imported_count,
                message="Imported player bonuses.",
                import_event_id=import_event_id,
                replay_skipped=replay_skipped,
                durable_success_recorded=durable_success_recorded,
            )
        if kind == "wishlist":
            wishlist = self._parser.parse_wishlist(raw_message)
            self._catalog.import_wishlist(wishlist, server, account, raw_message, source)
            return AutomaticImportResult(kind=kind, imported_count=len(wishlist.entries), message="Imported wishlist.")
        if kind == "disablelist":
            disablelist = self._parser.parse_disablelist(raw_message)
            self._catalog.import_disablelist(disablelist, server, account, raw_message, source)
            return AutomaticImportResult(kind=kind, imported_count=len(disablelist.entries), message="Imported disablelist.")
        if kind == "topx":
            page = self._parser.parse_unavailable_characters(raw_message)
            result = self._catalog.import_unavailable_characters(page, server, account, raw_message, source)
            return AutomaticImportResult(
                kind=kind,
                imported_count=result.characters_imported,
                message="Imported unavailable-character observations.",
            )
        if kind == "kakera":
            state = self._parser.parse_kakera_state(raw_message)
            if durable_kakera_context is None:
                self._catalog.import_kakera_state(state, server, account, raw_message, source)
                imported_count = len(state.badges)
                import_event_id = None
                replay_skipped = False
                durable_success_recorded = False
            else:
                coordinator = self._kakera_state_projection_coordinator
                if coordinator is None:
                    raise RuntimeError(
                        "A KakeraStateProjectionCoordinator is required for a durable Kakera import."
                    )
                coordinated = coordinator.coordinate_kakera_state(
                    source_event_id=durable_kakera_context.source_event_id,
                    attempt_id=durable_kakera_context.attempt_id,
                    state=state,
                    server=durable_kakera_context.server,
                    account=durable_kakera_context.account,
                    raw=durable_kakera_context.raw,
                    source=durable_kakera_context.source,
                    observed_at=durable_kakera_context.observed_at,
                    finished_at=durable_kakera_context.finished_at,
                )
                imported_count = coordinated.imported_count
                import_event_id = coordinated.import_event_id
                replay_skipped = coordinated.replay_skipped
                durable_success_recorded = coordinated.durable_success_recorded
            return AutomaticImportResult(
                kind=kind,
                imported_count=imported_count,
                message="Imported Kakera state.",
                import_event_id=import_event_id,
                replay_skipped=replay_skipped,
                durable_success_recorded=durable_success_recorded,
            )
        if kind == "personalrare":
            self._catalog.import_personal_rare(
                self._parser.parse_personal_rare(raw_message), server, account, raw_message, source
            )
            return AutomaticImportResult(kind=kind, imported_count=1, message="Imported personal rarity.")
        if kind == "timers":
            state = self._parser.parse_timer_state(raw_message)
            if durable_timer_context is None:
                self._catalog.import_timer_state(state, server, account, raw_message, source)
                imported_count = 1
                import_event_id = None
                replay_skipped = False
                durable_success_recorded = False
            else:
                coordinator = self._timer_projection_coordinator
                if coordinator is None:
                    raise RuntimeError(
                        "A TimerProjectionCoordinator is required for a durable timer import."
                    )
                coordinated = coordinator.coordinate_timer_state(
                    source_event_id=durable_timer_context.source_event_id,
                    attempt_id=durable_timer_context.attempt_id,
                    state=state,
                    server=durable_timer_context.server,
                    account=durable_timer_context.account,
                    raw=durable_timer_context.raw,
                    source=durable_timer_context.source,
                    observed_at=durable_timer_context.observed_at,
                    finished_at=durable_timer_context.finished_at,
                )
                imported_count = coordinated.imported_count
                import_event_id = coordinated.import_event_id
                replay_skipped = coordinated.replay_skipped
                durable_success_recorded = coordinated.durable_success_recorded
            return AutomaticImportResult(
                kind=kind,
                imported_count=imported_count,
                message="Imported action-timer snapshot.",
                import_event_id=import_event_id,
                replay_skipped=replay_skipped,
                durable_success_recorded=durable_success_recorded,
            )
        if kind == "towerstate":
            state = self._parser.parse_tower_state(raw_message)
            if durable_tower_state_context is None:
                self._catalog.import_tower_state(state, server, account, raw_message, source)
                imported_count = 1
                import_event_id = None
                replay_skipped = False
                durable_success_recorded = False
            else:
                coordinator = self._tower_state_projection_coordinator
                if coordinator is None:
                    raise RuntimeError(
                        "A TowerStateProjectionCoordinator is required for a durable Tower-state import."
                    )
                coordinated = coordinator.coordinate_tower_state(
                    source_event_id=durable_tower_state_context.source_event_id,
                    attempt_id=durable_tower_state_context.attempt_id,
                    state=state,
                    server=durable_tower_state_context.server,
                    account=durable_tower_state_context.account,
                    raw=durable_tower_state_context.raw,
                    source=durable_tower_state_context.source,
                    observed_at=durable_tower_state_context.observed_at,
                    finished_at=durable_tower_state_context.finished_at,
                )
                imported_count = coordinated.imported_count
                import_event_id = coordinated.import_event_id
                replay_skipped = coordinated.replay_skipped
                durable_success_recorded = coordinated.durable_success_recorded
            return AutomaticImportResult(
                kind=kind,
                imported_count=imported_count,
                message="Imported Kakera Tower state.",
                import_event_id=import_event_id,
                replay_skipped=replay_skipped,
                durable_success_recorded=durable_success_recorded,
            )
        if kind == "lootstate":
            state = self._parser.parse_kakeraloot_state(raw_message)
            if durable_kakeraloot_state_context is None:
                self._catalog.import_kakeraloot_state(state, server, account, raw_message, source)
                imported_count = 1
                import_event_id = None
                replay_skipped = False
                durable_success_recorded = False
            else:
                coordinator = self._kakeraloot_state_projection_coordinator
                if coordinator is None:
                    raise RuntimeError(
                        "A KakeralootStateProjectionCoordinator is required for a durable Kakeraloot-state import."
                    )
                coordinated = coordinator.coordinate_kakeraloot_state(
                    source_event_id=durable_kakeraloot_state_context.source_event_id,
                    attempt_id=durable_kakeraloot_state_context.attempt_id,
                    state=state,
                    server=durable_kakeraloot_state_context.server,
                    account=durable_kakeraloot_state_context.account,
                    raw=durable_kakeraloot_state_context.raw,
                    source=durable_kakeraloot_state_context.source,
                    observed_at=durable_kakeraloot_state_context.observed_at,
                    finished_at=durable_kakeraloot_state_context.finished_at,
                )
                imported_count = coordinated.imported_count
                import_event_id = coordinated.import_event_id
                replay_skipped = coordinated.replay_skipped
                durable_success_recorded = coordinated.durable_success_recorded
            return AutomaticImportResult(
                kind=kind,
                imported_count=imported_count,
                message="Imported Kakeraloot state.",
                import_event_id=import_event_id,
                replay_skipped=replay_skipped,
                durable_success_recorded=durable_success_recorded,
            )
        if kind == "sphere_result":
            state = self._parser.parse_sphere_result(raw_message)
            if durable_sphere_result_context is None:
                self._catalog.import_sphere_result(state, server, account, raw_message, source)
                imported_count = 1
                import_event_id = None
                replay_skipped = False
                durable_success_recorded = False
            else:
                coordinator = self._sphere_result_projection_coordinator
                if coordinator is None:
                    raise RuntimeError(
                        "A SphereResultProjectionCoordinator is required for a durable sphere-result import."
                    )
                coordinated = coordinator.coordinate_sphere_result(
                    source_event_id=durable_sphere_result_context.source_event_id,
                    attempt_id=durable_sphere_result_context.attempt_id,
                    state=state,
                    server=durable_sphere_result_context.server,
                    account=durable_sphere_result_context.account,
                    raw=durable_sphere_result_context.raw,
                    source=durable_sphere_result_context.source,
                    observed_at=durable_sphere_result_context.observed_at,
                    finished_at=durable_sphere_result_context.finished_at,
                )
                imported_count = coordinated.imported_count
                import_event_id = coordinated.import_event_id
                replay_skipped = coordinated.replay_skipped
                durable_success_recorded = coordinated.durable_success_recorded
            stock_note = f" Stock: {state.stock:,}." if state.stock is not None else ""
            return AutomaticImportResult(
                kind=kind,
                imported_count=imported_count,
                message=f"Imported +{state.total_gained:,} spheres.{stock_note}",
                import_event_id=import_event_id,
                replay_skipped=replay_skipped,
                durable_success_recorded=durable_success_recorded,
            )
        raise ValueError(f"Automatic import is not implemented for {kind!r}.")

    @staticmethod
    def _require(value: str | None, label: str, kind: str) -> str:
        if value is None or not value.strip():
            raise ValueError(f"A --{label} value is required to import a {kind} message.")
        return value.strip()
