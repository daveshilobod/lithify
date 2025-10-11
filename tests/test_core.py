# tests/test_core.py

import json
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from lithify.core import (
    _iter_refs,
    _rewrite_single_ref,
    build_schema_map,
    mirror_yaml_to_json,
    rewrite_remote_refs,
    validate_schema_consistency,
)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestCore:
    def test_build_schema_map(self, temp_dir):
        (temp_dir / "01_user.yaml").touch()
        (temp_dir / "02_audit.yaml").touch()
        (temp_dir / "test.yml").touch()

        schema_map = build_schema_map(temp_dir)

        assert "user.schema.json" in schema_map
        assert "user.json" in schema_map
        assert "./user.schema.json" in schema_map
        assert "./user.json" in schema_map
        assert schema_map["user.schema.json"] == "01_user.json"
        assert schema_map["user.json"] == "01_user.json"

        assert "test.schema.json" in schema_map
        assert "test.json" in schema_map

    def test_rewrite_single_ref(self):
        schema_map = {"user.schema.json": "01_user.json", "user.json": "01_user.json"}
        base_url = "https://example.com/schemas/"

        ref = "https://example.com/schemas/user.schema.json"
        result = _rewrite_single_ref(ref, base_url, schema_map)
        assert result == "./01_user.json"

        ref = "https://example.com/schemas/user.schema.json#/definitions/Name"
        result = _rewrite_single_ref(ref, base_url, schema_map)
        assert result == "./01_user.json#/definitions/Name"

        ref = "./user.schema.json"
        result = _rewrite_single_ref(ref, None, schema_map)
        assert result == "./01_user.json"

        ref = "#/definitions/Something"
        result = _rewrite_single_ref(ref, None, schema_map)
        assert result == "#/definitions/Something"

        ref = "./unknown.schema.json"
        result = _rewrite_single_ref(ref, None, {})
        assert result == "./unknown.json"

    def test_rewrite_remote_refs_nested(self):
        schema_map = {"user.json": "01_user.json"}
        base_url = "https://example.com/"

        data = {
            "allOf": [
                {"$ref": "https://example.com/user.json"},
                {"properties": {"nested": {"items": {"$ref": "https://example.com/user.json#/definitions/Name"}}}},
            ],
            "definitions": {"Local": {"$ref": "#/definitions/Other"}},
        }

        result = rewrite_remote_refs(data, schema_map, base_url)

        assert result["allOf"][0]["$ref"] == "./01_user.json"
        assert result["allOf"][1]["properties"]["nested"]["items"]["$ref"] == "./01_user.json#/definitions/Name"
        assert result["definitions"]["Local"]["$ref"] == "#/definitions/Other"

    def test_iter_refs(self):
        schema = {
            "$ref": "./top.json",
            "properties": {
                "user": {"$ref": "./user.json"},
                "nested": {"allOf": [{"$ref": "#/definitions/Local"}, {"$ref": "./other.json"}]},
                "no_ref": {"type": "string"},
            },
            "items": {"$ref": "./item.json"},
        }

        refs = list(_iter_refs(schema))

        assert "./top.json" in refs
        assert "./user.json" in refs
        assert "#/definitions/Local" in refs
        assert "./other.json" in refs
        assert "./item.json" in refs
        assert len(refs) == 5

    def test_validate_schema_consistency_missing_ref(self, temp_dir):
        schema = {"properties": {"user": {"$ref": "./missing.json"}}}
        (temp_dir / "test.json").write_text(json.dumps(schema))

        with pytest.raises((SystemExit, Exception)):
            validate_schema_consistency(temp_dir)

    def test_validate_schema_consistency_circular_ok(self, temp_dir):
        schema1 = {"properties": {"other": {"$ref": "./schema2.json"}}}
        schema2 = {"properties": {"back": {"$ref": "./schema1.json"}}}

        (temp_dir / "schema1.json").write_text(json.dumps(schema1))
        (temp_dir / "schema2.json").write_text(json.dumps(schema2))

        validate_schema_consistency(temp_dir)

    def test_validate_schema_consistency_out_of_tree(self, temp_dir):
        schema = {"properties": {"user": {"$ref": "../../outside.json"}}}
        (temp_dir / "test.json").write_text(json.dumps(schema))

        outside = temp_dir.parent / "outside.json"
        outside.write_text(json.dumps({"type": "object"}))

        try:
            with pytest.raises((SystemExit, Exception)):
                validate_schema_consistency(temp_dir)
        finally:
            if outside.exists():
                outside.unlink()

    def test_validate_schema_consistency_remote_refs(self, temp_dir):
        schema = {"properties": {"user": {"$ref": "https://example.com/user.json"}}}
        (temp_dir / "test.json").write_text(json.dumps(schema))

        validate_schema_consistency(temp_dir, block_remote_refs=False)

        with pytest.raises((SystemExit, Exception)):
            validate_schema_consistency(temp_dir, block_remote_refs=True)

    def test_validate_schema_consistency_invalid_json(self, temp_dir):
        (temp_dir / "bad.json").write_text("{invalid json}")

        with pytest.raises((SystemExit, Exception)):
            validate_schema_consistency(temp_dir)


