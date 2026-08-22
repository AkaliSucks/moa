"""Application service for report-only local data-health scans."""

from __future__ import annotations

from pathlib import Path

from moa.database.sqlite import DEFAULT_DATABASE_PATH, connect_read_only
from moa.models.data_health import DataHealthFinding
from moa.repositories.data_health_repository import DataHealthRepository


def _finding_sort_key(finding: DataHealthFinding) -> tuple[str, str, tuple[int, int | str]]:
    identifier = finding.local_identifier
    if isinstance(identifier, int):
        identifier_key: tuple[int, int | str] = (0, identifier)
    else:
        identifier_key = (1, identifier)
    return finding.check_id, finding.entity, identifier_key


class DataHealthService:
    """Coordinate one consistent, read-only data-health scan."""

    def __init__(self, database_path: Path | None = None) -> None:
        self._database_path = database_path

    def find_orphans(self) -> tuple[DataHealthFinding, ...]:
        connection = connect_read_only(self._database_path or DEFAULT_DATABASE_PATH)
        try:
            connection.execute("BEGIN")
            repository = DataHealthRepository(connection)
            repository.validate_schema()
            findings = repository.find_orphans()
            return tuple(sorted(findings, key=_finding_sort_key))
        finally:
            try:
                if connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()

    def find_impossible_identities(self) -> tuple[DataHealthFinding, ...]:
        connection = connect_read_only(self._database_path or DEFAULT_DATABASE_PATH)
        try:
            connection.execute("BEGIN")
            repository = DataHealthRepository(connection)
            repository.validate_schema()
            findings = repository.scan_impossible_identities()
            return tuple(sorted(findings, key=_finding_sort_key))
        finally:
            try:
                if connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()

    def find_duplicates(self) -> tuple[DataHealthFinding, ...]:
        connection = connect_read_only(self._database_path or DEFAULT_DATABASE_PATH)
        try:
            connection.execute("BEGIN")
            repository = DataHealthRepository(connection)
            repository.validate_schema()
            findings = repository.scan_duplicates()
            return tuple(sorted(findings, key=_finding_sort_key))
        finally:
            try:
                if connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()

    def find_projection_gaps(self) -> tuple[DataHealthFinding, ...]:
        connection = connect_read_only(self._database_path or DEFAULT_DATABASE_PATH)
        try:
            connection.execute("BEGIN")
            repository = DataHealthRepository(connection)
            repository.validate_schema()
            findings = repository.scan_projection_gaps()
            return tuple(sorted(findings, key=_finding_sort_key))
        finally:
            try:
                if connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()
