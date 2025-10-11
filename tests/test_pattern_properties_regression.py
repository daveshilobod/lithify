# tests/test_pattern_properties_regression.py


import json
from pathlib import Path

import pytest
from pydantic import ValidationError


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "pattern_properties"


class TestPatternPropertiesFullPipeline:
    def test_full_pipeline_with_pattern_properties(self, tmp_path, fixtures_dir):
        from lithify.enums import FormatChoice, Mutability, OutputMode
        from lithify.orchestrator import GenerationConfig, SimpleReporter, run_generation

        schemas_dir = fixtures_dir
        models_out = tmp_path / "models"

        cfg = GenerationConfig(
            schemas=schemas_dir,
            json_out=None,
            models_out=models_out,
            package_name="test_models",
            exclude=None,
            mutability=Mutability.deep_frozen,
            lenient_allof=False,
            base_url=None,
            block_remote_refs=False,
            custom_ref_resolver=None,
            immutable_hints=False,
            use_frozendict=False,
            from_attributes=False,
            partial=False,
            clean_first=False,
            check=False,
            verbose=2,
            output_mode=OutputMode.clean,
            fmt=FormatChoice.none,
            no_rewrite=False,
            dry_run=False,
        )

        reporter = SimpleReporter()

        result = run_generation(cfg, reporter)

        package_dir = result.package_dir
        assert package_dir.exists()

        pattern_base = package_dir / "pattern_validated_base.py"
        assert pattern_base.exists(), "PatternValidatedModel base class not generated"

        flexible_data_file = package_dir / "flexible_data_v1.py"
        assert flexible_data_file.exists(), "FlexibleData model not generated"

        content = flexible_data_file.read_text()

        assert "PatternValidatedModel" in content, "Model doesn't inherit from PatternValidatedModel"

        assert "__pattern_properties__" in content, "__pattern_properties__ not added to model"
        assert "re.compile" in content, "Pattern not compiled in __pattern_properties__"

        assert 'extra="allow"' in content or "extra='allow'" in content, "Config not changed to extra='allow'"

        assert "import re" in content, "re module not imported"

        import sys

        sys.path.insert(0, str(models_out))

        from test_models.flexible_data_v1 import FlexibleData

        obj = FlexibleData(
            id="test-123",
            timestamp="1234567890",
            payload={},
            meta_version="1.0.0",
            meta_count=42,
            meta_enabled=True,
            meta_tags=["tag1", "tag2"],
        )

        assert obj.id == "test-123"
        assert obj.meta_version == "1.0.0"
        assert obj.meta_count == 42
        assert obj.meta_enabled is True
        assert obj.meta_tags == ("tag1", "tag2")

        with pytest.raises(ValidationError) as exc_info:
            FlexibleData(
                id="test-123",
                timestamp="1234567890",
                payload={},
                custom_field="invalid",
            )

        error = str(exc_info.value)
        assert "custom_field" in error
        assert "does not match" in error.lower() or "forbidden" in error.lower()

    def test_no_regression_on_models_without_pattern_properties(self, tmp_path):
        from lithify.enums import FormatChoice, Mutability, OutputMode
        from lithify.orchestrator import GenerationConfig, SimpleReporter, run_generation

        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        (schemas_dir / "simple.yaml").write_text(
            "# Simple test schema\n"
            + "title: SimpleModel\n"
            + "type: object\n"
            + "properties:\n"
            + "  id:\n"
            + "    type: string\n"
            + "  count:\n"
            + "    type: integer\n"
            + "required:\n"
            + "  - id\n"
            + "additionalProperties: false\n"
        )

        models_out = tmp_path / "models"

        cfg = GenerationConfig(
            schemas=schemas_dir,
            json_out=None,
            models_out=models_out,
            package_name="simple_models",
            exclude=None,
            mutability=Mutability.frozen,
            lenient_allof=False,
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
        )

        reporter = SimpleReporter()
        result = run_generation(cfg, reporter)

        package_dir = result.package_dir
        simple_file = package_dir / "simple.py"

        assert simple_file.exists()
        content = simple_file.read_text()

        assert "PatternValidatedModel" not in content, "PatternValidatedModel incorrectly added"
        assert "__pattern_properties__" not in content, "__pattern_properties__ incorrectly added"

        assert "FrozenBase" in content, "Normal base class missing"
        assert 'extra="forbid"' in content or "extra='forbid'" in content, "Config incorrectly changed"

    def test_multiple_patterns_and_semantics(self, tmp_path, fixtures_dir):
        from lithify.enums import FormatChoice, Mutability, OutputMode
        from lithify.orchestrator import GenerationConfig, SimpleReporter, run_generation

        schemas_dir = fixtures_dir
        models_out = tmp_path / "models"

        cfg = GenerationConfig(
            schemas=schemas_dir,
            json_out=None,
            models_out=models_out,
            package_name="multi_models",
            exclude=None,
            mutability=Mutability.deep_frozen,
            lenient_allof=False,
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
        )

        reporter = SimpleReporter()
        _ = run_generation(cfg, reporter)

        import sys

        sys.path.insert(0, str(models_out))

        from multi_models.multi_pattern_v1 import MultiPattern

        obj1 = MultiPattern(
            id="test",
            x_normal="value",
        )
        assert obj1.x_normal == "value"

        obj2 = MultiPattern(
            id="test",
            x_special_field="short",
        )
        assert obj2.x_special_field == "short"

        with pytest.raises(ValidationError) as exc_info:
            MultiPattern(
                id="test",
                x_special_field="a" * 51,
            )

        error = str(exc_info.value)
        assert "x_special_field" in error
        assert "50" in error or "length" in error.lower()


