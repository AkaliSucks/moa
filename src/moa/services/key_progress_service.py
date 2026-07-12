"""Interpret imported harem key counts through the universal key reference."""

from moa.models.catalog import KeyProgressObservation
from moa.models.key import KeyTierDefinition
from moa.services.catalog_service import CatalogService
from moa.services.key_service import KeyService


class KeyProgressService:
    """Join account-owned key counts with universal milestone rules."""

    def __init__(
        self,
        catalog_service: CatalogService | None = None,
        key_service: KeyService | None = None,
    ) -> None:
        self._catalog = catalog_service or CatalogService()
        self._keys = key_service or KeyService()

    def progress(self, server_name: str, account_name: str) -> tuple[KeyProgressObservation, ...]:
        """Return next-unlock progress for every currently imported keyed character."""
        tiers = self._keys.all()
        observations = tuple(
            self._progress_for_entry(entry, tiers)
            for entry in self._catalog.harem_keys(server_name, account_name)
        )
        return tuple(
            sorted(
                observations,
                key=lambda item: (
                    item.keys_until_next_milestone is None,
                    item.keys_until_next_milestone or 0,
                    -(item.kakera_value or 0),
                    item.character_name.casefold(),
                ),
            )
        )

    @staticmethod
    def _progress_for_entry(entry: object, tiers: tuple[KeyTierDefinition, ...]) -> KeyProgressObservation:
        key_count = entry.key_count
        tier = next(
            (
                candidate
                for candidate in tiers
                if key_count >= candidate.minimum_key_count
                and (candidate.maximum_key_count is None or key_count <= candidate.maximum_key_count)
            ),
            None,
        )
        if tier is None:
            raise ValueError(f"No universal key tier is defined for {key_count} keys.")

        next_milestone = min(
            (
                milestone
                for candidate in tiers
                for milestone in candidate.milestones
                if milestone.key_count > key_count
            ),
            key=lambda milestone: milestone.key_count,
            default=None,
        )
        if tier.id == "CHAOS" and key_count >= 11:
            next_key_count = key_count + 1
            effects = ["+5% Kakera value."]
            special = next(
                (milestone for milestone in tier.milestones if milestone.key_count == next_key_count),
                None,
            )
            if special is not None:
                effects.extend(special.effects)
            return KeyProgressObservation(
                character_name=entry.character_name,
                key_count=key_count,
                current_tier=tier.name,
                next_milestone_key_count=next_key_count,
                keys_until_next_milestone=1,
                next_effects=tuple(effects),
                kakera_value=entry.kakera_value,
            )

        if next_milestone is None:
            return KeyProgressObservation(
                character_name=entry.character_name,
                key_count=key_count,
                current_tier=tier.name,
                next_milestone_key_count=None,
                keys_until_next_milestone=None,
                next_effects=("No further fixed milestone is currently modeled.",),
                kakera_value=entry.kakera_value,
            )
        return KeyProgressObservation(
            character_name=entry.character_name,
            key_count=key_count,
            current_tier=tier.name,
            next_milestone_key_count=next_milestone.key_count,
            keys_until_next_milestone=next_milestone.key_count - key_count,
            next_effects=next_milestone.effects,
            kakera_value=entry.kakera_value,
        )
