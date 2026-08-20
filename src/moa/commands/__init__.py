"""Declarative command-recognition metadata."""

from .registry import (
    ArgumentPolicy,
    CommandCapability,
    CommandForm,
    CommandMatch,
    CommandRegistry,
    CommandSpec,
    InvocationSource,
    PrefixPolicy,
    RecognitionPolicy,
    ResponseVariant,
    COMMAND_REGISTRY,
    build_registry,
)

__all__ = [
    "ArgumentPolicy",
    "CommandCapability",
    "CommandForm",
    "CommandMatch",
    "CommandRegistry",
    "CommandSpec",
    "InvocationSource",
    "PrefixPolicy",
    "RecognitionPolicy",
    "ResponseVariant",
    "COMMAND_REGISTRY",
    "build_registry",
]
