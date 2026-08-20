import pytest

from moa.commands.registry import (
    CommandCapability,
    CommandForm,
    CommandMatch,
    CommandSpec,
    InvocationSource,
    PrefixPolicy,
    RecognitionPolicy,
    COMMAND_REGISTRY,
    build_registry,
)


LISTENER_CASES = (
    ("$mm", "harem"), ("$mmw", "harem"), ("$mmrkty+", "ranked_harem"),
    ("$adl", "antidisable"), ("$ADLopaque", "antidisable"),
    ("$top", "top"), ("$topo", "top"), ("$topx", "topx"),
    ("$wl", "wishlist"), ("$wishlist", "wishlist"),
    ("$persr", "personalrare"), ("$personalrare", "personalrare"),
    ("$infokl", "infokl"), ("$kakeralootinfo", "infokl"),
    ("$profile", "profile"), ("$pr", "profile"),
    ("$mp", "mudapins"), ("$mudapins", "mudapins"), ("$mudapin", "mudapins"),
    ("$k", "kakera"), ("$kakera", "kakera"),
    ("$settings", "settings"), ("$set", "settings"),
    ("$help", "help"), ("$tuarrange", "help"), ("$ta", "help"), ("$infopin", "help"),
    ("$tuto", "tutorial"), ("$tutorial", "tutorial"),
    *tuple((f"${token}", "timers") for token in ("tu", "timersup", "mu", "ru", "du", "ku", "dk", "dku", "bku", "rtu", "ohu", "rolls", "daily")),
    ("$bonus", "bonus"), ("$bonuses", "bonus"),
    ("$oq", "sphere_result"), ("$ouroquest", "sphere_result"),
    ("$kt", "towerstate"), ("$tower", "towerstate"),
    ("$lk", "lootstate"), ("$kakeraloots", "lootstate"), ("$kl", "lootstate"),
    ("$im", "im"), ("$info", "im"),
    ("$divorce", "divorce"), ("$div", "divorce"),
    ("$givek", "gift_kakera"), ("$givekakera", "gift_kakera"),
    ("$givesp", "gift_spheres"), ("$givespheres", "gift_spheres"),
    ("$give", "gift_character"), ("$trade", "trade"),
    ("$dl", "disablelist"), ("$DLopaque", "disablelist"),
)


@pytest.mark.parametrize(("token", "response"), LISTENER_CASES)
def test_current_listener_matrix(token: str, response: str) -> None:
    match = COMMAND_REGISTRY.lookup(token)
    assert isinstance(match, CommandMatch)
    assert match.expected_response == response


def test_listener_roll_matrix_and_near_misses() -> None:
    forms = (
        "m", "mx", "ma", "mg", "marry", "marrya", "marryg",
        "w", "wx", "wa", "wg", "waifu", "waifua", "waifug",
        "h", "hx", "ha", "hg", "husbando", "husbandoa", "husbandog",
    )
    for form in forms:
        assert COMMAND_REGISTRY.expected_response(f"${form}") == "roll"
    for token in ("$marryx", "$waifux", "$husbandoe", "$m+a"):
        assert COMMAND_REGISTRY.lookup(token) is None


def test_generic_listener_prefix_run_and_case_policy() -> None:
    for token in ("$wa", "/wa", "$$wa", "$/wa", "/$/WA"):
        match = COMMAND_REGISTRY.lookup(token)
        assert match is not None
        assert match.canonical_name == "roll"
    match = COMMAND_REGISTRY.lookup("$MMRopaque")
    assert match is not None
    assert match.modifier_text == "ropaque"
    assert match.expected_response == "ranked_harem"
    assert match.raw_token == "$MMRopaque"
    assert match.raw_prefix == "$"


def test_interaction_names_are_bare_and_prefixes_are_not_textual_prefixes() -> None:
    assert COMMAND_REGISTRY.lookup("wa", source=InvocationSource.INTERACTION_NAME) is not None
    assert COMMAND_REGISTRY.lookup("$wa", source=InvocationSource.INTERACTION_NAME) is None
    assert COMMAND_REGISTRY.lookup("/wa", source=InvocationSource.INTERACTION_NAME) is None


def test_command_service_has_its_own_surface_and_opaque_delegate_tail() -> None:
    for token, canonical in (("$mmrkty+", "harem"), ("$topk", "top"), ("$imr", "im")):
        match = COMMAND_REGISTRY.lookup(
            token, capability=CommandCapability.COMMAND_SERVICE
        )
        assert match is not None
        assert match.canonical_name == canonical
        assert match.modifier_text == {"$mmrkty+": "rkty+", "$topk": "k", "$imr": "r"}[token]
    for token in ("$$mm", "$/mm", "/$mm", "mm"):
        assert COMMAND_REGISTRY.lookup(token, capability=CommandCapability.COMMAND_SERVICE) is None
    for token in ("$topk", "$imk"):
        assert COMMAND_REGISTRY.lookup(token) is None


