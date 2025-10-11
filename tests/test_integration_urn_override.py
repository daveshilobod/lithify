# tests/test_integration_urn_override.py

import json

from lithify.slas_alias_generator import emit_alias_modules
from lithify.slas_field_mapper import build_field_map
from lithify.slas_rewriter import rewrite_module_with_aliases
from lithify.slas_schema_index import SchemaIndex


def test_urn_with_override_full_pipeline(tmp_path):
    common_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:example:common:v1",
        "title": "CommonTypes",
        "$defs": {
            "Identifier": {
                "type": "string",
                "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            },
            "Checksum": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
    }

    record_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:example:record:v1",
        "title": "SystemRecordV1",
        "x-python-class-name": "SystemRecord",
        "type": "object",
        "required": ["record_id", "data_checksum"],
        "properties": {
            "record_id": {"$ref": "urn:example:common:v1#/$defs/Identifier"},
            "data_checksum": {"$ref": "urn:example:common:v1#/$defs/Checksum"},
        },
    }

    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "common_types.json").write_text(json.dumps(common_schema, indent=2))
    (schemas_dir / "system_record.json").write_text(json.dumps(record_schema, indent=2))

    resolver_file = tmp_path / "resolver.py"
    resolver_file.write_text(f"""
from pathlib import Path

URN_MAP = {{
    "urn:example:common:v1": Path("{schemas_dir / "common_types.json"}"),
    "urn:example:record:v1": Path("{schemas_dir / "system_record.json"}"),
}}

def resolve_urn(urn: str) -> Path:
    if urn not in URN_MAP:
        raise KeyError(f"Unknown URN: {{urn}}")
    return URN_MAP[urn]
""")

    index = SchemaIndex.load(list(schemas_dir.glob("*.json")))

    assert "SystemRecordV1" in index.class_name_overrides
    assert index.class_name_overrides["SystemRecordV1"] == "SystemRecord"

    output_dir = tmp_path / "generated"
    ref_map, modules_created = emit_alias_modules(index, output_dir, "test_models", "BaseModel", False, verbose=0)

    common_module = output_dir / "common_types.py"
    assert common_module.exists()

    alias_content = common_module.read_text()
    assert "Identifier = Annotated" in alias_content
    assert "Checksum = Annotated" in alias_content

    field_map = build_field_map(index, ref_map, schemas_dir, verbose=0)

    assert "SystemRecord.record_id" in field_map
    assert "SystemRecord.data_checksum" in field_map
    assert "SystemRecordV1.record_id" not in field_map

    model_file = output_dir / "system_record.py"
    model_file.write_text("""from pydantic import BaseModel

class SystemRecord(BaseModel):
    record_id: str
    data_checksum: str
""")

    success = rewrite_module_with_aliases(model_file, field_map, depth=0, package_name="test_models", verbose=0)

    assert success, "Rewrite should succeed"

    rewritten = model_file.read_text()

    assert "from .common_types import" in rewritten
    assert "Identifier" in rewritten
    assert "Checksum" in rewritten

    assert "record_id: Identifier" in rewritten
    assert "data_checksum: Checksum" in rewritten

    assert "record_id: str" not in rewritten
    assert "data_checksum: str" not in rewritten

    print("Full pipeline works: URN refs + class override + type aliases applied")
