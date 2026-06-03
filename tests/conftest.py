import shutil
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path() -> Path:
    root = Path("test_tmp_runtime")
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path.resolve()
    finally:
        shutil.rmtree(path, ignore_errors=True)
