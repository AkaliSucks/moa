from __future__ import annotations

import pytest

from moa.database.sqlite import connect
from moa.parser.mudae import MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.roll_key_display_presence_backfill_service import (
    RollKeyDisplayBackfillStatus,
    RollKeyDisplayPresenceBackfillService,
)
from test_retained_source_reprojection_admission_service import _fixture


NO_KEY_ROLL = "Character\nSeries\nClaims: #1\n100:kakera:"
DISPLAYED_KEY_ROLL = "Character\nSeries\n:bronzekey: (1) $embedcolor unlocked!\n100:kakera:"


@pytest.mark.parametrize(
    ("raw", "presence", "key_rows"),
    ((NO_KEY_ROLL, 0, 0), (DISPLAYED_KEY_ROLL, 1, 1)),
)
def test_forward_roll_persists_parser_presence_without_synthetic_keys(
    tmp_path, raw, presence, key_rows
) -> None:
    path = tmp_path / "forward.db"
    catalog = CatalogRepository(path)
    catalog.import_roll(MudaeTextParser().parse_roll(raw), "Server", "Account", raw, "test")
    with connect(path) as connection:
        assert connection.execute(
            "SELECT displayed_key_count_present FROM roll_observations"
        ).fetchone()[0] == presence
        rows = connection.execute(
            "SELECT key_type, key_count FROM harem_key_observations"
        ).fetchall()
        assert len(rows) == key_rows
        if key_rows:
            assert tuple(rows[0]) == ("bronze", 1)
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations WHERE key_count = 0"
        ).fetchone()[0] == 0
    assert catalog.recent_rolls("Server", "Account", 1)[0].displayed_key_count_present is bool(presence)


def _seed_retained_roll(path, raw: str, *, without_key: bool) -> tuple[int, int, int]:
    CatalogRepository(path)
    with connect(path) as connection:
        source_id = _fixture(connection, "roll")
        import_id = int(connection.execute(
            "SELECT legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_id,),
        ).fetchone()[0])
        if without_key:
            connection.execute("DELETE FROM harem_key_observations")
        if raw == DISPLAYED_KEY_ROLL:
            connection.execute("UPDATE roll_observations SET claim_rank = NULL")
        connection.execute(
            "UPDATE discord_source_events SET raw_text = ? WHERE id = ?", (raw, source_id)
        )
        roll_id = int(connection.execute(
            "SELECT id FROM roll_observations WHERE import_event_id = ?", (import_id,)
        ).fetchone()[0])
        connection.commit()
    return source_id, import_id, roll_id


@pytest.mark.parametrize(
    ("raw", "presence", "without_key"),
    ((NO_KEY_ROLL, 0, True), (DISPLAYED_KEY_ROLL, 1, False)),
)
def test_backfill_establishes_both_values_and_is_idempotent(
    tmp_path, raw, presence, without_key
) -> None:
    path = tmp_path / "backfill.db"
    source_id, import_id, roll_id = _seed_retained_roll(path, raw, without_key=without_key)
    with connect(path) as connection:
        before = tuple(connection.execute(
            "SELECT key_type, key_count, import_event_id FROM harem_key_observations"
        ).fetchall())
        assert connection.execute(
            "SELECT displayed_key_count_present FROM roll_observations WHERE id = ?", (roll_id,)
        ).fetchone()[0] is None
    service = RollKeyDisplayPresenceBackfillService(path)
    first = service.backfill(source_id)
    second = service.backfill(source_id)
    assert first.status is RollKeyDisplayBackfillStatus.UPDATED
    assert second.status is RollKeyDisplayBackfillStatus.ALREADY_ESTABLISHED
    assert first.source_event_id == second.source_event_id == source_id
    assert first.import_event_id == second.import_event_id == import_id
    assert first.roll_observation_id == second.roll_observation_id == roll_id
    assert first.displayed_key_count_present is bool(presence)
    assert first.parser_version == "p"
    with connect(path) as connection:
        assert connection.execute(
            "SELECT displayed_key_count_present FROM roll_observations WHERE id = ?", (roll_id,)
        ).fetchone()[0] == presence
        assert tuple(connection.execute(
            "SELECT key_type, key_count, import_event_id FROM harem_key_observations"
        ).fetchall()) == before
        assert connection.execute(
            "SELECT legacy_import_event_id FROM discord_source_events WHERE id = ?", (source_id,)
        ).fetchone()[0] == import_id
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations WHERE key_count = 0"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("mutation", "status"),
    (
        ("UPDATE discord_source_events SET raw_text = ''", RollKeyDisplayBackfillStatus.UNAVAILABLE),
        ("UPDATE discord_source_events SET raw_text = '[moa:raw-evidence-expired:v1]', raw_evidence_expired_at = 'now'", RollKeyDisplayBackfillStatus.UNAVAILABLE),
        ("UPDATE discord_source_events SET raw_text = 'not a roll'", RollKeyDisplayBackfillStatus.UNAVAILABLE),
        ("DELETE FROM roll_observations", RollKeyDisplayBackfillStatus.INCOHERENT),
        ("INSERT INTO roll_observations (account_context_id, character_id, claim_rank, kakera_value, observed_at, import_event_id) SELECT account_context_id, character_id, claim_rank, kakera_value, observed_at, import_event_id FROM roll_observations", RollKeyDisplayBackfillStatus.INCOHERENT),
        ("UPDATE roll_observations SET displayed_key_count_present = 1", RollKeyDisplayBackfillStatus.CONFLICT),
        ("UPDATE discord_source_events SET legacy_import_event_id = NULL", RollKeyDisplayBackfillStatus.INCOHERENT),
        ("UPDATE characters SET normalized_name = 'other'", RollKeyDisplayBackfillStatus.INCOHERENT),
    ),
)
def test_backfill_fails_closed_without_other_writes(tmp_path, mutation, status) -> None:
    path = tmp_path / "blocked.db"
    source_id, _, _ = _seed_retained_roll(path, NO_KEY_ROLL, without_key=True)
    with connect(path) as connection:
        connection.execute(mutation)
        connection.commit()
        before_rolls = tuple(connection.execute("SELECT * FROM roll_observations").fetchall())
        before_keys = tuple(connection.execute("SELECT * FROM harem_key_observations").fetchall())
        before_sources = tuple(connection.execute("SELECT * FROM discord_source_events").fetchall())
    outcome = RollKeyDisplayPresenceBackfillService(path).backfill(source_id)
    assert outcome.status is status
    with connect(path) as connection:
        assert tuple(connection.execute("SELECT * FROM roll_observations").fetchall()) == before_rolls
        assert tuple(connection.execute("SELECT * FROM harem_key_observations").fetchall()) == before_keys
        assert tuple(connection.execute("SELECT * FROM discord_source_events").fetchall()) == before_sources
