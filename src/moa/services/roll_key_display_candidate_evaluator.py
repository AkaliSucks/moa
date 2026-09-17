"""Read-only, fresh-parser eligibility for retained roll key-display presence.

Results are observations, not authorization to mutate. A writer must evaluate
again inside its own transaction before applying a conditional update.
"""

from dataclasses import dataclass
from enum import Enum
import sqlite3

from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository


class RollKeyDisplayCandidateStatus(str, Enum):
    ELIGIBLE_NULL = "ELIGIBLE_NULL"
    ALREADY_ESTABLISHED_MATCHING = "ALREADY_ESTABLISHED_MATCHING"
    ALREADY_ESTABLISHED_CONFLICT = "ALREADY_ESTABLISHED_CONFLICT"
    SOURCE_MISSING = "SOURCE_MISSING"
    SOURCE_NOT_SUCCEEDED = "SOURCE_NOT_SUCCEEDED"
    SOURCE_UNUSABLE = "SOURCE_UNUSABLE"
    SOURCE_EXPIRED = "SOURCE_EXPIRED"
    IMPORT_LINK_MISSING = "IMPORT_LINK_MISSING"
    IMPORT_LINK_AMBIGUOUS = "IMPORT_LINK_AMBIGUOUS"
    NON_ROLL_SOURCE = "NON_ROLL_SOURCE"
    PARSER_PROVENANCE_MISSING_OR_AMBIGUOUS = "PARSER_PROVENANCE_MISSING_OR_AMBIGUOUS"
    ATTRIBUTION_UNRESOLVED = "ATTRIBUTION_UNRESOLVED"
    ATTRIBUTION_AMBIGUOUS = "ATTRIBUTION_AMBIGUOUS"
    ATTRIBUTION_CONFLICT = "ATTRIBUTION_CONFLICT"
    ROLL_TARGET_MISSING = "ROLL_TARGET_MISSING"
    ROLL_TARGET_AMBIGUOUS = "ROLL_TARGET_AMBIGUOUS"
    PARSE_FAILURE = "PARSE_FAILURE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


@dataclass(frozen=True)
class RollKeyDisplayCandidate:
    source_event_id: int | None
    import_event_id: int | None
    roll_observation_id: int | None
    current_displayed_key_count_present: bool | None
    status: RollKeyDisplayCandidateStatus
    proposed_value: bool | None = None
    parser_version: str | None = None
    retention_state: str = "unknown"
    failure_category: str | None = None


