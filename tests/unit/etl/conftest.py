from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent.joinpath("fixture")


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR
