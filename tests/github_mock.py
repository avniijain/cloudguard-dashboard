from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import httpx

from app.github_client import ARTIFACT_NAME, DECISION_MEMBER

DEMO_ENTRY = {
    "repo": "avniijain/demo-repo",
    "workflow": "security.yml",
    "container_app": "ca-cloudguard-demo",
    "resource_group": "rg-cloudguard-demo",
    "image_repository": "demo-repo",
    "rollback_workflow": "rollback.yml",
}

RUN_ID = 101
ARTIFACT_ID = 501
HEAD_SHA = "abcdef1234567890deadbeef"
ZIP_REDIRECT = "https://objects.test.local/cloudguard-scan-reports.zip"

SAMPLE_DECISION = {
    "decision": "allow",
    "risk_score": 12,
    "counts": {
        "by_severity": {"critical": 0, "high": 0, "medium": 1, "low": 2}
    },
    "reasons": ["no critical findings"],
    "generated_at": "2026-10-29T12:00:00Z",
    "findings": [{"id": 1}, {"id": 2}, {"id": 3}],
}


def make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def decision_zip(payload: dict[str, Any] | None = None) -> bytes:
    body = json.dumps(payload or SAMPLE_DECISION).encode("utf-8")
    return make_zip({DECISION_MEMBER: body})


class GitHubMock:
    def __init__(
        self,
        artifacts: list[dict[str, Any]] | None = None,
        zip_bytes: bytes | None = None,
        zip_status: int = 302,
    ) -> None:
        self.artifacts = (
            artifacts
            if artifacts is not None
            else [
                {
                    "id": ARTIFACT_ID,
                    "name": ARTIFACT_NAME,
                    "expired": False,
                    "size_in_bytes": 128,
                }
            ]
        )
        self.zip_bytes = zip_bytes if zip_bytes is not None else decision_zip()
        self.zip_status = zip_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        host = request.url.host

        if host == "objects.test.local":
            return httpx.Response(200, content=self.zip_bytes)

        if "/actions/workflows/" in path and path.endswith("/runs"):
            workflow = path.split("/actions/workflows/", 1)[1].rsplit("/runs", 1)[0]
            if workflow == "rollback.yml":
                return httpx.Response(200, json={"total_count": 1, "workflow_runs": [
                    {"id": 202, "html_url": "https://github.com/avniijain/demo-repo/actions/runs/202",
                     "status": "completed", "conclusion": "success", "actor": {"login": "demo"},
                     "created_at": "2026-10-30T10:00:00Z"}
                ]})
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "workflow_runs": [
                        {
                            "id": RUN_ID,
                            "html_url": (
                                "https://github.com/avniijain/demo-repo"
                                "/actions/runs/101"
                            ),
                            "event": "push",
                            "head_branch": "main",
                            "head_sha": HEAD_SHA,
                            "status": "completed",
                            "conclusion": "success",
                            "created_at": "2026-10-29T11:00:00Z",
                        }
                    ],
                },
            )

        if path.endswith(f"/actions/runs/{RUN_ID}/artifacts"):
            return httpx.Response(
                200,
                json={"total_count": len(self.artifacts), "artifacts": self.artifacts},
            )

        if path.endswith(f"/actions/artifacts/{ARTIFACT_ID}/zip"):
            if self.zip_status in {301, 302, 303, 307, 308}:
                return httpx.Response(
                    self.zip_status, headers={"Location": ZIP_REDIRECT}
                )
            return httpx.Response(self.zip_status, content=self.zip_bytes)

        return httpx.Response(404, json={"message": "Not Found"})

    def auth_on_redirect(self) -> bool:
        for request in self.requests:
            if request.url.host == "objects.test.local":
                if "authorization" in request.headers:
                    return True
        return False
