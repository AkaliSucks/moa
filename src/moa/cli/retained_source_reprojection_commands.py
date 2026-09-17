"""Direct registration for the one-source retained reprojection operator."""

from pathlib import Path

import typer
from rich.console import Console

from moa.services.retained_source_reprojection_operator import (
    RetainedSourceReprojectionOperator,
    RetainedSourceReprojectionOperatorError,
    RetainedSourceReprojectionOperatorFailure,
    RetainedSourceReprojectionOperatorResult,
)


def register_retained_source_reprojection_command(
    catalog_app: typer.Typer,
    console: Console,
) -> None:
    @catalog_app.command("retained-source-reproject")
    def retained_source_reproject(
        source_event_id: int = typer.Argument(
            ...,
            help="One positive retained Discord source-event ID.",
        ),
        database_path: Path = typer.Argument(
            ...,
            help="Explicit existing MOA SQLite database path.",
        ),
        confirm: bool = typer.Option(
            False,
            "--confirm",
            help="Confirm one retained-source reprojection after stopping the listener.",
        ),
    ) -> None:
        """Reproject exactly one retained source into the current generation."""
        if not confirm:
            console.print(
                "[yellow]No changes made. Stop the listener and rerun this one-source "
                "mutation with --confirm.[/yellow]"
            )
            raise typer.Exit(1)
        try:
            result = RetainedSourceReprojectionOperator(database_path).execute(source_event_id)
        except RetainedSourceReprojectionOperatorError as error:
            _render_failure(console, error)
            raise typer.Exit(1) from None
        _render_success(console, result)


def _render_success(
    console: Console,
    result: RetainedSourceReprojectionOperatorResult,
) -> None:
    execution = result.execution
    verification = result.verification
    console.print("[green]Retained-source reprojection completed.[/green]")
    console.print(f"Source event ID: {execution.source_event_id}")
    console.print(f"Derived family: {execution.source_family}")
    console.print(f"Selected executor: {execution.executor_name}")
    console.print(f"Terminal status: {execution.terminal_status}")
    console.print(f"Expected link count: {verification.expected_link_count}")
    console.print(f"Completed link count: {verification.completed_link_count}")
    console.print(f"Current generation ID: {verification.current_generation_id}")
    console.print(f"Post-write verification: {verification.status}")


def _render_failure(
    console: Console,
    error: RetainedSourceReprojectionOperatorError,
) -> None:
    if error.reason is RetainedSourceReprojectionOperatorFailure.INVALID_SOURCE_EVENT_ID:
        message = "Reprojection blocked: SOURCE_EVENT_ID must be a positive integer."
    elif error.reason is RetainedSourceReprojectionOperatorFailure.INVALID_DATABASE_PATH:
        message = "Reprojection blocked: DATABASE_PATH must be an existing file-backed database."
    elif error.reason is RetainedSourceReprojectionOperatorFailure.SCHEMA_INVALID:
        message = "Reprojection blocked: database schema or migration identity is incompatible."
    elif error.reason is RetainedSourceReprojectionOperatorFailure.LISTENER_OWNERSHIP_UNAVAILABLE:
        message = "Reprojection blocked: the listener is active or database ownership is unavailable."
    elif error.reason is RetainedSourceReprojectionOperatorFailure.ADMISSION_REJECTED:
        rejection = (
            error.admission_reason.value
            if error.admission_reason is not None
            else "admission_rejected"
        )
        message = f"Reprojection rejected by execution-time admission: {rejection}."
    elif error.reason is RetainedSourceReprojectionOperatorFailure.VERIFICATION_FAILED:
        message = (
            "Reprojection failed: mutation may already be durably committed; "
            "post-write verification failed and requires operator review."
        )
    else:
        message = (
            "Reprojection failed: coordinator execution did not complete; "
            "durable commit state is unknown."
        )
    console.print(f"[red]{message}[/red]")
