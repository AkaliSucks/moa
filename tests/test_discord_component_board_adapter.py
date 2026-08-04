from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json

import pytest

from moa.models.ourosphere import OuroHuntBoard
from moa.services.discord_component_board_adapter import (
    DiscordComponentBoardAdapter,
    DiscordComponentBoardProjectionError,
)
from moa.services.ourosphere_board_service import OuroHuntBoardService


ADAPTER = DiscordComponentBoardAdapter()


@dataclass
class EmojiDouble:
    id: object
    name: object
    animated: bool = False


@dataclass
class ButtonDouble:
    custom_id: object
    emoji: object
    disabled: object
    type: object = 2
    style: object = "secondary"
    label: object = "ignored"


@dataclass
class ActionRowDouble:
    children: object
    type: object = 1


@dataclass
class MessageDouble:
    components: object
    content: str = "ignored content"
    id: str = "ignored-message-id"
    author: str = "ignored-author-id"
    guild: str = "ignored-guild-id"
    channel: str = "ignored-channel-id"


def _message(
    *, row_count: int = 5, child_count: int = 5, disabled: bool = False
) -> MessageDouble:
    rows = []
    for row_index in range(row_count):
        buttons = [
            ButtonDouble(
                custom_id=f"button-{row_index}-{column_index}",
                emoji=EmojiDouble(
                    id=1000 + row_index * 5 + column_index,
                    name=f"visual-{row_index}-{column_index}",
                ),
                disabled=disabled,
            )
            for column_index in range(child_count)
        ]
        rows.append(ActionRowDouble(children=buttons))
    return MessageDouble(components=rows)


def test_successful_5x5_projection_returns_complete_board() -> None:
    board = ADAPTER.project(_message())

    assert isinstance(board, OuroHuntBoard)
    assert len(board.cells) == 25
    assert board.coordinates == tuple((row, column) for row in range(5) for column in range(5))
    assert board.cell_at((0, 0)).disabled is False
    assert board.cell_at((4, 4)).coordinate == (4, 4)
    assert {field.name for field in fields(board.cell_at((0, 0)))} == {
        "coordinate",
        "component_identity",
        "visual_identity",
        "disabled",
    }


def test_component_id_hash_has_no_prefix() -> None:
    message = _message()
    message.components[0].children[0].custom_id = "distinctive-component-id"

    cell = ADAPTER.project(message).cell_at((0, 0))

    assert cell.component_identity == hashlib.sha256(b"distinctive-component-id").hexdigest()


def test_custom_emoji_id_hash_uses_exact_prefix() -> None:
    message = _message()
    message.components[0].children[0].emoji.id = 987654321

    visual = ADAPTER.project(message).cell_at((0, 0)).visual_identity

    assert visual.id_sha256 == hashlib.sha256(b"custom-id:987654321").hexdigest()


def test_custom_emoji_name_hash_and_length_use_exact_input() -> None:
    message = _message()
    message.components[0].children[0].emoji.name = "distinctive-name"

    visual = ADAPTER.project(message).cell_at((0, 0)).visual_identity

    assert visual.name_sha256 == hashlib.sha256(b"custom-name:distinctive-name").hexdigest()
    assert visual.name_length == len("distinctive-name")


def test_full_visual_identity_changes_when_custom_identity_changes() -> None:
    baseline = ADAPTER.project(_message()).cell_at((0, 0)).visual_identity

    changed_id_message = _message()
    changed_id_message.components[0].children[0].emoji.id = 9000
    changed_id = ADAPTER.project(changed_id_message).cell_at((0, 0)).visual_identity

    changed_name_message = _message()
    changed_name_message.components[0].children[0].emoji.name = "a-longer-visual-name"
    changed_name = ADAPTER.project(changed_name_message).cell_at((0, 0)).visual_identity

    assert changed_id != baseline
    assert changed_name != baseline
    assert changed_name.name_length != baseline.name_length
    assert changed_name.kind == "custom"
    assert not hasattr(changed_name, "color")


def test_projection_is_deterministic_for_logically_identical_trees() -> None:
    assert ADAPTER.project(_message()) == ADAPTER.project(_message())


def test_disabled_values_are_preserved_without_claim_semantics() -> None:
    message = _message()
    message.components[0].children[0].disabled = True
    message.components[4].children[4].disabled = True

    board = ADAPTER.project(message)

    assert board.cell_at((0, 0)).disabled is True
    assert board.cell_at((0, 1)).disabled is False
    assert board.cell_at((4, 4)).disabled is True
    assert not hasattr(board.cell_at((0, 0)), "claimed")


@pytest.mark.parametrize("row_count", [4, 6])
def test_invalid_top_level_row_count_is_rejected(row_count: int) -> None:
    with pytest.raises(DiscordComponentBoardProjectionError, match="exactly five"):
        ADAPTER.project(_message(row_count=row_count))