class TestCoreModuleCoverage:
    def test_mirror_yaml_to_json_nested(self, temp_dir):
        (temp_dir / "schemas" / "v1").mkdir(parents=True)
        (temp_dir / "schemas" / "v2").mkdir(parents=True)

        schema_v1 = {"version": 1, "type": "object"}
        schema_v2 = {"version": 2, "type": "object"}

        (temp_dir / "schemas" / "v1" / "user.yaml").write_text(yaml.dump(schema_v1))
        (temp_dir / "schemas" / "v2" / "user.yaml").write_text(yaml.dump(schema_v2))

        json_out = temp_dir / "json"

        base_url = None

        mirror_yaml_to_json(temp_dir / "schemas", json_out, base_url)

        assert (json_out / "v1" / "user.json").exists()
        assert (json_out / "v2" / "user.json").exists()

        v1_data = json.loads((json_out / "v1" / "user.json").read_text())
        assert v1_data["version"] == 1

    def test_mirror_with_yml_extension(self, temp_dir):
        schema = {"type": "string"}
        (temp_dir / "test.yml").write_text(yaml.dump(schema))

        json_out = temp_dir / "json"

        base_url = None

        mirror_yaml_to_json(temp_dir, json_out, base_url)

        assert (json_out / "test.json").exists()

    def test_rewrite_refs_all_patterns(self):
        from lithify.core import _rewrite_single_ref

        schema_map = {
            "user.schema.json": "01_user.json",
            "user.json": "01_user.json",
            "./user.schema.json": "01_user.json",
            "./user.json": "01_user.json",
        }
        base_url = "https://example.com/schemas/"

        test_cases = [
            # (input, expected)
            ("https://example.com/schemas/user.json", "./01_user.json"),
            ("https://example.com/schemas/user.schema.json", "./01_user.json"),
            ("./user.json", "./01_user.json"),
            ("./user.schema.json", "./01_user.json"),
            ("user.json", "./01_user.json"),
            ("#/definitions/User", "#/definitions/User"),
        ]

        for input_ref, expected in test_cases[:-1]:
            result = _rewrite_single_ref(input_ref, base_url, schema_map)
            assert result == expected, f"Failed for {input_ref}: got {result}, expected {expected}"


