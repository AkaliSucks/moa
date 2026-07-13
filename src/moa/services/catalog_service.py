"""Business operations for MOA's persisted character catalog."""

from moa.models.catalog import (
    CharacterDetailsImportResult,
    CharacterProfile,
    CatalogRankSnapshot,
    HaremKeyImportResult,
    HaremKeyObservation,
    HaremScanProgress,
    ImportEventSummary,
    RankedCatalogCharacter,
    TopImportResult,
    PlayerBonusImportResult,
    PlayerBonusObservation,
    DisableListImportResult,
    DisableListObservation,
    RollabilityImportResult,
    UnavailableCharacterObservation,
    RollImportResult,
    KakeraReactionImportResult,
    KakeraReactionObservation,
    KakeraReactionSummary,
    RollStatistics,
    StoredRollObservation,
    KakeraStateImportResult,
    KakeraStateObservation,
    KakeraProgressPoint,
    PersonalRareImportResult,
    PersonalRareObservation,
    ServerSettingsImportResult,
    ServerSettingsObservation,
    KakeralootStateImportResult,
    KakeralootStateObservation,
    KakeralootSettingsImportResult,
    KakeralootSettingsObservation,
    TowerStateImportResult,
    TowerStateObservation,
    TimerStateImportResult,
    TimerStateObservation,
    WishlistImportResult,
    WishlistObservation,
)
from moa.models.character import (
    CharacterDetails,
    DisableListSnapshot,
    HaremKeyPage,
    KakeraStateSnapshot,
    KakeralootStateSnapshot,
    KakeralootSettingsSnapshot,
    PersonalRareSnapshot,
    ServerSettingsSnapshot,
    TowerStateSnapshot,
    TimerStateSnapshot,
    PlayerBonusSnapshot,
    TopPage,
    RollObservation,
    KakeraReactionReceipt,
    WishlistSnapshot,
    UnavailableCharacterPage,
)
from moa.repositories.catalog_repository import CatalogRepository, CatalogRepositoryProtocol


