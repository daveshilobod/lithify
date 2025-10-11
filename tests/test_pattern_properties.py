# tests/test_pattern_properties.py


import pytest


class TestPatternValidationLogic:
    def test_validate_type_boolean_before_integer(self):
        from src.lithify.pattern_validated_base import _validate_type

        _validate_type("field", True, "boolean")
        _validate_type("field", False, "boolean")

        with pytest.raises(ValueError) as exc:
            _validate_type("field", True, "integer")
        assert "boolean" in str(exc.value).lower()

        _validate_type("field", 42, "integer")

        with pytest.raises(ValueError):
            _validate_type("field", 42, "boolean")

    def test_validate_type_number_accepts_int_and_float(self):
        from src.lithify.pattern_validated_base import _validate_type

        _validate_type("field", 42, "number")
        _validate_type("field", 42.5, "number")

        with pytest.raises(ValueError):
            _validate_type("field", True, "number")

    def test_oneof_requires_exactly_one_match(self):
        from src.lithify.pattern_validated_base import _validate_pattern_value

        schema = {"oneOf": [{"type": "string", "minLength": 5}, {"type": "integer", "minimum": 100}]}

        with pytest.raises(ValueError) as exc:
            _validate_pattern_value("field", "ab", schema)  # Too short

        error = str(exc.value).lower()
        assert "doesn't match any" in error or "no match" in error

        schema = {"oneOf": [{"type": "integer"}, {"type": "number"}]}

        with pytest.raises(ValueError) as exc:
            _validate_pattern_value("field", 42, schema)

        error = str(exc.value).lower()
        assert "matches 2" in error or "multiple" in error
        assert "exactly 1" in error or "exactly one" in error

        schema = {"oneOf": [{"type": "string"}, {"type": "integer"}]}

        _validate_pattern_value("field", "test", schema)
        _validate_pattern_value("field", 42, schema)

    def test_string_constraints(self):
        from src.lithify.pattern_validated_base import _validate_against_schema

        schema = {"type": "string", "minLength": 3, "maxLength": 10, "pattern": "^[a-z]+$"}

        _validate_against_schema("field", "abc", schema)
        _validate_against_schema("field", "abcdefghij", schema)

        with pytest.raises(ValueError, match="minLength"):
            _validate_against_schema("field", "ab", schema)

        with pytest.raises(ValueError, match="maxLength"):
            _validate_against_schema("field", "abcdefghijk", schema)

        with pytest.raises(ValueError, match="pattern"):
            _validate_against_schema("field", "abc123", schema)

    def test_numeric_constraints(self):
        from src.lithify.pattern_validated_base import _validate_against_schema

        schema = {"type": "number", "minimum": 0, "maximum": 100, "multipleOf": 5}

        _validate_against_schema("field", 50, schema)
        _validate_against_schema("field", 0, schema)
        _validate_against_schema("field", 100, schema)

        with pytest.raises(ValueError, match="minimum"):
            _validate_against_schema("field", -1, schema)

        with pytest.raises(ValueError, match="maximum"):
            _validate_against_schema("field", 101, schema)

        with pytest.raises(ValueError, match="multiple"):
            _validate_against_schema("field", 42, schema)

    def test_array_validation(self):
        from src.lithify.pattern_validated_base import _validate_against_schema

        schema = {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string", "minLength": 2}}

        _validate_against_schema("field", ["ab", "cd"], schema)

        with pytest.raises(ValueError, match="minItems"):
            _validate_against_schema("field", [], schema)

        with pytest.raises(ValueError, match="maxItems"):
            _validate_against_schema("field", ["a", "b", "c", "d"], schema)

        with pytest.raises(ValueError, match="minLength"):
            _validate_against_schema("field", ["ab", "x"], schema)


class TestPatternDetection:
    def test_extract_patterns_validates_regex(self):
        from src.lithify.pattern_properties import _extract_patterns

        result = _extract_patterns({"^test.*$": {"type": "string"}})
        assert result == {"^test.*$": {"type": "string"}}

        with pytest.raises(ValueError, match="Invalid regex"):
            _extract_patterns({"^test[": {"type": "string"}})

    def test_detect_uses_class_name_override(self):
        pass


class TestPatternCodeGeneration:
    def test_generate_pattern_class_code(self):
        from src.lithify.pattern_properties import generate_pattern_class_code

        patterns = {"^p_.*$": {"type": "string"}, "^meta_.*$": {"oneOf": [{"type": "string"}, {"type": "number"}]}}

        code = generate_pattern_class_code("TestModel", patterns)

        assert "__pattern_properties__" in code
        assert "re.compile" in code
        assert "^p_.*$" in code
        assert "^meta_.*$" in code

        import ast

        ast.parse(code)

    def test_pattern_escaping(self):
        from src.lithify.pattern_properties import generate_pattern_class_code

        patterns = {r"^\d+$": {"type": "string"}}

        code = generate_pattern_class_code("TestModel", patterns)

        assert r"\\d" in code


class TestSchemaDescription:
    def test_describe_simple_types(self):
        from src.lithify.pattern_validated_base import _describe_schema

        assert _describe_schema({"type": "string"}) == "string"
        assert _describe_schema({"type": "integer"}) == "integer"
        assert _describe_schema({"type": "boolean"}) == "boolean"

    def test_describe_with_constraints(self):
        from src.lithify.pattern_validated_base import _describe_schema

        desc = _describe_schema({"type": "string", "pattern": "^[a-z]{10,20}$"})
        assert "string" in desc
        assert "pattern" in desc

        desc = _describe_schema({"type": "number", "minimum": 0, "maximum": 100})
        assert "number" in desc
        assert "range" in desc or "0" in desc

    def test_describe_enum(self):
        from src.lithify.pattern_validated_base import _describe_schema

        desc = _describe_schema({"enum": ["a", "b", "c"]})
        assert "enum" in desc


class TestASTRewriting:
    def test_rewriter_changes_inheritance(self):
        import ast

        from src.lithify.pattern_properties import PatternPropertyInfo
        from src.lithify.pattern_properties_rewriter import PatternPropertiesRewriter

        source = """
class TestModel(FrozenModel):
    model_config = ConfigDict(extra="forbid")
    id: str
"""
        tree = ast.parse(source)

        pattern_info = {
            "TestModel": PatternPropertyInfo(model_name="TestModel", patterns={"^p_.*$": {"type": "string"}})
        }

        rewriter = PatternPropertiesRewriter(pattern_info)
        new_tree = rewriter.visit(tree)

        assert rewriter.modified

        class_def = new_tree.body[0]
        assert isinstance(class_def, ast.ClassDef)

        assert class_def.bases[0].id == "PatternValidatedModel"

    def test_rewriter_changes_config(self):
        import ast

        from src.lithify.pattern_properties import PatternPropertyInfo
        from src.lithify.pattern_properties_rewriter import PatternPropertiesRewriter

        source = """
class TestModel(FrozenModel):
    model_config = ConfigDict(extra="forbid")
"""
        tree = ast.parse(source)

        pattern_info = {
            "TestModel": PatternPropertyInfo(model_name="TestModel", patterns={"^p_.*$": {"type": "string"}})
        }

        rewriter = PatternPropertiesRewriter(pattern_info)
        new_tree = rewriter.visit(tree)

        new_source = ast.unparse(new_tree)
        assert 'extra="allow"' in new_source or "extra='allow'" in new_source


class TestErrorMessages:
    def test_no_pattern_match_error_message(self):
        pytest.skip("Requires generated FrozenModel")

    def test_oneof_error_shows_branches(self):
        from src.lithify.pattern_validated_base import _validate_pattern_value

        schema = {"oneOf": [{"type": "string", "minLength": 5}, {"type": "integer"}]}

        with pytest.raises(ValueError) as exc:
            _validate_pattern_value("field", "ab", schema)

        error = str(exc.value)
        assert "string" in error.lower()
        assert "integer" in error.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
