"""Direct registration for the catalog database-relocation command."""

from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console

from moa.database.legacy_database_relocation import (
    DatabaseFileObservation,
    DatabaseRelocationAuthorizationIdentity,
    DatabaseRelocationError,
    DatabaseRelocationJournalModePreparationAuthority,
    DatabaseRelocationJournalModePreparationError,
    DatabaseSidecarObservation,
    DatabaseWalRecoveryAuthority,
    certify_database_relocation_identity,
    prepare_database_relocation_journal_mode,
    recover_database_wal,
    relocate_database,
    relocate_database_with_authorization,
)
from moa.models.data_health import DataHealthFinding


class _AuthorizationIdentityCliError(ValueError):
    """Bounded invalid-input error for authorization CLI fields."""


class _PreparationAuthorityCliError(ValueError):
    """Bounded invalid-input error for journal-mode preparation authority."""


class _RecoveryAuthorityCliError(ValueError):
    """Bounded invalid-input error for WAL-recovery authority."""


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


def _build_file_observation(
    *,
    state: str,
    size: int | None,
    sha256: str | None,
    field: str,
) -> DatabaseFileObservation:
    if state == "absent":
        if size is not None or sha256 is not None:
            raise _PreparationAuthorityCliError(
                f"{field} size/SHA-256 must be omitted when state is absent."
            )
        return DatabaseFileObservation(False, None, None)
    if state != "present":
        raise _PreparationAuthorityCliError(
            f"{field} state must be exactly 'absent' or 'present'."
        )
    if size is None or sha256 is None:
        raise _PreparationAuthorityCliError(
            f"{field} size and SHA-256 are required when state is present."
        )
    if size < 0:
        raise _PreparationAuthorityCliError(f"{field} size must be nonnegative.")
    if not _is_sha256(sha256):
        raise _PreparationAuthorityCliError(
            f"{field} SHA-256 must be exactly 64 lowercase hexadecimal characters."
        )
    return DatabaseFileObservation(True, size, sha256)


def _build_recovery_file_observation(
    *,
    state: str,
    size: int | None,
    sha256: str | None,
    field: str,
) -> DatabaseFileObservation:
    try:
        return _build_file_observation(
            state=state,
            size=size,
            sha256=sha256,
            field=field,
        )
    except _PreparationAuthorityCliError as error:
        raise _RecoveryAuthorityCliError(str(error)) from None