class TestConstToEnumRewriting:
    def test_fractional_float_const_is_rewritten(self):
        import copy

        from lithify.core import rewrite_const_to_enum

        schema = {"type": "number", "const": 0.3}
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert "const" not in out
        assert "enum" not in out
        assert out["minimum"] == 0.3
        assert out["maximum"] == 0.3

    def test_integer_const_is_rewritten(self):
        import copy

        from lithify.core import rewrite_const_to_enum

        schema = {"type": "integer", "const": 2}
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert "const" not in out
        assert out["enum"] == [2]
        assert out["type"] == "integer"

    def test_whole_number_float_const_is_rewritten(self):
        import copy

        from lithify.core import rewrite_const_to_enum

        schema = {"type": "number", "const": 1.0}
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert "const" not in out
        assert "enum" not in out
        assert out["minimum"] == 1.0
        assert out["maximum"] == 1.0
        assert out["type"] == "number"

    def test_nested_in_oneof_and_properties(self):
        import copy

        from lithify.core import rewrite_const_to_enum

        schema = {
            "oneOf": [
                {"type": "number", "const": 0.25},
                {"type": "object", "properties": {"x": {"const": 0.5}}},
            ]
        }
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert "const" not in out["oneOf"][0]
        assert "enum" not in out["oneOf"][0]
        assert out["oneOf"][0]["minimum"] == 0.25
        assert out["oneOf"][0]["maximum"] == 0.25
        assert "const" not in out["oneOf"][1]["properties"]["x"]
        assert "enum" not in out["oneOf"][1]["properties"]["x"]
        assert out["oneOf"][1]["properties"]["x"]["minimum"] == 0.5
        assert out["oneOf"][1]["properties"]["x"]["maximum"] == 0.5

    def test_decimal_support(self):
        import copy
        from decimal import Decimal

        from lithify.core import rewrite_const_to_enum

        schema = {"type": "number", "const": Decimal("0.10")}
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert out["enum"] == [Decimal("0.10")]
        assert "const" not in out

    def test_string_const_is_rewritten(self):
        import copy

        from lithify.core import rewrite_const_to_enum

        schema = {"type": "string", "const": "hello"}
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert "const" not in out
        assert out["enum"] == ["hello"]
        assert out["type"] == "string"

    def test_boolean_const_is_rewritten(self):
        import copy

        from lithify.core import rewrite_const_to_enum

        schema = {"type": "boolean", "const": True}
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert "const" not in out
        assert out["enum"] == [True]
        assert out["type"] == "boolean"

    def test_null_const_is_rewritten(self):
        import copy

        from lithify.core import rewrite_const_to_enum

        schema = {"type": "null", "const": None}
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert "const" not in out
        assert out["enum"] == [None]
        assert out["type"] == "null"

    def test_const_with_existing_enum(self):
        import copy

        from lithify.core import rewrite_const_to_enum

        schema = {"type": "string", "const": "c", "enum": ["a", "b"]}
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert "const" not in out
        assert set(out["enum"]) == {"a", "b", "c"}
        assert out["type"] == "string"

    def test_const_without_type_infers_type(self):
        import copy

        from lithify.core import rewrite_const_to_enum

        schema = {"const": "hello"}
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert out["enum"] == ["hello"]
        assert out["type"] == "string"

        schema = {"const": 3.14}
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert "enum" not in out
        assert out["minimum"] == 3.14
        assert out["maximum"] == 3.14
        assert out["type"] == "number"

        schema = {"const": False}
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert out["enum"] == [False]
        assert out["type"] == "boolean"

    def test_scientific_notation_preserved(self):
        import copy

        from lithify.core import rewrite_const_to_enum

        schema = {"type": "number", "const": 1.23e-4}
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert "const" not in out
        assert "enum" not in out
        assert out["minimum"] == 1.23e-4
        assert out["maximum"] == 1.23e-4
        assert abs(out["minimum"] - 0.000123) < 1e-10

    def test_deep_nested_in_definitions(self):
        import copy

        from lithify.core import rewrite_const_to_enum

        schema = {
            "$defs": {
                "Weight": {"type": "number", "const": 0.3},
                "Count": {"type": "integer", "const": 5},
                "Label": {"type": "string", "const": "test"},
            },
            "properties": {"weight": {"$ref": "#/$defs/Weight"}},
        }
        out = rewrite_const_to_enum(copy.deepcopy(schema))
        assert "enum" not in out["$defs"]["Weight"]
        assert out["$defs"]["Weight"]["minimum"] == 0.3
        assert out["$defs"]["Weight"]["maximum"] == 0.3
        assert "const" not in out["$defs"]["Weight"]
        assert out["$defs"]["Count"]["enum"] == [5]
        assert "const" not in out["$defs"]["Count"]
        assert out["$defs"]["Label"]["enum"] == ["test"]
        assert "const" not in out["$defs"]["Label"]


class TestDCGFlags:
    def test_dcg_flags_include_literal_generation(self, temp_dir, monkeypatch):
        from lithify.core import run_datamodel_codegen

        commands_run = []

        def mock_run(cmd, *args, **kwargs):
            commands_run.append(cmd)

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        monkeypatch.setattr(subprocess, "run", mock_run)

        json_dir = temp_dir / "json"
        json_dir.mkdir()
        (json_dir / "test.json").write_text('{"type": "object"}')

        try:
            run_datamodel_codegen(json_dir, temp_dir, "test_pkg")
        except Exception:
            pass

        assert len(commands_run) == 1
        cmd = commands_run[0]
        assert "--enum-field-as-literal" in cmd
        assert "one" in cmd[cmd.index("--enum-field-as-literal") + 1]
        assert "--use-title-as-name" in cmd
        assert "--reuse-model" in cmd


