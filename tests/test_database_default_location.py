from pathlib import Path

import pytest

from moa.database import legacy_database_relocation, sqlite
from moa.database.legacy_database_relocation import (
    LegacyDatabaseAuthorityConflictError,
    LegacyDatabaseRelocationRequiredError,
)
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.automatic_import_service import AutomaticImportService
from moa.services.catalog_service import CatalogService
from moa.services.data_health_service import DataHealthService


def _disable_checkout_detection(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        legacy_database_relocation,
        "_source_file_path",
        lambda: tmp_path / "site-packages" / "moa" / "database" / "module.py",
    )


def _configure_checkout(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    checkout = tmp_path / "checkout"
    source_file = checkout / "src" / "moa" / "database" / "legacy_database_relocation.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("# synthetic source", encoding="utf-8")
    (source_file.parent / "sqlite.py").write_text("# synthetic sqlite", encoding="utf-8")
    (checkout / "pyproject.toml").write_text("[project]", encoding="utf-8")
    (checkout / ".git").mkdir()
    monkeypatch.setattr(legacy_database_relocation, "_source_file_path", lambda: source_file)
    return checkout, checkout / "data" / "database" / "moa.db"


def test_default_database_path_is_platform_data_and_cwd_independent(
    monkeypatch, tmp_path
) -> None:
    platform_root = tmp_path / "user-data" / "moa"
    checkout = tmp_path / "checkout"
    first_cwd = tmp_path / "cwd-a"
    second_cwd = tmp_path / "cwd-b"
    checkout.mkdir()
    first_cwd.mkdir()
    second_cwd.mkdir()
    _disable_checkout_detection(monkeypatch, tmp_path)
    monkeypatch.setattr(sqlite, "user_data_path", lambda **_kwargs: platform_root)

    monkeypatch.chdir(first_cwd)
    first = sqlite.default_database_path()
    monkeypatch.chdir(second_cwd)
    second = sqlite.default_database_path()

    assert first == second == platform_root / "moa.db"
    assert not first.is_relative_to(checkout)
    assert first.name == "moa.db"


def test_clean_install_creates_only_platform_data_parent(monkeypatch, tmp_path) -> None:
    platform_root = tmp_path / "user-data" / "moa"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _disable_checkout_detection(monkeypatch, tmp_path)
    monkeypatch.setattr(sqlite, "user_data_path", lambda **_kwargs: platform_root)

    repository = CatalogRepository()

    assert repository.database_path == platform_root / "moa.db"
    assert repository.database_path.is_file()
    assert not (checkout / "data").exists()


def test_verified_legacy_only_blocks_before_new_database_creation(
    monkeypatch, tmp_path
) -> None:
    _checkout, legacy_path = _configure_checkout(monkeypatch, tmp_path)
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"legacy remains untouched")
    platform_root = tmp_path / "user-data" / "moa"
    monkeypatch.setattr(sqlite, "user_data_path", lambda **_kwargs: platform_root)

    with pytest.raises(LegacyDatabaseRelocationRequiredError) as error:
        sqlite.connect()

    assert str(legacy_path.resolve()) in str(error.value)
    assert str((platform_root / "moa.db").resolve()) in str(error.value)
    assert legacy_path.read_bytes() == b"legacy remains untouched"
    assert not (platform_root / "moa.db").exists()


def test_verified_legacy_and_new_database_fail_as_dual_authority(
    monkeypatch, tmp_path
) -> None:
    _checkout, legacy_path = _configure_checkout(monkeypatch, tmp_path)
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"legacy")
    platform_root = tmp_path / "user-data" / "moa"
    target = platform_root / "moa.db"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"new")
    monkeypatch.setattr(sqlite, "user_data_path", lambda **_kwargs: platform_root)

    with pytest.raises(LegacyDatabaseAuthorityConflictError):
        sqlite.connect()

    assert legacy_path.read_bytes() == b"legacy"
    assert target.read_bytes() == b"new"


