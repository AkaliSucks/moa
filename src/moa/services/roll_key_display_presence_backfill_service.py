"""Explicit, parser-backed reconstruction of retained roll key-display presence."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sqlite3

from moa.database.sqlite import run_write_transaction
from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository


class RollKeyDisplayBackfillStatus(str, Enum):
    UPDATED = "updated"
    ALREADY_ESTABLISHED = "already_established"
    UNAVAILABLE = "unavailable"
    INCOHERENT = "incoherent"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class RollKeyDisplayBackfillResult:
    status: RollKeyDisplayBackfillStatus
    source_event_id: int
    import_event_id: int | None = None
    roll_observation_id: int | None = None
    displayed_key_count_present: bool | None = None
    parser_version: str | None = None


class RollKeyDisplayPresenceBackfillService:
    """Backfill one source only when retained evidence and durable identity agree."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def backfill(self, source_event_id: int) -> RollKeyDisplayBackfillResult:
        """Explicitly inspect and atomically update one roll in a write transaction."""
        if isinstance(source_event_id, bool) or not isinstance(source_event_id, int) or source_event_id <= 0:
            raise ValueError("source_event_id must be a positive integer")
        return run_write_transaction(
            self._database_path,
            lambda connection: self._backfill(connection, source_event_id),
        )

    @staticmethod
    def _backfill(
        connection: sqlite3.Connection, source_event_id: int
    ) -> RollKeyDisplayBackfillResult:
        def result(
            status: RollKeyDisplayBackfillStatus,
            import_id: int | None = None,
            roll_id: int | None = None,
            presence: bool | None = None,
            parser_version: str | None = None,
        ) -> RollKeyDisplayBackfillResult:
            return RollKeyDisplayBackfillResult(
                status, source_event_id, import_id, roll_id, presence, parser_version
            )

        source = connection.execute(
            "SELECT status, raw_text, raw_evidence_expired_at, legacy_import_event_id "
            "FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        if source is None or source["status"] != "succeeded":
            return result(RollKeyDisplayBackfillStatus.INCOHERENT)
        raw_text = source["raw_text"]
        if (
            source["raw_evidence_expired_at"] is not None
            or not isinstance(raw_text, str)
            or not raw_text.strip()
            or raw_text.startswith("[moa:raw-evidence-expired:")
        ):
            return result(RollKeyDisplayBackfillStatus.UNAVAILABLE)
        import_id = source["legacy_import_event_id"]
        if isinstance(import_id, bool) or not isinstance(import_id, int) or import_id <= 0:
            return result(RollKeyDisplayBackfillStatus.INCOHERENT)
        if connection.execute(
            "SELECT COUNT(*) FROM discord_source_events WHERE legacy_import_event_id = ?",
            (import_id,),
        ).fetchone()[0] != 1:
            return result(RollKeyDisplayBackfillStatus.INCOHERENT, import_id)
        imported = connection.execute(
            "SELECT kind, raw_message_expired_at FROM import_events WHERE id = ?",
            (import_id,),
        ).fetchone()
        if imported is None or imported["kind"] != "roll":
            return result(RollKeyDisplayBackfillStatus.INCOHERENT, import_id)
        if imported["raw_message_expired_at"] is not None:
            return result(RollKeyDisplayBackfillStatus.UNAVAILABLE, import_id)
        attempts = connection.execute(
            "SELECT parser_version FROM discord_processing_attempts "
            "WHERE source_event_id = ? AND status = 'succeeded'",
            (source_event_id,),
        ).fetchall()
        if len(attempts) != 1:
            return result(RollKeyDisplayBackfillStatus.INCOHERENT, import_id)
        parser_version = attempts[0]["parser_version"]
        server = connection.execute(
            "SELECT status, server_name FROM discord_source_event_server_attributions "
            "WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT status, server_name, account_name "
            "FROM discord_source_event_account_attributions WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()
        if (
            server is None
            or account is None
            or server["status"] != "resolved"
            or account["status"] != "resolved"
            or not isinstance(server["server_name"], str)
            or not isinstance(account["server_name"], str)
            or not isinstance(account["account_name"], str)
        ):
            return result(RollKeyDisplayBackfillStatus.INCOHERENT, import_id)
        server_name = CatalogRepository._normalize(server["server_name"])
        account_name = CatalogRepository._normalize(account["account_name"])
        if not server_name or not account_name or CatalogRepository._normalize(account["server_name"]) != server_name:
            return result(RollKeyDisplayBackfillStatus.INCOHERENT, import_id)
        rolls = connection.execute(
            "SELECT ro.id, ro.claim_rank, ro.kakera_value, ro.displayed_key_count_present, "
            "sc.normalized_name AS server_name, ac.normalized_name AS account_name, "
            "c.normalized_name AS character_name, c.normalized_series AS series_name "
            "FROM roll_observations AS ro "
            "LEFT JOIN account_contexts AS ac ON ac.id = ro.account_context_id "
            "LEFT JOIN server_contexts AS sc ON sc.id = ac.server_context_id "
            "LEFT JOIN characters AS c ON c.id = ro.character_id "
            "WHERE ro.import_event_id = ?",
            (import_id,),
        ).fetchall()
        if len(rolls) != 1:
            return result(RollKeyDisplayBackfillStatus.INCOHERENT, import_id)
        roll = rolls[0]
        roll_id = int(roll["id"])
        if roll["server_name"] != server_name or roll["account_name"] != account_name:
            return result(RollKeyDisplayBackfillStatus.INCOHERENT, import_id, roll_id)
        try:
            parsed = MudaeTextParser().parse_roll(raw_text)
        except (MudaeParseError, TypeError, UnicodeError, ValueError):
            return result(RollKeyDisplayBackfillStatus.UNAVAILABLE, import_id, roll_id)
        if (
            roll["character_name"] != CatalogRepository._normalize(parsed.name)
            or roll["series_name"] != CatalogRepository._normalize(parsed.series)
            or roll["claim_rank"] != parsed.claim_rank
            or roll["kakera_value"] != parsed.kakera_value
        ):
            return result(RollKeyDisplayBackfillStatus.INCOHERENT, import_id, roll_id)
        presence = parsed.displayed_key_count is not None
        existing = roll["displayed_key_count_present"]
        if existing is not None:
            status = (
                RollKeyDisplayBackfillStatus.ALREADY_ESTABLISHED
                if existing == int(presence)
                else RollKeyDisplayBackfillStatus.CONFLICT
            )
            return result(status, import_id, roll_id, presence, parser_version)
        updated = connection.execute(
            "UPDATE roll_observations SET displayed_key_count_present = ? "
            "WHERE id = ? AND import_event_id = ? AND displayed_key_count_present IS NULL",
            (int(presence), roll_id, import_id),
        ).rowcount
        if updated != 1:
            return result(RollKeyDisplayBackfillStatus.CONFLICT, import_id, roll_id, presence, parser_version)
        return result(RollKeyDisplayBackfillStatus.UPDATED, import_id, roll_id, presence, parser_version)
