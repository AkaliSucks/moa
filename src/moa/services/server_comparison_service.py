"""Compare the latest imported Mudae server configurations."""

from moa.models.catalog import (
    ServerSettingComparison,
    ServerSettingsComparison,
)
from moa.services.catalog_service import CatalogService


class ServerComparisonService:
    """Build a transparent, settings-only comparison between two servers."""

    def __init__(self, catalog_service: CatalogService | None = None) -> None:
        self._catalog = catalog_service or CatalogService()

    def compare(self, left_server_name: str, right_server_name: str) -> ServerSettingsComparison:
        """Compare the latest `$settings` imports in a stable display order."""
        left = self._catalog.server_settings(left_server_name)
        right = self._catalog.server_settings(right_server_name)
        if left is None:
            raise ValueError(f"No $settings snapshot imported for {left_server_name!r}.")
        if right is None:
            raise ValueError(f"No $settings snapshot imported for {right_server_name!r}.")

        left_metrics = {metric.label: metric.value for metric in left.metrics}
        right_metrics = {metric.label: metric.value for metric in right.metrics}
        labels = list(left_metrics)
        labels.extend(label for label in right_metrics if label not in left_metrics)
        entries = tuple(
            ServerSettingComparison(
                label=label,
                left_value=left_metrics.get(label, "Not reported"),
                right_value=right_metrics.get(label, "Not reported"),
                matches=left_metrics.get(label) == right_metrics.get(label),
            )
            for label in labels
        )
        return ServerSettingsComparison(
            left_server_name=left.server_name,
            right_server_name=right.server_name,
            entries=entries,
        )
