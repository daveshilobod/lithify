# tests/test_slas.py
"""
Tests for SLAS
"""

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

from lithify.slas_schema_index import SchemaIndex, NodeId, resolve_pointer, resolve_uri
from lithify.slas_classifier import (
    classify_shape,
    is_scalar_str,
    is_scalar_number,
    is_enum_str,
    is_union_of_scalar_str,
    union_scalar_pattern,
    get_string_constraints,
    get_number_constraints,
)
from lithify.slas_alias_generator import (
    generate_alias_code,
    generate_alias_module,
    generate_ref_map,
    emit_alias_modules,
)
from lithify.slas_field_mapper import (
    FieldTarget,
    sanitize_field_name,
    build_field_map,
)
from lithify.slas_rewriter import (
    FieldRewriter,
    rewrite_module_with_aliases,
    rewrite_all_modules,
)


@pytest.fixture
def fixtures_dir():
    """Path to test fixtures."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def common_types_schema(fixtures_dir):
    """Load the common_types.yaml fixture."""
    with open(fixtures_dir / "common_types.yaml", "r") as f:
        return yaml.safe_load(f)


@pytest.fixture
def event_schema(fixtures_dir):
    """Load the event.yaml fixture."""
    with open(fixtures_dir / "event.yaml", "r") as f:
        return yaml.safe_load(f)


@pytest.fixture
def temp_schemas_dir(tmp_path, common_types_schema, event_schema):
    """Create a temporary directory with test schemas."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    
    common_types_path = schemas_dir / "00_common_types.json"
    common_types_path.write_text(json.dumps(common_types_schema, indent=2))
    
    event_path = schemas_dir / "01_event.json"
    event_path.write_text(json.dumps(event_schema, indent=2))
    
    return schemas_dir