def test_explicit_database_path_bypasses_default_legacy_detection(
    monkeypatch, tmp_path
) -> None:
    _checkout, legacy_path = _configure_checkout(monkeypatch, tmp_path)
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"legacy")
    platform_root = tmp_path / "user-data" / "moa"
    explicit_path = tmp_path / "explicit" / "custom.db"
    monkeypatch.setattr(sqlite, "user_data_path", lambda **_kwargs: platform_root)

    connection = sqlite.connect(explicit_path)
    connection.close()

    assert explicit_path.is_file()
    assert legacy_path.read_bytes() == b"legacy"
    assert not (platform_root / "moa.db").exists()


def test_similarly_named_database_outside_verified_checkout_does_not_block(
    monkeypatch, tmp_path
) -> None:
    _disable_checkout_detection(monkeypatch, tmp_path)
    random_database = tmp_path / "unrelated" / "data" / "database" / "moa.db"
    random_database.parent.mkdir(parents=True)
    random_database.write_bytes(b"not a candidate")
    platform_root = tmp_path / "user-data" / "moa"
    monkeypatch.setattr(sqlite, "user_data_path", lambda **_kwargs: platform_root)

    connection = sqlite.connect()
    connection.close()

    assert (platform_root / "moa.db").is_file()
    assert random_database.read_bytes() == b"not a candidate"


def test_invalid_platform_data_root_does_not_fall_back_to_checkout(
    monkeypatch, tmp_path
) -> None:
    _disable_checkout_detection(monkeypatch, tmp_path)
    structural_blocker = tmp_path / "not-a-directory"
    structural_blocker.write_text("file", encoding="utf-8")
    monkeypatch.setattr(
        sqlite,
        "user_data_path",
        lambda **_kwargs: structural_blocker / "moa",
    )

    with pytest.raises(OSError):
        sqlite.connect()

    assert structural_blocker.read_text(encoding="utf-8") == "file"
    assert not (tmp_path / "data" / "database" / "moa.db").exists()


def test_database_path_override_is_late_bound_and_returns_disposable_path(
    monkeypatch, tmp_path
) -> None:
    override = tmp_path / "disposable" / "smoke.db"
    monkeypatch.setenv("MOA_DATABASE_PATH", str(override))

    assert sqlite.effective_default_database_path() == override
    assert sqlite.DEFAULT_DATABASE_PATH.__fspath__() == str(override)


def test_database_path_override_preserves_default_when_absent(monkeypatch, tmp_path) -> None:
    platform_root = tmp_path / "user-data" / "moa"
    _disable_checkout_detection(monkeypatch, tmp_path)
    monkeypatch.delenv("MOA_DATABASE_PATH", raising=False)
    monkeypatch.setattr(sqlite, "user_data_path", lambda **_kwargs: platform_root)

    assert sqlite.effective_default_database_path() == platform_root / "moa.db"


@pytest.mark.parametrize("configured_path", ["", "relative/moa.db"])
def test_database_path_override_rejects_invalid_paths(monkeypatch, configured_path) -> None:
    monkeypatch.setenv("MOA_DATABASE_PATH", configured_path)

    with pytest.raises(ValueError, match="MOA_DATABASE_PATH must be an absolute SQLite"):
        sqlite.connect()


def test_database_path_override_converges_across_import_catalog_and_data_health_paths(
    monkeypatch, tmp_path
) -> None:
    override = tmp_path / "disposable" / "smoke.db"
    monkeypatch.setenv("MOA_DATABASE_PATH", str(override))

    def unexpected_default_path(**_kwargs):
        raise AssertionError("the canonical/default database resolver was used")

    monkeypatch.setattr(sqlite, "default_database_path", unexpected_default_path)
    catalog = CatalogService()
    import_result = AutomaticImportService(catalog_service=catalog).import_message(
        "TOP 1000\n#1 - Hatsune Miku - VOCALOID\nPage 1 / 67",
        "disposable-fixture",
    )
    catalog_result = CatalogService().recent_imports(1)
    health_result = DataHealthService().find_orphans()

    assert import_result.imported_count == 1
    assert len(catalog_result) == 1
    assert health_result == ()
    assert override.is_file()
    assert catalog.database_path == override
