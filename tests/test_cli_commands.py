# tests/test_cli_commands.py

import json
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


class TestCLI:
    def test_info_command(self):
        result = subprocess.run([sys.executable, "-m", "lithify.cli", "info"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "Lithify Mutability Modes" in result.stdout
        assert "mutable" in result.stdout
        assert "frozen" in result.stdout
        assert "deep-frozen" in result.stdout

    def test_diagnose_command(self):
        result = subprocess.run([sys.executable, "-m", "lithify.cli", "diagnose"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "Dependencies" in result.stdout

    def test_validate_command(self, sample_schemas, temp_dir):
        json_out = temp_dir / "json"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "lithify.cli",
                "validate",
                "--schemas",
                str(sample_schemas),
                "--json-out",
                str(json_out),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert json_out.exists()
        assert (json_out / "user.json").exists()
        assert (json_out / "event.json").exists()
