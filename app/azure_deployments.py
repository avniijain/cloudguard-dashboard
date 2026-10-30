from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


COMMIT_PATTERN = re.compile(r"^.+--g([0-9a-f]{7})-\d+-\d+$")
IMAGE_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-fA-F]{64}")


class DeploymentServiceError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def create_azure_client(subscription_id: str):
    if not subscription_id:
        raise DeploymentServiceError(
            503, "Azure is not configured: AZURE_SUBSCRIPTION_ID is missing."
        )
    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.appcontainers import ContainerAppsAPIClient

        credential = DefaultAzureCredential()
        return ContainerAppsAPIClient(
            credential=credential, subscription_id=subscription_id
        ), credential
    except ImportError as exc:
        raise DeploymentServiceError(
            503, "Azure deployment dependencies are unavailable."
        ) from exc
    except Exception as exc:
        raise _azure_error(exc) from exc


def list_revisions_sync(client: Any, resource_group: str, container_app: str) -> list[dict[str, Any]]:
    try:
        revisions = client.container_apps_revisions.list_revisions(
            resource_group_name=resource_group,
            container_app_name=container_app,
        )
        result = [_revision_payload(revision) for revision in revisions]
    except Exception as exc:
        raise _azure_error(exc) from exc
    result.sort(key=lambda revision: _created_timestamp(revision["created_time"]), reverse=True)
    for revision in result:
        revision["live"] = revision["traffic_weight"] == 100
    return result


def _created_timestamp(value: str | None) -> float:
    if not value:
        return float("-inf")
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return created.timestamp()
    except (ValueError, OverflowError):
        return float("-inf")


def _revision_payload(revision: Any) -> dict[str, Any]:
    name = _read(revision, "name", str)
    properties = _read(revision, "properties")
    source = properties if properties is not None else revision
    created = _read(source, "created_time")
    if isinstance(created, datetime):
        created = created.isoformat()
    elif not isinstance(created, str):
        created = None
    active = _read(source, "active", bool)
    traffic = _read(source, "traffic_weight", int)
    health = _read(source, "health_state", str)
    running = _read(source, "running_state", str)
    template = _read(source, "template")
    containers = _read(template, "containers")
    first = containers[0] if isinstance(containers, list) and containers else None
    image = _read(first, "image", str)
    digest_match = IMAGE_DIGEST_PATTERN.search(image) if image else None
    commit_match = COMMIT_PATTERN.match(name) if name else None
    return {
        "name": name,
        "created_time": created,
        "active": active,
        "traffic_weight": traffic,
        "health_state": health,
        "running_state": running,
        "image": image,
        "image_digest": digest_match.group(0) if digest_match else None,
        "commit_sha": commit_match.group(1) if commit_match else None,
        "live": False,
    }


def _read(obj: Any, field: str, expected: type | None = None) -> Any:
    if obj is None:
        return None
    value = obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)
    if expected is not None and not isinstance(value, expected):
        return None
    return value


def _azure_error(exc: Exception) -> DeploymentServiceError:
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    error = getattr(exc, "error", None)
    code = getattr(error, "code", None)
    code = code or getattr(exc, "code", None)
    if name in {"ClientAuthenticationError", "CredentialUnavailableError"}:
        return DeploymentServiceError(
            503, "Azure credentials are missing or unavailable."
        )
    if status == 401:
        return DeploymentServiceError(503, "Azure authentication failed.")
    if status == 403 or code in {"AuthorizationFailed", "Forbidden"}:
        return DeploymentServiceError(
            403, "Azure denied permission to read Container Apps revisions."
        )
    return DeploymentServiceError(502, "Unable to read Azure deployment data.")
