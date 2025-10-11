# tests/test_mutability.py

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


class TestMutableMode:
    def test_generate_mutable(self, sample_schemas, temp_dir):
        json_out = temp_dir / "json"
        models_out = temp_dir / "models"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd())

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "lithify.cli",
                "generate",
                "--schemas",
                str(sample_schemas),
                "--json-out",
                str(json_out),
                "--models-out",
                str(models_out),
                "--package-name",
                "test_mutable",
                "--mutability",
                "mutable",
            ],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0
        assert (models_out / "test_mutable").exists()
        assert (models_out / "test_mutable" / "mutable_base.py").exists()
        assert not (models_out / "test_mutable" / "manifest.json").exists()
        assert not (models_out / "test_mutable" / "py.typed").exists()
        assert (models_out / "test_mutable" / "__init__.py").exists()

    def test_mutable_models_are_mutable(self, sample_schemas, temp_dir):
        json_out = temp_dir / "json"
        models_out = temp_dir / "models"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd())

        subprocess.run(
            [
                sys.executable,
                "-m",
                "lithify.cli",
                "generate",
                "--schemas",
                str(sample_schemas),
                "--json-out",
                str(json_out),
                "--models-out",
                str(models_out),
                "--package-name",
                "test_mut",
                "--mutability",
                "mutable",
                "--clean",
            ],
            capture_output=True,
            env=env,
        )

        test_code = f"""
import sys
sys.path.insert(0, r'{models_out}')
from test_mut.user import User

# Create instance
user = User(id=1, name="Alice", emails=["alice@example.com"], metadata={{"key": "value"}})

# Test attribute mutation
user.name = "Bob"
assert user.name == "Bob"

# Test list mutation
user.emails.append("bob@example.com")
assert len(user.emails) == 2

# Test dict mutation
user.metadata["new_key"] = "new_value"
assert "new_key" in user.metadata

print("OK")
"""
        result = subprocess.run([sys.executable, "-c", test_code], capture_output=True, text=True, env=env)
        assert result.returncode == 0
        assert "OK" in result.stdout


class TestFrozenMode:
    def test_generate_frozen(self, sample_schemas, temp_dir):
        json_out = temp_dir / "json"
        models_out = temp_dir / "models"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd())

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "lithify.cli",
                "generate",
                "--schemas",
                str(sample_schemas),
                "--json-out",
                str(json_out),
                "--models-out",
                str(models_out),
                "--package-name",
                "test_frozen",
                "--mutability",
                "frozen",
            ],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0
        assert (models_out / "test_frozen" / "frozen_base.py").exists()

        base_content = (models_out / "test_frozen" / "frozen_base.py").read_text()
        assert "class FrozenBase" in base_content
        assert "frozen=True" in base_content

    def test_frozen_models_shallow_freeze(self, sample_schemas, temp_dir):
        json_out = temp_dir / "json"
        models_out = temp_dir / "models"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd())

        subprocess.run(
            [
                sys.executable,
                "-m",
                "lithify.cli",
                "generate",
                "--schemas",
                str(sample_schemas),
                "--json-out",
                str(json_out),
                "--models-out",
                str(models_out),
                "--package-name",
                "test_frz",
                "--mutability",
                "frozen",
                "--clean",
            ],
            capture_output=True,
            env=env,
        )

        test_code = f"""
import sys
sys.path.insert(0, r'{models_out}')
from test_frz.user import User

user = User(id=1, name="Alice", emails=["alice@example.com"], metadata={{"key": "value"}})

# Attribute mutation should fail
try:
    user.name = "Bob"
    raise AssertionError("Should not allow attribute mutation")
except Exception:
    pass

# But container mutation should work (shallow freeze)
user.emails.append("new@example.com")
assert len(user.emails) == 2

user.metadata["new"] = "value"
assert "new" in user.metadata

print("OK")
"""
        result = subprocess.run([sys.executable, "-c", test_code], capture_output=True, text=True, env=env)
        assert result.returncode == 0
        assert "OK" in result.stdout


class TestDeepFrozenMode:
    def test_generate_deep_frozen(self, sample_schemas, temp_dir):
        json_out = temp_dir / "json"
        models_out = temp_dir / "models"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd())

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "lithify.cli",
                "generate",
                "--schemas",
                str(sample_schemas),
                "--json-out",
                str(json_out),
                "--models-out",
                str(models_out),
                "--package-name",
                "test_lithified",
                "--mutability",
                "deep-frozen",
                "--immutable-hints",
            ],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0
        assert (models_out / "test_lithified" / "frozen_base.py").exists()

        base_content = (models_out / "test_lithified" / "frozen_base.py").read_text()
        assert "_deep_freeze" in base_content
        assert "class FrozenModel" in base_content

    def test_deep_frozen_models_are_immutable(self, sample_schemas, temp_dir):
        json_out = temp_dir / "json"
        models_out = temp_dir / "models"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd())

        subprocess.run(
            [
                sys.executable,
                "-m",
                "lithify.cli",
                "generate",
                "--schemas",
                str(sample_schemas),
                "--json-out",
                str(json_out),
                "--models-out",
                str(models_out),
                "--package-name",
                "test_lith",
                "--mutability",
                "deep-frozen",
                "--clean",
            ],
            capture_output=True,
            env=env,
        )

        test_code = f"""
import sys
sys.path.insert(0, r'{models_out}')
from test_lith.user import User

user = User(id=1, name="Alice", emails=["alice@example.com"], metadata={{"key": "value"}})

# Check that lists became tuples
assert isinstance(user.emails, tuple)

# Attribute mutation should fail
try:
    user.name = "Bob"
    raise AssertionError("Should not allow attribute mutation")
except Exception:
    pass

# Container mutation should also fail (deep freeze)
try:
    if hasattr(user.emails, 'append'):
        user.emails.append("new@example.com")
        raise AssertionError("Tuple should not have append")
except AttributeError:
    pass

print("OK")
"""
        result = subprocess.run([sys.executable, "-c", test_code], capture_output=True, text=True, env=env)
        assert result.returncode == 0
        assert "OK" in result.stdout
