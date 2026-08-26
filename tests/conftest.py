import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from machine_locator.config import Settings
from machine_locator.db import Database


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.data_dir = tmp_path / "data"
    s.rate_limit_seconds = 0.0
    s.ensure_dirs()
    return s


@pytest.fixture
def db(settings):
    database = Database(settings.db_path)
    yield database
    database.close()
