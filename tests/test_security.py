from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient
from httpx import MockTransport

from tests.conftest import SECRET_TOKEN, build_app
from tests.github_mock import GitHubMock


def test_token_never_appears_in_response_or_logs(
    registry_file, secret_token, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    mock = GitHubMock()
    app = build_app(registry_file, MockTransport(mock), token=secret_token)
    with TestClient(app) as client:
        responses = [
            client.get("/api/health"),
            client.get("/api/repos"),
            client.get("/api/repos/avniijain/demo-repo/runs"),
            client.get("/api/repos/not-in/registry/runs"),
        ]
    for response in responses:
        text = response.text
        assert SECRET_TOKEN not in text
        assert secret_token not in text
        try:
            dumped = json.dumps(response.json())
        except Exception:
            dumped = text
        assert SECRET_TOKEN not in dumped
    for record in caplog.records:
        message = record.getMessage()
        assert SECRET_TOKEN not in message
        assert secret_token not in message
        assert SECRET_TOKEN not in logging.getLevelName(record.levelno)
        extras = getattr(record, "__dict__", {})
        blob = json.dumps(extras, default=str)
        assert SECRET_TOKEN not in blob
    assert mock.auth_on_redirect() is False
