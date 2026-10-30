from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.github_client import GitHubClient
from app.main import create_app
from tests.github_mock import DEMO_ENTRY

SECRET_TOKEN = "ghs_test_token_never_leak_12345"


@pytest.fixture
def registry_file(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps([DEMO_ENTRY]), encoding="utf-8")
    return path


@pytest.fixture
def secret_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("GITHUB_TOKEN", SECRET_TOKEN)
    return SECRET_TOKEN


def build_app(registry_path: Path, transport, token: str = SECRET_TOKEN, azure_client=None):
    client = GitHubClient(token=token, transport=transport)
    return create_app(registry_path=registry_path, github_client=client, azure_client=azure_client)