class TestExcludeFilter:
    def test_exclude_directories(self, temp_dir):
        (temp_dir / "yaml" / "core").mkdir(parents=True)
        (temp_dir / "yaml" / "ai").mkdir(parents=True)
        (temp_dir / "yaml" / "other").mkdir(parents=True)

        (temp_dir / "yaml" / "core" / "schema.yaml").write_text(yaml.dump({"type": "object"}))
        (temp_dir / "yaml" / "ai" / "schema.yaml").write_text(yaml.dump({"type": "object"}))
        (temp_dir / "yaml" / "other" / "schema.yaml").write_text(yaml.dump({"type": "object"}))

        json_out = temp_dir / "json"

        written = mirror_yaml_to_json(temp_dir / "yaml", json_out, base_url=None, exclude=["ai"], verbose=0)

        written_names = [p.name for p in written]
        assert "schema.json" in written_names
        assert len(written) == 2

        assert not (json_out / "ai").exists()

    def test_exclude_nested_directories(self, temp_dir):
        (temp_dir / "yaml" / "payloads" / "ai" / "models").mkdir(parents=True)
        (temp_dir / "yaml" / "payloads" / "other").mkdir(parents=True)

        (temp_dir / "yaml" / "payloads" / "ai" / "models" / "schema.yaml").write_text(yaml.dump({"type": "object"}))
        (temp_dir / "yaml" / "payloads" / "other" / "schema.yaml").write_text(yaml.dump({"type": "object"}))

        json_out = temp_dir / "json"

        written = mirror_yaml_to_json(temp_dir / "yaml", json_out, base_url=None, exclude=["ai"], verbose=0)

        assert len(written) == 1
        assert not (json_out / "payloads" / "ai").exists()
        assert (json_out / "payloads" / "other").exists()


class TestCustomRefResolver:
    def test_custom_ref_resolution(self, temp_dir):
        from lithify import resolver as resolver_module

        resolver_module._resolver_cache = None

        resolver_file = temp_dir / "resolver.py"
        resolver_file.write_text("""
from pathlib import Path

def resolve_ref(ref: str) -> Path:
    if ref == "urn:test:common":
        return Path(__file__).parent / "common.json"
    raise KeyError(f"Unknown ref: {ref}")
""")

        common_schema = {"type": "string", "minLength": 1}
        (temp_dir / "common.json").write_text(json.dumps(common_schema))

        schema_with_ref = {"type": "object", "properties": {"name": {"$ref": "urn:test:common"}}}

        yaml_dir = temp_dir / "yaml"
        yaml_dir.mkdir()
        (yaml_dir / "user.yaml").write_text(yaml.dump(schema_with_ref))

        json_out = temp_dir / "json"

        from lithify.resolver import load_resolver

        resolver = load_resolver(f"{resolver_file}:resolve_ref")

        _written = mirror_yaml_to_json(yaml_dir, json_out, base_url=None, custom_ref_resolver=resolver, verbose=0)

        user_json = json.loads((json_out / "user.json").read_text())
        ref = user_json["properties"]["name"]["$ref"]

        assert not ref.startswith("urn:")
        assert "common.json" in ref

    def test_custom_ref_with_json_pointer(self, temp_dir):
        from lithify import resolver as resolver_module

        resolver_module._resolver_cache = None

        resolver_file = temp_dir / "resolver.py"
        resolver_file.write_text("""
from pathlib import Path

def resolve_ref(ref: str) -> Path:
    if ref == "urn:test:defs":
        return Path(__file__).parent / "defs.json"
    raise KeyError(f"Unknown ref: {ref}")
""")

        defs_schema = {"$defs": {"Name": {"type": "string"}}}
        (temp_dir / "defs.json").write_text(json.dumps(defs_schema))

        schema_with_pointer = {"type": "object", "properties": {"name": {"$ref": "urn:test:defs#/$defs/Name"}}}

        yaml_dir = temp_dir / "yaml"
        yaml_dir.mkdir()
        (yaml_dir / "schema.yaml").write_text(yaml.dump(schema_with_pointer))

        json_out = temp_dir / "json"

        from lithify.resolver import load_resolver

        resolver = load_resolver(f"{resolver_file}:resolve_ref")

        _written = mirror_yaml_to_json(yaml_dir, json_out, base_url=None, custom_ref_resolver=resolver, verbose=0)

        result = json.loads((json_out / "schema.json").read_text())
        ref = result["properties"]["name"]["$ref"]

        assert "#/$defs/Name" in ref
        assert "defs.json" in ref