class CatalogService:
    """Catalog operations independent of the SQLite implementation."""

    def __init__(self, repository: CatalogRepositoryProtocol | None = None) -> None:
        self._repository = repository or CatalogRepository()

    def import_top_page(self, page: TopPage, raw_message: str, source: str) -> TopImportResult:
        return self._repository.import_top_page(page, raw_message, source)

    def import_character_details(
        self,
        details: CharacterDetails,
        server_name: str,
        raw_message: str,
        source: str,
    ) -> CharacterDetailsImportResult:
        return self._repository.import_character_details(details, server_name, raw_message, source)

    def top(self, limit: int | None = 15) -> tuple[RankedCatalogCharacter, ...]:
        return self._repository.top(limit)

    def character_count(self) -> int:
        return self._repository.character_count()

    def import_roll(
        self,
        roll: RollObservation,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> RollImportResult:
        return self._repository.import_roll(roll, server_name, account_name, raw_message, source)

    def recent_rolls(
        self, server_name: str, account_name: str, limit: int = 20
    ) -> tuple[StoredRollObservation, ...]:
        return self._repository.recent_rolls(server_name, account_name, limit)

    def roll_statistics(self, server_name: str, account_name: str) -> RollStatistics:
        return self._repository.roll_statistics(server_name, account_name)

    def import_kakera_reaction(self, receipt: KakeraReactionReceipt, server_name: str, raw_message: str, source: str) -> KakeraReactionImportResult:
        return self._repository.import_kakera_reaction(receipt, server_name, raw_message, source)

    def kakera_reactions(self, server_name: str, account_name: str, limit: int = 20) -> tuple[KakeraReactionObservation, ...]:
        return self._repository.kakera_reactions(server_name, account_name, limit)

    def kakera_reaction_summary(self, server_name: str, account_name: str) -> KakeraReactionSummary:
        return self._repository.kakera_reaction_summary(server_name, account_name)

    def get_profile(self, name: str, series: str) -> CharacterProfile | None:
        return self._repository.get_profile(name, series)

    def rank_history(
        self, name: str, series: str, limit: int = 20
    ) -> tuple[CatalogRankSnapshot, ...]:
        return self._repository.rank_history(name, series, limit)

    def recent_imports(self, limit: int = 20) -> tuple[ImportEventSummary, ...]:
        return self._repository.recent_imports(limit)

    def delete_import_event(self, import_event_id: int) -> bool:
        return self._repository.delete_import_event(import_event_id)

    def import_harem_key_page(
        self,
        page: HaremKeyPage,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
        scan_id: int | None = None,
    ) -> HaremKeyImportResult:
        return self._repository.import_harem_key_page(
            page, server_name, account_name, raw_message, source, scan_id
        )

    def harem_keys(self, server_name: str, account_name: str) -> tuple[HaremKeyObservation, ...]:
        return self._repository.harem_keys(server_name, account_name)

    def recent_key_gains(
        self, server_name: str, account_name: str, limit: int = 20
    ) -> tuple[HaremKeyObservation, ...]:
        return self._repository.recent_key_gains(server_name, account_name, limit)

    def begin_harem_scan(self, server_name: str, account_name: str) -> HaremScanProgress:
        return self._repository.begin_harem_scan(server_name, account_name)

    def harem_scan_progress(self, scan_id: int) -> HaremScanProgress | None:
        return self._repository.harem_scan_progress(scan_id)

    def complete_harem_scan(self, scan_id: int) -> HaremScanProgress:
        return self._repository.complete_harem_scan(scan_id)

    def import_player_bonus(
        self,
        bonus: PlayerBonusSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> PlayerBonusImportResult:
        return self._repository.import_player_bonus(
            bonus, server_name, account_name, raw_message, source
        )

    def player_bonus(self, server_name: str, account_name: str) -> PlayerBonusObservation | None:
        return self._repository.player_bonus(server_name, account_name)

    def import_wishlist(
        self,
        wishlist: WishlistSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> WishlistImportResult:
        return self._repository.import_wishlist(
            wishlist, server_name, account_name, raw_message, source
        )

    def wishlist(self, server_name: str, account_name: str) -> WishlistObservation | None:
        return self._repository.wishlist(server_name, account_name)

    def import_disablelist(
        self,
        disablelist: DisableListSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> DisableListImportResult:
        return self._repository.import_disablelist(
            disablelist, server_name, account_name, raw_message, source
        )

    def disablelist(self, server_name: str, account_name: str) -> DisableListObservation | None:
        return self._repository.disablelist(server_name, account_name)

    def import_unavailable_characters(
        self,
        page: UnavailableCharacterPage,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> RollabilityImportResult:
        return self._repository.import_unavailable_characters(
            page, server_name, account_name, raw_message, source
        )

    def unavailable_characters(
        self, server_name: str, account_name: str
    ) -> tuple[UnavailableCharacterObservation, ...]:
        return self._repository.unavailable_characters(server_name, account_name)

    def import_kakera_state(
        self,
        state: KakeraStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> KakeraStateImportResult:
        return self._repository.import_kakera_state(
            state, server_name, account_name, raw_message, source
        )

    def kakera_state(self, server_name: str, account_name: str) -> KakeraStateObservation | None:
        return self._repository.kakera_state(server_name, account_name)

    def kakera_history(
        self, server_name: str, account_name: str
    ) -> tuple[KakeraProgressPoint, ...]:
        return self._repository.kakera_history(server_name, account_name)

    def import_personal_rare(
        self,
        state: PersonalRareSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> PersonalRareImportResult:
        return self._repository.import_personal_rare(
            state, server_name, account_name, raw_message, source
        )

    def personal_rare(
        self, server_name: str, account_name: str
    ) -> PersonalRareObservation | None:
        return self._repository.personal_rare(server_name, account_name)

    def import_tower_state(
        self,
        state: TowerStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> TowerStateImportResult:
        return self._repository.import_tower_state(
            state, server_name, account_name, raw_message, source
        )

    def tower_state(self, server_name: str, account_name: str) -> TowerStateObservation | None:
        return self._repository.tower_state(server_name, account_name)

    def import_timer_state(
        self,
        state: TimerStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> TimerStateImportResult:
        return self._repository.import_timer_state(state, server_name, account_name, raw_message, source)

    def timer_state(self, server_name: str, account_name: str) -> TimerStateObservation | None:
        return self._repository.timer_state(server_name, account_name)

    def import_kakeraloot_state(
        self,
        state: KakeralootStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> KakeralootStateImportResult:
        return self._repository.import_kakeraloot_state(
            state, server_name, account_name, raw_message, source
        )

    def kakeraloot_state(
        self, server_name: str, account_name: str
    ) -> KakeralootStateObservation | None:
        return self._repository.kakeraloot_state(server_name, account_name)

    def import_kakeraloot_settings(
        self,
        settings: KakeralootSettingsSnapshot,
        server_name: str,
        raw_message: str,
        source: str,
    ) -> KakeralootSettingsImportResult:
        return self._repository.import_kakeraloot_settings(settings, server_name, raw_message, source)

    def kakeraloot_settings(self, server_name: str) -> KakeralootSettingsObservation | None:
        return self._repository.kakeraloot_settings(server_name)

    def import_server_settings(
        self,
        settings: ServerSettingsSnapshot,
        server_name: str,
        raw_message: str,
        source: str,
    ) -> ServerSettingsImportResult:
        return self._repository.import_server_settings(settings, server_name, raw_message, source)

    def server_settings(self, server_name: str) -> ServerSettingsObservation | None:
        return self._repository.server_settings(server_name)
