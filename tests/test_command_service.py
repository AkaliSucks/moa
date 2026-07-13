import pytest

from moa.services.command_service import CommandService


def test_command_service_explains_combined_flags_and_exclusions() -> None:
    query = CommandService().explain("$mmwy=a+ Re:Zero$--Some bundle")

    assert query.command == "mm"
    assert [flag.token for flag in query.flags] == ["w", "y=", "a+"]
    assert query.arguments == ("Re:Zero",)
    assert query.exclusions == ("Some bundle",)
    assert query.flags[0].definition.meaning == "Waifu characters."
    assert query.flags[1].definition.meaning == "Keys only, sorted by key count."


def test_command_service_supports_numeric_key_and_sphere_flags() -> None:
    query = CommandService().explain("$mmz<5y!>7 Re:Zero")

    assert [flag.token for flag in query.flags] == ["z<5", "y!>7"]
    assert [flag.definition.category for flag in query.flags] == ["spheres", "soulkeys"]


def test_command_service_distinguishes_display_flags_from_sort_flags() -> None:
    rank_and_value = CommandService().explain("$mmrk")
    value_sorted = CommandService().explain("$mmrk=")
    keys_only = CommandService().explain("$mmy")
    keys_sorted = CommandService().explain("$mmy=")
    full_list_with_keys = CommandService().explain("$mmy+")
    keys_with_values = CommandService().explain("$mmyk")

    assert [flag.token for flag in rank_and_value.flags] == ["r", "k"]
    assert [flag.token for flag in value_sorted.flags] == ["r", "k="]
    assert [flag.token for flag in keys_only.flags] == ["y"]
    assert [flag.token for flag in keys_sorted.flags] == ["y="]
    assert [flag.token for flag in full_list_with_keys.flags] == ["y+"]
    assert [flag.token for flag in keys_with_values.flags] == ["y", "k"]
    assert "Display-only" in rank_and_value.flags[1].definition.notes
    assert "orders the keyed subset" in keys_sorted.flags[0].definition.notes


def test_command_service_identifies_full_ranked_harem_snapshot_flags() -> None:
    query = CommandService().explain("$mmrkty+")

    assert [flag.token for flag in query.flags] == ["r", "k", "t", "y+"]
    assert query.flags[-1].definition.meaning == "Show the full key list."


def test_command_service_rejects_unknown_flags() -> None:
    with pytest.raises(ValueError, match="Unknown Mudae flag"):
        CommandService().explain("$mm?")