class TestPatternPropertiesDetection:
    def test_detects_pattern_properties_in_root(self, tmp_path):
        """Detect patternProperties at root level."""
        from src.lithify.pattern_properties import detect_all_pattern_properties
        from src.lithify.slas_schema_index import SchemaIndex

        json_dir = tmp_path / "schemas"
        json_dir.mkdir()

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "TestModel",
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "patternProperties": {"^p_.*$": {"type": "string"}},
        }

        (json_dir / "test.json").write_text(json.dumps(schema))

        index = SchemaIndex.load(list(json_dir.glob("*.json")), None)
        result = detect_all_pattern_properties(json_dir, index)

        assert "TestModel" in result
        assert "^p_.*$" in result["TestModel"].patterns

    def test_uses_x_python_class_name_override(self, tmp_path):
        from src.lithify.pattern_properties import detect_all_pattern_properties
        from src.lithify.slas_schema_index import SchemaIndex

        json_dir = tmp_path / "schemas"
        json_dir.mkdir()

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "MyModelV1",
            "x-python-class-name": "MyModel",
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "patternProperties": {"^meta_.*$": {"type": "string"}},
        }

        (json_dir / "test.json").write_text(json.dumps(schema))

        index = SchemaIndex.load(list(json_dir.glob("*.json")), None)
        result = detect_all_pattern_properties(json_dir, index)

        assert "MyModel" in result
        assert "MyModelV1" not in result


class TestPatternValidationSemantics:
    def test_oneof_requires_exactly_one_match(self):
        from src.lithify.pattern_validated_base import _validate_pattern_value

        schema = {"oneOf": [{"type": "integer"}, {"type": "number"}]}

        with pytest.raises(ValueError) as exc_info:
            _validate_pattern_value("test_field", 42, schema)

        error = str(exc_info.value).lower()
        assert "matches" in error and ("2" in error or "multiple" in error)
        assert "exactly 1" in error or "exactly one" in error

    def test_boolean_checked_before_integer(self):
        from src.lithify.pattern_validated_base import _validate_type

        with pytest.raises(ValueError) as exc_info:
            _validate_type("test_field", True, "integer")

        error = str(exc_info.value).lower()
        assert "boolean" in error

        _validate_type("test_field", True, "boolean")

        with pytest.raises(ValueError):
            _validate_type("test_field", 42, "boolean")

    def test_pattern_matched_fields_are_deep_frozen(self, tmp_path):
        json_dir = tmp_path / "schemas"
        json_dir.mkdir()

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "TestFreeze",
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "patternProperties": {
                "^data_.*$": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}
            },
            "additionalProperties": False,
        }

        import yaml

        (json_dir / "test.yaml").write_text(yaml.dump(schema))

        from lithify.enums import FormatChoice, Mutability, OutputMode
        from lithify.orchestrator import GenerationConfig, SimpleReporter, run_generation

        models_out = tmp_path / "models"

        cfg = GenerationConfig(
            schemas=json_dir,
            json_out=None,
            models_out=models_out,
            package_name="freeze_test",
            exclude=None,
            mutability=Mutability.deep_frozen,
            lenient_allof=False,
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
        )

        reporter = SimpleReporter()
        _ = run_generation(cfg, reporter)

        import sys

        sys.path.insert(0, str(models_out))

        from freeze_test.test import TestFreeze

        obj = TestFreeze(id="test", data_items=["a", "b", "c"])

        assert isinstance(obj.data_items, tuple), "Pattern field not frozen to tuple"
        assert obj.data_items == ("a", "b", "c")

        with pytest.raises((AttributeError, TypeError)):
            obj.data_items.append("d")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
