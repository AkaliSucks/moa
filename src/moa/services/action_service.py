"""Turn a fresh `$tu` snapshot into a conservative action checklist."""

from datetime import datetime, timezone

from moa.models.catalog import ActionReadiness
from moa.services.catalog_service import CatalogService


class ActionService:
    """Report only actions a recent Mudae `$tu` snapshot directly supports."""

    _STALE_AFTER_SECONDS = 5 * 60

    def __init__(self, catalog_service: CatalogService | None = None) -> None:
        self._catalog = catalog_service or CatalogService()

    def readiness(
        self,
        server_name: str,
        account_name: str,
        now: datetime | None = None,
    ) -> ActionReadiness:
        """Return the current checklist, refusing to treat old timers as live state."""
        state = self._catalog.timer_state(server_name, account_name)
        if state is None:
            return ActionReadiness(
                server_name=server_name.strip(),
                account_name=account_name.strip(),
                observed_at=None,
                snapshot_age_seconds=None,
                is_stale=True,
                status="No $tu snapshot imported. Refresh Mudae timers first.",
                available_actions=(),
                upcoming_events=(),
            )

        current_time = now or datetime.now(timezone.utc)
        age_seconds = max(0, int((current_time - state.observed_at).total_seconds()))
        if age_seconds > self._STALE_AFTER_SECONDS:
            return ActionReadiness(
                server_name=state.server_name,
                account_name=state.account_name,
                observed_at=state.observed_at,
                snapshot_age_seconds=age_seconds,
                is_stale=True,
                status="Timer snapshot is stale. Run and import a fresh $tu before acting on it.",
                available_actions=(),
                upcoming_events=(),
            )

        snapshot = state.snapshot
        actions: list[str] = []
        if snapshot.can_claim_now:
            actions.append("Claim")
        if snapshot.rolls_left is not None and snapshot.rolls_left > 0:
            actions.append(f"Roll ({snapshot.rolls_left} left)")
        if snapshot.daily_kakera_ready:
            actions.append("$dk")
        if snapshot.rt_available:
            actions.append("$rt")
        if snapshot.can_react_kakera_now and (snapshot.reaction_power_percent or 0) > 0:
            actions.append(f"React to Kakera ({snapshot.reaction_power_percent}% power)")
        if (snapshot.oq_stored or 0) > 0:
            actions.append(f"Use stored $oq ({snapshot.oq_stored})")

        upcoming: list[tuple[str, int]] = []
        if snapshot.rolls_reset_minutes is not None:
            upcoming.append(("Roll reset", snapshot.rolls_reset_minutes))
        if snapshot.claim_reset_minutes is not None:
            label = "Claim reset window" if snapshot.can_claim_now else "Claim available"
            upcoming.append((label, snapshot.claim_reset_minutes))
        if snapshot.daily_reset_minutes is not None:
            upcoming.append(("$daily reset", snapshot.daily_reset_minutes))
        if snapshot.ouro_refill_minutes is not None:
            upcoming.append(("$oh refill", snapshot.ouro_refill_minutes))
        if snapshot.vote_reset_minutes is not None:
            upcoming.append(("Vote", snapshot.vote_reset_minutes))
        if snapshot.gold_key_reset_minutes is not None:
            upcoming.append(("Gold-key stock reset", snapshot.gold_key_reset_minutes))
        return ActionReadiness(
            server_name=state.server_name,
            account_name=state.account_name,
            observed_at=state.observed_at,
            snapshot_age_seconds=age_seconds,
            is_stale=False,
            status="Actions were available when this $tu snapshot was imported.",
            available_actions=tuple(actions),
            upcoming_events=tuple(sorted(upcoming, key=lambda event: event[1])),
        )
