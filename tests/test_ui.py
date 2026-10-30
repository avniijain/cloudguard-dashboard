from __future__ import annotations

from fastapi.testclient import TestClient
from httpx import MockTransport

from app.main import SECURITY_HEADERS
from tests.conftest import ROOT, build_app
from tests.github_mock import GitHubMock


def _assert_security_headers(response) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value


def test_index_returns_html(registry_file, secret_token) -> None:
    mock = GitHubMock()
    app = build_app(registry_file, MockTransport(mock), token=secret_token)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "text/html" in content_type
    body = response.text.lower()
    assert "<html" in body
    assert "cloudguard dashboard" in body
    _assert_security_headers(response)


def test_security_headers_on_health(registry_file, secret_token) -> None:
    mock = GitHubMock()
    app = build_app(registry_file, MockTransport(mock), token=secret_token)
    with TestClient(app) as client:
        index = client.get("/")
        health = client.get("/api/health")
    assert index.status_code == 200
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    _assert_security_headers(index)
    _assert_security_headers(health)


def test_app_js_does_not_contain_innerHTML() -> None:
    source = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in source
