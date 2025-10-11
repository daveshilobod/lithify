# src/lithify/slas_classifier.py
"""
Shape classification for SLAS - identifies scalar types and unions.
"""

from __future__ import annotations

from typing import Any

ALLOWED_STRING_KEYS = {
    "type",
    "title",
    "description",
    "pattern",
    "format",
    "minLength",
    "maxLength",
    "deprecated",
    "$id",
    "$anchor",
    "default",
}

ALLOWED_NUMBER_KEYS = {
    "type",
    "title",
    "description",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "deprecated",
    "$id",
    "$anchor",
    "default",
}


def is_scalar_str(schema: dict) -> bool:
    if not isinstance(schema, dict):
        return False

    if schema.get("type") != "string":
        return False

    denied_keys = {"properties", "items", "oneOf", "anyOf", "allOf"}
    if denied_keys & set(schema.keys()):
        return False

    constraints = {"pattern", "format", "minLength", "maxLength"}
    return bool(constraints & set(schema.keys()))


def is_scalar_number(schema: dict) -> bool:
    if not isinstance(schema, dict):
        return False

    type_val = schema.get("type")
    if type_val not in {"number", "integer"}:
        return False

    denied_keys = {"properties", "items", "oneOf", "anyOf", "allOf"}
    if denied_keys & set(schema.keys()):
        return False

    # Must have at least one constraint
    constraints = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
    return bool(constraints & set(schema.keys()))


def is_enum_str(schema: dict) -> bool:
    if not isinstance(schema, dict):
        return False

    if "enum" in schema:
        values = schema["enum"]
        if isinstance(values, list) and all(isinstance(v, str) for v in values):
            return True

    # const should have been normalized to  enum in earlier processing
    if "const" in schema and isinstance(schema["const"], str):
        return True

    return False


def is_union_of_scalar_str(schema: dict) -> bool:
    if not isinstance(schema, dict):
        return False

    if "oneOf" not in schema:
        return False

    branches = schema["oneOf"]
    if not isinstance(branches, list) or len(branches) < 2:
        return False

    for branch in branches:
        if not is_scalar_str(branch):
            return False

    return any("pattern" in b for b in branches)


def union_scalar_pattern(branches: list[dict]) -> str | None:
    patterns = []

    for branch in branches:
        if not is_scalar_str(branch):
            return None

        if "pattern" in branch:
            # Strip anchors - we'll re-add them in the union pattern
            pat = branch["pattern"]
            if pat.startswith("^"):
                pat = pat[1:]
            if pat.endswith("$"):
                pat = pat[:-1]
            patterns.append(pat)
        else:
            return None

    if not patterns:
        return None

    return r"^(?:" + "|".join(patterns) + ")$"


def classify_shape(schema: dict) -> str:
    """Classify the shape of a schema node.

    Returns one of:
    - "scalar_str": Pure string with constraints
    - "scalar_num": Pure number/integer
    - "enum_str": String enum or const
    - "union_scalar_str": Union of scalar strings
    - "object": Object type
    - "array": Array type
    - "map": Object with additionalProperties
    - "mixed": Complex or mixed type
    - "unknown": Can't determine
    """
    if not isinstance(schema, dict):
        return "unknown"

    if is_scalar_str(schema):
        return "scalar_str"

    if is_scalar_number(schema):
        return "scalar_number"

    if is_enum_str(schema):
        return "enum_str"

    if is_union_of_scalar_str(schema):
        return "union_scalar_str"

    if schema.get("type") == "object":
        if "additionalProperties" in schema and "properties" not in schema:
            return "map"
        return "object"

    if schema.get("type") == "array":
        return "array"

    if any(key in schema for key in ["allOf", "anyOf", "oneOf"]):
        return "mixed"

    if "$ref" in schema:
        return "ref"

    return "unknown"


def get_string_constraints(schema: dict) -> dict[str, Any]:
    """Extract string constraints from a schema for Pydantic field generation."""
    constraints = {}

    if "pattern" in schema:
        constraints["pattern"] = schema["pattern"]

    if "minLength" in schema:
        constraints["min_length"] = schema["minLength"]

    if "maxLength" in schema:
        constraints["max_length"] = schema["maxLength"]

    if "format" in schema:
        if schema["format"] == "date-time":
            # RFC 3339 pattern when no explicit pattern provided
            if "pattern" not in constraints:
                constraints["pattern"] = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}" r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
        elif schema["format"] == "email":
            if "pattern" not in constraints:
                constraints["pattern"] = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        elif schema["format"] == "uri" or schema["format"] == "url":
            if "pattern" not in constraints:
                constraints["pattern"] = r"^[a-zA-Z][a-zA-Z0-9+.-]*:"

    return constraints


def get_number_constraints(schema: dict) -> dict[str, Any]:
    """Extract number constraints from a schema for Pydantic field generation."""
    constraints = {}

    if "minimum" in schema:
        constraints["ge"] = schema["minimum"]

    if "maximum" in schema:
        constraints["le"] = schema["maximum"]

    if "exclusiveMinimum" in schema:
        constraints["gt"] = schema["exclusiveMinimum"]

    if "exclusiveMaximum" in schema:
        constraints["lt"] = schema["exclusiveMaximum"]

    if "multipleOf" in schema:
        constraints["multiple_of"] = schema["multipleOf"]

    return constraints
