# tests/test_slas.py

import json
from pathlib import Path

import pytest
import yaml

from lithify.slas_alias_generator import (
    emit_alias_modules,
    generate_alias_code,
)
from lithify.slas_classifier import (
    classify_shape,
    get_number_constraints,
    get_string_constraints,
    is_enum_str,
    is_scalar_number,
    is_scalar_str,
    is_union_of_scalar_str,
    union_scalar_pattern,
)
from lithify.slas_field_mapper import (
    build_field_map,
    sanitize_field_name,
)
from lithify.slas_rewriter import (
    rewrite_module_with_aliases,
)
from lithify.slas_schema_index import SchemaIndex, resolve_pointer, resolve_uri


@pytest.fixture
def fixtures_dir():
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def common_types_schema(fixtures_dir):
    with open(fixtures_dir / "common_types.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def event_schema(fixtures_dir):
    with open(fixtures_dir / "event.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def temp_schemas_dir(tmp_path, common_types_schema, event_schema):
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()

    common_types_path = schemas_dir / "00_common_types.json"
    common_types_path.write_text(json.dumps(common_types_schema, indent=2))

    event_path = schemas_dir / "01_event.json"
    event_path.write_text(json.dumps(event_schema, indent=2))

    return schemas_dir


class TestSchemaIndex:
    def test_resolve_pointer(self):
        doc = {
            "properties": {"name": {"type": "string"}, "a/b": {"type": "number"}, "c~d": {"type": "boolean"}},
            "$defs": {"Foo": {"type": "string"}},
        }

        assert resolve_pointer(doc, "#/properties/name") == {"type": "string"}

        assert resolve_pointer(doc, "#/properties/a~1b") == {"type": "number"}

        assert resolve_pointer(doc, "#/properties/c~0d") == {"type": "boolean"}

        assert resolve_pointer(doc, "#/$defs/Foo") == {"type": "string"}

    def test_resolve_uri(self):
        base = "https://example.com/schemas/base.json"

        doc_uri, frag = resolve_uri(base, "./other.json#/foo")
        assert doc_uri == "https://example.com/schemas/other.json"
        assert frag == "#/foo"

        doc_uri, frag = resolve_uri(base, "#/bar")
        assert doc_uri == "https://example.com/schemas/base.json"
        assert frag == "#/bar"

        doc_uri, frag = resolve_uri(base, "https://other.com/schema.json")
        assert doc_uri == "https://other.com/schema.json"
        assert frag == ""

    def test_schema_index_loading(self, temp_schemas_dir):
        index = SchemaIndex.load(list(temp_schemas_dir.glob("*.json")), base_url="https://example.com/schemas/core/v1/")

        assert len(index.docs) == 2

        common_types_uri = "https://example.com/schemas/core/v1/common_types.schema.json"
        node = index.node_for(common_types_uri)
        assert node is not None
        assert node["title"] == "CommonTypes"

        version_node = index.node_for(f"{common_types_uri}#/$defs/VersionString")
        assert version_node is not None
        assert version_node["type"] == "string"
        assert "pattern" in version_node

    def test_exportables(self, temp_schemas_dir):
        index = SchemaIndex.load(list(temp_schemas_dir.glob("*.json")), base_url="https://example.com/schemas/core/v1/")

        common_types_uri = "https://example.com/schemas/core/v1/common_types.schema.json"
        exports = index.exportables(common_types_uri)

        assert len(exports) > 0

        export_names = [name for _, name, _ in exports]
        assert "VersionString" in export_names
        assert "UuidLower" in export_names
        assert "IdHex16Or32" in export_names
        assert "CountryCode" in export_names

        for _, _, module in exports:
            assert module == "common_types"


class TestShapeClassification:
    def test_scalar_string_classification(self, common_types_schema):
        defs = common_types_schema["$defs"]

        assert classify_shape(defs["VersionString"]) == "scalar_str"
        assert is_scalar_str(defs["VersionString"])

        assert classify_shape(defs["UuidLower"]) == "scalar_str"
        assert is_scalar_str(defs["UuidLower"])

        assert classify_shape(defs["IdHex16"]) == "scalar_str"
        assert is_scalar_str(defs["IdHex16"])

        assert classify_shape(defs["NonEmptyString"]) == "scalar_str"
        assert is_scalar_str(defs["NonEmptyString"])

        assert classify_shape(defs["Rfc3339Timestamp"]) == "scalar_str"
        assert is_scalar_str(defs["Rfc3339Timestamp"])

    def test_union_classification(self, common_types_schema):
        defs = common_types_schema["$defs"]

        assert classify_shape(defs["IdHex16Or32"]) == "union_scalar_str"
        assert is_union_of_scalar_str(defs["IdHex16Or32"])

        branches = defs["IdHex16Or32"]["oneOf"]
        pattern = union_scalar_pattern(branches)
        assert pattern is not None
        assert "^(?:" in pattern
        assert "[0-9a-f]{16}" in pattern
        assert "[0-9a-f]{32}" in pattern

    def test_enum_classification(self, common_types_schema):
        defs = common_types_schema["$defs"]

        assert classify_shape(defs["CountryCode"]) == "enum_str"
        assert is_enum_str(defs["CountryCode"])

    def test_scalar_number_classification(self, common_types_schema):
        defs = common_types_schema["$defs"]

        assert classify_shape(defs["Latitude"]) == "scalar_number"
        assert is_scalar_number(defs["Latitude"])

        assert classify_shape(defs["Longitude"]) == "scalar_number"
        assert is_scalar_number(defs["Longitude"])

        assert classify_shape(defs["UnixTimestamp"]) == "scalar_number"
        assert is_scalar_number(defs["UnixTimestamp"])

        assert classify_shape(defs["PortNumber"]) == "scalar_number"
        assert is_scalar_number(defs["PortNumber"])

        assert classify_shape(defs["Percentage"]) == "scalar_number"
        assert is_scalar_number(defs["Percentage"])

    def test_get_number_constraints(self, common_types_schema):
        defs = common_types_schema["$defs"]

        constraints = get_number_constraints(defs["Latitude"])
        assert constraints["ge"] == -90
        assert constraints["le"] == 90

        constraints = get_number_constraints(defs["Longitude"])
        assert constraints["ge"] == -180
        assert constraints["le"] == 180

        constraints = get_number_constraints(defs["UnixTimestamp"])
        assert constraints["ge"] == 0
        assert "le" not in constraints

        constraints = get_number_constraints(defs["Percentage"])
        assert constraints["ge"] == 0
        assert constraints["le"] == 100
        assert constraints["multiple_of"] == 0.01

    def test_format_based_strings(self, common_types_schema):
        defs = common_types_schema["$defs"]

        assert classify_shape(defs["Email"]) == "scalar_str"
        assert is_scalar_str(defs["Email"])

        assert classify_shape(defs["HttpUrl"]) == "scalar_str"
        assert is_scalar_str(defs["HttpUrl"])

    def test_combined_string_constraints(self, common_types_schema):
        defs = common_types_schema["$defs"]

        assert classify_shape(defs["Username"]) == "scalar_str"
        constraints = get_string_constraints(defs["Username"])
        assert "pattern" in constraints
        assert constraints["min_length"] == 3
        assert constraints["max_length"] == 30

        assert classify_shape(defs["StrongPassword"]) == "scalar_str"
        constraints = get_string_constraints(defs["StrongPassword"])
        assert "pattern" in constraints
        assert constraints["min_length"] == 12
        assert constraints["max_length"] == 128

        assert classify_shape(defs["Tag"]) == "scalar_str"
        constraints = get_string_constraints(defs["Tag"])
        assert "pattern" in constraints
        assert constraints["max_length"] == 32

    def test_complex_union_types(self, common_types_schema):
        defs = common_types_schema["$defs"]

        shape = classify_shape(defs["PortOrEnvVar"])
        assert shape != "union_scalar_str"

    def test_additional_enums(self, common_types_schema):
        defs = common_types_schema["$defs"]

        assert classify_shape(defs["LogLevel"]) == "enum_str"
        assert is_enum_str(defs["LogLevel"])

    def test_get_string_constraints(self, common_types_schema):
        defs = common_types_schema["$defs"]

        constraints = get_string_constraints(defs["VersionString"])
        assert "pattern" in constraints
        assert constraints["pattern"].startswith("^")

        constraints = get_string_constraints(defs["NonEmptyString"])
        assert "min_length" in constraints
        assert constraints["min_length"] == 1

        constraints = get_string_constraints(defs["Rfc3339Timestamp"])
        assert "pattern" in constraints


class TestAliasGeneration:
    def test_generate_scalar_string_alias(self, common_types_schema):
        defs = common_types_schema["$defs"]

        code = generate_alias_code("VersionString", defs["VersionString"], "scalar_str")
        assert "VersionString = Annotated[str, StringConstraints(" in code
        assert "pattern=" in code

        code = generate_alias_code("NonEmptyString", defs["NonEmptyString"], "scalar_str")
        assert "NonEmptyString = Annotated[str, StringConstraints(" in code
        assert "min_length=1" in code

    def test_generate_union_alias(self, common_types_schema):
        defs = common_types_schema["$defs"]

        code = generate_alias_code("IdHex16Or32", defs["IdHex16Or32"], "union_scalar_str")
        assert "IdHex16Or32 = Annotated[str, StringConstraints(" in code
        assert "pattern=" in code
        assert "(?:" in code

    def test_generate_numeric_alias(self, common_types_schema):
        defs = common_types_schema["$defs"]

        code = generate_alias_code("Latitude", defs["Latitude"], "scalar_number")
        assert "Latitude = Annotated[float, Field(" in code
        assert "ge=-90" in code
        assert "le=90" in code

        code = generate_alias_code("UnixTimestamp", defs["UnixTimestamp"], "scalar_number")
        assert "UnixTimestamp = Annotated[int, Field(" in code
        assert "ge=0" in code

        code = generate_alias_code("Percentage", defs["Percentage"], "scalar_number")
        assert "Percentage = Annotated[float, Field(" in code
        assert "ge=0" in code
        assert "le=100" in code
        assert "multiple_of=0.01" in code

    def test_generate_format_based_alias(self, common_types_schema):
        defs = common_types_schema["$defs"]

        code = generate_alias_code("Email", defs["Email"], "scalar_str")
        assert "Email = Annotated[str, StringConstraints(" in code
        assert "pattern=" in code

        code = generate_alias_code("HttpUrl", defs["HttpUrl"], "scalar_str")
        assert "HttpUrl = Annotated[str, StringConstraints(" in code
        assert "^https?://" in code

    def test_generate_combined_constraints_alias(self, common_types_schema):
        defs = common_types_schema["$defs"]

        code = generate_alias_code("Username", defs["Username"], "scalar_str")
        assert "Username = Annotated[str, StringConstraints(" in code
        assert "pattern=" in code
        assert "min_length=3" in code
        assert "max_length=30" in code

    def test_generate_additional_enum_alias(self, common_types_schema):
        defs = common_types_schema["$defs"]

        code = generate_alias_code("LogLevel", defs["LogLevel"], "enum_str")
        assert "LogLevel = Literal[" in code
        assert "'debug'" in code or '"debug"' in code
        assert "'info'" in code or '"info"' in code
        assert "'warn'" in code or '"warn"' in code
        assert "'error'" in code or '"error"' in code
        assert "'fatal'" in code or '"fatal"' in code

    def test_emit_origin_module(self, temp_schemas_dir, tmp_path):
        index = SchemaIndex.load(list(temp_schemas_dir.glob("*.json")), base_url="https://example.com/schemas/core/v1/")

        output_dir = tmp_path / "generated"
        ref_map, modules_created = emit_alias_modules(index, output_dir, "test_package", "BaseModel", False, verbose=0)

        assert "common_types" in modules_created
        assert len(modules_created["common_types"]) > 0

        module_path = output_dir / "common_types.py"
        assert module_path.exists()

        code = module_path.read_text()

        assert "from typing import Annotated, Literal" in code
        assert "from pydantic import Field, StringConstraints" in code

        assert "__all__ =" in code

        assert "VersionString" in code
        assert "UuidLower" in code
        assert "IdHex16Or32" in code
        assert "CountryCode" in code

        assert "Email" in code
        assert "HttpUrl" in code
        assert "Latitude" in code
        assert "Longitude" in code
        assert "UnixTimestamp" in code
        assert "Percentage" in code
        assert "Username" in code
        assert "StrongPassword" in code
        assert "PhoneNumber" in code
        assert "Tag" in code
        assert "LogLevel" in code

        assert any("#/$defs/VersionString" in k for k in ref_map.keys())
        assert any("#/$defs/Email" in k for k in ref_map.keys())
        assert any("#/$defs/Latitude" in k for k in ref_map.keys())
        assert any("#/$defs/Username" in k for k in ref_map.keys())

        assert any("Key~With~Tilde" in k for k in ref_map.keys())
        assert any("Path/With/Slash" in k for k in ref_map.keys())


class TestFieldMapping:
    def test_sanitize_field_name(self):
        assert sanitize_field_name("eventId") == "event_id"
        assert sanitize_field_name("trace-id") == "trace_id"
        assert sanitize_field_name("123field") == "field_123field"
        assert sanitize_field_name("class") == "class_"
        assert sanitize_field_name("from") == "from_"

    def test_build_field_map(self, temp_schemas_dir, tmp_path):
        index = SchemaIndex.load(list(temp_schemas_dir.glob("*.json")), base_url="https://example.com/schemas/core/v1/")

        output_dir = tmp_path / "generated"
        ref_map, _ = emit_alias_modules(index, output_dir, "test_package", "BaseModel", False, verbose=0)

        field_map = build_field_map(index, ref_map, temp_schemas_dir, verbose=0)

        assert len(field_map) > 0

        assert any("event_id" in key for key in field_map.keys())

        tags_mapping = next((v for k, v in field_map.items() if "tags" in k), None)
        if tags_mapping:
            assert "NonEmptyString" in tags_mapping.alias_fqn
            assert tags_mapping.slot == "list_item"

        metadata_mapping = next((v for k, v in field_map.items() if "metadata" in k), None)
        if metadata_mapping:
            assert "NonEmptyString" in metadata_mapping.alias_fqn
            assert metadata_mapping.slot == "dict_value"

    def test_field_map_with_urn_refs(self, tmp_path):
        common_types_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:test:common:v1",
            "title": "CommonTypes",
            "$defs": {
                "UUID": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                },
                "Sha256Hex": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
        }

        model_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:test:model:v1",
            "title": "Model",
            "type": "object",
            "properties": {
                "id": {"$ref": "urn:test:common:v1#/$defs/UUID"},
                "digest": {"$ref": "urn:test:common:v1#/$defs/Sha256Hex"},
            },
        }

        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        common_types_path = schemas_dir / "common_types.json"
        common_types_path.write_text(json.dumps(common_types_schema, indent=2))

        model_path = schemas_dir / "model.json"
        model_path.write_text(json.dumps(model_schema, indent=2))

        index = SchemaIndex.load(
            list(schemas_dir.glob("*.json")),
            base_url=None,
        )

        output_dir = tmp_path / "generated"
        ref_map, _ = emit_alias_modules(index, output_dir, "test_package", "BaseModel", False, verbose=0)

        field_map = build_field_map(index, ref_map, schemas_dir, verbose=0)

        assert "Model.id" in field_map, "Model.id should be in field_map"
        assert "Model.digest" in field_map, "Model.digest should be in field_map"

        id_mapping = field_map["Model.id"]
        assert "UUID" in id_mapping.alias_fqn, f"Expected UUID in alias, got {id_mapping.alias_fqn}"

        digest_mapping = field_map["Model.digest"]
        assert "Sha256Hex" in digest_mapping.alias_fqn, f"Expected Sha256Hex in alias, got {digest_mapping.alias_fqn}"

    def test_field_map_with_file_refs(self, tmp_path):
        common_types_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.com/schemas/common_types.json",
            "title": "CommonTypes",
            "$defs": {
                "UUID": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                }
            },
        }

        model_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.com/schemas/model.json",
            "title": "Model",
            "type": "object",
            "properties": {"id": {"$ref": "./common_types.json#/$defs/UUID"}},
        }

        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        common_types_path = schemas_dir / "common_types.json"
        common_types_path.write_text(json.dumps(common_types_schema, indent=2))

        model_path = schemas_dir / "model.json"
        model_path.write_text(json.dumps(model_schema, indent=2))

        index = SchemaIndex.load(list(schemas_dir.glob("*.json")), base_url="https://example.com/schemas/")

        output_dir = tmp_path / "generated"
        ref_map, _ = emit_alias_modules(index, output_dir, "test_package", "BaseModel", False, verbose=0)

        field_map = build_field_map(index, ref_map, schemas_dir, verbose=0)

        assert "Model.id" in field_map, "Model.id should be in field_map"
        assert "UUID" in field_map["Model.id"].alias_fqn

    def test_field_map_respects_class_name_override_with_urns(self, tmp_path):
        common_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:test:common:v1",
            "title": "CommonTypes",
            "$defs": {"Identifier": {"type": "string", "pattern": "^[a-f0-9]{32}$"}},
        }

        record_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:test:record:v1",
            "title": "RecordV1",
            "x-python-class-name": "Record",  # Override!
            "type": "object",
            "properties": {"record_id": {"$ref": "urn:test:common:v1#/$defs/Identifier"}},
        }

        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        (schemas_dir / "common.json").write_text(json.dumps(common_schema, indent=2))
        (schemas_dir / "record.json").write_text(json.dumps(record_schema, indent=2))

        index = SchemaIndex.load(list(schemas_dir.glob("*.json")))

        assert "RecordV1" in index.class_name_overrides
        assert index.class_name_overrides["RecordV1"] == "Record"

        output_dir = tmp_path / "generated"
        ref_map, _ = emit_alias_modules(index, output_dir, "test_pkg", "BaseModel", False)

        field_map = build_field_map(index, ref_map, schemas_dir, verbose=2)

        assert "Record.record_id" in field_map, f"Expected 'Record.record_id', got keys: {list(field_map.keys())}"
        assert "RecordV1.record_id" not in field_map, "Should NOT use schema title when override present"


