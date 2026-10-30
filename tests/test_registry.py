from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.registry import RegistryError, load_registry
from tests.github_mock import DEMO_ENTRY


def test_load_registry_good_file(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps([DEMO_ENTRY]), encoding="utf-8")
    registry = load_registry(path)
    assert len(registry.entries) == 1
    entry = registry.entries[0]
    assert entry.repo == "avniijain/demo-repo"
    assert entry.workflow == "security.yml"
    assert entry.container_app == "ca-cloudguard-demo"
    assert entry.resource_group == "rg-cloudguard-demo"
    assert entry.image_repository == "demo-repo"
    assert registry.contains("avniijain", "demo-repo")


def test_load_registry_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(RegistryError, match="malformed JSON"):
        load_registry(path)


def test_load_registry_not_a_list(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"repo": "avniijain/demo-repo"}), encoding="utf-8")
    with pytest.raises(RegistryError, match="list of entries"):
        load_registry(path)


def test_load_registry_invalid_repo(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    bad = dict(DEMO_ENTRY)
    bad["repo"] = "not a valid repo"
    path.write_text(json.dumps([bad]), encoding="utf-8")
    with pytest.raises(RegistryError, match="invalid repo"):
        load_registry(path)


def test_load_registry_missing_field(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    incomplete = dict(DEMO_ENTRY)
    incomplete.pop("workflow")
    path.write_text(json.dumps([incomplete]), encoding="utf-8")
    with pytest.raises(RegistryError, match="missing required field 'workflow'"):
        load_registry(path)
