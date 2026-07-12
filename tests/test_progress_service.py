from datetime import datetime, timedelta, timezone

from moa.models.catalog import KakeraProgressPoint
from moa.services.progress_service import ProgressService


class InMemoryProgressCatalog:
    def __init__(self, points: tuple[KakeraProgressPoint, ...]) -> None:
        self._points = points

    def kakera_history(self, server_name: str, account_name: str) -> tuple[KakeraProgressPoint, ...]:
        return self._points


def test_kakera_progress_measures_change_only_from_multiple_snapshots() -> None:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    service = ProgressService(
        InMemoryProgressCatalog(
            (
                KakeraProgressPoint(kakera_balance=1000, max_badge_count=0, observed_at=start),
                KakeraProgressPoint(
                    kakera_balance=3400,
                    max_badge_count=1,
                    observed_at=start + timedelta(days=2),
                ),
            )
        )
    )

    progress = service.kakera_progress("Lake", "ernieuuu")

    assert progress.kakera_change == 2400
    assert progress.elapsed_seconds == 172800
    assert progress.kakera_per_day == 1200


def test_kakera_progress_does_not_invent_a_rate_from_one_snapshot() -> None:
    point = KakeraProgressPoint(
        kakera_balance=354,
        max_badge_count=0,
        observed_at=datetime.now(timezone.utc),
    )
    progress = ProgressService(InMemoryProgressCatalog((point,))).kakera_progress("Fresh", "cute_beagle")

    assert progress.kakera_change is None
    assert progress.kakera_per_day is None