class TestEndToEnd:
    def test_full_pipeline(self, temp_schemas_dir, tmp_path):
        index = SchemaIndex.load(list(temp_schemas_dir.glob("*.json")), base_url="https://example.com/schemas/core/v1/")

        output_dir = tmp_path / "generated"
        ref_map, modules_created = emit_alias_modules(index, output_dir, "generated", "BaseModel", False, verbose=0)

        assert "common_types" in modules_created

        field_map = build_field_map(index, ref_map, temp_schemas_dir, verbose=0)
        assert len(field_map) > 0

        sample_model = output_dir / "event.py"
        sample_model.write_text("""
from pydantic import BaseModel
from typing import Optional

class Event(BaseModel):
    event_id: str
    trace_id: str
    span_id: Optional[str]
    timestamp: str
    version: str
    tags: list[str]
    metadata: dict[str, str]
    country: str
    hash: str
""")

        success = rewrite_module_with_aliases(sample_model, field_map, depth=0, package_name="generated", verbose=0)

        if success:
            rewritten = sample_model.read_text()
            assert "from" in rewritten

    def test_generate_with_urn_refs_applies_aliases(self, tmp_path):
        common_types_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:test:common:v1",
            "title": "CommonTypes",
            "$defs": {
                "UUID": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                },
                "Sha256Hex": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
        }

        model_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:test:model:v1",
            "title": "TestModel",
            "type": "object",
            "required": ["id", "digest"],
            "properties": {
                "id": {"$ref": "urn:test:common:v1#/$defs/UUID"},
                "digest": {"$ref": "urn:test:common:v1#/$defs/Sha256Hex"},
                "tags": {"type": "array", "items": {"$ref": "urn:test:common:v1#/$defs/UUID"}},
            },
        }

        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        (schemas_dir / "common_types.json").write_text(json.dumps(common_types_schema, indent=2))
        (schemas_dir / "model.json").write_text(json.dumps(model_schema, indent=2))

        index = SchemaIndex.load(list(schemas_dir.glob("*.json")), base_url=None)

        output_dir = tmp_path / "generated"
        ref_map, modules_created = emit_alias_modules(index, output_dir, "test_pkg", "BaseModel", False, verbose=0)

        common_types_module = output_dir / "common_types.py"
        assert common_types_module.exists(), "Aliases module should exist"

        aliases_content = common_types_module.read_text()
        assert "UUID = Annotated" in aliases_content
        assert "Sha256Hex = Annotated" in aliases_content

        field_map = build_field_map(index, ref_map, schemas_dir, verbose=0)

        assert "TestModel.id" in field_map
        assert "TestModel.digest" in field_map
        assert "TestModel.tags" in field_map

        model_file = output_dir / "model.py"
        model_file.write_text("""from pydantic import BaseModel

class TestModel(BaseModel):
    id: str
    digest: str
    tags: list[str]
""")

        success = rewrite_module_with_aliases(model_file, field_map, depth=0, package_name="test_pkg", verbose=0)

        assert success, "Rewrite should succeed"

        rewritten = model_file.read_text()

        assert "from .common_types import" in rewritten
        assert "UUID" in rewritten
        assert "Sha256Hex" in rewritten

        assert "id: UUID" in rewritten
        assert "digest: Sha256Hex" in rewritten
        assert "list[UUID]" in rewritten

        assert "id: str" not in rewritten
        assert "digest: str" not in rewritten

    def test_cli_integration(self, temp_schemas_dir, tmp_path):
        from lithify.cli import app
        from lithify.slas_alias_generator import emit_alias_modules
        from lithify.slas_schema_index import SchemaIndex

        assert app is not None
        assert SchemaIndex is not None
        assert emit_alias_modules is not None
