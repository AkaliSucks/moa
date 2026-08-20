"""Immutable, declarative metadata for the current Mudae command surface.

This module intentionally has no MOA imports.  The registry is foundation
infrastructure for later consumer migrations; current consumers remain
authoritative until those migrations are performed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class InvocationSource(_StringEnum):
    TEXT = "text"
    INTERACTION_NAME = "interaction_name"


class CommandCapability(_StringEnum):
    COMMAND_SERVICE = "command_service"
    LISTENER = "listener"
    CAPTURE = "capture"
    DIRECT_WORKFLOW = "direct_workflow"


class RecognitionPolicy(_StringEnum):
    EXACT = "exact"
    PREFIX_WITH_OPAQUE_SUFFIX = "prefix_with_opaque_suffix"
    LIST_QUERY = "list_query"


class PrefixPolicy(_StringEnum):
    GENERIC_LISTENER = "generic_listener"
    COMMAND_SERVICE = "command_service"
    DOLLAR_ONLY = "dollar_only"
    DOLLAR_ONLY_CAPTURE = "dollar_only_capture"


class ArgumentPolicy(_StringEnum):
    NONE = "none"
    OPAQUE_OPTIONAL = "opaque_optional"
    LIST_QUERY_DELEGATE = "list_query_delegate"
    PERSONAL_RARE_DELEGATE = "personal_rare_delegate"


@dataclass(frozen=True, slots=True)
class ResponseVariant:
    """A response selected by an opaque modifier prefix."""

    modifier_prefix: str
    response_kind: str | None


@dataclass(frozen=True, slots=True)
class CommandForm:
    """One source- and capability-scoped recognition form."""

    token: str
    recognition: RecognitionPolicy
    capabilities: frozenset[CommandCapability]
    sources: frozenset[InvocationSource]
    prefix_policy: PrefixPolicy
    response_kind: str | None = None
    response_variants: tuple[ResponseVariant, ...] = ()

    @property
    def is_prefix(self) -> bool:
        return self.recognition is not RecognitionPolicy.EXACT


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """Canonical command identity and its immutable recognition metadata."""

    canonical_name: str
    forms: tuple[CommandForm, ...]
    expected_response: str | None
    capabilities: frozenset[CommandCapability] = frozenset()
    argument_policy: ArgumentPolicy = ArgumentPolicy.NONE
    workflow_key: str | None = None

    @property
    def aliases(self) -> tuple[str, ...]:
        """Return forms in their intentionally declared enumeration order."""
        return tuple(form.token for form in self.forms)

    @property
    def consumer_capabilities(self) -> frozenset[CommandCapability]:
        form_capabilities = frozenset(
            capability
            for form in self.forms
            for capability in form.capabilities
        )
        return self.capabilities | form_capabilities


@dataclass(frozen=True, slots=True)
class CommandMatch:
    """A normalized match retaining source text needed by a future consumer."""

    spec: CommandSpec
    form: CommandForm
    raw_token: str
    raw_prefix: str
    normalized_token: str
    modifier_text: str
    source: InvocationSource
    capability: CommandCapability

    @property
    def canonical_name(self) -> str:
        return self.spec.canonical_name

    @property
    def matched_form(self) -> str:
        return self.form.token

    @property
    def original_prefix(self) -> str:
        return self.raw_prefix

    @property
    def expected_response(self) -> str | None:
        for variant in self.form.response_variants:
            if self.modifier_text.casefold().startswith(
                variant.modifier_prefix.casefold()
            ):
                return variant.response_kind
        if self.form.response_kind is not None:
            return self.form.response_kind
        return self.spec.expected_response


class CommandRegistry:
    """Validated immutable command metadata and deterministic lookup indexes."""

    __slots__ = ("_specs", "_canonical", "_forms")

    def __init__(self, specs: Iterable[CommandSpec]) -> None:
        declared_specs = tuple(specs)
        canonical: dict[str, CommandSpec] = {}
        exact: dict[tuple[CommandCapability, InvocationSource, str], tuple[CommandSpec, CommandForm]] = {}
        prefixes: dict[tuple[CommandCapability, InvocationSource, str], tuple[CommandSpec, CommandForm]] = {}
        seen_forms: set[tuple[CommandCapability, InvocationSource, str, bool]] = set()

        for spec in declared_specs:
            canonical_key = _normalized_name(spec.canonical_name)
            if not canonical_key:
                raise ValueError("canonical command name must not be empty")
            if canonical_key in canonical:
                raise ValueError(f"duplicate canonical command: {spec.canonical_name}")
            if not spec.forms:
                raise ValueError(f"command {spec.canonical_name} has no forms")
            canonical[canonical_key] = spec

            for form in spec.forms:
                form_key = _normalized_name(form.token)
                if not form_key:
                    raise ValueError(
                        f"command {spec.canonical_name} has an empty form"
                    )
                if not form.capabilities:
                    raise ValueError(
                        f"form {form.token} has no consumer capability"
                    )
                if not form.sources:
                    raise ValueError(f"form {form.token} has no invocation source")
                _validate_form(spec, form)
                for capability in form.capabilities:
                    for source in form.sources:
                        key = (capability, source, form_key, form.is_prefix)
                        if key in seen_forms:
                            raise ValueError(
                                "duplicate or ambiguous command form: "
                                f"{form.token} for {capability}/{source}"
                            )
                        seen_forms.add(key)
                        target = prefixes if form.is_prefix else exact
                        namespace_key = (capability, source, form_key)
                        if namespace_key in target:
                            raise ValueError(
                                "duplicate command form: "
                                f"{form.token} for {capability}/{source}"
                            )
                        other = exact if form.is_prefix else prefixes
                        if namespace_key in other:
                            raise ValueError(
                                "ambiguous exact/prefix command form: "
                                f"{form.token} for {capability}/{source}"
                            )
                        target[namespace_key] = (spec, form)

        self._specs = declared_specs
        self._canonical = MappingProxyType(canonical)
        self._forms = MappingProxyType(
            {
                "exact": MappingProxyType(exact),
                "prefix": MappingProxyType(prefixes),
            }
        )

    @property
    def specs(self) -> tuple[CommandSpec, ...]:
        return self._specs

    def enumerate(self) -> tuple[CommandSpec, ...]:
        return self._specs

    def lookup(
        self,
        raw_command_token: str,
        *,
        source: InvocationSource | str = InvocationSource.TEXT,
        capability: CommandCapability | str = CommandCapability.LISTENER,
    ) -> CommandMatch | None:
        """Match a command token without parsing message arguments."""
        if not isinstance(raw_command_token, str) or not raw_command_token:
            return None
        invocation_source = _coerce_enum(source, InvocationSource)
        consumer = _coerce_enum(capability, CommandCapability)
        normalized, raw_prefix = _normalize_token(
            raw_command_token, invocation_source, consumer
        )
        if not normalized:
            return None

        exact_candidates = self._applicable_exact(
            normalized, invocation_source, consumer
        )
        if exact_candidates:
            spec, form = exact_candidates[0]
            return _match(
                spec,
                form,
                raw_command_token,
                raw_prefix,
                normalized,
                invocation_source,
                consumer,
            )

        prefix_candidates = self._applicable_prefixes(
            normalized, invocation_source, consumer
        )
        if not prefix_candidates:
            return None
        longest = max(len(form.token) for _, form in prefix_candidates)
        best = [
            (spec, form)
            for spec, form in prefix_candidates
            if len(form.token) == longest
        ]
        if len(best) != 1:
            raise RuntimeError(
                f"unvalidated ambiguous command lookup for {normalized!r}"
            )
        spec, form = best[0]
        return _match(
            spec,
            form,
            raw_command_token,
            raw_prefix,
            normalized,
            invocation_source,
            consumer,
        )

    def expected_response(
        self,
        raw_command_token: str,
        *,
        source: InvocationSource | str = InvocationSource.TEXT,
        capability: CommandCapability | str = CommandCapability.LISTENER,
    ) -> str | None:
        match = self.lookup(
            raw_command_token, source=source, capability=capability
        )
        return match.expected_response if match is not None else None

    def by_canonical(self, canonical_name: str) -> CommandSpec | None:
        return self._canonical.get(_normalized_name(canonical_name))

    def _applicable_exact(
        self,
        normalized: str,
        source: InvocationSource,
        capability: CommandCapability,
    ) -> tuple[tuple[CommandSpec, CommandForm], ...]:
        result = self._forms["exact"].get((capability, source, normalized), ())
        return (result,) if result else ()

    def _applicable_prefixes(
        self,
        normalized: str,
        source: InvocationSource,
        capability: CommandCapability,
    ) -> tuple[tuple[CommandSpec, CommandForm], ...]:
        result = []
        for (form_capability, form_source, token), value in self._forms[
            "prefix"
        ].items():
            if (
                form_capability is capability
                and form_source is source
                and normalized.startswith(token)
            ):
                result.append(value)
        return tuple(result)


def build_registry(specs: Iterable[CommandSpec]) -> CommandRegistry:
    """Build and validate a registry for construction-time conflict tests."""
    return CommandRegistry(specs)


def _normalized_name(value: str) -> str:
    return value.casefold() if isinstance(value, str) else ""


def _coerce_enum(value: object, enum_type: type[_StringEnum]) -> _StringEnum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown {enum_type.__name__}: {value!r}") from exc


def _normalize_token(
    raw_token: str,
    source: InvocationSource,
    capability: CommandCapability,
) -> tuple[str, str]:
    if source is InvocationSource.INTERACTION_NAME:
        if raw_token.startswith(("$", "/")):
            return "", ""
        return raw_token.casefold(), ""
    if capability is CommandCapability.COMMAND_SERVICE:
        if not raw_token.startswith(("$", "/")):
            return "", ""
        return raw_token[1:].casefold(), raw_token[:1]
    if capability is CommandCapability.CAPTURE:
        if not raw_token.startswith("$"):
            return "", ""
        return raw_token[1:].casefold(), "$"
    if capability is CommandCapability.DIRECT_WORKFLOW:
        if not raw_token.startswith("$"):
            return "", ""
        return raw_token[1:].casefold(), "$"
    prefix_length = len(raw_token) - len(raw_token.lstrip("$/"))
    if prefix_length == 0:
        return "", ""
    return raw_token[prefix_length:].casefold(), raw_token[:prefix_length]


def _match(
    spec: CommandSpec,
    form: CommandForm,
    raw_token: str,
    raw_prefix: str,
    normalized: str,
    source: InvocationSource,
    capability: CommandCapability,
) -> CommandMatch:
    modifier = normalized[len(form.token) :] if form.is_prefix else ""
    return CommandMatch(
        spec=spec,
        form=form,
        raw_token=raw_token,
        raw_prefix=raw_prefix,
        normalized_token=normalized,
        modifier_text=modifier,
        source=source,
        capability=capability,
    )


def _validate_form(spec: CommandSpec, form: CommandForm) -> None:
    if form.recognition is RecognitionPolicy.EXACT and form.response_variants:
        raise ValueError(f"exact form {form.token} cannot have suffix variants")
    seen_variant_prefixes: set[str] = set()
    for variant in form.response_variants:
        prefix = _normalized_name(variant.modifier_prefix)
        if not prefix:
            raise ValueError(f"form {form.token} has an empty response variant")
        if prefix in seen_variant_prefixes:
            raise ValueError(f"form {form.token} has duplicate response variant")
        seen_variant_prefixes.add(prefix)
        if variant.response_kind is None:
            continue
        if not _normalized_name(variant.response_kind):
            raise ValueError(f"form {form.token} has an invalid response variant")
    if form.prefix_policy is PrefixPolicy.COMMAND_SERVICE and (
        form.recognition is not RecognitionPolicy.LIST_QUERY
    ):
        raise ValueError(
            f"command-service form {form.token} must use list-query recognition"
        )
    if form.prefix_policy in {
        PrefixPolicy.DOLLAR_ONLY,
        PrefixPolicy.DOLLAR_ONLY_CAPTURE,
    } and form.recognition is not RecognitionPolicy.EXACT and form.prefix_policy is PrefixPolicy.DOLLAR_ONLY:
        raise ValueError(f"dollar-only workflow form {form.token} must be exact")
    if spec.expected_response is None and form.response_kind is not None:
        raise ValueError(
            f"response-less command {spec.canonical_name} cannot define a response"
        )


def _exact(
    *tokens: str,
    capability: CommandCapability = CommandCapability.LISTENER,
    prefix_policy: PrefixPolicy = PrefixPolicy.GENERIC_LISTENER,
    response_overrides: Mapping[str, str | None] | None = None,
) -> tuple[CommandForm, ...]:
    overrides = response_overrides or {}
    sources = (
        frozenset({InvocationSource.TEXT})
        if prefix_policy
        in {PrefixPolicy.DOLLAR_ONLY, PrefixPolicy.DOLLAR_ONLY_CAPTURE}
        else frozenset({InvocationSource.TEXT, InvocationSource.INTERACTION_NAME})
    )
    return tuple(
        CommandForm(
            token=token,
            recognition=RecognitionPolicy.EXACT,
            capabilities=frozenset({capability}),
            sources=sources,
            prefix_policy=prefix_policy,
            response_kind=overrides.get(token.casefold()),
        )
        for token in tokens
    )


def _listener_prefix(
    token: str,
    *,
    response_variants: tuple[ResponseVariant, ...] = (),
) -> CommandForm:
    return CommandForm(
        token=token,
        recognition=RecognitionPolicy.PREFIX_WITH_OPAQUE_SUFFIX,
        capabilities=frozenset({CommandCapability.LISTENER}),
        sources=frozenset({InvocationSource.TEXT, InvocationSource.INTERACTION_NAME}),
        prefix_policy=PrefixPolicy.GENERIC_LISTENER,
        response_variants=response_variants,
    )


def _list_query(token: str) -> CommandForm:
    return CommandForm(
        token=token,
        recognition=RecognitionPolicy.LIST_QUERY,
        capabilities=frozenset({CommandCapability.COMMAND_SERVICE}),
        sources=frozenset({InvocationSource.TEXT}),
        prefix_policy=PrefixPolicy.COMMAND_SERVICE,
    )


def _spec(
    canonical_name: str,
    expected_response: str | None,
    forms: tuple[CommandForm, ...],
    *,
    argument_policy: ArgumentPolicy = ArgumentPolicy.NONE,
    workflow_key: str | None = None,
) -> CommandSpec:
    return CommandSpec(
        canonical_name=canonical_name,
        forms=forms,
        expected_response=expected_response,
        argument_policy=argument_policy,
        workflow_key=workflow_key,
    )


_COMMAND_SPECS = (
    _spec(
        "harem",
        "harem",
        (
            _listener_prefix(
                "mm",
                response_variants=(ResponseVariant("r", "ranked_harem"),),
            ),
            _list_query("mm"),
        ),
        argument_policy=ArgumentPolicy.LIST_QUERY_DELEGATE,
        workflow_key="harem_scan",
    ),
    _spec(
        "antidisable",
        "antidisable",
        (
            _listener_prefix("adl"),
            CommandForm(
                token="adl",
                recognition=RecognitionPolicy.PREFIX_WITH_OPAQUE_SUFFIX,
                capabilities=frozenset({CommandCapability.CAPTURE}),
                sources=frozenset({InvocationSource.TEXT}),
                prefix_policy=PrefixPolicy.DOLLAR_ONLY_CAPTURE,
            ),
        ),
        workflow_key="antidisable_scan",
    ),
    _spec(
        "top",
        "top",
        _exact("top", "topo", "topx", response_overrides={"topx": "topx"})
        + (_list_query("top"),),
        argument_policy=ArgumentPolicy.LIST_QUERY_DELEGATE,
    ),
    _spec("wishlist", "wishlist", _exact("wl", "wishlist")),
    _spec(
        "personalrare",
        "personalrare",
        _exact("persr", "personalrare"),
        argument_policy=ArgumentPolicy.PERSONAL_RARE_DELEGATE,
        workflow_key="personal_rare_acknowledgement",
    ),
    _spec("infokl", "infokl", _exact("infokl", "kakeralootinfo")),
    _spec("profile", "profile", _exact("profile", "pr")),
    _spec("mudapins", "mudapins", _exact("mp", "mudapins", "mudapin")),
    _spec("kakera", "kakera", _exact("k", "kakera")),
    _spec("settings", "settings", _exact("settings", "set")),
    _spec("help", "help", _exact("help")),
    _spec("tuarrange", "help", _exact("tuarrange", "ta")),
    _spec("infopin", "help", _exact("infopin")),
    _spec("tutorial", "tutorial", _exact("tuto", "tutorial")),
    _spec("tu", "timers", _exact("tu", "timersup")),
    _spec("mu", "timers", _exact("mu")),
    _spec("ru", "timers", _exact("ru")),
    _spec("du", "timers", _exact("du")),
    _spec("ku", "timers", _exact("ku")),
    _spec("dk", "timers", _exact("dk")),
    _spec("dku", "timers", _exact("dku")),
    _spec("bku", "timers", _exact("bku")),
    _spec("rtu", "timers", _exact("rtu")),
    _spec("ohu", "timers", _exact("ohu")),
    _spec("rolls", "timers", _exact("rolls")),
    _spec("daily", "timers", _exact("daily")),
    _spec("bonus", "bonus", _exact("bonus", "bonuses")),
    _spec("oq", "sphere_result", _exact("oq", "ouroquest")),
    _spec("kt", "towerstate", _exact("kt", "tower")),
    _spec("lk", "lootstate", _exact("lk", "kakeraloots")),
    _spec("kl", "lootstate", _exact("kl")),
    _spec(
        "im",
        "im",
        _exact("im", "info") + (_list_query("im"),),
        argument_policy=ArgumentPolicy.LIST_QUERY_DELEGATE,
    ),
    _spec("divorce", "divorce", _exact("divorce", "div"), workflow_key="divorce"),
    _spec("givek", "gift_kakera", _exact("givek", "givekakera"), workflow_key="transaction"),
    _spec("givesp", "gift_spheres", _exact("givesp", "givespheres"), workflow_key="transaction"),
    _spec("give", "gift_character", _exact("give"), workflow_key="transaction"),
    _spec("trade", "trade", _exact("trade"), workflow_key="transaction"),
    _spec("disablelist", "disablelist", (_listener_prefix("dl"),)),
    _spec(
        "roll",
        "roll",
        _exact(
            "m",
            "mx",
            "ma",
            "mg",
            "marry",
            "marrya",
            "marryg",
            "w",
            "wx",
            "wa",
            "wg",
            "waifu",
            "waifua",
            "waifug",
            "h",
            "hx",
            "ha",
            "hg",
            "husbando",
            "husbandoa",
            "husbandog",
        ),
    ),
    _spec(
        "ourochest",
        None,
        _exact(
            "oc",
            "ourochest",
            capability=CommandCapability.DIRECT_WORKFLOW,
            prefix_policy=PrefixPolicy.DOLLAR_ONLY,
        ),
        workflow_key="ourochest",
    ),
)


COMMAND_REGISTRY = CommandRegistry(_COMMAND_SPECS)
