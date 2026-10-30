from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REQUIRED_FIELDS = (
    "repo",
    "workflow",
    "container_app",
    "resource_group",
    "image_repository",
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_PATH = ROOT / "registry.json"


class RegistryError(ValueError):
    """Raised when registry.json is missing or malformed."""


@dataclass(frozen=True)
class RegistryEntry:
    repo: str
    workflow: str
    container_app: str
    resource_group: str
    image_repository: str
    rollback_workflow: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class Registry:
    entries: tuple[RegistryEntry, ...]

    def find(self, owner: str, name: str) -> RegistryEntry | None:
        key = f"{owner}/{name}"
        for entry in self.entries:
            if entry.repo == key:
                return entry
        return None

    def contains(self, owner: str, name: str) -> bool:
        return self.find(owner, name) is not None


def load_registry(path: Path | None = None) -> Registry:
    registry_path = path or DEFAULT_REGISTRY_PATH
    if not registry_path.is_file():
        raise RegistryError(f"registry file not found: {registry_path}")

    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError(
            f"registry file is malformed JSON ({registry_path}): {exc}"
        ) from exc

    if not isinstance(raw, list):
        raise RegistryError(
            f"registry file must contain a list of entries ({registry_path})"
        )

    entries: list[RegistryEntry] = []
    for index, item in enumerate(raw):
        entries.append(_parse_entry(item, index, registry_path))
    return Registry(entries=tuple(entries))


def _parse_entry(item: Any, index: int, registry_path: Path) -> RegistryEntry:
    prefix = f"registry entry {index}"
    if not isinstance(item, dict):
        raise RegistryError(f"{prefix} is not an object ({registry_path})")

    values: dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        if field not in item:
            raise RegistryError(
                f"{prefix} is missing required field '{field}' ({registry_path})"
            )
        value = item[field]
        if not isinstance(value, str):
            raise RegistryError(
                f"{prefix} field '{field}' must be a string ({registry_path})"
            )
        values[field] = value

    if not REPO_PATTERN.fullmatch(values["repo"]):
        raise RegistryError(
            f"{prefix} has invalid repo '{values['repo']}': "
            f"must match {REPO_PATTERN.pattern} ({registry_path})"
        )
    if not values["workflow"].strip():
        raise RegistryError(f"{prefix} has empty workflow ({registry_path})")

    rollback_workflow = item.get("rollback_workflow")
    if rollback_workflow is not None and not isinstance(rollback_workflow, str):
        raise RegistryError(
            f"{prefix} field 'rollback_workflow' must be a string ({registry_path})"
        )
    if rollback_workflow is not None and not rollback_workflow.strip():
        rollback_workflow = None
    return RegistryEntry(**values, rollback_workflow=rollback_workflow)
