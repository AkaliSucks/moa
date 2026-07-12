"""Explain and tokenize Mudae list/search command flags."""

import re

from moa.models.command import MudaeCommandQuery, ParsedMudaeFlag
from moa.repositories.command_repository import CommandRepository, CommandRepositoryProtocol


class CommandService:
    """Provide a source-accurate reference for Mudae command flags."""

    _COMMANDS = ("top", "mm", "im")

    def __init__(self, repository: CommandRepositoryProtocol | None = None) -> None:
        self._repository = repository or CommandRepository()

    def all(self):
        return self._repository.all()

    def explain(self, raw_query: str) -> MudaeCommandQuery:
        """Parse a combined command token and `$`-separated search arguments."""
        query = raw_query.strip()
        if not query:
            raise ValueError("Provide a Mudae command such as `$mmwy= Re:Zero$--Some series`.")

        parts = query.split(maxsplit=1)
        command_token = parts[0]
        command, flag_text = self._split_command(command_token)
        flags = self._parse_flags(flag_text)
        arguments, exclusions = self._parse_arguments(parts[1] if len(parts) == 2 else "")
        return MudaeCommandQuery(
            raw_query=raw_query,
            command=command,
            flags=flags,
            arguments=arguments,
            exclusions=exclusions,
        )

    def _split_command(self, token: str) -> tuple[str, str]:
        if not token or token[0] not in "$/":
            raise ValueError("Mudae command must start with `$` or `/`.")
        normalized = token[1:].casefold()
        for command in sorted(self._COMMANDS, key=len, reverse=True):
            if normalized.startswith(command):
                return command, normalized[len(command) :]
        raise ValueError(f"Unsupported command `{token}`. Supported bases: $mm, $im, and $top.")

    def _parse_flags(self, flag_text: str) -> tuple[ParsedMudaeFlag, ...]:
        definitions = self._repository.all()
        exact = sorted(
            (definition for definition in definitions if definition.match_prefix is None),
            key=lambda definition: len(definition.token),
            reverse=True,
        )
        numeric = tuple(definition for definition in definitions if definition.match_prefix is not None)
        flags: list[ParsedMudaeFlag] = []
        position = 0
        while position < len(flag_text):
            matched = False
            for definition in numeric:
                prefix = definition.match_prefix.casefold()
                if not flag_text.startswith(prefix, position):
                    continue
                number = re.match(r"\d+", flag_text[position + len(prefix) :])
                if number is None:
                    continue
                actual_token = flag_text[position : position + len(prefix) + len(number.group(0))]
                flags.append(ParsedMudaeFlag(token=actual_token, definition=definition))
                position += len(actual_token)
                matched = True
                break
            if matched:
                continue

            for definition in exact:
                if flag_text.startswith(definition.token.casefold(), position):
                    flags.append(ParsedMudaeFlag(token=definition.token, definition=definition))
                    position += len(definition.token)
                    matched = True
                    break
            if matched:
                continue

            raise ValueError(f"Unknown Mudae flag near `{flag_text[position:]}`.")
        return tuple(flags)

    @staticmethod
    def _parse_arguments(raw_arguments: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        arguments: list[str] = []
        exclusions: list[str] = []
        for value in raw_arguments.split("$"):
            normalized = value.strip()
            if not normalized:
                continue
            if normalized.startswith("--"):
                excluded = normalized[2:].strip()
                if excluded:
                    exclusions.append(excluded)
            else:
                arguments.append(normalized)
        return tuple(arguments), tuple(exclusions)