@pytest.mark.parametrize("child_count", [4, 6])
def test_invalid_child_count_is_rejected(child_count: int) -> None:
    with pytest.raises(DiscordComponentBoardProjectionError, match="exactly five"):
        ADAPTER.project(_message(child_count=child_count))


def test_missing_row_children_are_rejected() -> None:
    message = _message()
    del message.components[0].children

    with pytest.raises(DiscordComponentBoardProjectionError, match="children"):
        ADAPTER.project(message)


def test_wrong_top_level_component_type_is_rejected() -> None:
    message = _message()
    message.components[0].type = 2

    with pytest.raises(DiscordComponentBoardProjectionError, match="ActionRow"):
        ADAPTER.project(message)


def test_wrong_child_component_type_is_rejected() -> None:
    message = _message()
    message.components[0].children[0].type = 1

    with pytest.raises(DiscordComponentBoardProjectionError, match="Button"):
        ADAPTER.project(message)


def test_nested_button_children_are_rejected() -> None:
    message = _message()
    message.components[0].children[0].children = []

    with pytest.raises(DiscordComponentBoardProjectionError, match="nested"):
        ADAPTER.project(message)


@pytest.mark.parametrize("custom_id", [None, 1234, ""])
def test_missing_or_malformed_custom_id_is_rejected(custom_id: object) -> None:
    message = _message()
    message.components[0].children[0].custom_id = custom_id

    with pytest.raises(DiscordComponentBoardProjectionError, match="custom_id"):
        ADAPTER.project(message)


def test_missing_custom_id_is_rejected() -> None:
    message = _message()
    del message.components[0].children[0].custom_id

    with pytest.raises(DiscordComponentBoardProjectionError, match="custom_id"):
        ADAPTER.project(message)


def test_missing_emoji_is_rejected() -> None:
    message = _message()
    message.components[0].children[0].emoji = None

    with pytest.raises(DiscordComponentBoardProjectionError, match="custom emoji"):
        ADAPTER.project(message)


def test_unicode_emoji_is_rejected_fail_closed() -> None:
    message = _message()
    message.components[0].children[0].emoji = EmojiDouble(id=None, name="✨")

    with pytest.raises(DiscordComponentBoardProjectionError, match="custom emoji"):
        ADAPTER.project(message)


@pytest.mark.parametrize(
    ("field", "value"),
    [("id", None), ("id", object()), ("name", None), ("name", 42)],
)
def test_malformed_custom_emoji_is_rejected(field: str, value: object) -> None:
    message = _message()
    setattr(message.components[0].children[0].emoji, field, value)

    with pytest.raises(DiscordComponentBoardProjectionError, match="custom emoji"):
        ADAPTER.project(message)


def test_missing_custom_emoji_id_is_rejected() -> None:
    message = _message()
    del message.components[0].children[0].emoji.id

    with pytest.raises(DiscordComponentBoardProjectionError, match="custom emoji"):
        ADAPTER.project(message)


def test_privacy_boundary_keeps_only_safe_board_fields() -> None:
    raw_values = {
        "button-custom-id-secret",
        "emoji-id-secret",
        "emoji-name-secret",
        "message-content-secret",
        "message-id-secret",
        "author-id-secret",
        "guild-id-secret",
        "channel-id-secret",
    }
    message = _message()
    message.content = "message-content-secret"
    message.id = "message-id-secret"
    message.author = "author-id-secret"
    message.guild = "guild-id-secret"
    message.channel = "channel-id-secret"
    message.components[0].children[0].custom_id = "button-custom-id-secret"
    message.components[0].children[0].emoji = EmojiDouble(
        id="emoji-id-secret", name="emoji-name-secret"
    )

    serialized = json.dumps(asdict(ADAPTER.project(message)), sort_keys=True)

    assert all(raw_value not in serialized for raw_value in raw_values)
    assert "custom_id" not in serialized
    assert "emoji-name-secret" not in serialized


def test_irrelevant_message_and_button_metadata_do_not_change_projection() -> None:
    first = _message()
    second = _message()
    first.content, first.id, first.author, first.guild, first.channel = (
        "first content",
        "first id",
        "first author",
        "first guild",
        "first channel",
    )
    second.content, second.id, second.author, second.guild, second.channel = (
        "second content",
        "second id",
        "second author",
        "second guild",
        "second channel",
    )
    for row in second.components:
        for button in row.children:
            button.style = "primary"
            button.label = "different presentation"

    assert ADAPTER.project(first) == ADAPTER.project(second)


def test_adapter_output_is_accepted_by_generic_board_service() -> None:
    board = ADAPTER.project(_message())
    service = OuroHuntBoardService()

    transition = service.compare(board, board)

    assert transition.before == board
    assert transition.after == board
    assert transition.cell_at((4, 4)).coordinate == (4, 4)
    assert board.is_terminal is False
    assert ADAPTER.project(_message(disabled=True)).is_terminal is True
