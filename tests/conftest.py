import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"
DELTA_GML = FIXTURES / "od14do27marca2026.gml"


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("RCN_DATA_DIR", str(data))
    # Tests używają 'change-me' default password -- fail-fast w config.py
    # nie powinien rzucić podczas test setup. RCN_ALLOW_INSECURE_DEFAULT=1
    # aktywuje override.
    monkeypatch.setenv("RCN_ALLOW_INSECURE_DEFAULT", "1")
    yield data
    shutil.rmtree(data, ignore_errors=True)


@pytest.fixture
def client(tmp_data_dir):
    for mod in list(sys.modules):
        if mod == "app.config" or mod.startswith("app.") or mod == "app":
            del sys.modules[mod]
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture
def auth():
    return (os.environ.get("RCN_AUTH_USER", "admin"),
            os.environ.get("RCN_AUTH_PASSWORD", "change-me"))


@pytest.fixture
def delta_gml():
    if not DELTA_GML.exists():
        pytest.skip(f"Missing fixture {DELTA_GML}. Extract from Referencje/RCN Łódź/od14do27marca2026.zip")
    return DELTA_GML