class RollKeyDisplayCandidateEvaluator:
    """SELECT-only evaluator shared by the writer and inventory."""

    @staticmethod
    def evaluate(
        connection: sqlite3.Connection,
        *,
        source_event_id: int | None = None,
        roll_observation_id: int | None = None,
    ) -> RollKeyDisplayCandidate:
        if (source_event_id is None) == (roll_observation_id is None):
            raise ValueError("specify exactly one source or roll identity")

        import_id: int | None = None
        current: bool | None = None
        if roll_observation_id is not None:
            target = connection.execute(
                "SELECT import_event_id, displayed_key_count_present "
                "FROM roll_observations WHERE id = ?",
                (roll_observation_id,),
            ).fetchone()
            if target is None:
                return RollKeyDisplayCandidate(
                    None,
                    None,
                    roll_observation_id,
                    None,
                    RollKeyDisplayCandidateStatus.ROLL_TARGET_MISSING,
                    failure_category="roll_target",
                )
            import_id = target["import_event_id"]
            current = (
                bool(target["displayed_key_count_present"])
                if target["displayed_key_count_present"] is not None
                else None
            )
            if isinstance(import_id, bool) or not isinstance(import_id, int) or import_id <= 0:
                return RollKeyDisplayCandidate(
                    None,
                    None,
                    roll_observation_id,
                    current,
                    RollKeyDisplayCandidateStatus.IMPORT_LINK_MISSING,
                    failure_category="import_link",
                )
            sources = connection.execute(
                "SELECT id FROM discord_source_events "
                "WHERE legacy_import_event_id = ?",
                (import_id,),
            ).fetchall()
            if not sources:
                return RollKeyDisplayCandidate(
                    None,
                    import_id,
                    roll_observation_id,
                    current,
                    RollKeyDisplayCandidateStatus.SOURCE_MISSING,
                    failure_category="source",
                )
            if len(sources) != 1:
                return RollKeyDisplayCandidate(
                    None,
                    import_id,
                    roll_observation_id,
                    current,
                    RollKeyDisplayCandidateStatus.IMPORT_LINK_AMBIGUOUS,
                    failure_category="import_link",
                )
            source_event_id = int(sources[0]["id"])

        assert source_event_id is not None

        def result(
            status: RollKeyDisplayCandidateStatus,
            *,
            proposed: bool | None = None,
            parser_version: str | None = None,
            retention: str = "unknown",
        ) -> RollKeyDisplayCandidate:
            return RollKeyDisplayCandidate(
                source_event_id,
                import_id,
                roll_observation_id,
                current,
                status,
                proposed,
                parser_version,
                retention,
                None
                if status
                in (
                    RollKeyDisplayCandidateStatus.ELIGIBLE_NULL,
                    RollKeyDisplayCandidateStatus.ALREADY_ESTABLISHED_MATCHING,
                )
                else status.value.lower(),
            )

        source = connection.execute(
            "SELECT status, raw_text, raw_evidence_expired_at, legacy_import_event_id "
            "FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        if source is None:
            return result(RollKeyDisplayCandidateStatus.SOURCE_MISSING)
        if source["status"] != "succeeded":
            return result(RollKeyDisplayCandidateStatus.SOURCE_NOT_SUCCEEDED)
        raw_text = source["raw_text"]
        if source["raw_evidence_expired_at"] is not None or (
            isinstance(raw_text, str) and raw_text.startswith("[moa:raw-evidence-expired:")
        ):
            return result(RollKeyDisplayCandidateStatus.SOURCE_EXPIRED, retention="expired")
        if not isinstance(raw_text, str) or not raw_text.strip():
            return result(RollKeyDisplayCandidateStatus.SOURCE_UNUSABLE, retention="unusable")
        linked_import_id = source["legacy_import_event_id"]
        if (
            isinstance(linked_import_id, bool)
            or not isinstance(linked_import_id, int)
            or linked_import_id <= 0
        ):
            return result(RollKeyDisplayCandidateStatus.IMPORT_LINK_MISSING, retention="retained")
        if import_id is not None and linked_import_id != import_id:
            return result(RollKeyDisplayCandidateStatus.IDENTITY_MISMATCH, retention="retained")
        import_id = linked_import_id
        if connection.execute(
            "SELECT COUNT(*) FROM discord_source_events WHERE legacy_import_event_id = ?",
            (import_id,),
        ).fetchone()[0] != 1:
            return result(RollKeyDisplayCandidateStatus.IMPORT_LINK_AMBIGUOUS, retention="retained")
        imported = connection.execute(
            "SELECT kind, raw_message_expired_at FROM import_events WHERE id = ?",
            (import_id,),
        ).fetchone()
        if imported is None:
            return result(RollKeyDisplayCandidateStatus.IMPORT_LINK_MISSING, retention="retained")
        if imported["kind"] != "roll":
            return result(RollKeyDisplayCandidateStatus.NON_ROLL_SOURCE, retention="retained")
        if imported["raw_message_expired_at"] is not None:
            return result(RollKeyDisplayCandidateStatus.SOURCE_EXPIRED, retention="expired")
        attempts = connection.execute(
            "SELECT parser_version FROM discord_processing_attempts "
            "WHERE source_event_id = ? AND status = 'succeeded'",
            (source_event_id,),
        ).fetchall()
        if (
            len(attempts) != 1
            or not isinstance(attempts[0]["parser_version"], str)
            or not attempts[0]["parser_version"].strip()
        ):
            return result(
                RollKeyDisplayCandidateStatus.PARSER_PROVENANCE_MISSING_OR_AMBIGUOUS,
                retention="retained",
            )
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
        if (server is not None and server["status"] == "ambiguous") or (
            account is not None and account["status"] == "ambiguous"
        ):
            return result(
                RollKeyDisplayCandidateStatus.ATTRIBUTION_AMBIGUOUS,
                parser_version=parser_version,
                retention="retained",
            )
        if (
            server is None
            or account is None
            or server["status"] != "resolved"
            or account["status"] != "resolved"
            or not isinstance(server["server_name"], str)
            or not isinstance(account["server_name"], str)
            or not isinstance(account["account_name"], str)
        ):
            return result(
                RollKeyDisplayCandidateStatus.ATTRIBUTION_UNRESOLVED,
                parser_version=parser_version,
                retention="retained",
            )
        server_name = CatalogRepository._normalize(server["server_name"])
        account_name = CatalogRepository._normalize(account["account_name"])
        if (
            not server_name
            or not account_name
            or CatalogRepository._normalize(account["server_name"]) != server_name
        ):
            return result(
                RollKeyDisplayCandidateStatus.ATTRIBUTION_CONFLICT,
                parser_version=parser_version,
                retention="retained",
            )
        rolls = connection.execute(
            "SELECT ro.id, ro.claim_rank, ro.kakera_value, "
            "ro.displayed_key_count_present, "
            "sc.normalized_name AS server_name, ac.normalized_name AS account_name, "
            "c.normalized_name AS character_name, c.normalized_series AS series_name "
            "FROM roll_observations AS ro "
            "LEFT JOIN account_contexts AS ac ON ac.id = ro.account_context_id "
            "LEFT JOIN server_contexts AS sc ON sc.id = ac.server_context_id "
            "LEFT JOIN characters AS c ON c.id = ro.character_id "
            "WHERE ro.import_event_id = ?",
            (import_id,),
        ).fetchall()
        if not rolls:
            return result(
                RollKeyDisplayCandidateStatus.ROLL_TARGET_MISSING,
                parser_version=parser_version,
                retention="retained",
            )
        if len(rolls) != 1:
            return result(
                RollKeyDisplayCandidateStatus.ROLL_TARGET_AMBIGUOUS,
                parser_version=parser_version,
                retention="retained",
            )
        roll = rolls[0]
        found_roll_id = int(roll["id"])
        if roll_observation_id is not None and roll_observation_id != found_roll_id:
            return result(
                RollKeyDisplayCandidateStatus.IDENTITY_MISMATCH,
                parser_version=parser_version,
                retention="retained",
            )
        roll_observation_id = found_roll_id
        current = (
            bool(roll["displayed_key_count_present"])
            if roll["displayed_key_count_present"] is not None
            else None
        )
        if roll["server_name"] != server_name or roll["account_name"] != account_name:
            return result(
                RollKeyDisplayCandidateStatus.ATTRIBUTION_CONFLICT,
                parser_version=parser_version,
                retention="retained",
            )
        try:
            parsed = MudaeTextParser().parse_roll(raw_text)
        except (MudaeParseError, TypeError, UnicodeError, ValueError):
            return result(
                RollKeyDisplayCandidateStatus.PARSE_FAILURE,
                parser_version=parser_version,
                retention="retained",
            )
        if (
            roll["character_name"] != CatalogRepository._normalize(parsed.name)
            or roll["series_name"] != CatalogRepository._normalize(parsed.series)
            or roll["claim_rank"] != parsed.claim_rank
            or roll["kakera_value"] != parsed.kakera_value
        ):
            return result(
                RollKeyDisplayCandidateStatus.IDENTITY_MISMATCH,
                parser_version=parser_version,
                retention="retained",
            )
        presence = parsed.displayed_key_count is not None
        if current is None:
            return result(
                RollKeyDisplayCandidateStatus.ELIGIBLE_NULL,
                proposed=presence,
                parser_version=parser_version,
                retention="retained",
            )
        status = (
            RollKeyDisplayCandidateStatus.ALREADY_ESTABLISHED_MATCHING
            if current == presence
            else RollKeyDisplayCandidateStatus.ALREADY_ESTABLISHED_CONFLICT
        )
        return result(status, parser_version=parser_version, retention="retained")
