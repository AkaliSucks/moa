from types import SimpleNamespace

import pytest

from moa.services.harem_import_dispatch import (
    HaremImportDispatcher,
    HaremResponseProfile,
    harem_import_from_response_kind,
    require_harem_import,
)


@pytest.mark.parametrize(
    ("raw_invocation", "flags", "profile"),
    (
        ("$mmyk", ("y", "k"), HaremResponseProfile.KEYED),
        ("$mmr", ("r",), HaremResponseProfile.RANKED),
        ("$mmrkty+", ("r", "k", "t", "y+"), HaremResponseProfile.RANKED),
    ),
)
def test_harem_dispatch_normalizes_supported_mm_variants(
    raw_invocation, flags, profile
) -> None:
    dispatch = require_harem_import(raw_invocation)

    assert dispatch.canonical_command == "mm"
    assert dispatch.flags == flags
    assert dispatch.response_profile is profile


def test_harem_dispatch_keeps_mmr_as_a_ranked_compatibility_entry() -> None:
    dispatch = require_harem_import("$mmr")

    assert dispatch.canonical_command == "mm"
    assert dispatch.flags == ("r",)
    assert dispatch.response_profile.import_kind == "ranked_harem"


@pytest.mark.parametrize(
    ("response_kind", "expected_flags", "profile"),
    (
        ("harem", (), HaremResponseProfile.KEYED),
        ("ranked_harem", ("r",), HaremResponseProfile.RANKED),
    ),
)
def test_detected_harem_profiles_reenter_the_canonical_dispatch(
    response_kind, expected_flags, profile
) -> None:
    dispatch = harem_import_from_response_kind(response_kind)

    assert dispatch is not None
    assert dispatch.canonical_command == "mm"
    assert dispatch.flags == expected_flags
    assert dispatch.response_profile is profile


def test_keyed_dispatch_uses_only_the_existing_keyed_adapters() -> None:
    page = SimpleNamespace(page_number=1, page_count=2)
    result = SimpleNamespace(entries_imported=1)
    parser = _Parser(keyed_page=page)
    catalog = _Catalog(keyed_result=result)

    imported = HaremImportDispatcher(parser, catalog).import_page(
        require_harem_import("$mmy"), "keyed page", "Server", "Account", "test", 7
    )

    assert imported.page is page
    assert imported.result is result
    assert parser.calls == [("keyed", "keyed page")]
    assert catalog.calls == [
        ("keyed", page, "Server", "Account", "keyed page", "test", 7)
    ]


def test_ranked_dispatch_preserves_the_existing_ranked_adapter_and_key_fields() -> None:
    page = SimpleNamespace(
        page_number=1,
        page_count=2,
        entries=(SimpleNamespace(key_type="gold", key_count=7),),
    )
    result = SimpleNamespace(entries_imported=1)
    parser = _Parser(ranked_page=page)
    catalog = _Catalog(ranked_result=result)

    imported = HaremImportDispatcher(parser, catalog).import_page(
        require_harem_import("$mmrkty+"), "ranked page", "Server", "Account", "test", 9
    )

    assert imported.page.entries[0].key_count == 7
    assert imported.result is result
    assert parser.calls == [("ranked", "ranked page")]
    assert catalog.calls == [
        ("ranked", page, "Server", "Account", "ranked page", "test", 9)
    ]


class _Parser:
    def __init__(self, *, keyed_page=None, ranked_page=None) -> None:
        self._keyed_page = keyed_page
        self._ranked_page = ranked_page
        self.calls: list[tuple[str, str]] = []

    def parse_harem_key_page(self, raw_message):
        self.calls.append(("keyed", raw_message))
        return self._keyed_page

    def parse_ranked_harem_page(self, raw_message):
        self.calls.append(("ranked", raw_message))
        return self._ranked_page


class _Catalog:
    def __init__(self, *, keyed_result=None, ranked_result=None) -> None:
        self._keyed_result = keyed_result
        self._ranked_result = ranked_result
        self.calls: list[tuple[object, ...]] = []

    def import_harem_key_page(self, *values):
        self.calls.append(("keyed", *values))
        return self._keyed_result

    def import_ranked_harem_page(self, *values):
        self.calls.append(("ranked", *values))
        return self._ranked_result
