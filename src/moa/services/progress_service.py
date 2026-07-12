"""Summarize measured Kakera progression from imported history."""

from moa.models.catalog import KakeraProgressSummary
from moa.services.catalog_service import CatalogService


class ProgressService:
    """Calculate trends only from timestamped `$k` observations."""

    def __init__(self, catalog_service: CatalogService | None = None) -> None:
        self._catalog = catalog_service or CatalogService()

    def kakera_progress(self, server_name: str, account_name: str) -> KakeraProgressSummary:
        """Return measured change and rate; never infer a rate from one sample."""
        observations = self._catalog.kakera_history(server_name, account_name)
        if len(observations) < 2:
            return KakeraProgressSummary(
                server_name=server_name.strip(),
                account_name=account_name.strip(),
                observations=observations,
                kakera_change=None,
                elapsed_seconds=None,
                kakera_per_day=None,
            )
        first = observations[0]
        latest = observations[-1]
        elapsed_seconds = max(0, int((latest.observed_at - first.observed_at).total_seconds()))
        kakera_change = latest.kakera_balance - first.kakera_balance
        kakera_per_day = (
            kakera_change / (elapsed_seconds / 86_400) if elapsed_seconds > 0 else None
        )
        return KakeraProgressSummary(
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observations=observations,
            kakera_change=kakera_change,
            elapsed_seconds=elapsed_seconds,
            kakera_per_day=kakera_per_day,
        )
