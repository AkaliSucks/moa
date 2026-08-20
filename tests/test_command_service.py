import pytest

from moa.services.command_service import CommandService


# Current behavior characterization: this table is intentionally independent
# from CommandService._COMMANDS so a future registry migration cannot make the
# oracle tautological.
COMMAND_SERVICE_BASE_CASES = (
    ("$top", "top"),
    ("/TOP", "top"),
    ("$mm", "mm"),
    ("/MM", "mm"),
    ("$im", "im"),
    ("/IM", "im"),
)


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


@pytest.mark.parametrize(("query_text", "expected_base"), COMMAND_SERVICE_BASE_CASES)
def test_command_service_current_supported_bases_and_prefixes(
    query_text: str, expected_base: str
) -> None:
    query = CommandService().explain(query_text)

    assert query.command == expected_base
    assert query.flags == ()
    assert query.arguments == ()
    assert query.exclusions == ()


@pytest.mark.parametrize(
    ("query_text", "message"),
    (
        ("top", "must start"),
        ("mm", "must start"),
        ("im", "must start"),
        ("$$mm", "Unsupported command"),
        ("$/mm", "Unsupported command"),
        ("$$/MM", "Unsupported command"),
        ("/$mm", "Unsupported command"),
    ),
)
def test_command_service_requires_exactly_one_supported_prefix(
    query_text: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        CommandService().explain(query_text)


def test_command_service_preserves_flag_order_and_repetition() -> None:
    query = CommandService().explain("$mmwryw")

    assert [flag.token for flag in query.flags] == ["w", "r", "y", "w"]


def test_command_service_casefolds_command_and_flag_tokens_but_preserves_arguments() -> None:
    query = CommandService().explain("$MMWYA+ Re:Zero$--Some Series")

    assert query.command == "mm"
    assert [flag.token for flag in query.flags] == ["w", "y", "a+"]
    assert query.arguments == ("Re:Zero",)
    assert query.exclusions == ("Some Series",)


@pytest.mark.parametrize(
    ("query_text", "expected_flags"),
    (
        ("$topk", ("k",)),
        ("$imk", ("k",)),
        ("$mmz<5", ("z<5",)),
        ("$mmy!>7", ("y!>7",)),
    ),
)
def test_command_service_current_flag_capability_and_numeric_boundaries(
    query_text: str, expected_flags: tuple[str, ...]
) -> None:
    query = CommandService().explain(query_text)

    assert tuple(flag.token for flag in query.flags) == expected_flags


@pytest.mark.parametrize(
    ("query_text", "message"),
    (
        ("$mm?", "Unknown Mudae flag"),
        ("$top?", "Unknown Mudae flag"),
        ("$im?", "Unknown Mudae flag"),
        ("$unknown", "Unsupported command"),
        ("$profile", "Unsupported command"),
        ("$info", "Unsupported command"),
        ("$oc", "Unsupported command"),
    ),
)
def test_command_service_rejects_unsupported_bases_or_flags(
    query_text: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        CommandService().explain(query_text)


@pytest.mark.parametrize(
    ("query_text", "expected_arguments", "expected_exclusions"),
    (
        ("$mm", (), ()),
        ("$mm   ", (), ()),
        ("$mm A$B$--C", ("A", "B"), ("C",)),
        ("$mm --first$Second$--third", ("Second",), ("first", "third")),
        ("$mm First$second", ("First", "second"), ()),
    ),
)
def test_command_service_current_argument_boundary(
    query_text: str,
    expected_arguments: tuple[str, ...],
    expected_exclusions: tuple[str, ...],
) -> None:
    query = CommandService().explain(query_text)

    assert query.arguments == expected_arguments
    assert query.exclusions == expected_exclusions
