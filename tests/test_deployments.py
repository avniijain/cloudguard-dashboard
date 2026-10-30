from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from httpx import MockTransport

from app.azure_deployments import list_revisions_sync
from tests.conftest import build_app
from tests.github_mock import GitHubMock


class FakeAzureClient:
    def __init__(self, revisions=None, error=None):
        self.container_apps_revisions = self
        self.revisions = revisions or []
        self.error = error

    def list_revisions(self, resource_group_name, container_app_name):
        if self.error:
            raise self.error
        return self.revisions


class FakeAzureAuthorizationError(Exception):
    status_code = 403
    error = SimpleNamespace(code="AuthorizationFailed")


def revision(name, created, weight, image="registry.test/app@sha256:" + "a" * 64):
    return SimpleNamespace(
        name=name,
        created_time=datetime.fromisoformat(created).replace(tzinfo=timezone.utc),
        active=True,
        traffic_weight=weight,
        health_state="Healthy",
        running_state="Running",
        template=SimpleNamespace(containers=[SimpleNamespace(image=image)]),
    )


def test_revision_parsing_sorting_live_and_commit_regex() -> None:
    older = revision("demo--gabcdef0-100-1", "2026-10-29T10:00:00", 100)
    newer = revision("bad--gABCDEF0-101-2", "2026-10-30T10:00:00", 0)
    no_name = revision(None, "2026-10-28T10:00:00", 0, image="registry.test/app:latest")
    output = list_revisions_sync(FakeAzureClient([older, newer, no_name]), "rg", "app")
    assert [item["name"] for item in output] == [newer.name, older.name, None]
    assert output[0]["commit_sha"] is None
    assert output[1]["commit_sha"] == "abcdef0"
    assert output[2]["commit_sha"] is None
    assert [item["live"] for item in output] == [False, True, False]
    assert output[1]["image_digest"] == "sha256:" + "a" * 64
    assert output[2]["image_digest"] is None


def test_unknown_repo_deployments_returns_404(registry_file, secret_token, monkeypatch) -> None:
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "subscription")
    app = build_app(registry_file, MockTransport(GitHubMock()), azure_client=FakeAzureClient())
    with TestClient(app) as client:
        response = client.get("/api/repos/someone/else/deployments")
    assert response.status_code == 404


def test_missing_subscription_returns_503(registry_file, secret_token, monkeypatch) -> None:
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    app = build_app(registry_file, MockTransport(GitHubMock()), azure_client=FakeAzureClient())
    with TestClient(app) as client:
        response = client.get("/api/repos/avniijain/demo-repo/deployments")
    assert response.status_code == 503
    assert "AZURE_SUBSCRIPTION_ID" in response.json()["detail"]


def test_azure_authorization_error_has_clear_json_message(registry_file, secret_token, monkeypatch) -> None:
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "subscription")
    error = FakeAzureAuthorizationError("forbidden")
    app = build_app(
        registry_file, MockTransport(GitHubMock()),
        azure_client=FakeAzureClient(error=error),
    )
    with TestClient(app) as client:
        response = client.get("/api/repos/avniijain/demo-repo/deployments")
    assert response.status_code == 403
    assert response.json() == {
        "detail": "Azure denied permission to read Container Apps revisions."
    }
