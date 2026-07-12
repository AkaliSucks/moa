from datetime import datetime, timedelta, timezone

from moa.models.catalog import TimerStateObservation
from moa.models.character import TimerStateSnapshot
from moa.services.action_service import ActionService


class InMemoryTimerCatalog:
    def __init__(self, observed_at: datetime) -> None:
        self._state = TimerStateObservation(
            server_name="Lake",
            account_name="ernieuuu",
            snapshot=TimerStateSnapshot(
                can_claim_now=True,
                claim_reset_minutes=152,
                rolls_left=0,
                rolls_reset_minutes=32,
                rolls_reset_stock=0,
                vote_reset_minutes=350,
                daily_reset_minutes=496,
                daily_kakera_ready=True,
                rt_available=True,
                can_react_kakera_now=True,
                reaction_power_percent=72,
                kakera_button_power_cost_percent=36,
                soulmate_button_power_cost_percent=18,
                kakera_stock=12114,
                gold_key_stock_remaining=5000,
                gold_key_reset_minutes=152,
                bku_reset_probability_percent=10,
                oh_remaining=0,
                oc_remaining=0,
                oq_remaining=0,
                oq_stored=1,
                ot_remaining=0,
                ouro_refill_minutes=918,
            ),
            observed_at=observed_at,
        )

    def timer_state(self, server_name: str, account_name: str) -> TimerStateObservation:
        return self._state


def test_action_readiness_lists_only_actions_reported_by_a_fresh_snapshot() -> None:
    now = datetime.now(timezone.utc)
    readiness = ActionService(InMemoryTimerCatalog(now)).readiness("Lake", "ernieuuu", now=now)

    assert not readiness.is_stale
    assert readiness.available_actions == (
        "Claim",
        "$dk",
        "$rt",
        "React to Kakera (72% power)",
        "Use stored $oq (1)",
    )
    assert readiness.upcoming_events[0] == ("Roll reset", 32)


def test_action_readiness_requires_a_refresh_for_stale_timer_state() -> None:
    now = datetime.now(timezone.utc)
    readiness = ActionService(InMemoryTimerCatalog(now - timedelta(minutes=6))).readiness(
        "Lake", "ernieuuu", now=now
    )

    assert readiness.is_stale
    assert not readiness.available_actions
    assert "Run and import a fresh $tu" in readiness.status
