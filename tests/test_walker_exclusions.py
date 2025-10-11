# tests/test_walker_exclusions.py

from lithify.utils import walk_schema_nodes


def test_walker_ignores_unevaluated_properties():
    schema = {
        "properties": {"name": {"type": "string"}},
        "unevaluatedProperties": {"type": "string"},
    }

    paths = [ptr for _, ptr in walk_schema_nodes(schema)]

    assert "#" in paths
    assert "#/properties/name" in paths

    assert "#/unevaluatedProperties" not in paths


def test_walker_ignores_unevaluated_items():
    schema = {
        "type": "array",
        "items": {"type": "string"},
        "unevaluatedItems": {"type": "number"},
    }

    paths = [ptr for _, ptr in walk_schema_nodes(schema)]

    assert "#" in paths
    assert "#/items" in paths

    assert "#/unevaluatedItems" not in paths


def test_walker_ignores_property_names():
    schema = {
        "properties": {"name": {"type": "string"}},
        "propertyNames": {"pattern": "^[a-z]+$"},
    }

    paths = [ptr for _, ptr in walk_schema_nodes(schema)]

    assert "#" in paths
    assert "#/properties/name" in paths

    assert "#/propertyNames" not in paths


def test_walker_includes_supported_keywords():
    schema = {
        "properties": {"name": {"type": "string"}},
        "patternProperties": {"^p_": {"type": "string"}},
        "additionalProperties": {"type": "number"},
        "items": {"type": "string"},
        "prefixItems": [{"type": "number"}],
        "contains": {"type": "boolean"},
        "allOf": [{"type": "string"}],
        "anyOf": [{"type": "string"}],
        "oneOf": [{"type": "string"}],
        "if": {"type": "string"},
        "then": {"type": "string"},
        "else": {"type": "string"},
        "not": {"type": "null"},
        "dependentSchemas": {"name": {"required": ["age"]}},
        "$defs": {"UUID": {"type": "string"}},
    }

    paths = [ptr for _, ptr in walk_schema_nodes(schema)]

    assert "#" in paths

    assert "#/properties/name" in paths
    assert "#/patternProperties/^p_" in paths
    assert "#/additionalProperties" in paths

    assert "#/items" in paths
    assert "#/prefixItems/0" in paths
    assert "#/contains" in paths

    assert "#/allOf/0" in paths
    assert "#/anyOf/0" in paths
    assert "#/oneOf/0" in paths

    assert "#/if" in paths
    assert "#/then" in paths
    assert "#/else" in paths
    assert "#/not" in paths

    assert "#/dependentSchemas/name" in paths
    assert "#/$defs/UUID" in paths


def test_walker_excludes_only_specified_keywords():
    schema = {
        "properties": {"valid": {"type": "string"}},
        "unevaluatedProperties": False,
        "unevaluatedItems": False,
        "propertyNames": {"pattern": "^[a-z]+$"},
    }

    paths = [ptr for _, ptr in walk_schema_nodes(schema)]

    assert "#" in paths
    assert "#/properties/valid" in paths

    assert "#/unevaluatedProperties" not in paths
    assert "#/unevaluatedItems" not in paths
    assert "#/propertyNames" not in paths

    assert len(paths) == 2


def test_walker_handles_nested_structures():
    schema = {
        "properties": {
            "nested": {
                "type": "object",
                "properties": {"inner": {"type": "string"}},
                "unevaluatedProperties": {"type": "number"},
            }
        }
    }

    paths = [ptr for _, ptr in walk_schema_nodes(schema)]

    assert "#" in paths
    assert "#/properties/nested" in paths
    assert "#/properties/nested/properties/inner" in paths

    assert "#/properties/nested/unevaluatedProperties" not in paths


def test_walker_handles_exclusions_in_allof():
    schema = {"allOf": [{"properties": {"name": {"type": "string"}}}, {"unevaluatedProperties": False}]}

    paths = [ptr for _, ptr in walk_schema_nodes(schema)]

    assert "#" in paths
    assert "#/allOf/0" in paths
    assert "#/allOf/0/properties/name" in paths
    assert "#/allOf/1" in paths

    assert "#/allOf/1/unevaluatedProperties" not in paths


def test_walker_exclusions_are_intentional():
    excluded_keywords = {
        "unevaluatedProperties": "Not supported by Pydantic v2",
        "unevaluatedItems": "Not supported by Pydantic v2",
        "propertyNames": "Rare, complex validation not needed for current use cases",
    }

    future_exclusions = {
        "$dynamicRef": "Advanced recursion not yet implemented",
        "$dynamicAnchor": "Advanced recursion not yet implemented",
    }

    assert len(excluded_keywords) == 3
    assert len(future_exclusions) == 2

    assert "unevaluatedProperties" in excluded_keywords
    assert "unevaluatedItems" in excluded_keywords
    assert "propertyNames" in excluded_keywords
