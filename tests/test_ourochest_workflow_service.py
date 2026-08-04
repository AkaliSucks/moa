"""Offline tests for the isolated Ourochest pending/bound workflow seam."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import pytest

from moa.models.ourochest_workflow import (
    OurochestWorkflowStatus,
    OurochestWorkflowState,
)
from moa.models.ourosphere import OuroHuntBoard, OuroHuntVisualIdentity
from moa.services.ourochest_workflow_service import (
    OurochestActiveReplaceKind,
    OurochestBindKind,
    OurochestWorkflowService,
    WORKFLOW_TTL_SECONDS,
)
from moa.services.ourosphere_board_service import OuroHuntBoardService


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "discord" / "oc_structural_capture.v1.json"
BOARD_SERVICE = OuroHuntBoardService()


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _boards() -> list[OuroHuntBoard]:
    return [BOARD_SERVICE.project(record["message"]) for record in _fixture()["records"]]


def _service() -> tuple[FakeClock, OurochestWorkflowService]:
    clock = FakeClock()
    return clock, OurochestWorkflowService(clock=clock)


def _active_workflow(
    service: OurochestWorkflowService,
    *,
    guild_id: str = "guild-a",
    channel_id: str = "channel-a",
    user_id: str = "user-a",
    board_message_id: str = "message-a",
) -> OurochestWorkflowState:
    service.create_pending(guild_id, channel_id, user_id)
    result = service.bind_initial_board(
        guild_id,
        channel_id,
        board_message_id,
        _boards()[0],
    )
    assert result.workflow is not None
    return result.workflow


def test_pending_creation_preserves_scope_and_has_no_active_state() -> None:
    clock, service = _service()

    state = service.create_pending("guild-a", "channel-a", "user-a")

    assert isinstance(state, OurochestWorkflowState)
    assert state.status is OurochestWorkflowStatus.PENDING_BOARD
    assert state.guild_id == "guild-a"
    assert state.channel_id == "channel-a"
    assert state.initiating_user_id == "user-a"
    assert state.created_at == clock.value
    assert state.expires_at == clock.value + WORKFLOW_TTL_SECONDS
    assert state.board_message_id is None
    assert state.bound_at is None
    assert state.last_board is None
    assert state.red_candidates is None
    assert service.get_active("guild-a", "channel-a", "board-a") is None


def test_same_user_pending_creation_replaces_only_pending_state() -> None:
    clock, service = _service()
    first = service.create_pending("guild-a", "channel-a", "user-a")
    clock.advance(5)

    second = service.create_pending("guild-a", "channel-a", "user-a")

    assert second.created_at == first.created_at + 5
    assert second.expires_at == second.created_at + WORKFLOW_TTL_SECONDS
    assert service.get_pending("guild-a", "channel-a", "user-a") == second


def test_same_user_pending_isolated_by_channel_and_guild() -> None:
    _, service = _service()

    channel_a = service.create_pending("guild-a", "channel-a", "user-a")
    channel_b = service.create_pending("guild-a", "channel-b", "user-a")
    guild_b = service.create_pending("guild-b", "channel-a", "user-a")

    assert service.get_pending("guild-a", "channel-a", "user-a") == channel_a
    assert service.get_pending("guild-a", "channel-b", "user-a") == channel_b
    assert service.get_pending("guild-b", "channel-a", "user-a") == guild_b


def test_exact_one_bind_removes_pending_and_starts_active_window_at_bind() -> None:
    clock, service = _service()
    board = _boards()[0]
    pending = service.create_pending("guild-a", "channel-a", "user-a")
    clock.advance(30)

    result = service.bind_initial_board("guild-a", "channel-a", "message-a", board)

    assert result.kind is OurochestBindKind.BOUND
    assert result.workflow is not None
    active = result.workflow
    assert active.status is OurochestWorkflowStatus.ACTIVE
    assert active.initiating_user_id == pending.initiating_user_id
    assert active.board_message_id == "message-a"
    assert active.last_board == board
    assert active.bound_at == clock.value
    assert active.expires_at == clock.value + WORKFLOW_TTL_SECONDS
    assert len(active.red_candidates or ()) == 24
    assert service.get_pending("guild-a", "channel-a", "user-a") is None
    assert service.get_active("guild-a", "channel-a", "message-a") == active


def test_eligible_board_without_pending_has_no_side_effects() -> None:
    _, service = _service()

    result = service.bind_initial_board("guild-a", "channel-a", "message-a", _boards()[0])

    assert result.kind is OurochestBindKind.NO_MATCH
    assert result.workflow is None
    assert service.get_active("guild-a", "channel-a", "message-a") is None


def test_two_users_in_one_channel_are_ambiguous_and_both_remain_pending() -> None:
    _, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    service.create_pending("guild-a", "channel-a", "user-b")

    result = service.bind_initial_board("guild-a", "channel-a", "message-a", _boards()[0])

    assert result.kind is OurochestBindKind.AMBIGUOUS
    assert result.workflow is None
    assert service.get_pending("guild-a", "channel-a", "user-a") is not None
    assert service.get_pending("guild-a", "channel-a", "user-b") is not None
    assert service.get_active("guild-a", "channel-a", "message-a") is None


def test_ineligible_visible_board_preserves_pending_and_creates_no_active_state() -> None:
    _, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    hidden, revealed = _boards()[:2]
    visible_board = replace(
        hidden,
        cells=hidden.cells[:18]
        + (replace(hidden.cells[18], visual_identity=revealed.cells[18].visual_identity),)
        + hidden.cells[19:],
    )

    result = service.bind_initial_board("guild-a", "channel-a", "message-a", visible_board)

    assert result.kind is OurochestBindKind.INELIGIBLE_BOARD
    assert service.get_pending("guild-a", "channel-a", "user-a") is not None
    assert service.get_active("guild-a", "channel-a", "message-a") is None


def test_ineligible_disabled_board_preserves_pending() -> None:
    _, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    board = _boards()[0]
    disabled_board = replace(board, cells=(replace(board.cells[0], disabled=True),) + board.cells[1:])

    result = service.bind_initial_board("guild-a", "channel-a", "message-a", disabled_board)

    assert result.kind is OurochestBindKind.INELIGIBLE_BOARD
    assert service.get_pending("guild-a", "channel-a", "user-a") is not None


def test_unknown_visual_initial_board_is_ineligible() -> None:
    _, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    board = _boards()[0]
    unknown = OuroHuntVisualIdentity(
        kind="custom", id_sha256="0" * 64, name_sha256="1" * 64, name_length=1
    )
    unknown_board = replace(board, cells=(replace(board.cells[0], visual_identity=unknown),) + board.cells[1:])

    result = service.bind_initial_board("guild-a", "channel-a", "message-a", unknown_board)

    assert result.kind is OurochestBindKind.INELIGIBLE_BOARD
    assert service.get_pending("guild-a", "channel-a", "user-a") is not None


def test_candidates_are_initialized_only_by_successful_bind() -> None:
    _, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    pending = service.get_pending("guild-a", "channel-a", "user-a")
    assert pending is not None
    assert pending.red_candidates is None

    ambiguous_service = _service()[1]
    ambiguous_service.create_pending("guild-a", "channel-a", "user-a")
    ambiguous_service.create_pending("guild-a", "channel-a", "user-b")
    ambiguous = ambiguous_service.bind_initial_board("guild-a", "channel-a", "message-a", _boards()[0])
    assert ambiguous.kind is OurochestBindKind.AMBIGUOUS
    assert ambiguous_service.get_active("guild-a", "channel-a", "message-a") is None

    bound = service.bind_initial_board("guild-a", "channel-a", "message-a", _boards()[0])
    assert bound.workflow is not None
    assert len(bound.workflow.red_candidates or ()) == 24


def test_active_lookup_requires_exact_guild_channel_and_board_key() -> None:
    _, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    service.bind_initial_board("guild-a", "channel-a", "message-a", _boards()[0])

    assert service.get_active("guild-a", "channel-a", "message-a") is not None
    assert service.get_active("guild-b", "channel-a", "message-a") is None
    assert service.get_active("guild-a", "channel-b", "message-a") is None
    assert service.get_active("guild-a", "channel-a", "message-b") is None


def test_active_key_collision_is_safe_and_does_not_consume_new_pending() -> None:
    _, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    first = service.bind_initial_board("guild-a", "channel-a", "message-a", _boards()[0])
    service.create_pending("guild-a", "channel-a", "user-b")

    result = service.bind_initial_board("guild-a", "channel-a", "message-a", _boards()[0])

    assert result.kind is OurochestBindKind.ALREADY_BOUND
    assert result.workflow is None
    assert service.get_active("guild-a", "channel-a", "message-a") == first.workflow
    assert service.get_pending("guild-a", "channel-a", "user-b") is not None


def test_active_workflows_for_two_users_and_board_ids_are_independent() -> None:
    _, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    service.bind_initial_board("guild-a", "channel-a", "message-a", _boards()[0])
    service.create_pending("guild-a", "channel-a", "user-b")
    service.bind_initial_board("guild-a", "channel-a", "message-b", _boards()[0])

    active_a = service.get_active("guild-a", "channel-a", "message-a")
    active_b = service.get_active("guild-a", "channel-a", "message-b")
    assert active_a is not None and active_a.initiating_user_id == "user-a"
    assert active_b is not None and active_b.initiating_user_id == "user-b"


def test_pending_creation_never_replaces_an_existing_active_workflow() -> None:
    _, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    result = service.bind_initial_board("guild-a", "channel-a", "message-a", _boards()[0])
    service.create_pending("guild-a", "channel-a", "user-a")

    assert service.get_active("guild-a", "channel-a", "message-a") == result.workflow
    assert service.get_pending("guild-a", "channel-a", "user-a") is not None


def test_pending_expiry_is_lazy_at_exact_boundary_and_does_not_refresh() -> None:
    clock, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    clock.advance(WORKFLOW_TTL_SECONDS - 1)
    assert service.get_pending("guild-a", "channel-a", "user-a") is not None
    clock.advance(0.5)
    assert service.get_pending("guild-a", "channel-a", "user-a") is not None
    clock.advance(0.5)

    assert service.get_pending("guild-a", "channel-a", "user-a") is None
    assert service.get_pending("guild-a", "channel-a", "user-a") is None


def test_active_expiry_starts_at_bind_and_does_not_resurrect_pending() -> None:
    clock, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    clock.advance(60)
    service.bind_initial_board("guild-a", "channel-a", "message-a", _boards()[0])
    clock.advance(WORKFLOW_TTL_SECONDS - 1)
    assert service.get_active("guild-a", "channel-a", "message-a") is not None
    clock.advance(1)

    assert service.get_active("guild-a", "channel-a", "message-a") is None
    assert service.get_pending("guild-a", "channel-a", "user-a") is None


def test_repeated_active_lookups_do_not_extend_expiry() -> None:
    clock, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    service.bind_initial_board("guild-a", "channel-a", "message-a", _boards()[0])
    clock.advance(WORKFLOW_TTL_SECONDS - 1)
    assert service.get_active("guild-a", "channel-a", "message-a") is not None
    clock.advance(1)

    assert service.get_active("guild-a", "channel-a", "message-a") is None


def test_explicit_active_removal_is_exact_harmless_and_leaves_pending_alone() -> None:
    _, service = _service()
    service.create_pending("guild-a", "channel-a", "user-a")
    service.bind_initial_board("guild-a", "channel-a", "message-a", _boards()[0])
    service.create_pending("guild-a", "channel-a", "user-b")

    removed = service.remove_active("guild-a", "channel-a", "message-a")

    assert removed is not None and removed.board_message_id == "message-a"
    assert service.remove_active("guild-a", "channel-a", "message-a") is None
    assert service.get_pending("guild-a", "channel-a", "user-b") is not None


def test_fixture_s0_is_eligible_and_post_action_board_is_ineligible() -> None:
    _, service = _service()
    initial, post_action = _boards()[:2]
    service.create_pending("guild-a", "channel-a", "user-a")
    bound = service.bind_initial_board("guild-a", "channel-a", "message-a", initial)
    assert bound.kind is OurochestBindKind.BOUND

    service.create_pending("guild-b", "channel-b", "user-b")
    result = service.bind_initial_board("guild-b", "channel-b", "message-b", post_action)

    assert result.kind is OurochestBindKind.INELIGIBLE_BOARD
    assert service.get_pending("guild-b", "channel-b", "user-b") is not None


def test_service_uses_only_ids_and_domain_board_objects() -> None:
    _, service = _service()
    pending = service.create_pending(1, 2, 3)
    result = service.bind_initial_board(1, 2, 4, _boards()[0])

    assert pending.guild_id == 1
    assert result.kind is OurochestBindKind.BOUND
    assert result.workflow is not None
    assert result.workflow.board_message_id == 4


def test_active_replacement_accepts_coordinator_like_progress() -> None:
    _, service = _service()
    active = _active_workflow(service)
    updated = replace(
        active,
        last_board=_boards()[1],
        red_candidates=((0, 0),),
    )

    result = service.replace_active(active, updated)

    assert result.kind is OurochestActiveReplaceKind.REPLACED
    assert service.get_active("guild-a", "channel-a", "message-a") == updated
    assert updated.expires_at == active.expires_at


def test_active_replacement_reports_missing_without_creating_state() -> None:
    _, service = _service()
    active = _active_workflow(service)
    service.remove_active("guild-a", "channel-a", "message-a")
    updated = replace(active, red_candidates=((0, 0),))

    result = service.replace_active(active, updated)

    assert result.kind is OurochestActiveReplaceKind.MISSING
    assert service.get_active("guild-a", "channel-a", "message-a") is None


def test_active_replacement_reports_stale_and_preserves_current_state() -> None:
    _, service = _service()
    active = _active_workflow(service)
    current = replace(active, red_candidates=((0, 0),))
    assert service.replace_active(active, current).kind is OurochestActiveReplaceKind.REPLACED
    proposed = replace(active, red_candidates=((0, 1),))

    result = service.replace_active(active, proposed)

    assert result.kind is OurochestActiveReplaceKind.STALE
    assert service.get_active("guild-a", "channel-a", "message-a") == current


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("guild_id", "guild-b"),
        ("channel_id", "channel-b"),
        ("initiating_user_id", "user-b"),
        ("board_message_id", "message-b"),
        ("created_at", 99.0),
        ("bound_at", 101.0),
        ("expires_at", 221.0),
    ],
)
def test_active_replacement_rejects_immutable_metadata_changes(
    field_name: str,
    value: object,
) -> None:
    _, service = _service()
    active = _active_workflow(service)
    updated = replace(active, **{field_name: value})

    with pytest.raises(ValueError, match=field_name):
        service.replace_active(active, updated)

    assert service.get_active("guild-a", "channel-a", "message-a") == active


def test_active_replacement_rejects_wrong_workflow_kind() -> None:
    _, service = _service()
    active = _active_workflow(service)
    updated = replace(active, red_candidates=((0, 0),))
    object.__setattr__(updated, "workflow_kind", "not-ourochest")

    with pytest.raises(ValueError, match="workflow_kind"):
        service.replace_active(active, updated)

    assert service.get_active("guild-a", "channel-a", "message-a") == active


@pytest.mark.parametrize(
    "status",
    [
        OurochestWorkflowStatus.PENDING_BOARD,
        OurochestWorkflowStatus.TERMINAL,
        OurochestWorkflowStatus.UNSUPPORTED,
        OurochestWorkflowStatus.MALFORMED,
        OurochestWorkflowStatus.CONTRADICTION,
    ],
)
def test_active_replacement_rejects_non_active_updated_state(
    status: OurochestWorkflowStatus,
) -> None:
    _, service = _service()
    active = _active_workflow(service)
    if status is OurochestWorkflowStatus.PENDING_BOARD:
        updated = service.create_pending("guild-b", "channel-b", "user-b")
    else:
        candidates = () if status is OurochestWorkflowStatus.CONTRADICTION else active.red_candidates
        updated = replace(active, status=status, red_candidates=candidates)

    with pytest.raises(ValueError, match="updated must be an ACTIVE"):
        service.replace_active(active, updated)

    assert service.get_active("guild-a", "channel-a", "message-a") == active


def test_active_replacement_rejects_non_active_expected_state() -> None:
    _, service = _service()
    pending = service.create_pending("guild-a", "channel-a", "user-a")
    active = _active_workflow(service)
    updated = replace(active, red_candidates=((0, 0),))

    with pytest.raises(ValueError, match="expected must be an ACTIVE"):
        service.replace_active(pending, updated)

    assert service.get_active("guild-a", "channel-a", "message-a") == active


def test_active_replacement_expiry_returns_missing_without_resurrection() -> None:
    clock, service = _service()
    active = _active_workflow(service)
    updated = replace(active, red_candidates=((0, 0),))
    clock.advance(active.expires_at - clock.value)

    result = service.replace_active(active, updated)

    assert result.kind is OurochestActiveReplaceKind.MISSING
    assert service.get_active("guild-a", "channel-a", "message-a") is None


def test_active_replacement_does_not_refresh_expiry() -> None:
    clock, service = _service()
    active = _active_workflow(service)
    updated = replace(active, red_candidates=((0, 0),))

    assert service.replace_active(active, updated).kind is OurochestActiveReplaceKind.REPLACED
    clock.advance(active.expires_at - clock.value)

    assert service.get_active("guild-a", "channel-a", "message-a") is None


def test_has_active_for_owner_matches_exact_scope() -> None:
    _, service = _service()
    _active_workflow(service, guild_id="guild-a", channel_id="channel-a", user_id="user-a")

    assert service.has_active_for_owner("guild-a", "channel-a", "user-a") is True


def test_has_active_for_owner_rejects_same_owner_in_different_channel() -> None:
    _, service = _service()
    _active_workflow(service, guild_id="guild-a", channel_id="channel-b", user_id="user-a")

    assert service.has_active_for_owner("guild-a", "channel-a", "user-a") is False


def test_has_active_for_owner_rejects_same_owner_in_different_guild() -> None:
    _, service = _service()
    _active_workflow(service, guild_id="guild-b", channel_id="channel-a", user_id="user-a")

    assert service.has_active_for_owner("guild-a", "channel-a", "user-a") is False


def test_has_active_for_owner_rejects_different_owner() -> None:
    _, service = _service()
    _active_workflow(service, guild_id="guild-a", channel_id="channel-a", user_id="user-b")

    assert service.has_active_for_owner("guild-a", "channel-a", "user-a") is False


def test_has_active_for_owner_ignores_expired_entries_without_refreshing_expiry() -> None:
    clock, service = _service()
    active = _active_workflow(service)

    assert service.has_active_for_owner("guild-a", "channel-a", "user-a") is True
    clock.advance(active.expires_at - clock.value)

    assert service.has_active_for_owner("guild-a", "channel-a", "user-a") is False
    assert service.get_active("guild-a", "channel-a", "message-a") is None


def test_has_active_for_owner_handles_multiple_owned_active_entries() -> None:
    _, service = _service()
    first = _active_workflow(service)
    second = _active_workflow(
        service,
        board_message_id="message-b",
    )

    assert first.initiating_user_id == second.initiating_user_id == "user-a"
    assert service.has_active_for_owner("guild-a", "channel-a", "user-a") is True
    assert service.get_active("guild-a", "channel-a", "message-a") == first
    assert service.get_active("guild-a", "channel-a", "message-b") == second


def test_replacement_preserves_unrelated_active_entry_and_removal_regression() -> None:
    _, service = _service()
    active = _active_workflow(service)
    unrelated = _active_workflow(
        service,
        guild_id="guild-b",
        channel_id="channel-b",
        user_id="user-b",
        board_message_id="message-b",
    )
    updated = replace(active, red_candidates=((0, 0),))

    assert service.replace_active(active, updated).kind is OurochestActiveReplaceKind.REPLACED
    assert service.get_active("guild-b", "channel-b", "message-b") == unrelated
    assert service.remove_active("guild-a", "channel-a", "message-a") == updated
    assert service.remove_active("guild-a", "channel-a", "message-a") is None
