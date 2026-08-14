import json

from moa.database.sqlite import connect
from moa.models.character import TimerStateSnapshot
from moa.repositories.catalog_repository import CatalogRepository


TIMER_STATE_FIELDS = (
    "can_claim_now",
    "claim_reset_minutes",
    "rolls_left",
    "rolls_reset_minutes",
    "rolls_reset_stock",
    "vote_reset_minutes",
    "daily_reset_minutes",
    "daily_kakera_ready",
    "rt_available",
    "can_react_kakera_now",
    "reaction_power_percent",
    "kakera_button_power_cost_percent",
    "soulmate_button_power_cost_percent",
    "kakera_stock",
    "gold_key_stock_remaining",
    "gold_key_reset_minutes",
    "bku_reset_probability_percent",
    "oh_remaining",
    "oc_remaining",
    "oq_remaining",
    "oq_stored",
    "ot_remaining",
    "ouro_refill_minutes",
    "rolls_reset_status",
    "rolls_per_hour_limit",
    "rt_reset_minutes",
)


def _complete_timer_state() -> TimerStateSnapshot:
    return TimerStateSnapshot(
        can_claim_now=True,
        claim_reset_minutes=0,
        rolls_left=17,
        rolls_reset_minutes=None,
        rolls_reset_stock=0,
        vote_reset_minutes=650,
        daily_reset_minutes=None,
        daily_kakera_ready=False,
        rt_available=None,
        can_react_kakera_now=True,
        reaction_power_percent=0,
        kakera_button_power_cost_percent=36,
        soulmate_button_power_cost_percent=18,
        kakera_stock=12114,
        gold_key_stock_remaining=None,
        gold_key_reset_minutes=6,
        bku_reset_probability_percent=10,
        oh_remaining=0,
        oc_remaining=3,
        oq_remaining=0,
        oq_stored=1,
        ot_remaining=8,
        ouro_refill_minutes=918,
        rolls_reset_status="limited_timer",
        rolls_per_hour_limit=17,
        rt_reset_minutes=None,
    )


def test_timer_state_round_trips_complete_snapshot(tmp_path) -> None:
    database_path = tmp_path / "timer-state-round-trip.db"
    catalog = CatalogRepository(database_path)
    state = _complete_timer_state()

    assert tuple(state.model_dump()) == TIMER_STATE_FIELDS
    result = catalog.import_timer_state(
        state,
        "Server A",
        "Account X",
        "timer payload",
        "test",
    )

    observation = catalog.timer_state("server a", "account x")

    assert observation is not None
    assert observation.snapshot == state
    assert observation.snapshot.model_dump() == state.model_dump()
    assert set(observation.snapshot.model_dump()) == set(TIMER_STATE_FIELDS)
    assert observation.snapshot.rolls_reset_status == "limited_timer"
    assert observation.snapshot.oq_stored == 1
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT snapshot_json FROM timer_state_observations WHERE import_event_id = ?",
            (result.import_event_id,),
        ).fetchone()

    assert row is not None
    decoded = json.loads(row["snapshot_json"])
    assert set(decoded) == set(TIMER_STATE_FIELDS)
    assert decoded == state.model_dump()


def test_timer_state_preserves_absence_latest_and_account_within_server_scope(tmp_path) -> None:
    database_path = tmp_path / "timer-state-scope.db"
    catalog = CatalogRepository(database_path)
    snapshot_a = _complete_timer_state()
    snapshot_b = snapshot_a.model_copy(
        update={
            "can_claim_now": False,
            "claim_reset_minutes": 9,
            "rolls_left": 1,
            "rolls_reset_status": "timer",
            "daily_kakera_ready": True,
            "oq_stored": 0,
        }
    )
    snapshot_c = snapshot_a.model_copy(
        update={
            "can_claim_now": None,
            "claim_reset_minutes": 27,
            "rolls_left": 4,
            "rolls_reset_status": "vote_required",
            "daily_kakera_ready": None,
            "oq_stored": 2,
        }
    )

    assert catalog.timer_state("Server A", "Account X") is None

    catalog.import_timer_state(snapshot_a, "Server A", "Account X", "first", "test")
    assert catalog.timer_state("server a", "account x").snapshot == snapshot_a

    catalog.import_timer_state(snapshot_b, " server a ", " ACCOUNT X ", "second", "test")
    latest_a = catalog.timer_state("SERVER A", "account x")
    assert latest_a is not None
    assert latest_a.snapshot == snapshot_b

    catalog.import_timer_state(snapshot_c, "Server B", "Account X", "third", "test")
    latest_b = catalog.timer_state("server b", "account x")
    assert latest_b is not None
    assert latest_b.snapshot == snapshot_c
    assert catalog.timer_state("server a", "account x").snapshot == snapshot_b

    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, account_context_id
            FROM timer_state_observations
            ORDER BY id
            """
        ).fetchall()

    assert len(rows) == 3
    assert rows[0]["id"] < rows[1]["id"] < rows[2]["id"]
    assert rows[0]["account_context_id"] == rows[1]["account_context_id"]
    assert rows[1]["account_context_id"] != rows[2]["account_context_id"]
