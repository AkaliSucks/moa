"""Privacy-safe models for report-only evidence retention eligibility."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


IMPORT_RAW_MESSAGE = "import_raw_message"
DISCORD_SOURCE_RAW_EVIDENCE = "discord_source_raw_evidence"
PROCESSING_ATTEMPT_FAILURE_DETAIL = "processing_attempt_failure_detail"


@dataclass(frozen=True, slots=True)
class RetentionCategoryReport:
    """Aggregate eligibility information for one sensitive evidence category."""

    category: str
    eligible_count: int
    retained_blocked_count: int
    already_expired_count: int
    absent_count: int
    oldest_eligible_anchor: datetime | None
    newest_eligible_anchor: datetime | None
    blocked_reason_counts: tuple[tuple[str, int], ...]

    @property
    def blocked_count(self) -> int:
        """Return the count retained but blocked from future expiry."""

        return self.retained_blocked_count

    @property
    def retained_count(self) -> int:
        """Return all retained evidence, eligible and blocked."""

        return self.eligible_count + self.retained_blocked_count

    @property
    def blocked_reasons(self) -> dict[str, int]:
        """Return bounded reason counts as a convenient read-only report copy."""

        return dict(self.blocked_reason_counts)


@dataclass(frozen=True, slots=True)
class RetentionEligibilityReport:
    """One deterministic, read-only retention eligibility report."""

    as_of: datetime
    cutoff: datetime
    categories: tuple[RetentionCategoryReport, ...]

    def category(self, name: str) -> RetentionCategoryReport:
        """Return a named category report."""

        for category in self.categories:
            if category.category == name:
                return category
        raise KeyError(name)
