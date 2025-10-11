# tests/test_packaging.py

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_schemas(temp_dir):
    schemas_dir = temp_dir / "schemas"
    schemas_dir.mkdir()

    user_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.com/schemas/user.json",
        "title": "User",
        "type": "object",
        "required": ["id", "name"],
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "emails": {"type": "array", "items": {"type": "string", "format": "email"}},
            "metadata": {"type": "object", "additionalProperties": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}, "default": []},
        },
    }

    event_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.com/schemas/event.json",
        "title": "Event",
        "type": "object",
        "required": ["event_id", "user"],
        "properties": {
            "event_id": {"type": "string"},
            "user": {"$ref": "./user.json"},
            "timestamp": {"type": "string", "format": "date-time"},
        },
    }

    (schemas_dir / "user.json").write_text(json.dumps(user_schema, indent=2))
    (schemas_dir / "event.json").write_text(json.dumps(event_schema, indent=2))

    (schemas_dir / "user.yaml").write_text(yaml.dump(user_schema))
    (schemas_dir / "event.yaml").write_text(yaml.dump(event_schema))

    return schemas_dir


class TestJSONPointerEscapes:
    def test_pointer_escapes_in_defs(self, temp_dir):
        from lithify.slas_schema_index import resolve_pointer

        schema = {
            "$defs": {
                "Key~With~Tilde": {"type": "string", "minLength": 1},
                "Path/With/Slash": {"type": "string", "pattern": "^[a-z]+$"},
                "Both~/Special": {"type": "integer"},
            }
        }

        result = resolve_pointer(schema, "#/$defs/Key~0With~0Tilde")
        assert result["type"] == "string"
        assert result["minLength"] == 1

        result = resolve_pointer(schema, "#/$defs/Path~1With~1Slash")
        assert result["type"] == "string"
        assert "pattern" in result

        result = resolve_pointer(schema, "#/$defs/Both~0~1Special")
        assert result["type"] == "integer"

    def test_refs_to_escaped_defs(self, temp_dir):
        schemas_dir = temp_dir / "schemas"
        schemas_dir.mkdir()

        common = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.com/common.json",
            "$defs": {
                "Key~Tilde": {"type": "string", "minLength": 1},
                "Path/Slash": {"type": "string", "pattern": "^[a-z]+$"},
            },
        }

        event = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.com/event.json",
            "type": "object",
            "properties": {
                "field1": {"$ref": "./common.json#/$defs/Key~0Tilde"},
                "field2": {"$ref": "./common.json#/$defs/Path~1Slash"},
            },
        }

        (schemas_dir / "common.json").write_text(json.dumps(common, indent=2))
        (schemas_dir / "event.json").write_text(json.dumps(event, indent=2))

        from lithify.slas_schema_index import SchemaIndex

        index = SchemaIndex.load(list(schemas_dir.glob("*.json")), base_url="https://example.com/")

        event_uri = "https://example.com/event.json"
        event_node = index.node_for(event_uri)
        assert event_node is not None


class TestSLASIntegration:
    def test_slas_generates_aliases(self, temp_dir):
        from lithify.slas_alias_generator import emit_alias_modules
        from lithify.slas_schema_index import SchemaIndex

        schemas_dir = temp_dir / "schemas"
        schemas_dir.mkdir()

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.com/types.json",
            "title": "Types",
            "$defs": {
                "UUID": {"type": "string", "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"},
                "SemVer": {"type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$"},
                "NonEmpty": {"type": "string", "minLength": 1},
            },
        }

        (schemas_dir / "types.json").write_text(json.dumps(schema, indent=2))

        index = SchemaIndex.load([schemas_dir / "types.json"])

        output_dir = temp_dir / "generated"
        ref_map, modules = emit_alias_modules(index, output_dir, "test_pkg", "BaseModel")

        assert "types" in modules
        assert "UUID" in modules["types"]
        assert "SemVer" in modules["types"]
        assert "NonEmpty" in modules["types"]

        types_module = output_dir / "types.py"
        assert types_module.exists()

        content = types_module.read_text()
        assert "UUID = Annotated[str, StringConstraints(" in content
        assert "SemVer = Annotated[str, StringConstraints(" in content
        assert "NonEmpty = Annotated[str, StringConstraints(" in content


class TestInitFileGeneration:
    def test_generate_init_file(self, temp_dir):
        from lithify.packaging import generate_init_file

        pkg_dir = temp_dir / "test_pkg"
        pkg_dir.mkdir()

        (pkg_dir / "frozen_base.py").write_text("class FrozenModel: pass")
        (pkg_dir / "common_types.py").write_text("UUID = str")
        (pkg_dir / "user.py").write_text("class User: pass")
        (pkg_dir / "event.py").write_text("class Event: pass")
        (pkg_dir / "_internal.py").write_text("# Internal file")

        generate_init_file(pkg_dir)

        init_file = pkg_dir / "__init__.py"
        assert init_file.exists()

        content = init_file.read_text()

        assert '"""' in content
        assert "Generated Pydantic models" in content
        assert "from __future__ import annotations" in content

        assert "# Base classes" in content
        assert "from . import frozen_base" in content

        assert "# Type aliases" in content
        assert "from . import common_types" in content

        assert "# Models" in content
        assert "from . import event" in content
        assert "from . import user" in content

        assert "__all__ = [" in content
        assert '"frozen_base",' in content
        assert '"common_types",' in content
        assert '"event",' in content
        assert '"user",' in content

        assert "_internal" not in content

    def test_generate_init_file_empty_dir(self, temp_dir):
        from lithify.packaging import generate_init_file

        pkg_dir = temp_dir / "empty_pkg"
        pkg_dir.mkdir()

        generate_init_file(pkg_dir)

        init_file = pkg_dir / "__init__.py"
        assert not init_file.exists()


class TestMetadataFileSuppression:
    def test_no_metadata_files_generated(self, temp_dir, sample_schemas):
        from pathlib import Path

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd())

        cmd = [
            sys.executable,
            "-m",
            "lithify.cli",
            "generate",
            "--schemas",
            str(sample_schemas),
            "--json-out",
            str(temp_dir / "json"),
            "--models-out",
            str(temp_dir / "models"),
            "--package-name",
            "test_pkg",
            "--mutability",
            "mutable",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, env=env)

        if result.returncode != 0:
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
        assert result.returncode == 0

        pkg_dir = temp_dir / "models" / "test_pkg"
        assert pkg_dir.exists()

        assert not (pkg_dir / "manifest.json").exists(), "manifest.json should not be created"
        assert not (pkg_dir / "py.typed").exists(), "py.typed should not be created"
        assert not (pkg_dir / "_slas_ref_map.json").exists(), "_slas_ref_map.json should not be created"

        assert (pkg_dir / "__init__.py").exists(), "__init__.py should be created"