def _build_recovery_sidecar_observation(
    *, state: str, size: int | None, field: str
) -> DatabaseSidecarObservation:
    if state == "absent":
        if size is not None:
            raise _RecoveryAuthorityCliError(
                f"{field} size must be omitted when state is absent."
            )
        return DatabaseSidecarObservation(False, None)
    if state != "present":
        raise _RecoveryAuthorityCliError(
            f"{field} state must be exactly 'absent' or 'present'."
        )
    if size is None or size < 0:
        raise _RecoveryAuthorityCliError(
            f"{field} requires a nonnegative size when present."
        )
    return DatabaseSidecarObservation(True, size)


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
    @catalog_app.command("recover-database-wal")
    def catalog_recover_database_wal(
        source: Path = typer.Argument(..., help="Explicit MOA WAL source database path."),
        expected_destination: Path = typer.Option(
            ...,
            "--expected-destination",
            help="Absent-only destination scope intended for a later relocation.",
        ),
        expected_moa_checkpoint: str = typer.Option(
            ...,
            "--expected-moa-checkpoint",
            help="Exact verified MOA checkout commit for this recovery.",
        ),
        authorization_format: str = typer.Option(
            ...,
            "--authorization-format",
            help="Exact typed recovery-authorization format.",
        ),
        authorization_version: int = typer.Option(
            ...,
            "--authorization-version",
            help="Exact typed recovery-authorization version.",
        ),
        authorization_action: str = typer.Option(
            ...,
            "--authorization-action",
            help="Exact recovery-only semantic action.",
        ),
        authorization_id: str = typer.Option(
            ...,
            "--authorization-id",
            help="Explicit caller-supplied one-attempt authorization ID.",
        ),
        authorization_sha256: str = typer.Option(
            ...,
            "--authorization-sha256",
            help="SHA-256 binding over the canonical recovery authority.",
        ),
        max_attempts: int = typer.Option(
            ...,
            "--max-attempts",
            help="Exact bounded attempt count; recovery requires 1.",
        ),
        expected_main_sha256: str = typer.Option(
            ...,
            "--expected-main-sha256",
            help="Exact pre-recovery source main-file SHA-256.",
        ),
        expected_main_size: int = typer.Option(
            ...,
            "--expected-main-size",
            help="Exact pre-recovery source main-file size in bytes.",
        ),
        expected_wal_state: str = typer.Option(
            ...,
            "--expected-wal-state",
            help="Exact pre-recovery -wal state: absent or present.",
        ),
        expected_wal_size: int | None = typer.Option(
            None,
            "--expected-wal-size",
            help="Exact pre-recovery -wal size when present.",
        ),
        expected_wal_sha256: str | None = typer.Option(
            None,
            "--expected-wal-sha256",
            help="Exact pre-recovery -wal SHA-256 when present.",
        ),
        expected_shm_state: str = typer.Option(
            ...,
            "--expected-shm-state",
            help="Exact pre-recovery -shm state: absent or present.",
        ),
        expected_shm_size: int | None = typer.Option(
            None,
            "--expected-shm-size",
            help="Exact pre-recovery -shm size when present; SHM is never hashed.",
        ),
        expected_journal_mode: str = typer.Option(
            ...,
            "--expected-journal-mode",
            help="Exact pre-recovery journal mode; recovery requires wal.",
        ),
        listener_known_writers_stopped: bool = typer.Option(
            False,
            "--listener-known-writers-stopped",
            help="Attest that the listener and known source writers are stopped.",
        ),
        apply: bool = typer.Option(
            False,
            "--apply",
            help="Execute only the explicitly bound WAL recovery attempt.",
        ),
    ) -> None:
        """Normalize one authorized WAL source without relocating or retiring it."""
        destination = Path(target_path_provider()).resolve(strict=False)
        supplied_destination = expected_destination.expanduser().resolve(strict=False)
        resolved_source = source.expanduser().resolve(strict=False)
        if supplied_destination != destination:
            console.print(
                "[red]DATABASE_WAL_RECOVERY_DESTINATION_MISMATCH: expected destination "
                "does not match MOA's current relocation destination.[/red]"
            )
            raise typer.Exit(1)
        if not apply:
            console.print(f"Source: {resolved_source}")
            console.print(f"Absent-only intended destination: {destination}")
            console.print(
                "[yellow]No changes made. Rerun with --apply only for this exact bound "
                "recovery authority after stopping known readers and writers.[/yellow]"
            )
            return
        try:
            if expected_main_size < 0:
                raise _RecoveryAuthorityCliError(
                    "--expected-main-size must be nonnegative."
                )
            if not _is_sha256(expected_main_sha256):
                raise _RecoveryAuthorityCliError(
                    "--expected-main-sha256 must be exactly 64 lowercase hexadecimal "
                    "characters."
                )
            if not _is_sha256(authorization_sha256):
                raise _RecoveryAuthorityCliError(
                    "--authorization-sha256 must be exactly 64 lowercase hexadecimal "
                    "characters."
                )
            wal = _build_recovery_file_observation(
                state=expected_wal_state,
                size=expected_wal_size,
                sha256=expected_wal_sha256,
                field="--expected-wal",
            )
            shm = _build_recovery_sidecar_observation(
                state=expected_shm_state,
                size=expected_shm_size,
                field="--expected-shm",
            )
            evidence = recover_database_wal(
                DatabaseWalRecoveryAuthority(
                    authorization_format=authorization_format,
                    authorization_version=authorization_version,
                    action=authorization_action,
                    authorization_id=authorization_id,
                    source=resolved_source,
                    intended_destination=destination,
                    expected_moa_checkpoint=expected_moa_checkpoint,
                    expected_main=DatabaseFileObservation(
                        True, expected_main_size, expected_main_sha256
                    ),
                    expected_wal=wal,
                    expected_shm=shm,
                    expected_journal_mode=expected_journal_mode,
                    listener_known_writers_stopped_attested=(
                        listener_known_writers_stopped
                    ),
                    max_attempts=max_attempts,
                    authorization_record_sha256=authorization_sha256,
                )
            )
        except _RecoveryAuthorityCliError as error:
            console.print(f"[red]Invalid recovery authority: {error}[/red]")
            raise typer.Exit(1) from None
        except DatabaseRelocationError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        typer.echo(evidence.to_json())

    @catalog_app.command("prepare-relocation-journal-mode")
    def catalog_prepare_relocation_journal_mode(
        source: Path = typer.Argument(..., help="Explicit MOA source database path."),
        expected_destination: Path = typer.Option(
            ...,
            "--expected-destination",
            help="Canonical destination context intended for a later relocation.",
        ),
        expected_moa_checkpoint: str = typer.Option(
            ...,
            "--expected-moa-checkpoint",
            help="Exact verified MOA checkout commit for this preparation.",
        ),
        expected_preparation_sha256: str = typer.Option(
            ...,
            "--expected-preparation-sha256",
            help="Exact pre-transition source main-file SHA-256.",
        ),
        expected_preparation_size: int = typer.Option(
            ...,
            "--expected-preparation-size",
            help="Exact pre-transition source main-file size in bytes.",
        ),
        expected_journal_mode: str = typer.Option(
            ...,
            "--expected-journal-mode",
            help="Exact pre-transition persistent rollback-journal mode.",
        ),
        expected_journal_state: str = typer.Option(
            ...,
            "--expected-journal-state",
            help="Exact -journal state: absent or present.",
        ),
        expected_journal_size: int | None = typer.Option(
            None,
            "--expected-journal-size",
            help="Exact -journal size when present.",
        ),
        expected_journal_sha256: str | None = typer.Option(
            None,
            "--expected-journal-sha256",
            help="Exact -journal SHA-256 when present.",
        ),
        expected_wal_state: str = typer.Option(
            ...,
            "--expected-wal-state",
            help="Exact -wal state: absent or present.",
        ),
        expected_wal_size: int | None = typer.Option(
            None,
            "--expected-wal-size",
            help="Exact -wal size when present.",
        ),
        expected_wal_sha256: str | None = typer.Option(
            None,
            "--expected-wal-sha256",
            help="Exact -wal SHA-256 when present.",
        ),
        expected_shm_state: str = typer.Option(
            ...,
            "--expected-shm-state",
            help="Exact -shm state: absent or present.",
        ),
        expected_shm_size: int | None = typer.Option(
            None,
            "--expected-shm-size",
            help="Exact -shm size when present.",
        ),
        expected_shm_sha256: str | None = typer.Option(
            None,
            "--expected-shm-sha256",
            help="Exact -shm SHA-256 when present.",
        ),
        authorization_id: str = typer.Option(
            ...,
            "--authorization-id",
            help="Explicit caller-supplied preparation authorization binding.",
        ),
        listener_known_writers_stopped: bool = typer.Option(
            False,
            "--listener-known-writers-stopped",
            help="Attest that the listener and known source writers are stopped.",
        ),
        apply: bool = typer.Option(
            False,
            "--apply",
            help="Apply only the explicitly authorized journal-mode transition.",
        ),
    ) -> None:
        """Prepare one exact rollback-mode source for later WAL-only certification."""
        destination = Path(target_path_provider()).resolve(strict=False)
        supplied_destination = expected_destination.expanduser().resolve(strict=False)
        resolved_source = source.expanduser().resolve(strict=False)
        if supplied_destination != destination:
            console.print(
                "[red]RELOCATION_JOURNAL_MODE_PREPARATION_DESTINATION_MISMATCH: expected "
                "destination does not match MOA's current relocation destination.[/red]"
            )
            raise typer.Exit(1)
        if not apply:
            console.print(f"Source: {resolved_source}")
            console.print(f"Intended destination: {destination}")
            console.print(
                "[yellow]No changes made. Rerun with --apply only after authorizing this "
                "exact SQLite representation transition and stopping known writers.[/yellow]"
            )
            return
        try:
            if expected_preparation_size < 0:
                raise _PreparationAuthorityCliError(
                    "--expected-preparation-size must be nonnegative."
                )
            if not _is_sha256(expected_preparation_sha256):
                raise _PreparationAuthorityCliError(
                    "--expected-preparation-sha256 must be exactly 64 lowercase "
                    "hexadecimal characters."
                )
            journal = _build_file_observation(
                state=expected_journal_state,
                size=expected_journal_size,
                sha256=expected_journal_sha256,
                field="--expected-journal",
            )
            wal = _build_file_observation(
                state=expected_wal_state,
                size=expected_wal_size,
                sha256=expected_wal_sha256,
                field="--expected-wal",
            )
            shm = _build_file_observation(
                state=expected_shm_state,
                size=expected_shm_size,
                sha256=expected_shm_sha256,
                field="--expected-shm",
            )
            evidence = prepare_database_relocation_journal_mode(
                DatabaseRelocationJournalModePreparationAuthority(
                    source=resolved_source,
                    intended_destination=destination,
                    expected_moa_checkpoint=expected_moa_checkpoint,
                    expected_main=DatabaseFileObservation(
                        True,
                        expected_preparation_size,
                        expected_preparation_sha256,
                    ),
                    expected_journal_mode=expected_journal_mode,
                    expected_journal=journal,
                    expected_wal=wal,
                    expected_shm=shm,
                    listener_known_writers_stopped_attested=(
                        listener_known_writers_stopped
                    ),
                    authorization_id=authorization_id,
                )
            )
        except _PreparationAuthorityCliError as error:
            console.print(f"[red]Invalid preparation authority: {error}[/red]")
            raise typer.Exit(1) from None
        except DatabaseRelocationJournalModePreparationError as error:
            typer.echo(error.to_json())
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        except DatabaseRelocationError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from error
        typer.echo(evidence.to_json())

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
