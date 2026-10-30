from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time
import zipfile
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger("cloudguard")

GITHUB_API = "https://api.github.com"
API_VERSION = "2022-11-28"
ARTIFACT_NAME = "cloudguard-scan-reports"
DECISION_MEMBER = "risk-decision.json"
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
MAX_DECISION_BYTES = 5 * 1024 * 1024
CACHE_TTL_SECONDS = 60.0
ARTIFACT_CONCURRENCY = 5
REQUEST_TIMEOUT = 10.0


class GitHubAPIError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _TTLCache:
    def __init__(self, ttl: float = CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> tuple[bool, Any]:
        item = self._store.get(key)
        if item is None:
            return False, None
        stamped, value = item
        if time.monotonic() - stamped > self._ttl:
            self._store.pop(key, None)
            return False, None
        return True, value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic(), value)


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if token is None:
            token = os.environ.get("GITHUB_TOKEN", "")
        self._token = token
        timeout = httpx.Timeout(REQUEST_TIMEOUT)
        common = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "cloudguard-dashboard",
        }
        auth_headers = dict(common)
        if self._token:
            auth_headers["Authorization"] = f"Bearer {self._token}"
        self._client = httpx.AsyncClient(
            headers=auth_headers,
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )
        self._anon_client = httpx.AsyncClient(
            headers=common,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._cache = _TTLCache()
        self._artifact_sema = asyncio.Semaphore(ARTIFACT_CONCURRENCY)

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._anon_client.aclose()

    async def list_workflow_runs(
        self, repo: str, workflow: str, limit: int
    ) -> list[dict[str, Any]]:
        encoded_workflow = quote(workflow, safe=".")
        url = (
            f"{GITHUB_API}/repos/{repo}/actions/workflows/"
            f"{encoded_workflow}/runs"
        )
        data = await self._get_json(url, params={"per_page": str(limit)})
        runs = data.get("workflow_runs") if isinstance(data, dict) else None
        if not isinstance(runs, list):
            return []
        return [run for run in runs if isinstance(run, dict)]

    async def enrich_runs(
        self, repo: str, runs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        async def one(run: dict[str, Any]) -> dict[str, Any]:
            async with self._artifact_sema:
                return await self._run_payload(repo, run)

        return list(await asyncio.gather(*[one(run) for run in runs]))

    async def _run_payload(self, repo: str, run: dict[str, Any]) -> dict[str, Any]:
        run_id = run.get("id")
        sha = str(run.get("head_sha") or "")
        payload: dict[str, Any] = {
            "run_id": run_id,
            "html_url": run.get("html_url"),
            "event": run.get("event"),
            "branch": run.get("head_branch"),
            "head_sha": sha[:7],
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "created_at": run.get("created_at"),
            "scan": None,
        }
        try:
            scan, error = await self._scan_for_run(repo, run_id)
        except Exception:
            logger.exception("Failed to load scan for run %s of %s", run_id, repo)
            payload["scan_error"] = "artifact unreadable"
            return payload
        if error:
            payload["scan_error"] = error
        payload["scan"] = scan
        return payload

    async def _scan_for_run(
        self, repo: str, run_id: Any
    ) -> tuple[dict[str, Any] | None, str | None]:
        if run_id is None:
            return None, "artifact not found"
        url = f"{GITHUB_API}/repos/{repo}/actions/runs/{run_id}/artifacts"
        try:
            data = await self._get_json(url)
        except GitHubAPIError as exc:
            return None, _scan_error_from_github(exc)
        artifacts = data.get("artifacts") if isinstance(data, dict) else None
        if not isinstance(artifacts, list):
            return None, "artifact not found"
        match = None
        for artifact in artifacts:
            if isinstance(artifact, dict) and artifact.get("name") == ARTIFACT_NAME:
                match = artifact
                break
        if match is None:
            return None, "artifact not found"
        if match.get("expired") is True:
            return None, "artifact expired"
        size = match.get("size_in_bytes")
        if isinstance(size, int) and size > MAX_ARTIFACT_BYTES:
            return None, "artifact too large"
        artifact_id = match.get("id")
        if artifact_id is None:
            return None, "artifact not found"
        try:
            zip_bytes = await self._download_artifact_zip(repo, artifact_id)
        except GitHubAPIError as exc:
            return None, _scan_error_from_github(exc)
        except ValueError as exc:
            return None, str(exc)
        return _parse_scan_zip(zip_bytes)

    async def _download_artifact_zip(self, repo: str, artifact_id: Any) -> bytes:
        url = f"{GITHUB_API}/repos/{repo}/actions/artifacts/{artifact_id}/zip"
        hit, cached = self._cache.get(url)
        if hit:
            return cached
        response = await self._client.get(url)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            if not location:
                raise GitHubAPIError(502, "GitHub artifact redirect was missing")
            # Follow the redirect without the GitHub Authorization header.
            response = await self._anon_client.get(location)
        _raise_for_github(response, url)
        content_length = response.headers.get("Content-Length")
        if content_length and content_length.isdigit():
            if int(content_length) > MAX_ARTIFACT_BYTES:
                raise ValueError("artifact too large")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_ARTIFACT_BYTES:
                raise ValueError("artifact too large")
        data = bytes(body)
        self._cache.set(url, data)
        return data

    async def _get_json(
        self, url: str, params: dict[str, str] | None = None
    ) -> Any:
        cache_key = url if not params else f"{url}?{httpx.QueryParams(params)}"
        hit, cached = self._cache.get(cache_key)
        if hit:
            return cached
        response = await self._client.get(url, params=params)
        _raise_for_github(response, url)
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise GitHubAPIError(502, "GitHub returned invalid JSON") from exc
        self._cache.set(cache_key, data)
        return data


def _raise_for_github(response: httpx.Response, url: str) -> None:
    status = response.status_code
    if 200 <= status < 300:
        return
    logger.warning("GitHub HTTP %s for %s", status, url)
    if status in {401, 403, 429} or _is_rate_limited(response):
        if _is_rate_limited(response):
            retry = _retry_after(response)
            raise GitHubAPIError(
                502, f"GitHub rate limit reached; retry after {retry}"
            )
        if status in {401, 403}:
            raise GitHubAPIError(502, "GitHub rejected the token")
        raise GitHubAPIError(
            502, f"GitHub rate limit reached; retry after {_retry_after(response)}"
        )
    if status == 404:
        raise GitHubAPIError(502, "GitHub resource not found")
    raise GitHubAPIError(502, f"GitHub request failed with HTTP {status}")


def _is_rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining == "0":
        return True
    snippet = (response.text or "").lower()
    return "rate limit" in snippet


def _retry_after(response: httpx.Response) -> str:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        return retry_after
    reset = response.headers.get("X-RateLimit-Reset")
    if reset:
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(reset)))
        except ValueError:
            return reset
    return "unknown"


