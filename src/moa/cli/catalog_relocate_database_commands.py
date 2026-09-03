"""Direct registration for the catalog database-relocation command."""

from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console

from moa.database.legacy_database_relocation import (
    DatabaseRelocationAuthorizationIdentity,
    DatabaseRelocationError,
    certify_database_relocation_identity,
    relocate_database,
    relocate_database_with_authorization,
)
from moa.models.data_health import DataHealthFinding


class _AuthorizationIdentityCliError(ValueError):
    """Bounded invalid-input error for authorization CLI fields."""


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _canonical_integer(value: str, *, field: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError:
        raise _AuthorizationIdentityCliError(
            f"{field} must use canonical decimal integer spelling."
        ) from None
    if str(parsed) != value:
        raise _AuthorizationIdentityCliError(
            f"{field} must use canonical decimal integer spelling."
        )
    return parsed


def _build_authorization_identity(
    *,
    normalized_sha256: str | None,
    normalized_size: int | None,
    migration_versions: list[int],
    migration_names: list[str],
    generation_ids: list[int],
    current_generation_id: int | None,
    source_event_count: int | None,
    generation_1_projection_link_count: int | None,
    projection_gap_check_ids: list[str],
    projection_gap_categories: list[str],
    projection_gap_entities: list[str],
    projection_gap_local_identifier_kinds: list[str],
    projection_gap_local_identifiers: list[str],
    projection_gap_reasons: list[str],
    retained_source_preflight_fingerprint: str | None,
) -> DatabaseRelocationAuthorizationIdentity:
    required_scalars = (
        ("--expected-normalized-sha256", normalized_sha256),
        ("--expected-normalized-size", normalized_size),
        ("--expected-current-generation-id", current_generation_id),
        ("--expected-source-event-count", source_event_count),
        (
            "--expected-generation-1-projection-link-count",
            generation_1_projection_link_count,
        ),
        (
            "--expected-retained-source-preflight-fingerprint",
            retained_source_preflight_fingerprint,
        ),
    )
    missing = tuple(name for name, value in required_scalars if value is None)
    if missing:
        raise _AuthorizationIdentityCliError(
            "Authorization-bound relocation requires " + ", ".join(missing) + "."
        )

    assert normalized_sha256 is not None
    assert normalized_size is not None
    assert current_generation_id is not None
    assert source_event_count is not None
    assert generation_1_projection_link_count is not None
    assert retained_source_preflight_fingerprint is not None

    if not _is_sha256(normalized_sha256):
        raise _AuthorizationIdentityCliError(
            "--expected-normalized-sha256 must be exactly 64 lowercase hexadecimal characters."
        )
    if normalized_size < 0:
        raise _AuthorizationIdentityCliError(
            "--expected-normalized-size must be nonnegative."
        )
    if source_event_count < 0:
        raise _AuthorizationIdentityCliError(
            "--expected-source-event-count must be nonnegative."
        )
    if generation_1_projection_link_count < 0:
        raise _AuthorizationIdentityCliError(
            "--expected-generation-1-projection-link-count must be nonnegative."
        )
    if not _is_sha256(retained_source_preflight_fingerprint):
        raise _AuthorizationIdentityCliError(
            "--expected-retained-source-preflight-fingerprint must be exactly 64 lowercase "
            "hexadecimal characters."
        )

    if not migration_versions or not migration_names:
        raise _AuthorizationIdentityCliError(
            "Authorization-bound relocation requires a nonempty migration identity."
        )
    if len(migration_versions) != len(migration_names):
        raise _AuthorizationIdentityCliError(
            "Migration versions and names must have equal lengths."
        )
    if any(version <= 0 for version in migration_versions):
        raise _AuthorizationIdentityCliError("Migration versions must be positive.")
    if migration_versions != sorted(set(migration_versions)):
        raise _AuthorizationIdentityCliError(
            "Migration versions must be strictly increasing and unique."
        )
    if any(name == "" for name in migration_names):
        raise _AuthorizationIdentityCliError("Migration names must be nonempty.")
    migration_identity = tuple(zip(migration_versions, migration_names, strict=True))

    if not generation_ids:
        raise _AuthorizationIdentityCliError(
            "Authorization-bound relocation requires a nonempty generation inventory."
        )
    if any(generation_id <= 0 for generation_id in generation_ids):
        raise _AuthorizationIdentityCliError("Generation IDs must be positive.")
    if generation_ids != sorted(set(generation_ids)):
        raise _AuthorizationIdentityCliError(
            "Generation IDs must be strictly increasing and unique."
        )
    if generation_ids.count(current_generation_id) != 1:
        raise _AuthorizationIdentityCliError(
            "The current generation ID must occur exactly once in the generation inventory."
        )
    generation_inventory = tuple(
        (generation_id, generation_id == current_generation_id)
        for generation_id in generation_ids
    )

    projection_gap_groups = (
        projection_gap_check_ids,
        projection_gap_categories,
        projection_gap_entities,
        projection_gap_local_identifier_kinds,
        projection_gap_local_identifiers,
        projection_gap_reasons,
    )
    if len({len(group) for group in projection_gap_groups}) != 1:
        raise _AuthorizationIdentityCliError(
            "Projection-gap authorization fields must have equal lengths."
        )
    projection_gaps = []
    for index, (check_id, category, entity, kind, identifier, reason) in enumerate(
        zip(*projection_gap_groups, strict=True), start=1
    ):
        if kind == "int":
            local_identifier: int | str = _canonical_integer(
                identifier,
                field=f"Projection-gap local identifier {index}",
            )
        elif kind == "str":
            local_identifier = identifier
        else:
            raise _AuthorizationIdentityCliError(
                "Projection-gap local identifier kinds must be exactly 'int' or 'str'."
            )
        projection_gaps.append(
            DataHealthFinding(check_id, category, entity, local_identifier, reason)
        )

    return DatabaseRelocationAuthorizationIdentity(
        normalized_sha256=normalized_sha256,
        normalized_size=normalized_size,
        migration_identity=migration_identity,
        generation_inventory=generation_inventory,
        source_event_count=source_event_count,
        generation_1_projection_link_count=generation_1_projection_link_count,
        projection_gaps=tuple(projection_gaps),
        retained_source_preflight_fingerprint=retained_source_preflight_fingerprint,
    )


def register_catalog_relocate_database_command(
    catalog_app: typer.Typer,
    console: Console,
    target_path_provider: Callable[[], Path],
) -> None:
    @catalog_app.command("certify-relocation-identity")
    def catalog_certify_relocation_identity(
        source: Path = typer.Argument(..., help="Explicit MOA source database path."),
        expected_destination: Path = typer.Option(
            ...,
            "--expected-destination",
            help="Canonical destination context intended for a later relocation.",
        ),
        expected_moa_checkpoint: str = typer.Option(
            ...,
            "--expected-moa-checkpoint",
            help="Exact verified MOA checkout commit for this certification.",
        ),
        normalize: bool = typer.Option(
            False,
            "--normalize",
            help="Permit SQLite representation normalization and certify identity.",
        ),
        listener_known_writers_stopped: bool = typer.Option(
            False,
            "--listener-known-writers-stopped",
            help="Attest that the listener and known source writers are stopped.",
        ),
    ) -> None:
        """Certify a relocation identity without authorizing or performing relocation."""
        destination = Path(target_path_provider()).resolve(strict=False)
        supplied_destination = expected_destination.expanduser().resolve(strict=False)
        resolved_source = source.expanduser().resolve(strict=False)
        if supplied_destination != destination:
            console.print(
                "[red]RELOCATION_CERTIFICATION_DESTINATION_MISMATCH: expected destination "
                "does not match MOA's current relocation destination.[/red]"
            )
            raise typer.Exit(1)
        if not normalize:
            console.print(f"Source: {resolved_source}")
            console.print(f"Intended destination: {destination}")
            console.print(
                "[yellow]No changes made. Rerun with --normalize only after explicitly "
                "authorizing SQLite representation normalization.[/yellow]"
            )
            return
        try:
            certification = certify_database_relocation_identity(
                resolved_source,
                destination,
                expected_moa_checkpoint,
                listener_known_writers_stopped_attested=(
                    listener_known_writers_stopped
                ),
            )
        except DatabaseRelocationError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        typer.echo(certification.to_json())

    @catalog_app.command("relocate-database")
    def catalog_relocate_database(
        source: Path = typer.Argument(..., help="Explicit legacy MOA database path."),
        authorization_bound: bool = typer.Option(
            False,
            "--authorization-bound",
            help="Require an exact trusted authorization identity before relocation.",
        ),
        expected_normalized_sha256: str | None = typer.Option(
            None,
            "--expected-normalized-sha256",
            help="Authorized normalized main-file SHA-256.",
        ),
        expected_normalized_size: int | None = typer.Option(
            None,
            "--expected-normalized-size",
            help="Authorized normalized main-file size in bytes.",
        ),
        expected_migration_version: list[int] = typer.Option(
            [],
            "--expected-migration-version",
            help="Authorized migration version; repeat in exact order.",
        ),
        expected_migration_name: list[str] = typer.Option(
            [],
            "--expected-migration-name",
            help="Authorized migration name; repeat in exact order.",
        ),
        expected_generation_id: list[int] = typer.Option(
            [],
            "--expected-generation-id",
            help="Authorized projection generation ID; repeat in exact order.",
        ),
        expected_current_generation_id: int | None = typer.Option(
            None,
            "--expected-current-generation-id",
            help="Authorized current projection generation ID.",
        ),
        expected_source_event_count: int | None = typer.Option(
            None,
            "--expected-source-event-count",
            help="Authorized durable source-event count.",
        ),
        expected_generation_1_projection_link_count: int | None = typer.Option(
            None,
            "--expected-generation-1-projection-link-count",
            help="Authorized generation-1 projection-link count.",
        ),
        expected_projection_gap_check_id: list[str] = typer.Option(
            [],
            "--expected-projection-gap-check-id",
            help="Authorized projection-gap check ID; repeat in exact order.",
        ),
        expected_projection_gap_category: list[str] = typer.Option(
            [],
            "--expected-projection-gap-category",
            help="Authorized projection-gap category; repeat in exact order.",
        ),
        expected_projection_gap_entity: list[str] = typer.Option(
            [],
            "--expected-projection-gap-entity",
            help="Authorized projection-gap entity; repeat in exact order.",
        ),
        expected_projection_gap_local_identifier_kind: list[str] = typer.Option(
            [],
            "--expected-projection-gap-local-identifier-kind",
            help="Projection-gap identifier kind, exactly int or str; repeat in order.",
        ),
        expected_projection_gap_local_identifier: list[str] = typer.Option(
            [],
            "--expected-projection-gap-local-identifier",
            help="Authorized projection-gap local identifier; repeat in exact order.",
        ),
        expected_projection_gap_reason: list[str] = typer.Option(
            [],
            "--expected-projection-gap-reason",
            help="Authorized projection-gap reason; repeat in exact order.",
        ),
        expected_retained_source_preflight_fingerprint: str | None = typer.Option(
            None,
            "--expected-retained-source-preflight-fingerprint",
            help="Authorized retained-source preflight fingerprint.",
        ),
        apply: bool = typer.Option(
            False,
            "--apply",
            help="Create the new database and retire the explicit source path.",
        ),
    ) -> None:
        """Move database authority to MOA's per-user application-data location."""
        target = Path(target_path_provider()).resolve(strict=False)
        resolved_source = source.expanduser().resolve(strict=False)
        console.print(f"Source: {resolved_source}")
        console.print(f"Target: {target}")
        console.print("[yellow]The Discord listener must be stopped before relocation.[/yellow]")
        console.print(
            "[yellow]Do not resume old MOA checkouts that write the legacy database after "
            "relocation; no cross-version synchronization is provided.[/yellow]"
        )
        if not apply:
            console.print(
                "[yellow]No changes made. Rerun with --apply after stopping the listener.[/yellow]"
            )
            return

        authorization_fields_supplied = any(
            value is not None
            for value in (
                expected_normalized_sha256,
                expected_normalized_size,
                expected_current_generation_id,
                expected_source_event_count,
                expected_generation_1_projection_link_count,
                expected_retained_source_preflight_fingerprint,
            )
        ) or any(
            (
                expected_migration_version,
                expected_migration_name,
                expected_generation_id,
                expected_projection_gap_check_id,
                expected_projection_gap_category,
                expected_projection_gap_entity,
                expected_projection_gap_local_identifier_kind,
                expected_projection_gap_local_identifier,
                expected_projection_gap_reason,
            )
        )
        if not authorization_bound and authorization_fields_supplied:
            console.print(
                "[red]Authorization identity options require --authorization-bound.[/red]"
            )
            raise typer.Exit(1)

        try:
            if authorization_bound:
                expected_identity = _build_authorization_identity(
                    normalized_sha256=expected_normalized_sha256,
                    normalized_size=expected_normalized_size,
                    migration_versions=expected_migration_version,
                    migration_names=expected_migration_name,
                    generation_ids=expected_generation_id,
                    current_generation_id=expected_current_generation_id,
                    source_event_count=expected_source_event_count,
                    generation_1_projection_link_count=(
                        expected_generation_1_projection_link_count
                    ),
                    projection_gap_check_ids=expected_projection_gap_check_id,
                    projection_gap_categories=expected_projection_gap_category,
                    projection_gap_entities=expected_projection_gap_entity,
                    projection_gap_local_identifier_kinds=(
                        expected_projection_gap_local_identifier_kind
                    ),
                    projection_gap_local_identifiers=(
                        expected_projection_gap_local_identifier
                    ),
                    projection_gap_reasons=expected_projection_gap_reason,
                    retained_source_preflight_fingerprint=(
                        expected_retained_source_preflight_fingerprint
                    ),
                )
                result = relocate_database_with_authorization(
                    resolved_source,
                    target,
                    expected_identity,
                )
            else:
                result = relocate_database(resolved_source, target)
        except _AuthorizationIdentityCliError as error:
            console.print(f"[red]Invalid relocation authorization: {error}[/red]")
            raise typer.Exit(1) from None
        except DatabaseRelocationError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        console.print(f"[green]Database relocated to: {result.target}[/green]")
        console.print(f"Legacy source archived at: {result.source_archive}")
