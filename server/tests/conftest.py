import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import db as _db


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Tests never read or change the installed service's preferences."""
    monkeypatch.setenv("BOPBOP_SETTINGS_PATH", str(tmp_path / "agent-settings.json"))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point db at a throwaway sqlite file and initialize the schema."""
    path = tmp_path / "bopbop-test.db"
    monkeypatch.setattr(_db, "DB_PATH", str(path))
    _db.init_db()
    return str(path)
