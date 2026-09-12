from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path

import moa.database.legacy_database_relocation as relocation_module


_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "moa"


def _python_trees():
    for path in _SOURCE_ROOT.rglob("*.py"):
        yield path, ast.parse(path.read_text(encoding="utf-8-sig"))


def _call_sites(call_name: str) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    for path, tree in _python_trees():
        relative = path.relative_to(_SOURCE_ROOT).as_posix()
        stack: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name) and node.func.id == call_name:
                    found.append((relative, stack[-1] if stack else "<module>"))
                self.generic_visit(node)

        Visitor().visit(tree)
    return tuple(found)


def _sqlite3_connect_sites() -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    for path, tree in _python_trees():
        relative = path.relative_to(_SOURCE_ROOT).as_posix()
        stack: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                function = node.func
                if (
                    isinstance(function, ast.Attribute)
                    and isinstance(function.value, ast.Name)
                    and function.value.id == "sqlite3"
                    and function.attr == "connect"
                ):
                    found.append((relative, stack[-1] if stack else "<module>"))
                self.generic_visit(node)

        Visitor().visit(tree)
    return tuple(found)


def test_all_57_central_transaction_callers_inherit_global_writer_lease() -> None:
    call_sites = _call_sites("run_write_transaction")

    assert len(call_sites) == 57
    assert ("repositories/catalog_repository.py", "import_command_observation") in call_sites
    assert (
        "services/discord_listener_service.py",
        "_start_antidisable_workflow",
    ) in call_sites
    assert ("services/roll_projection_coordinator.py", "coordinate_roll") in call_sites
    assert ("services/retention_expiry_service.py", "apply") in call_sites
    assert (
        "services/projection_generation_activation_service.py",
        "activate_projection_generation",
    ) in call_sites


def test_repository_readers_have_exact_read_only_connection_inventory() -> None:
    repository_sites = tuple(
        site
        for site in _call_sites("connect_read_only")
        if site[0].startswith("repositories/")
    )

    assert len(repository_sites) == 16
    assert _call_sites("connect") == (
        ("database/legacy_database_relocation.py", "_acquire_source_quiescence"),
        (
            "database/legacy_database_relocation.py",
            "_checkpoint_source_for_retirement",
        ),
        ("database/sqlite.py", "run_write_transaction"),
        ("repositories/catalog_repository.py", "_write_connection"),
    )


def test_raw_sqlite_connection_inventory_has_no_unclassified_open() -> None:
    assert _sqlite3_connect_sites() == (
        (
            "database/legacy_database_relocation.py",
            "_open_neutral_source_connection",
        ),
        ("database/legacy_database_relocation.py", "_open_read_only"),
        ("database/legacy_database_relocation.py", "_backup_database"),
        ("database/projection_generation_backup.py", "_acquire_writer_exclusion"),
        ("database/projection_generation_backup.py", "_validate_path"),
        ("database/projection_generation_backup.py", "_backup_and_promote"),
        ("database/projection_generation_backup.py", "_backup_and_promote"),
        ("database/sqlite.py", "connect"),
        ("database/sqlite.py", "connect_read_only"),
        (
            "services/projection_generation_activation_service.py",
            "_logical_path_identity",
        ),
    )


def test_relocation_wal_journal_and_certification_entrypoints_hold_one_lease(
    monkeypatch,
) -> None:
    active = False
    calls: list[str] = []
    sentinel = object()

    @contextmanager
    def observed_lease():
        nonlocal active
        assert not active
        active = True
        calls.append("enter")
        try:
            yield object()
        finally:
            calls.append("exit")
            active = False

    def relocated(*_args, **_kwargs):
        assert active
        calls.append("relocate")
        return sentinel

    def recovered(*_args, **_kwargs):
        assert active
        calls.append("recover")
        return sentinel

    def prepared(*_args, **_kwargs):
        assert active
        calls.append("prepare")
        return sentinel

    def certified(*_args, **_kwargs):
        assert active
        calls.append("certify")
        return sentinel

    monkeypatch.setattr(relocation_module, "shared_database_writer_lease", observed_lease)
    monkeypatch.setattr(
        relocation_module, "_relocate_database_under_writer_lease", relocated
    )
    monkeypatch.setattr(relocation_module, "_recover_database_wal", recovered)
    monkeypatch.setattr(
        relocation_module, "_prepare_database_relocation_journal_mode", prepared
    )
    monkeypatch.setattr(
        relocation_module, "_certify_database_relocation_identity", certified
    )

    assert relocation_module.relocate_database(Path("source"), Path("target")) is sentinel
    assert relocation_module.recover_database_wal(object()) is sentinel
    assert relocation_module.prepare_database_relocation_journal_mode(object()) is sentinel
    assert (
        relocation_module.certify_database_relocation_identity(
            Path("source"), Path("target"), "checkpoint"
        )
        is sentinel
    )
    assert calls == [
        "enter",
        "relocate",
        "exit",
        "enter",
        "recover",
        "exit",
        "enter",
        "prepare",
        "exit",
        "enter",
        "certify",
        "exit",
    ]
