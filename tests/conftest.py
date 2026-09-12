from pathlib import Path

import pytest

import moa.database.writer_lease as writer_lease_module


@pytest.fixture(autouse=True)
def _isolate_database_writer_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep every test's profile-global coordination artifact disposable."""
    original_path = writer_lease_module.database_writer_lease_path
    isolated_profile_root = tmp_path / "moa-profile"

    def isolated_path(profile_root: Path | None = None) -> Path:
        return original_path(
            isolated_profile_root if profile_root is None else profile_root
        )

    monkeypatch.setattr(writer_lease_module, "database_writer_lease_path", isolated_path)
