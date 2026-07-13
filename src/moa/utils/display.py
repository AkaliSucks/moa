"""Shared presentation helpers for values copied from Mudae."""


def format_mudae_key_marker(key_type: str | None, key_count: int | None) -> str:
    """Render a key tier and count using Mudae's custom-marker text format."""
    if key_count is None:
        return "-"
    normalized_type = key_type.strip().casefold() if key_type else ""
    if normalized_type.endswith(" key"):
        normalized_type = normalized_type[:-4].strip()
    marker = f":{normalized_type}key:" if normalized_type else ":key:"
    return f"{marker} ({key_count})"


def format_mudae_kakera(value: int | None) -> str:
    """Render a Kakera value using Mudae's custom-marker text format."""
    return "-" if value is None else f"{value:,}:kakera:"


def format_mudae_reaction_kakera(value: int, reaction_label: str | None) -> str:
    """Render a reaction amount with its observed tier marker."""
    marker = reaction_label.strip() if reaction_label else ":kakera:"
    return f"+{value:,}{marker}"


def format_mudae_gender(gender: str | None) -> str:
    """Render one or more stored gender tokens as Mudae gender markers."""
    if not gender:
        return "-"
    tokens = gender.replace(":", ",").split(",")
    markers = [token.strip().casefold() for token in tokens if token.strip() in {"female", "male"}]
    return "".join(f":{token}:" for token in markers) or "-"


def format_mudae_roulette_types(roulette_types: tuple[str, ...] | list[str] | None) -> str:
    """Render `$wa`/`$ha`/`$wg`/`$hg`-style roulette type markers."""
    if not roulette_types:
        return "-"
    return ", ".join(
        f"${roulette_type.strip().removeprefix('$').casefold()}"
        for roulette_type in roulette_types
        if roulette_type.strip()
    ) or "-"