def test_capture_and_ourochest_boundaries() -> None:
    for token in ("$adl", "$ADLopaque"):
        assert COMMAND_REGISTRY.lookup(token, capability=CommandCapability.CAPTURE) is not None
    for token in ("/adl", "adl", "adlopaque"):
        assert COMMAND_REGISTRY.lookup(token, capability=CommandCapability.CAPTURE) is None
    for token in ("$oc", "$OUROCHEST"):
        match = COMMAND_REGISTRY.lookup(token, capability=CommandCapability.DIRECT_WORKFLOW)
        assert match is not None and match.expected_response is None
    for token in ("/oc", "$ocx", "oc"):
        assert COMMAND_REGISTRY.lookup(token, capability=CommandCapability.DIRECT_WORKFLOW) is None


def test_response_equivalent_commands_remain_distinct() -> None:
    assert COMMAND_REGISTRY.lookup("$help").canonical_name == "help"
    assert COMMAND_REGISTRY.lookup("$infopin").canonical_name == "infopin"
    assert COMMAND_REGISTRY.lookup("$lk").canonical_name == "lk"
    assert COMMAND_REGISTRY.lookup("$kl").canonical_name == "kl"
    assert COMMAND_REGISTRY.lookup("$tu").canonical_name == "tu"
    assert COMMAND_REGISTRY.lookup("$daily").canonical_name == "daily"


def test_enumeration_is_stable_complete_and_has_no_planned_commands() -> None:
    first = COMMAND_REGISTRY.enumerate()
    second = COMMAND_REGISTRY.enumerate()
    assert first is second
    assert len({spec.canonical_name for spec in first}) == len(first)
    assert {spec.canonical_name for spec in first} == {
        "harem", "antidisable", "top", "wishlist", "personalrare", "infokl",
        "profile", "mudapins", "kakera", "settings", "help", "tuarrange", "infopin",
        "tutorial", "tu", "mu", "ru", "du", "ku", "dk", "dku", "bku", "rtu", "ohu",
        "rolls", "daily", "bonus", "oq", "kt", "lk", "kl", "im", "divorce", "givek",
        "givesp", "give", "trade", "disablelist", "roll", "ourochest",
    }


def _synthetic(
    name: str,
    token: str,
    *,
    recognition: RecognitionPolicy = RecognitionPolicy.EXACT,
    capability: CommandCapability = CommandCapability.LISTENER,
) -> CommandSpec:
    return CommandSpec(
        canonical_name=name,
        forms=(CommandForm(
            token=token,
            recognition=recognition,
            capabilities=frozenset({capability}),
            sources=frozenset({InvocationSource.TEXT}),
            prefix_policy=(PrefixPolicy.GENERIC_LISTENER if recognition is not RecognitionPolicy.LIST_QUERY else PrefixPolicy.COMMAND_SERVICE),
        ),),
        expected_response="test",
    )


@pytest.mark.parametrize(
    "specs",
    (
        (_synthetic("same", "a"), _synthetic("same", "b")),
        (_synthetic("one", "a"), _synthetic("two", "a")),
        (_synthetic("one", "a", recognition=RecognitionPolicy.PREFIX_WITH_OPAQUE_SUFFIX), _synthetic("two", "a", recognition=RecognitionPolicy.PREFIX_WITH_OPAQUE_SUFFIX)),
        (_synthetic("one", "a"), _synthetic("two", "a", recognition=RecognitionPolicy.PREFIX_WITH_OPAQUE_SUFFIX)),
    ),
)
def test_construction_rejects_duplicate_or_ambiguous_forms(specs) -> None:
    with pytest.raises(ValueError):
        build_registry(specs)


def test_construction_rejects_invalid_metadata_and_public_metadata_is_immutable() -> None:
    with pytest.raises(ValueError):
        build_registry((_synthetic("", "a"),))
    with pytest.raises(ValueError):
        build_registry((CommandSpec("empty", (), "test"),))
    with pytest.raises(ValueError):
        build_registry((CommandSpec("empty-cap", (CommandForm("a", RecognitionPolicy.EXACT, frozenset(), frozenset({InvocationSource.TEXT}), PrefixPolicy.GENERIC_LISTENER),), "test"),))
    spec = COMMAND_REGISTRY.by_canonical("harem")
    assert spec.forms.__class__ is tuple
    assert spec.consumer_capabilities.__class__ is frozenset
    with pytest.raises(AttributeError):
        spec.canonical_name = "changed"
    with pytest.raises(AttributeError):
        COMMAND_REGISTRY.specs += (spec,)
