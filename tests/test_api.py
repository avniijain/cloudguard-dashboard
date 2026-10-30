from __future__ import annotations

from fastapi.testclient import TestClient
from httpx import MockTransport

from app.github_client import DECISION_MEMBER
from app.github_client import _extract_scan
from tests.conftest import build_app
from tests.github_mock import DEMO_ENTRY
from tests.github_mock import GitHubMock, SAMPLE_DECISION, make_zip


def test_unknown_repo_returns_404(registry_file, secret_token) -> None:
    mock = GitHubMock()
    app = build_app(registry_file, MockTransport(mock), token=secret_token)
    with TestClient(app) as client:
        response = client.get("/api/repos/someone/else/runs")
    assert response.status_code == 404
    assert "not in registry" in response.json()["detail"].lower()


def test_run_with_valid_artifact_parses_scan(registry_file, secret_token) -> None:
    mock = GitHubMock()
    app = build_app(registry_file, MockTransport(mock), token=secret_token)
    with TestClient(app) as client:
        health = client.get("/api/health")
        repos = client.get("/api/repos")
        response = client.get("/api/repos/avniijain/demo-repo/runs?limit=10")
    assert health.json() == {"status": "ok"}
    assert repos.json() == [DEMO_ENTRY]
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    run = body[0]
    assert run["run_id"] == 101
    assert run["event"] == "push"
    assert run["branch"] == "main"
    assert run["head_sha"] == "abcdef1"
    assert run["status"] == "completed"
    assert run["conclusion"] == "success"
    scan = run["scan"]
    assert scan["decision"] == SAMPLE_DECISION["decision"]
    assert scan["risk_score"] == SAMPLE_DECISION["risk_score"]
    assert scan["counts"]["by_severity"] == SAMPLE_DECISION["counts"]["by_severity"]
    assert scan["reasons"] == SAMPLE_DECISION["reasons"]
    assert scan["generated_at"] == SAMPLE_DECISION["generated_at"]
    assert scan["findings_total"] == 3
    assert len(scan["findings"]) == 3
    assert scan["sonar_status"] is None
    assert scan["degraded"] is False
    assert "scan_error" not in run
    assert mock.auth_on_redirect() is False


def test_run_with_no_artifact_scan_null(registry_file, secret_token) -> None:
    mock = GitHubMock(artifacts=[])
    app = build_app(registry_file, MockTransport(mock), token=secret_token)
    with TestClient(app) as client:
        response = client.get("/api/repos/avniijain/demo-repo/runs")
    assert response.status_code == 200
    run = response.json()[0]
    assert run["scan"] is None
    assert run["scan_error"] == "artifact not found"


def test_zip_missing_risk_decision_scan_null(registry_file, secret_token) -> None:
    mock = GitHubMock(zip_bytes=make_zip({"notes.txt": b"hello"}))
    app = build_app(registry_file, MockTransport(mock), token=secret_token)
    with TestClient(app) as client:
        response = client.get("/api/repos/avniijain/demo-repo/runs")
    assert response.status_code == 200
    run = response.json()[0]
    assert run["scan"] is None
    assert run["scan_error"] == f"{DECISION_MEMBER} missing"


def test_findings_are_sorted_capped_and_sanitized() -> None:
    rows = [
        {"risk_score": score, "type": "x" * 250, "unexpected": "drop", "targets": ["t" * 250] * 7,
         "risk_factors": ["f" * 250] * 7}
        for score in range(25)
    ]
    scan = _extract_scan({"findings": rows})
    assert scan["findings_total"] == 25
    assert len(scan["findings"]) == 20
    assert [item["risk_score"] for item in scan["findings"]] == list(range(24, 4, -1))
    finding = scan["findings"][0]
    assert set(finding) == {
        "type", "severity", "rule_id", "cve", "package", "installed_version",
        "fixed_version", "targets", "risk_score", "risk_factors",
    }
    assert len(finding["type"]) == 200
    assert len(finding["targets"]) == len(finding["risk_factors"]) == 5
    assert len(finding["targets"][0]) == len(finding["risk_factors"][0]) == 200


def test_malformed_findings_do_not_break_scan_and_fields_default_to_null() -> None:
    scan = _extract_scan({"findings": [None, "bad", {"risk_score": "high", "targets": "bad"}]})
    assert scan["findings_total"] == 3
    assert len(scan["findings"]) == 1
    finding = scan["findings"][0]
    assert finding["risk_score"] is None
    assert finding["targets"] is None
    assert finding["type"] is None
    assert scan["sonar_status"] is None


def test_uppercase_by_severity_counts_include_misconfiguration_findings() -> None:
    scan = _extract_scan({
        "decision": "WARN",
        "counts": {
            "by_severity": {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 0, "LOW": 0},
            "by_type": {"vulnerability": 0},
        },
        "findings": [{
            "rule_id": "DS-0029",
            "severity": "HIGH",
            "type": "misconfiguration",
        }],
    })
    assert scan["counts"]["by_severity"] == {
        "critical": 0, "high": 1, "medium": 0, "low": 0,
    }
