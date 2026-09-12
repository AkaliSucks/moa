"""SQLite persistence for account-scoped profile observations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect_read_only, run_write_transaction
from moa.models.catalog import ProfileImportResult, ProfileObservation
from moa.models.character import ProfileSnapshot
from moa.repositories._catalog_identity import normalize, upsert_account, upsert_server


_PROFILE_PRESENCE_FIELDS = (
    "pokedex_observed",
    "reactions_observed",
    "mudapins_observed",
    "kakera_balance_observed",
    "keys_observed",
    "bronze_keys_observed",
    "silver_keys_observed",
    "gold_keys_observed",
    "sphere_stock_observed",
    "sphere_counts_observed",
    "badges_observed",
)


@dataclass(frozen=True, slots=True)
class _ProfileImportConnectionResult:
    """Rows created by one profile import on a caller-owned connection."""

    import_event_id: int
    profile_observation_id: int


class ProfileRepository:
    """Persist account-scoped profile observations in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_profile(
        self,
        snapshot: ProfileSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> ProfileImportResult:
        """Store one account-scoped `$profile` progress snapshot."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_profile_with_connection(
                connection,
                profile=snapshot,
                server=server_name,
                account=account_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return ProfileImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def _import_profile_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        profile: ProfileSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _ProfileImportConnectionResult:
        """Store one profile without taking ownership of the surrounding transaction."""
        self._validate_new_profile(profile)
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("profile", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        account_id = upsert_account(connection, server_id, account, observed_at)
        profile_observation_id = int(
            connection.execute(
                """
                INSERT INTO profile_observations (
                    account_context_id, profile_name, collection_size, female_percent, male_percent,
                    pokedex_count, pokedex_json, kakera_reacts_json, mudapins_collected,
                    mudapins_total, kakera_balance, bronze_keys, silver_keys, gold_keys,
                    sphere_stock, spheres_json, displayed_badges_json, observed_at, import_event_id,
                    pokedex_observed, reactions_observed, mudapins_observed,
                    kakera_balance_observed, keys_observed, bronze_keys_observed,
                    silver_keys_observed, gold_keys_observed, sphere_stock_observed,
                    sphere_counts_observed, badges_observed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    profile.profile_name,
                    profile.collection_size,
                    profile.female_percent,
                    profile.male_percent,
                    profile.pokedex_count,
                    json.dumps(list(profile.pokedex_pokemon or ())),
                    json.dumps(profile.kakera_reacts or {}),
                    profile.mudapins_collected,
                    profile.mudapins_total,
                    profile.kakera_balance,
                    profile.bronze_keys if profile.bronze_keys is not None else 0,
                    profile.silver_keys if profile.silver_keys is not None else 0,
                    profile.gold_keys if profile.gold_keys is not None else 0,
                    profile.sphere_stock,
                    json.dumps(profile.spheres or {}),
                    json.dumps(list(profile.displayed_badges or ())),
                    observed_at.isoformat(),
                    import_event_id,
                    *(int(getattr(profile, field_name)) for field_name in _PROFILE_PRESENCE_FIELDS),
                ),
            ).lastrowid
        )
        return _ProfileImportConnectionResult(
            import_event_id=import_event_id,
            profile_observation_id=profile_observation_id,
        )

    def profile(self, server_name: str, account_name: str) -> ProfileObservation | None:
        """Return the latest `$profile` snapshot for one account."""
        with connect_read_only(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT profile_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN profile_observations ON profile_observations.id = (
                    SELECT observations.id FROM profile_observations AS observations
                    WHERE observations.account_context_id = account_contexts.id
                    ORDER BY observations.id DESC LIMIT 1
                )
                WHERE server_contexts.normalized_name = ?
                  AND account_contexts.normalized_name = ?
                """,
                (normalize(server_name), normalize(account_name)),
            ).fetchone()
        if row is None:
            return None
        snapshot = self._snapshot_from_row(row)
        return ProfileObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            snapshot=snapshot,
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )

    @staticmethod
    def _validate_new_profile(profile: ProfileSnapshot) -> None:
        missing_presence = [
            field_name
            for field_name in _PROFILE_PRESENCE_FIELDS
            if getattr(profile, field_name) is None
        ]
        if missing_presence:
            raise ValueError(
                "new profile observations require explicit presence for: "
                + ", ".join(missing_presence)
            )

        ProfileRepository._require_group_presence(
            profile.pokedex_observed,
            (profile.pokedex_count, profile.pokedex_pokemon),
            "pokedex",
        )
        ProfileRepository._require_value_presence(
            profile.reactions_observed, profile.kakera_reacts, "reactions"
        )
        ProfileRepository._require_group_presence(
            profile.mudapins_observed,
            (profile.mudapins_collected, profile.mudapins_total),
            "mudapins",
        )
        ProfileRepository._require_value_presence(
            profile.kakera_balance_observed,
            profile.kakera_balance,
            "kakera_balance",
        )
        for field_name in ("bronze_keys", "silver_keys", "gold_keys"):
            ProfileRepository._require_value_presence(
                getattr(profile, f"{field_name}_observed"),
                getattr(profile, field_name),
                field_name,
            )
        if profile.keys_observed is False and any(
            getattr(profile, f"{field_name}_observed")
            for field_name in ("bronze_keys", "silver_keys", "gold_keys")
        ):
            raise ValueError("an absent keys section cannot contain observed key markers")
        ProfileRepository._require_value_presence(
            profile.sphere_stock_observed, profile.sphere_stock, "sphere_stock"
        )
        ProfileRepository._require_value_presence(
            profile.sphere_counts_observed, profile.spheres, "sphere_counts"
        )
        ProfileRepository._require_value_presence(
            profile.badges_observed, profile.displayed_badges, "badges"
        )

    @staticmethod
    def _require_value_presence(observed: bool | None, value: object, field_name: str) -> None:
        if bool(observed) != (value is not None):
            raise ValueError(f"profile {field_name} value does not match its presence state")

    @staticmethod
    def _require_group_presence(
        observed: bool | None,
        values: tuple[object, ...],
        field_name: str,
    ) -> None:
        if observed is True and any(value is None for value in values):
            raise ValueError(f"profile {field_name} values do not match their presence state")
        if observed is False and any(value is not None for value in values):
            raise ValueError(f"profile {field_name} values do not match their presence state")

    @staticmethod
    def _presence_from_row(row: sqlite3.Row, field_name: str) -> bool | None:
        value = row[field_name]
        if value is None:
            return None
        if value in (0, 1):
            return bool(value)
        raise sqlite3.IntegrityError(
            f"profile_observations has invalid {field_name} presence"
        )

    @classmethod
    def _snapshot_from_row(cls, row: sqlite3.Row) -> ProfileSnapshot:
        pokedex_items = tuple(json.loads(row["pokedex_json"]))
        reactions = dict(json.loads(row["kakera_reacts_json"]))
        spheres = dict(json.loads(row["spheres_json"]))
        badges = tuple(json.loads(row["displayed_badges_json"]))
        presence = {
            field_name: cls._presence_from_row(row, field_name)
            for field_name in _PROFILE_PRESENCE_FIELDS
        }

        pokedex_count, pokedex_pokemon = cls._read_pokedex(
            row["pokedex_count"], pokedex_items, presence["pokedex_observed"]
        )
        mudapins_collected, mudapins_total = cls._read_mudapins(
            row["mudapins_collected"],
            row["mudapins_total"],
            presence["mudapins_observed"],
        )
        key_values = {
            field_name: cls._read_zero_neutral_value(
                int(row[field_name]),
                presence[f"{field_name}_observed"],
                field_name,
            )
            for field_name in ("bronze_keys", "silver_keys", "gold_keys")
        }
        cls._validate_keys_section_from_row(row, presence)

        return ProfileSnapshot(
            profile_name=row["profile_name"],
            collection_size=row["collection_size"],
            female_percent=row["female_percent"],
            male_percent=row["male_percent"],
            pokedex_count=pokedex_count,
            pokedex_pokemon=pokedex_pokemon,
            kakera_reacts=cls._read_collection(
                reactions, presence["reactions_observed"], "reactions"
            ),
            mudapins_collected=mudapins_collected,
            mudapins_total=mudapins_total,
            kakera_balance=cls._read_nullable_scalar(
                row["kakera_balance"],
                presence["kakera_balance_observed"],
                "kakera_balance",
            ),
            **key_values,
            sphere_stock=cls._read_nullable_scalar(
                row["sphere_stock"],
                presence["sphere_stock_observed"],
                "sphere_stock",
            ),
            spheres=cls._read_collection(
                spheres, presence["sphere_counts_observed"], "sphere_counts"
            ),
            displayed_badges=cls._read_collection(
                badges, presence["badges_observed"], "badges"
            ),
            **presence,
        )

    @staticmethod
    def _read_pokedex(
        count: int | None,
        items: tuple[str, ...],
        observed: bool | None,
    ) -> tuple[int | None, tuple[str, ...] | None]:
        if observed is True:
            if count is None:
                raise sqlite3.IntegrityError(
                    "profile_observations has inconsistent pokedex presence"
                )
            return int(count), items
        if observed is False:
            if count is not None or items:
                raise sqlite3.IntegrityError(
                    "profile_observations has inconsistent pokedex presence"
                )
            return None, None
        if ProfileRepository._pokedex_is_coherently_observed(count, items):
            raise sqlite3.IntegrityError(
                "profile_observations has unbackfilled pokedex presence"
            )
        return (int(count) if count is not None else None), (items or None)

    @staticmethod
    def _read_mudapins(
        collected: int | None,
        total: int | None,
        observed: bool | None,
    ) -> tuple[int | None, int | None]:
        if observed is True:
            if collected is None or total is None:
                raise sqlite3.IntegrityError(
                    "profile_observations has inconsistent mudapins presence"
                )
            return int(collected), int(total)
        if observed is False:
            if collected is not None or total is not None:
                raise sqlite3.IntegrityError(
                    "profile_observations has inconsistent mudapins presence"
                )
            return None, None
        if collected is not None and total is not None:
            raise sqlite3.IntegrityError(
                "profile_observations has unbackfilled mudapins presence"
            )
        return (
            int(collected) if collected is not None else None,
            int(total) if total is not None else None,
        )

    @staticmethod
    def _read_nullable_scalar(
        value: int | None,
        observed: bool | None,
        field_name: str,
    ) -> int | None:
        if observed is True:
            if value is None:
                raise sqlite3.IntegrityError(
                    f"profile_observations has inconsistent {field_name} presence"
                )
            return int(value)
        if value is not None:
            state = "absent" if observed is False else "unbackfilled"
            raise sqlite3.IntegrityError(
                f"profile_observations has {state} {field_name} presence"
            )
        return None

    @staticmethod
    def _read_collection(value, observed: bool | None, field_name: str):
        if observed is True:
            return value
        if value:
            state = "absent" if observed is False else "unbackfilled"
            raise sqlite3.IntegrityError(
                f"profile_observations has {state} {field_name} presence"
            )
        return None

    @staticmethod
    def _read_zero_neutral_value(
        value: int,
        observed: bool | None,
        field_name: str,
    ) -> int | None:
        if observed is True:
            return value
        if value != 0:
            state = "absent" if observed is False else "unbackfilled"
            raise sqlite3.IntegrityError(
                f"profile_observations has {state} {field_name} presence"
            )
        return None

    @staticmethod
    def _validate_keys_section_from_row(
        row: sqlite3.Row,
        presence: dict[str, bool | None],
    ) -> None:
        keys_observed = presence["keys_observed"]
        marker_presence = tuple(
            presence[f"{field_name}_observed"]
            for field_name in ("bronze_keys", "silver_keys", "gold_keys")
        )
        if keys_observed is True:
            return
        if keys_observed is False:
            if marker_presence != (False, False, False):
                raise sqlite3.IntegrityError(
                    "profile_observations has inconsistent keys section presence"
                )
            return
        if any(value is True for value in marker_presence) or any(
            int(row[field_name]) != 0
            for field_name in ("bronze_keys", "silver_keys", "gold_keys")
        ):
            raise sqlite3.IntegrityError(
                "profile_observations has unbackfilled keys section presence"
            )

    @staticmethod
    def _pokedex_is_coherently_observed(
        count: int | None,
        items: tuple[str, ...],
    ) -> bool:
        return count is not None and (bool(items) or count == 0)

    @classmethod
    def _row_matches_profile(cls, row: sqlite3.Row, profile: ProfileSnapshot) -> bool:
        cls._validate_new_profile(profile)
        cls._snapshot_from_row(row)
        expected = {
            "profile_name": profile.profile_name,
            "collection_size": profile.collection_size,
            "female_percent": profile.female_percent,
            "male_percent": profile.male_percent,
            "pokedex_count": profile.pokedex_count,
            "pokedex_json": tuple(profile.pokedex_pokemon or ()),
            "kakera_reacts_json": dict(profile.kakera_reacts or {}),
            "mudapins_collected": profile.mudapins_collected,
            "mudapins_total": profile.mudapins_total,
            "kakera_balance": profile.kakera_balance,
            "bronze_keys": profile.bronze_keys if profile.bronze_keys is not None else 0,
            "silver_keys": profile.silver_keys if profile.silver_keys is not None else 0,
            "gold_keys": profile.gold_keys if profile.gold_keys is not None else 0,
            "sphere_stock": profile.sphere_stock,
            "spheres_json": dict(profile.spheres or {}),
            "displayed_badges_json": tuple(profile.displayed_badges or ()),
        }
        stored = {
            "profile_name": row["profile_name"],
            "collection_size": row["collection_size"],
            "female_percent": row["female_percent"],
            "male_percent": row["male_percent"],
            "pokedex_count": row["pokedex_count"],
            "pokedex_json": tuple(json.loads(row["pokedex_json"])),
            "kakera_reacts_json": dict(json.loads(row["kakera_reacts_json"])),
            "mudapins_collected": row["mudapins_collected"],
            "mudapins_total": row["mudapins_total"],
            "kakera_balance": row["kakera_balance"],
            "bronze_keys": row["bronze_keys"],
            "silver_keys": row["silver_keys"],
            "gold_keys": row["gold_keys"],
            "sphere_stock": row["sphere_stock"],
            "spheres_json": dict(json.loads(row["spheres_json"])),
            "displayed_badges_json": tuple(json.loads(row["displayed_badges_json"])),
        }
        if stored != expected:
            return False
        return all(
            row[field_name] is None
            or getattr(profile, field_name) is not None
            and bool(row[field_name]) == getattr(profile, field_name)
            for field_name in _PROFILE_PRESENCE_FIELDS
        )
