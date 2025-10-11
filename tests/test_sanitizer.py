# tests/test_sanitizer.py

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

from lithify.sanitizer import _rewrite_refs, build_filename_map, cleanup_temp_dir, safe_module_slug, sanitize_tree


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestNumberedSchemas:
    def test_numbered_schemas_generation(self, temp_dir):
        schemas_dir = temp_dir / "schemas"
        schemas_dir.mkdir()

        user_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.com/schemas/user.schema.json",
            "title": "User",
            "type": "object",
            "required": ["id", "name"],
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
            },
        }

        audit_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.com/schemas/audit.schema.json",
            "title": "AuditEvent",
            "type": "object",
            "required": ["event_id", "user"],
            "properties": {
                "event_id": {"type": "string"},
                "user": {"$ref": "./user.schema.json"},
            },
        }

        (schemas_dir / "01_user.yaml").write_text(yaml.dump(user_schema))
        (schemas_dir / "02_audit.yaml").write_text(yaml.dump(audit_schema))

        json_out = temp_dir / "json"
        models_out = temp_dir / "models"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "lithify.cli",
                "generate",
                "--schemas",
                str(schemas_dir),
                "--json-out",
                str(json_out),
                "--models-out",
                str(models_out),
                "--package-name",
                "test_numbered",
                "--mutability",
                "mutable",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0

        package_dir = models_out / "test_numbered"
        for py_file in package_dir.glob("*.py"):
            if py_file.name in ["__init__.py", "mutable_base.py", "frozen_base.py", "frozendict.py"]:
                continue
            assert not py_file.stem[0].isdigit(), f"Invalid module name: {py_file.name}"


class TestSanitizer:
    def test_safe_module_slug(self):
        assert safe_module_slug("01_user") == "user"
        assert safe_module_slug("123_test") == "test"
        assert safe_module_slug("class") == "class_mod"
        assert safe_module_slug("test-file") == "test_file"
        assert safe_module_slug("_already_safe") == "_already_safe"
        assert safe_module_slug("123") == "_123"
        assert safe_module_slug("for") == "for_mod"

    def test_safe_module_slug_edge_cases(self):
        result = safe_module_slug("")
        assert result.startswith("_") or result == "mod" or result == "_mod"

        result = safe_module_slug("@#$%")
        assert result
        assert not result[0].isdigit() if result else True

        assert safe_module_slug("if") == "if_mod"
        assert safe_module_slug("else") == "else_mod"
        assert safe_module_slug("while") == "while_mod"
        assert safe_module_slug("import") == "import_mod"

    def test_filename_mapping(self, temp_dir):
        (temp_dir / "01_user.json").touch()
        (temp_dir / "02_audit.json").touch()
        (temp_dir / "03_test.json").touch()
        (temp_dir / "04_class.json").touch()

        name_map = build_filename_map(temp_dir)

        assert name_map["01_user.json"] == "user.json"
        assert name_map["02_audit.json"] == "audit.json"
        assert name_map["03_test.json"] == "test.json"
        assert name_map["04_class.json"] == "class_mod.json"

    def test_filename_mapping_collisions(self, temp_dir):
        (temp_dir / "01_user.json").touch()
        (temp_dir / "02_user.json").touch()
        (temp_dir / "user.json").touch()

        name_map = build_filename_map(temp_dir)

        mapped_names = set(name_map.values())
        assert "user.json" in mapped_names
        assert len(mapped_names) == 3
        assert len(mapped_names) == len(name_map)

    def test_filename_mapping_init_file(self, temp_dir):
        (temp_dir / "__init__.json").touch()
        (temp_dir / "01___init__.json").touch()

        name_map = build_filename_map(temp_dir)

        for _original, mapped in name_map.items():
            assert mapped != "__init__.json"
            assert not mapped.startswith("__init__")

    def test_rewrite_refs(self, temp_dir):
        name_map = {"01_user.json": "user.json", "02_audit.json": "audit.json"}

        schema = {
            "properties": {
                "user": {"$ref": "./01_user.json"},
                "audit": {"$ref": "02_audit.json"},
                "fragment": {"$ref": "./01_user.json#/definitions/Name"},
                "internal": {"$ref": "#/definitions/Internal"},
                "nested": {"items": {"$ref": "./02_audit.json"}},
            }
        }

        rewritten = _rewrite_refs(schema, name_map)

        assert rewritten["properties"]["user"]["$ref"] == "./user.json"
        assert rewritten["properties"]["audit"]["$ref"] == "./audit.json"
        assert rewritten["properties"]["fragment"]["$ref"] == "./user.json#/definitions/Name"
        assert rewritten["properties"]["internal"]["$ref"] == "#/definitions/Internal"
        assert rewritten["properties"]["nested"]["items"]["$ref"] == "./audit.json"

    def test_sanitize_tree_creates_temp_dir(self, temp_dir):
        (temp_dir / "01_test.json").write_text(json.dumps({"type": "object"}))

        safe_dir, name_map = sanitize_tree(temp_dir)

        try:
            assert safe_dir.exists()
            assert safe_dir.is_dir()
            assert str(safe_dir).startswith(tempfile.gettempdir())
            assert "lithify_" in str(safe_dir)

            assert (safe_dir / "test.json").exists()
        finally:
            if safe_dir.exists():
                shutil.rmtree(safe_dir)

    def test_cleanup_temp_dir(self):
        temp_base = Path(tempfile.gettempdir())
        test_dir = temp_base / "lithify_test_cleanup"
        test_dir.mkdir(exist_ok=True)
        (test_dir / "test.json").touch()

        cleanup_temp_dir(test_dir)
        assert not test_dir.exists()

        cleanup_temp_dir(test_dir)

        home_dir = Path.home() / "lithify_should_not_delete"
        cleanup_temp_dir(home_dir)

    def test_sanitize_tree_nested_directories(self, temp_dir):
        subdir = temp_dir / "schemas" / "nested"
        subdir.mkdir(parents=True)

        (temp_dir / "01_root.json").write_text(json.dumps({"type": "object"}))
        (subdir / "02_nested.json").write_text(json.dumps({"type": "string"}))

        safe_dir, name_map = sanitize_tree(temp_dir)

        try:
            assert (safe_dir / "root.json").exists()
            assert (safe_dir / "schemas" / "nested" / "nested.json").exists()

            assert name_map["01_root.json"] == "root.json"
            assert name_map["02_nested.json"] == "nested.json"
        finally:
            if safe_dir.exists():
                shutil.rmtree(safe_dir)
