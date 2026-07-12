from moa.loader.command_loader import load_commands


def test_command_loader_contains_the_supplied_mudae_flag_reference() -> None:
    commands = load_commands()

    assert len(commands) >= 70
    assert any(command.token == "y><number>" for command in commands)
    assert any(command.token == "o-" and command.category == "ownership" for command in commands)
