"""Captured/default visual-identity resolution for the `$oc` board."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from moa.models.ourochest import (
    OurochestSphere,
    OurochestVisualResolution,
    OurochestVisualResolutionKind,
)
from moa.models.ourosphere import OuroHuntVisualIdentity


_HIDDEN = OurochestVisualResolution(OurochestVisualResolutionKind.HIDDEN)
_UNKNOWN = OurochestVisualResolution(OurochestVisualResolutionKind.UNKNOWN)


def _visual(kind: str, id_sha256: str, name_sha256: str, name_length: int) -> OuroHuntVisualIdentity:
    return OuroHuntVisualIdentity(
        kind=kind,
        id_sha256=id_sha256,
        name_sha256=name_sha256,
        name_length=name_length,
    )


def _sphere(sphere: OurochestSphere) -> OurochestVisualResolution:
    return OurochestVisualResolution(OurochestVisualResolutionKind.SPHERE, sphere)


# Evidence-bounded to the one committed captured/default visual profile.
_CAPTURED_DEFAULT_ALIASES: Mapping[
    OuroHuntVisualIdentity, OurochestVisualResolution
] = MappingProxyType(
    {
        _visual(
            "custom",
            "b2cd9c92f821fd331964149061b00f99efc606469ff1b9b16f1ac72c92ab2497",
            "39ec8463ec7eacc3f341236ea7c7417e71cf93741812d5234119017b8268e254",
            3,
        ): _HIDDEN,
        _visual(
            "custom",
            "3e64b4c4b45f51d24a831efb97de48a960793f3e299b3a2f94c129c96003fe6e",
            "c37025507bdaffde3d4f667af6a8155ed87978b45dff6518e0c53842652d1164",
            2,
        ): _sphere(OurochestSphere.RED),
        _visual(
            "custom",
            "f8666d54fd7b9e30230a10013b4ea078ce03fe9816a3d0ed6c5b41b5e07d900f",
            "622331044bf2986cd8c1eb8e1f17d95280f2ce81a9f6f412b97abafd5bd02c38",
            3,
        ): _sphere(OurochestSphere.ORANGE),
        _visual(
            "custom",
            "7d9b0892044a291c10f4b4b2833fde1ab44f8fd2d373f9a60ea0576e630b8ad5",
            "2924b8f19927f1b826cfe6a191c37500f93c9f205993349a9531fc2dccc3a0c0",
            3,
        ): _sphere(OurochestSphere.YELLOW),
        _visual(
            "custom",
            "50356ac444db86d80759ca5494ae84639a42546212b7be361cf5fc3396afdc30",
            "3b1a7edf886cf0fdfe5b2ad1d1910cf24663a1812b5e55fe50b307951ebdeffa",
            3,
        ): _sphere(OurochestSphere.GREEN),
        _visual(
            "custom",
            "6773dff8bbd4b09ff6e6aefac0a3c6f5acd520d4b3e829c00f8af32a93154ae8",
            "9ef2e8bd40c2152eb364615b14b25a0ee8ce8190ab6f84c0e36bd315d7403d19",
            3,
        ): _sphere(OurochestSphere.TEAL),
        _visual(
            "custom",
            "9f80f4e31b9a256fb7060256cf11d620f0f5b61c94dda92aed73eb14e3f69932",
            "7fca018d9bef713e870f5ac4b9480262283da3fd2c01323665480ff39d32b73b",
            3,
        ): _sphere(OurochestSphere.BLUE),
    }
)


def resolve_ourochest_visual(visual: OuroHuntVisualIdentity) -> OurochestVisualResolution:
    """Resolve one opaque visual using only the captured/default alias profile."""

    if not isinstance(visual, OuroHuntVisualIdentity):
        raise TypeError("visual must be an OuroHuntVisualIdentity")
    return _CAPTURED_DEFAULT_ALIASES.get(visual, _UNKNOWN)


class OurochestVisualAliasService:
    """Stateless facade for captured/default `$oc` visual resolution."""

    @staticmethod
    def resolve(visual: OuroHuntVisualIdentity) -> OurochestVisualResolution:
        return resolve_ourochest_visual(visual)
