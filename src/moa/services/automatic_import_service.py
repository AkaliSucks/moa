"""Route one recognized Mudae message into the correct existing importer."""

from moa.models.catalog import AutomaticImportResult
from moa.parser.message_router import MudaeMessageRouter
from moa.parser.mudae import MudaeTextParser
from moa.services.catalog_service import CatalogService


class AutomaticImportService:
    """Import one recognized message without duplicating parser or storage rules."""

    def __init__(
        self,
        catalog_service: CatalogService | None = None,
        parser: MudaeTextParser | None = None,
        router: MudaeMessageRouter | None = None,
    ) -> None:
        self._catalog = catalog_service or CatalogService()
        self._parser = parser or MudaeTextParser()
        self._router = router or MudaeMessageRouter(self._parser)

    def import_message(
        self,
        raw_message: str,
        source: str,
        server_name: str | None = None,
        account_name: str | None = None,
        harem_scan_id: int | None = None,
        detected_kind: str | None = None,
    ) -> AutomaticImportResult:
        """Detect and import one supported message, or explain why it cannot be routed."""
        kind = detected_kind or self._router.detect(raw_message).kind
        if kind == "unknown":
            raise ValueError("This message is not a supported Mudae import format.")
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

        server = self._require(server_name, "server", kind)
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
        if kind == "roll":
            account = self._require(account_name, "account", kind)
            roll = self._parser.parse_roll(raw_message)
            result = self._catalog.import_roll(
                roll, server, account, raw_message, source
            )
            key_note = ""
            if roll.displayed_key_type is not None and roll.displayed_key_count is not None:
                key_note = f" with :{roll.displayed_key_type}key: ({roll.displayed_key_count})"
            return AutomaticImportResult(
                kind=kind,
                imported_count=1,
                message=f"Imported roll observation{key_note}.",
            )
        if kind == "im":
            result = self._catalog.import_character_details(
                self._parser.parse_character_details(raw_message), server, raw_message, source
            )
            return AutomaticImportResult(kind=kind, imported_count=1, message="Imported one character profile.")
        if kind == "settings":
            settings = self._parser.parse_server_settings(raw_message)
            self._catalog.import_server_settings(settings, server, raw_message, source)
            return AutomaticImportResult(kind=kind, imported_count=len(settings.metrics), message="Imported server settings.")
        if kind == "infokl":
            self._catalog.import_kakeraloot_settings(
                self._parser.parse_kakeraloot_settings(raw_message), server, raw_message, source
            )
            return AutomaticImportResult(kind=kind, imported_count=1, message="Imported Kakeraloot configuration.")

        account = self._require(account_name, "account", kind)
        if kind == "bonus":
            bonus = self._parser.parse_player_bonus(raw_message)
            self._catalog.import_player_bonus(bonus, server, account, raw_message, source)
            return AutomaticImportResult(kind=kind, imported_count=len(bonus.metrics), message="Imported player bonuses.")
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
            self._catalog.import_kakera_state(state, server, account, raw_message, source)
            return AutomaticImportResult(kind=kind, imported_count=len(state.badges), message="Imported Kakera state.")
        if kind == "personalrare":
            self._catalog.import_personal_rare(
                self._parser.parse_personal_rare(raw_message), server, account, raw_message, source
            )
            return AutomaticImportResult(kind=kind, imported_count=1, message="Imported personal rarity.")
        if kind == "timers":
            self._catalog.import_timer_state(
                self._parser.parse_timer_state(raw_message), server, account, raw_message, source
            )
            return AutomaticImportResult(kind=kind, imported_count=1, message="Imported action-timer snapshot.")
        if kind == "towerstate":
            self._catalog.import_tower_state(
                self._parser.parse_tower_state(raw_message), server, account, raw_message, source
            )
            return AutomaticImportResult(kind=kind, imported_count=1, message="Imported Kakera Tower state.")
        if kind == "lootstate":
            self._catalog.import_kakeraloot_state(
                self._parser.parse_kakeraloot_state(raw_message), server, account, raw_message, source
            )
            return AutomaticImportResult(kind=kind, imported_count=1, message="Imported Kakeraloot state.")
        if kind == "sphere_result":
            state = self._parser.parse_sphere_result(raw_message)
            self._catalog.import_sphere_result(state, server, account, raw_message, source)
            stock_note = f" Stock: {state.stock:,}." if state.stock is not None else ""
            return AutomaticImportResult(
                kind=kind,
                imported_count=1,
                message=f"Imported +{state.total_gained:,} spheres.{stock_note}",
            )
        raise ValueError(f"Automatic import is not implemented for {kind!r}.")

    @staticmethod
    def _require(value: str | None, label: str, kind: str) -> str:
        if value is None or not value.strip():
            raise ValueError(f"A --{label} value is required to import a {kind} message.")
        return value.strip()
