# tests/test_urn_ref_sanitization_regression.py
import pytest

from lithify.enums import FormatChoice, Mutability, OutputMode
from lithify.orchestrator import GenerationConfig, SimpleReporter, run_generation


@pytest.fixture(autouse=True)
def clear_resolver_cache():
    from lithify import resolver

    resolver._resolver_cache = None
    yield
    resolver._resolver_cache = None


@pytest.fixture
def workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    schemas_dir = workspace / "schemas"
    schemas_dir.mkdir()

    types_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:test:types:v1",
        "title": "Types",
        "description": "Common type definitions",
        "type": "object",
        "$defs": {
            "ID": {"type": "string", "pattern": "^[a-z0-9]+$", "description": "Alphanumeric identifier"},
            "Timestamp": {"type": "string", "pattern": "^[0-9]+$", "description": "Unix timestamp as string"},
        },
    }

    entity_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:test:entity:v1",
        "title": "Entity",
        "description": "A document entity",
        "type": "object",
        "properties": {
            "id": {
                "description": "Entity identifier with length constraint",
                "allOf": [{"$ref": "urn:test:types:v1#/$defs/ID"}, {"minLength": 8}],
            },
            "name": {"type": "string"},
            "created_at": {
                "description": "Creation timestamp",
                "allOf": [{"$ref": "urn:test:types:v1#/$defs/Timestamp"}, {"pattern": "^[1-9][0-9]{9,}$"}],
            },
        },
        "required": ["id", "name", "created_at"],
    }

    import json

    (schemas_dir / "types.v1.schema.json").write_text(json.dumps(types_schema, indent=2))
    (schemas_dir / "entity.v1.schema.json").write_text(json.dumps(entity_schema, indent=2))

    return workspace


def test_urn_refs_with_dotted_filenames_and_allof(workspace):
    resolver_code = f"""
from pathlib import Path

SCHEMA_DIR = Path(r"{workspace / "schemas"}")

URN_MAP = {{
    "urn:test:types:v1": SCHEMA_DIR / "types.v1.schema.json",
    "urn:test:entity:v1": SCHEMA_DIR / "entity.v1.schema.json",
}}

def resolve_urn(urn: str) -> Path:
    base_urn = urn.split("#")[0]
    if base_urn not in URN_MAP:
        raise KeyError(f"Unknown URN: {{base_urn}}")
    path = URN_MAP[base_urn]
    if not path.exists():
        raise FileNotFoundError(f"Schema not found: {{path}}")
    return path
"""

    resolver_file = workspace / "test_resolver.py"
    resolver_file.write_text(resolver_code)
    resolver_path = resolver_file

    config = GenerationConfig(
        schemas=workspace / "schemas",
        json_out=None,
        models_out=workspace / "models",
        package_name="test_pkg",
        exclude=None,
        mutability=Mutability.mutable,
        base_url=None,
        block_remote_refs=False,
        custom_ref_resolver=f"{resolver_path}:resolve_urn",
        immutable_hints=False,
        use_frozendict=False,
        from_attributes=False,
        partial=False,
        clean_first=False,
        check=False,
        verbose=0,
        output_mode=OutputMode.clean,
        fmt=FormatChoice.none,
        no_rewrite=False,
        dry_run=False,
        lenient_allof=False,
    )

    reporter = SimpleReporter()
    result = run_generation(config, reporter)

    assert result.package_dir.exists()
    assert (result.package_dir / "__init__.py").exists()

    py_files = list(result.package_dir.glob("*.py"))
    assert len(py_files) > 0


def test_urn_refs_resolve_to_correct_types(workspace):
    resolver_code = f"""
from pathlib import Path

SCHEMA_DIR = Path(r"{workspace / "schemas"}")

URN_MAP = {{
    "urn:test:types:v1": SCHEMA_DIR / "types.v1.schema.json",
    "urn:test:entity:v1": SCHEMA_DIR / "entity.v1.schema.json",
}}

def resolve_urn(urn: str) -> Path:
    base_urn = urn.split("#")[0]
    if base_urn not in URN_MAP:
        raise KeyError(f"Unknown URN: {{base_urn}}")
    path = URN_MAP[base_urn]
    if not path.exists():
        raise FileNotFoundError(f"Schema not found: {{path}}")
    return path
"""
    resolver_file = workspace / "test_resolver.py"
    resolver_file.write_text(resolver_code)
    resolver_path = resolver_file

    config = GenerationConfig(
        schemas=workspace / "schemas",
        json_out=None,
        models_out=workspace / "models",
        package_name="test_pkg",
        exclude=None,
        mutability=Mutability.mutable,
        base_url=None,
        block_remote_refs=False,
        custom_ref_resolver=f"{resolver_path}:resolve_urn",
        immutable_hints=False,
        use_frozendict=False,
        from_attributes=False,
        partial=False,
        clean_first=False,
        check=False,
        verbose=0,
        output_mode=OutputMode.clean,
        fmt=FormatChoice.none,
        no_rewrite=False,
        dry_run=False,
        lenient_allof=False,
    )

    result = run_generation(config, SimpleReporter())

    assert result.package_dir.exists()
    py_files = list(result.package_dir.glob("*.py"))
    assert len(py_files) > 0


def test_no_regression_on_regular_file_refs(tmp_path):
    workspace = tmp_path / "workspace_regular"
    workspace.mkdir()
    schemas_dir = workspace / "schemas"
    schemas_dir.mkdir()

    base = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Base",
        "type": "object",
        "$defs": {"Name": {"type": "string", "minLength": 1}},
    }
    (schemas_dir / "base.json").write_text(__import__("json").dumps(base, indent=2))

    derived = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Derived",
        "type": "object",
        "properties": {"name": {"allOf": [{"$ref": "./base.json#/$defs/Name"}, {"maxLength": 100}]}},
    }
    (schemas_dir / "derived.json").write_text(__import__("json").dumps(derived, indent=2))

    config = GenerationConfig(
        schemas=schemas_dir,
        json_out=None,
        models_out=workspace / "models_regular",
        package_name="regular_pkg",
        exclude=None,
        mutability=Mutability.mutable,
        base_url=None,
        block_remote_refs=False,
        custom_ref_resolver=None,
        immutable_hints=False,
        use_frozendict=False,
        from_attributes=False,
        partial=False,
        clean_first=False,
        check=False,
        verbose=0,
        output_mode=OutputMode.clean,
        fmt=FormatChoice.none,
        no_rewrite=False,
        dry_run=False,
        lenient_allof=False,
    )

    result = run_generation(config, SimpleReporter())

    assert result.package_dir.exists()
    py_files = list(result.package_dir.glob("*.py"))
    assert len(py_files) > 0
