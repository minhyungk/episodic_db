"""Test fixtures for episodic_db."""

import tempfile
from pathlib import Path

import pytest

from episodic_db.config import Config
from episodic_db.store.db import Database


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def config(tmp_dir):
    return Config(
        db_path=tmp_dir / "test.db",
        blob_dir=tmp_dir / "blobs",
    )


@pytest.fixture
def db(config):
    database = Database(config.db_path)
    database.connect()
    yield database
    database.close()
