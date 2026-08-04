"""Project normal Discord component trees into opaque OuroHunt boards."""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
from typing import Any

from moa.models.ourosphere import (
    OuroHuntBoard,
    OuroHuntCell,
    OuroHuntVisualIdentity,
)


_ACTION_ROW_TYPE = 1
_BUTTON_TYPE = 2


class DiscordComponentBoardProjectionError(ValueError):
    """Raised when a normal Discord component tree is not a supported board."""


class DiscordComponentBoardAdapter:
    """Convert one normal Discord message-like component tree into a board."""

    def project(self, message: Any) -> OuroHuntBoard:
        """Project a complete five-row, five-button component tree."""

        components = self._attribute(message, "components", "message must contain components")
        if not self._is_sequence(components) or len(components) != 5:
            raise DiscordComponentBoardProjectionError(
                "message must contain exactly five ActionRows"
            )

        cells: list[OuroHuntCell] = []
        for row_index, row in enumerate(components):
            self._require_component_type(row, _ACTION_ROW_TYPE, row_index, None, "ActionRow")
            children = self._attribute(
                row,
                "children",
                f"row {row_index} must contain children",
            )
            if not self._is_sequence(children) or len(children) != 5:
                raise DiscordComponentBoardProjectionError(
                    f"row {row_index} must contain exactly five Buttons"
                )

            for column_index, button in enumerate(children):
                self._require_component_type(
                    button,
                    _BUTTON_TYPE,
                    row_index,
                    column_index,
                    "Button",
                )
                if hasattr(button, "children"):
                    raise self._location_error(
                        row_index, column_index, "Button must not contain nested children"
                    )
                cells.append(self._project_cell(button, row_index, column_index))

        return OuroHuntBoard(tuple(cells))

    @classmethod
    def _project_cell(cls, button: Any, row_index: int, column_index: int) -> OuroHuntCell:
        custom_id = cls._attribute(
            button,
            "custom_id",
            cls._location_message(row_index, column_index, "button must contain custom_id"),
        )
        if not isinstance(custom_id, str) or not custom_id:
            raise cls._location_error(row_index, column_index, "button custom_id is invalid")

        emoji = cls._attribute(
            button,
            "emoji",
            cls._location_message(row_index, column_index, "button must contain a custom emoji"),
        )
        visual_identity = cls._project_visual_identity(emoji, row_index, column_index)

        disabled = cls._attribute(
            button,
            "disabled",
            cls._location_message(row_index, column_index, "button must contain disabled"),
        )
        if not isinstance(disabled, bool):
            raise cls._location_error(row_index, column_index, "button disabled must be boolean")

        return OuroHuntCell(
            coordinate=(row_index, column_index),
            component_identity=cls._digest(custom_id),
            visual_identity=visual_identity,
            disabled=disabled,
        )

    @classmethod
    def _project_visual_identity(
        cls, emoji: Any, row_index: int, column_index: int
    ) -> OuroHuntVisualIdentity:
        if emoji is None or isinstance(emoji, str):
            raise cls._location_error(
                row_index,
                column_index,
                "button emoji must be a custom emoji object",
            )

        raw_id = cls._attribute(
            emoji,
            "id",
            cls._location_message(row_index, column_index, "custom emoji id is missing"),
        )
        if raw_id is None or isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
            raise cls._location_error(row_index, column_index, "custom emoji id is invalid")
        if isinstance(raw_id, str) and not raw_id.strip():
            raise cls._location_error(row_index, column_index, "custom emoji id is invalid")

        raw_name = cls._attribute(
            emoji,
            "name",
            cls._location_message(row_index, column_index, "custom emoji name is missing"),
        )
        if not isinstance(raw_name, str):
            raise cls._location_error(row_index, column_index, "custom emoji name is invalid")

        return OuroHuntVisualIdentity(
            kind="custom",
            id_sha256=cls._digest(f"custom-id:{raw_id}"),
            name_sha256=cls._digest(f"custom-name:{raw_name}"),
            name_length=len(raw_name),
        )

    @classmethod
    def _require_component_type(
        cls,
        component: Any,
        expected: int,
        row_index: int,
        column_index: int | None,
        expected_name: str,
    ) -> None:
        type_value = cls._attribute(
            component,
            "type",
            cls._location_message(
                row_index, column_index, f"component must be a {expected_name}"
            ),
        )
        protocol_value = getattr(type_value, "value", type_value)
        if (
            isinstance(protocol_value, bool)
            or not isinstance(protocol_value, int)
            or protocol_value != expected
        ):
            raise cls._location_error(
                row_index,
                column_index,
                f"component must be a {expected_name}",
            )

    @staticmethod
    def _attribute(value: Any, name: str, message: str) -> Any:
        try:
            result = getattr(value, name)
        except AttributeError:
            raise DiscordComponentBoardProjectionError(message) from None
        return result

    @staticmethod
    def _is_sequence(value: Any) -> bool:
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _location_error(row_index: int, column_index: int | None, reason: str) -> DiscordComponentBoardProjectionError:
        return DiscordComponentBoardProjectionError(
            DiscordComponentBoardAdapter._location_message(row_index, column_index, reason)
        )

    @staticmethod
    def _location_message(row_index: int, column_index: int | None, reason: str) -> str:
        location = f"row {row_index}"
        if column_index is not None:
            location += f", column {column_index}"
        return f"{location}: {reason}"
