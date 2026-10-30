from __future__ import annotations

import logging
import os
import re
import time
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.github_client import GitHubAPIError, GitHubClient
from app.azure_deployments import (
    DeploymentServiceError,
    create_azure_client,
    list_revisions_sync,
)
from app.registry import REPO_PATTERN, Registry, RegistryError, load_registry

logger = logging.getLogger("cloudguard")
logging.basicConfig(level=logging.INFO)

OWNER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
STATIC_DIR = Path(__file__).resolve().parent / "static"
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def create_app(
    registry_path: Path | None = None,
    github_client: GitHubClient | None = None,
    github_transport=None,
    azure_client=None,
) -> FastAPI:
    try:
        registry = load_registry(registry_path)
    except RegistryError as exc:
        raise SystemExit(f"Failed to load registry: {exc}") from exc

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = github_client or GitHubClient(transport=github_transport)
        app.state.github = client
        app.state.azure_client = azure_client
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(title="CloudGuard Dashboard", lifespan=lifespan)
    app.state.registry = registry
    app.state.azure_client = azure_client
    app.state.azure_credential = None
    app.state.deployment_cache = {}

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    @app.exception_handler(GitHubAPIError)
    async def github_error_handler(_, exc: GitHubAPIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.message}
        )

    @app.exception_handler(DeploymentServiceError)
    async def deployment_error_handler(_, exc: DeploymentServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.message}
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/repos")
    async def list_repos() -> list[dict[str, str | None]]:
        current: Registry = app.state.registry
        return [entry.as_dict() for entry in current.entries]

    @app.get("/api/repos/{owner}/{name}/runs")
    async def list_runs(
        owner: str,
        name: str,
        limit: int = Query(default=10, ge=1, le=30),
    ) -> list[dict]:
        _validate_repo_path(owner, name)
        entry = app.state.registry.find(owner, name)
        if entry is None:
            raise HTTPException(status_code=404, detail="Repository not in registry")
        client: GitHubClient = app.state.github
        runs = await client.list_workflow_runs(entry.repo, entry.workflow, limit)
        return await client.enrich_runs(entry.repo, runs)

    @app.get("/api/repos/{owner}/{name}/deployments")
    async def list_deployments(owner: str, name: str) -> dict:
        _validate_repo_path(owner, name)
        entry = app.state.registry.find(owner, name)
        if entry is None:
            raise HTTPException(status_code=404, detail="Repository not in registry")
        subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip()
        if not subscription_id:
            raise DeploymentServiceError(
                503, "Azure is not configured: AZURE_SUBSCRIPTION_ID is missing."
            )
        cache = app.state.deployment_cache
        cache_key = f"{subscription_id}:{entry.repo}"
        cached = cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
        client = app.state.azure_client
        if client is None:
            client, credential = create_azure_client(subscription_id)
            app.state.azure_client = client
            app.state.azure_credential = credential
        revisions = await asyncio.to_thread(
            list_revisions_sync, client, entry.resource_group, entry.container_app
        )
        rollbacks = []
        if entry.rollback_workflow:
            github: GitHubClient = app.state.github
            runs = await github.list_workflow_runs(
                entry.repo, entry.rollback_workflow, 10
            )
            for run in runs[:10]:
                actor = run.get("actor")
                rollbacks.append({
                    "run_id": run.get("id"),
                    "html_url": run.get("html_url"),
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "actor": actor.get("login") if isinstance(actor, dict) else None,
                    "created_at": run.get("created_at"),
                })
        response = {"revisions": revisions, "rollbacks": rollbacks}
        cache[cache_key] = (time.monotonic(), response)
        return response

    app.mount(
        "/",
        StaticFiles(directory=STATIC_DIR, html=True),
        name="ui",
    )
    return app


def _validate_repo_path(owner: str, name: str) -> None:
    if not OWNER_NAME_PATTERN.fullmatch(owner) or not OWNER_NAME_PATTERN.fullmatch(
        name
    ):
        raise HTTPException(status_code=404, detail="Repository not in registry")
    if not REPO_PATTERN.fullmatch(f"{owner}/{name}"):
        raise HTTPException(status_code=404, detail="Repository not in registry")


app = create_app()
