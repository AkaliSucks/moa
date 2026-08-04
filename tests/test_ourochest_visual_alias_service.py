from __future__ import annotations

from dataclasses import fields, replace
import json
from pathlib import Path

import pytest

from moa.models.ourochest import (
    OurochestSphere,
    OurochestVisualResolution,
    OurochestVisualResolutionKind,
)
from moa.models.ourosphere import OuroHuntVisualIdentity
from moa.services.ourochest_visual_alias_service import resolve_ourochest_visual
from moa.services.ourosphere_board_service import OuroHuntBoardService


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "discord" / "oc_structural_capture.v1.json"
ANNOTATION_PATH = Path(__file__).parent / "fixtures" / "discord" / "oc_characterization.v1.json"
BOARD_SERVICE = OuroHuntBoardService()
EXPECTED_SEMANTICS = {
    OurochestSphere.RED,
    OurochestSphere.ORANGE,
    OurochestSphere.YELLOW,
    OurochestSphere.GREEN,
    OurochestSphere.TEAL,
    OurochestSphere.BLUE,
}


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _annotation() -> dict[str, object]:
    return json.loads(ANNOTATION_PATH.read_text(encoding="utf-8"))


def _characterized_visuals() -> dict[str, OuroHuntVisualIdentity]:
    visuals = _annotation()["semantic_visual_aliases"]["visuals"]
    return {
        label: OuroHuntVisualIdentity(**identity)
        for label, identity in visuals.items()
    }


def _captured_visuals() -> set[OuroHuntVisualIdentity]:
    return {
        cell.visual_identity
        for record in _fixture()["records"]
        for cell in BOARD_SERVICE.project(record["message"]).cells
    }


def test_resolution_states_enforce_hidden_unknown_and_sphere_invariants() -> None:
    assert fields(OurochestVisualResolution)
    assert OurochestVisualResolution(OurochestVisualResolutionKind.HIDDEN).sphere is None
    assert OurochestVisualResolution(OurochestVisualResolutionKind.UNKNOWN).sphere is None
    assert OurochestVisualResolution(
        OurochestVisualResolutionKind.SPHERE, OurochestSphere.RED
    ).sphere is OurochestSphere.RED

    with pytest.raises(ValueError, match="must not carry"):
        OurochestVisualResolution(OurochestVisualResolutionKind.HIDDEN, OurochestSphere.RED)
    with pytest.raises(ValueError, match="must not carry"):
        OurochestVisualResolution(OurochestVisualResolutionKind.UNKNOWN, OurochestSphere.BLUE)
    with pytest.raises(ValueError, match="requires"):
        OurochestVisualResolution(OurochestVisualResolutionKind.SPHERE)
    with pytest.raises(ValueError, match="cannot carry"):
        OurochestVisualResolution(
            OurochestVisualResolutionKind.SPHERE, OurochestSphere.UNKNOWN
        )


@pytest.mark.parametrize(
    ("label", "kind", "sphere"),
    [
        ("hidden/question", OurochestVisualResolutionKind.HIDDEN, None),
        ("Red", OurochestVisualResolutionKind.SPHERE, OurochestSphere.RED),
        ("Orange", OurochestVisualResolutionKind.SPHERE, OurochestSphere.ORANGE),
        ("Yellow", OurochestVisualResolutionKind.SPHERE, OurochestSphere.YELLOW),
        ("Green", OurochestVisualResolutionKind.SPHERE, OurochestSphere.GREEN),
        ("Teal", OurochestVisualResolutionKind.SPHERE, OurochestSphere.TEAL),
        ("Blue", OurochestVisualResolutionKind.SPHERE, OurochestSphere.BLUE),
    ],
)
def test_captured_visuals_resolve_to_their_committed_semantics(
    label: str,
    kind: OurochestVisualResolutionKind,
    sphere: OurochestSphere | None,
) -> None:
    visual = _characterized_visuals()[label]

    assert visual in _captured_visuals()
    assert resolve_ourochest_visual(visual) == OurochestVisualResolution(kind, sphere)


def test_complete_characterized_profile_has_exactly_one_hidden_and_six_spheres() -> None:
    visuals = _characterized_visuals()
    resolutions = {label: resolve_ourochest_visual(visual) for label, visual in visuals.items()}

    assert len(visuals) == 7
    assert set(visuals.values()) == _captured_visuals()
    assert all(resolution.kind is not OurochestVisualResolutionKind.UNKNOWN for resolution in resolutions.values())
    assert sum(
        resolution.kind is OurochestVisualResolutionKind.HIDDEN
        for resolution in resolutions.values()
    ) == 1
    assert {
        resolution.sphere
        for resolution in resolutions.values()
        if resolution.kind is OurochestVisualResolutionKind.SPHERE
    } == EXPECTED_SEMANTICS


def test_captured_profile_has_one_unique_identity_per_resolution() -> None:
    visuals = _characterized_visuals()
    by_resolution: dict[OurochestVisualResolution, list[OuroHuntVisualIdentity]] = {}
    for visual in visuals.values():
        resolution = resolve_ourochest_visual(visual)
        by_resolution.setdefault(resolution, []).append(visual)

    assert len(by_resolution) == 7
    assert all(len(identities) == 1 for identities in by_resolution.values())


@pytest.mark.parametrize("field", ["id_sha256", "name_sha256", "name_length", "kind"])
def test_altered_known_identity_fails_closed(field: str) -> None:
    visual = _characterized_visuals()["Red"]
    altered = {
        "id_sha256": "0" * 64,
        "name_sha256": "0" * 64,
        "name_length": visual.name_length + 1,
        "kind": "alternate",
    }[field]

    assert resolve_ourochest_visual(replace(visual, **{field: altered})).kind is OurochestVisualResolutionKind.UNKNOWN


def test_well_formed_identity_absent_from_bounded_profile_is_unknown() -> None:
    visual = OuroHuntVisualIdentity(
        kind="custom",
        id_sha256="a" * 64,
        name_sha256="b" * 64,
        name_length=4,
    )

    assert resolve_ourochest_visual(visual) == OurochestVisualResolution(
        OurochestVisualResolutionKind.UNKNOWN
    )


def test_resolver_api_has_no_board_or_candidate_inputs() -> None:
    result = resolve_ourochest_visual(_characterized_visuals()["Orange"])

    assert result.kind is OurochestVisualResolutionKind.SPHERE
    assert result.sphere is OurochestSphere.ORANGE
    assert {field.name for field in fields(result)} == {"kind", "sphere"}
    assert not hasattr(result, "coordinate")
    assert not hasattr(result, "disabled")
    assert not hasattr(result, "reward")


def test_fixture_backed_alias_evidence_is_sanitized_structural_data_only() -> None:
    fixture = _fixture()
    provenance = fixture["provenance"]
    assert provenance["contains_message_content"] is False
    assert provenance["contains_raw_discord_ids"] is False
    assert set(_annotation()["semantic_visual_aliases"]["visuals"]) == {
        "hidden/question",
        "Red",
        "Orange",
        "Yellow",
        "Green",
        "Teal",
        "Blue",
    }
    assert all(
        set(identity) == {"kind", "id_sha256", "name_sha256", "name_length"}
        for identity in _annotation()["semantic_visual_aliases"]["visuals"].values()
    )