def _scan_error_from_github(exc: GitHubAPIError) -> str:
    if "not found" in exc.message.lower():
        return "artifact not found"
    if "rate limit" in exc.message.lower():
        return "github rate limit"
    if "rejected the token" in exc.message.lower():
        return "github rejected the token"
    return "artifact unreadable"


def _parse_scan_zip(data: bytes) -> tuple[dict[str, Any] | None, str | None]:
    if len(data) > MAX_ARTIFACT_BYTES:
        return None, "artifact too large"
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None, "artifact unreadable"
    try:
        info = archive.getinfo(DECISION_MEMBER)
    except KeyError:
        return None, f"{DECISION_MEMBER} missing"
    if info.file_size > MAX_DECISION_BYTES:
        return None, f"{DECISION_MEMBER} too large"
    try:
        with archive.open(DECISION_MEMBER) as handle:
            raw = handle.read(MAX_DECISION_BYTES + 1)
    except Exception:
        return None, f"{DECISION_MEMBER} unreadable"
    if len(raw) > MAX_DECISION_BYTES:
        return None, f"{DECISION_MEMBER} too large"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, f"{DECISION_MEMBER} unreadable"
    if not isinstance(payload, dict):
        return None, f"{DECISION_MEMBER} unreadable"
    return _extract_scan(payload), None


def _extract_scan(payload: dict[str, Any]) -> dict[str, Any]:
    counts = payload.get("counts")
    by_severity = None
    if isinstance(counts, dict):
        raw_severity = counts.get("by_severity")
        if isinstance(raw_severity, dict):
            by_severity = {}
            for severity in ("critical", "high", "medium", "low"):
                matching = next(
                    (value for key, value in raw_severity.items()
                     if isinstance(key, str) and key.casefold() == severity),
                    0,
                )
                by_severity[severity] = matching
        else:
            by_severity = raw_severity
    findings = payload.get("findings")
    findings_list = findings if isinstance(findings, list) else []
    findings_count = len(findings_list) if isinstance(findings, list) else (
        findings if isinstance(findings, int) and not isinstance(findings, bool)
        else payload.get("findings_count", 0)
    )
    if not isinstance(findings_count, int) or isinstance(findings_count, bool):
        findings_count = 0
    sanitized_findings = []
    for finding in findings_list:
        if isinstance(finding, dict):
            sanitized_findings.append(_sanitize_finding(finding))
    sanitized_findings.sort(
        key=lambda item: item["risk_score"]
        if isinstance(item["risk_score"], (int, float))
        and not isinstance(item["risk_score"], bool) else float("-inf"),
        reverse=True,
    )
    sonar = payload.get("sonar")
    sonar_status = sonar.get("status") if isinstance(sonar, dict) else None
    if not isinstance(sonar_status, str):
        sonar_status = None
    degraded_data = payload.get("degraded_data")
    degraded = isinstance(degraded_data, dict) and degraded_data.get("degraded") is True
    raw_reasons = None
    if isinstance(degraded_data, dict):
        raw_reasons = degraded_data.get(
            "degraded_reasons", degraded_data.get("reasons")
        )
    if raw_reasons is None:
        raw_reasons = payload.get("degraded_reasons")
    degraded_reasons = _sanitize_string_list(raw_reasons, 5)
    return {
        "decision": payload.get("decision"),
        "risk_score": payload.get("risk_score"),
        "counts": {"by_severity": by_severity},
        "reasons": payload.get("reasons"),
        "generated_at": payload.get("generated_at"),
        "findings": sanitized_findings[:20],
        "findings_total": findings_count,
        "sonar_status": sonar_status,
        "degraded": degraded,
        "degraded_reasons": degraded_reasons,
    }


_FINDING_FIELDS = (
    "type", "severity", "rule_id", "cve", "package", "installed_version",
    "fixed_version", "targets", "risk_score", "risk_factors",
)


def _sanitize_string(value: Any) -> str | None:
    return value[:200] if isinstance(value, str) else None


def _sanitize_string_list(value: Any, limit: int) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [item[:200] for item in value[:limit] if isinstance(item, str)]


def _sanitize_finding(finding: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in _FINDING_FIELDS:
        value = finding.get(field)
        if field in {"targets", "risk_factors"}:
            result[field] = _sanitize_string_list(value, 5)
        elif field == "risk_score":
            result[field] = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        else:
            result[field] = _sanitize_string(value)
    return result
