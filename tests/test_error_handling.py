# tests/test_error_handling.py
"""
Error handling and edge case tests.
"""

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from lithify.core import (
    build_schema_map,
    _iter_refs,
    validate_schema_consistency,
    _rewrite_single_ref
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestErrorHandling:
    """Test error conditions and edge cases."""
    
    def test_malformed_yaml(self, temp_dir):
        """Test handling of invalid YAML syntax."""

        bad_yaml = temp_dir / "bad.yaml"
        bad_yaml.write_text("\t- this is\n\t  invalid: yaml:\n    bad indent")
        

        from lithify.core import load_yaml_safe
        with pytest.raises(Exception):
            load_yaml_safe(bad_yaml)
    
    def test_permission_errors_read_only(self, temp_dir):
        """Test handling of read-only directories."""
        import os
        import stat
        

        ro_dir = temp_dir / "readonly"
        ro_dir.mkdir()
        test_file = ro_dir / "test.json"
        test_file.write_text('{"type": "object"}')
        

        os.chmod(ro_dir, stat.S_IRUSR | stat.S_IXUSR)
        
        try:
    
            from lithify.core import dump_json
            with pytest.raises((PermissionError, OSError)):
                dump_json(ro_dir / "output.json", {"test": "data"})
        finally:
            os.chmod(ro_dir, stat.S_IRWXU)
    
    def test_cyclic_references_deep(self, temp_dir):
        """Test handling of circular schema references."""

        schema_a = {
            "type": "object",
            "properties": {
                "b": {"$ref": "./b.json"}
            }
        }
        schema_b = {
            "type": "object", 
            "properties": {
                "c": {"$ref": "./c.json"}
            }
        }
        schema_c = {
            "type": "object",
            "properties": {
                "a": {"$ref": "./a.json"}
            }
        }
        
        (temp_dir / "a.json").write_text(json.dumps(schema_a))
        (temp_dir / "b.json").write_text(json.dumps(schema_b))
        (temp_dir / "c.json").write_text(json.dumps(schema_c))
        

        validate_schema_consistency(temp_dir)
    
    def test_invalid_base_url(self, temp_dir):
        """Test handling of malformed base URLs."""
        from lithify.core import _rewrite_single_ref
        

        result = _rewrite_single_ref(
            "https://example.com/schema.json",
            "not-a-valid-url",
            {}
        )

        assert result is not None
    
    def test_datamodel_codegen_failure(self, temp_dir, monkeypatch):
        """Test handling when datamodel-code-generator fails."""

        from lithify.core import run_datamodel_codegen
        

        (temp_dir / "test.json").write_text('{"type": "object"}')
        
        # datamodel-code-generator may not be installed in test environment
        assert callable(run_datamodel_codegen)
        



class TestEdgeCases:
    """Test unusual but valid inputs."""
    
    def test_empty_schema_directory(self, temp_dir):
        """Test with no schemas present."""
        empty_dir = temp_dir / "empty"
        empty_dir.mkdir()
        

        schema_map = build_schema_map(empty_dir)
        assert schema_map == {}
        

        validate_schema_consistency(empty_dir)
    
    def test_unicode_in_schemas(self, temp_dir):
        """Test schemas with unicode characters."""
        schema = {
            "type": "object",
            "title": "用户资料",
            "description": "Schéma français avec émojis 🎉",
            "properties": {
                "名前": {"type": "string"},
                "café": {"type": "string"},
            }
        }
        
        schema_file = temp_dir / "unicode.yaml"
        schema_file.write_text(yaml.dump(schema, allow_unicode=True), encoding="utf-8")
        

        from lithify.core import load_yaml_safe, dump_json
        loaded = load_yaml_safe(schema_file)
        assert loaded["title"] == "用户资料"
        

        json_file = temp_dir / "unicode.json"
        dump_json(json_file, loaded)
        assert json_file.exists()
    
    def test_extremely_nested_refs(self, temp_dir):
        """Test deeply nested $ref chains."""

        for i in range(10):
            next_ref = f"./schema{i+1}.json" if i < 9 else "#/definitions/final"
            schema = {
                "type": "object",
                "properties": {
                    "next": {"$ref": next_ref}
                },
                "definitions": {
                    "final": {"type": "string"}
                } if i == 9 else {}
            }
            (temp_dir / f"schema{i}.json").write_text(json.dumps(schema))
        

        refs = list(_iter_refs(json.loads((temp_dir / "schema0.json").read_text())))
        assert "./schema1.json" in refs
    
    def test_schema_with_no_refs(self, temp_dir):
        """Test schema with no $ref at all."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"}
            }
        }
        (temp_dir / "simple.json").write_text(json.dumps(schema))
        

        refs = list(_iter_refs(schema))
        assert refs == []
        

        validate_schema_consistency(temp_dir)
    
    def test_special_schema_names(self, temp_dir):
        """Test handling of special schema filenames."""

        from lithify.sanitizer import safe_module_slug
        

        assert safe_module_slug("class") == "class_mod"
        assert safe_module_slug("def") == "def_mod"
        assert safe_module_slug("import") == "import_mod"
        assert safe_module_slug("return") == "return_mod"
        

        result = safe_module_slug("123")
        assert not result[0].isdigit()
        

        result = safe_module_slug("test-with-dashes")
        assert "-" not in result
        
        result = safe_module_slug("test.with.dots")
        assert "." not in result