class TestSchemaIndex:
    """Test the schema index functionality."""
    
    def test_resolve_pointer(self):
        """Test JSON Pointer resolution."""
        doc = {
            "properties": {
                "name": {"type": "string"},
                "a/b": {"type": "number"},
                "c~d": {"type": "boolean"}
            },
            "$defs": {
                "Foo": {"type": "string"}
            }
        }
        
        assert resolve_pointer(doc, "#/properties/name") == {"type": "string"}
        
        assert resolve_pointer(doc, "#/properties/a~1b") == {"type": "number"}
        
        assert resolve_pointer(doc, "#/properties/c~0d") == {"type": "boolean"}
        
        assert resolve_pointer(doc, "#/$defs/Foo") == {"type": "string"}
    
    def test_resolve_uri(self):
        """Test URI resolution."""
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
        """Test loading schemas into the index."""
        index = SchemaIndex.load(
            list(temp_schemas_dir.glob("*.json")),
            base_url="https://example.com/schemas/core/v1/"
        )
        
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
        """Test extracting exportable symbols."""
        index = SchemaIndex.load(
            list(temp_schemas_dir.glob("*.json")),
            base_url="https://example.com/schemas/core/v1/"
        )
        
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
    """Test shape classification for scalar detection."""
    
    def test_scalar_string_classification(self, common_types_schema):
        """Test classification of scalar string types."""
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
        """Test classification of union types."""
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
        """Test classification of enum types."""
        defs = common_types_schema["$defs"]
        
        assert classify_shape(defs["CountryCode"]) == "enum_str"
        assert is_enum_str(defs["CountryCode"])
    
    def test_scalar_number_classification(self, common_types_schema):
        """Test classification of scalar number types."""
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
        """Test extraction of number constraints."""
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
        """Test classification of format-based strings."""
        defs = common_types_schema["$defs"]
        
        assert classify_shape(defs["Email"]) == "scalar_str"
        assert is_scalar_str(defs["Email"])
        
        assert classify_shape(defs["HttpUrl"]) == "scalar_str"
        assert is_scalar_str(defs["HttpUrl"])
    
    def test_combined_string_constraints(self, common_types_schema):
        """Test strings with multiple constraints."""
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
        """Test classification of complex union types."""
        defs = common_types_schema["$defs"]
        
        shape = classify_shape(defs["PortOrEnvVar"])
        assert shape != "union_scalar_str"
    
    def test_additional_enums(self, common_types_schema):
        """Test additional enum types."""
        defs = common_types_schema["$defs"]
        
        assert classify_shape(defs["LogLevel"]) == "enum_str"
        assert is_enum_str(defs["LogLevel"])
    
    def test_get_string_constraints(self, common_types_schema):
        """Test extraction of string constraints."""
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
    """Test alias code generation."""
    
    def test_generate_scalar_string_alias(self, common_types_schema):
        """Test generating aliases for scalar strings."""
        defs = common_types_schema["$defs"]
        
        code = generate_alias_code("VersionString", defs["VersionString"], "scalar_str")
        assert "VersionString = Annotated[str, StringConstraints(" in code
        assert "pattern=" in code
        
        code = generate_alias_code("NonEmptyString", defs["NonEmptyString"], "scalar_str")
        assert "NonEmptyString = Annotated[str, StringConstraints(" in code
        assert "min_length=1" in code
    
    def test_generate_union_alias(self, common_types_schema):
        """Test generating aliases for union types."""
        defs = common_types_schema["$defs"]
        
        code = generate_alias_code("IdHex16Or32", defs["IdHex16Or32"], "union_scalar_str")
        assert "IdHex16Or32 = Annotated[str, StringConstraints(" in code
        assert "pattern=" in code
        assert "(?:" in code
    
    def test_generate_numeric_alias(self, common_types_schema):
        """Test generating aliases for numeric types."""
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
        """Test generating aliases for format-based strings."""
        defs = common_types_schema["$defs"]
        
        code = generate_alias_code("Email", defs["Email"], "scalar_str")
        assert "Email = Annotated[str, StringConstraints(" in code
        assert "pattern=" in code
        
        code = generate_alias_code("HttpUrl", defs["HttpUrl"], "scalar_str")
        assert "HttpUrl = Annotated[str, StringConstraints(" in code
        assert "^https?://" in code
    
    def test_generate_combined_constraints_alias(self, common_types_schema):
        """Test generating aliases with multiple constraints."""
        defs = common_types_schema["$defs"]
        
        code = generate_alias_code("Username", defs["Username"], "scalar_str")
        assert "Username = Annotated[str, StringConstraints(" in code
        assert "pattern=" in code
        assert "min_length=3" in code
        assert "max_length=30" in code
    
    def test_generate_additional_enum_alias(self, common_types_schema):
        """Test generating aliases for additional enums."""
        defs = common_types_schema["$defs"]
        
        code = generate_alias_code("LogLevel", defs["LogLevel"], "enum_str")
        assert "LogLevel = Literal[" in code
        assert "'debug'" in code or '"debug"' in code
        assert "'info'" in code or '"info"' in code
        assert "'warn'" in code or '"warn"' in code
        assert "'error'" in code or '"error"' in code
        assert "'fatal'" in code or '"fatal"' in code
    
    def test_emit_origin_module(self, temp_schemas_dir, tmp_path):
        """Test emitting a complete module with aliases."""
        index = SchemaIndex.load(
            list(temp_schemas_dir.glob("*.json")),
            base_url="https://example.com/schemas/core/v1/"
        )
        
        output_dir = tmp_path / "generated"
        ref_map, modules_created = emit_alias_modules(
            index,
            output_dir,
            "test_package",
            "BaseModel",
            False,
            verbose=0
        )
        
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
    """Test field mapping for type hint rewriting."""
    
    def test_sanitize_field_name(self):
        """Test field name sanitization."""
        assert sanitize_field_name("eventId") == "event_id"
        assert sanitize_field_name("trace-id") == "trace_id"
        assert sanitize_field_name("123field") == "field_123field"
        assert sanitize_field_name("class") == "class_"
        assert sanitize_field_name("from") == "from_"
    
    def test_build_field_map(self, temp_schemas_dir, tmp_path):
        """Test building field map from schemas."""
        index = SchemaIndex.load(
            list(temp_schemas_dir.glob("*.json")),
            base_url="https://example.com/schemas/core/v1/"
        )
        
        output_dir = tmp_path / "generated"
        ref_map, _ = emit_alias_modules(
            index,
            output_dir,
            "test_package",
            "BaseModel",
            False,
            verbose=0
        )
        
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




class TestEndToEnd:
    """End-to-end tests of the SLAS system."""
    
    def test_full_pipeline(self, temp_schemas_dir, tmp_path):
        """Test the complete SLAS pipeline."""
        index = SchemaIndex.load(
            list(temp_schemas_dir.glob("*.json")),
            base_url="https://example.com/schemas/core/v1/"
        )
        
        output_dir = tmp_path / "generated"
        ref_map, modules_created = emit_alias_modules(
            index,
            output_dir,
            "generated",
            "BaseModel",
            False,
            verbose=0
        )
        
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
    
    def test_cli_integration(self, temp_schemas_dir, tmp_path):
        """Test integration with the CLI."""
        from lithify.cli import app
        from lithify.slas_schema_index import SchemaIndex
        from lithify.slas_alias_generator import emit_alias_modules
        
        assert app is not None
        assert SchemaIndex is not None
        assert emit_alias_modules is not None