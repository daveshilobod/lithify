# src/lithify/pattern_properties.py
"""Detection and code generation for patternProperties."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .slas_schema_index import SchemaIndex
from .utils import walk_schema_nodes


@dataclass
class PatternPropertyInfo:
    """Info about a model's patternProperties."""

    model_name: str  # Uses x-python-class-name if present
    patterns: dict[str, dict]


def detect_all_pattern_properties(json_dir: Path, index: SchemaIndex) -> dict[str, PatternPropertyInfo]:
    """
    Scan ALL schemas for patternProperties using complete tree walk.

    Uses x-python-class-name when present, falls back to title.

    Returns:
        Map of actual class name -> PatternPropertyInfo
    """
    all_patterns = {}

    for schema_file in sorted(json_dir.glob("*.json")):
        schema = json.loads(schema_file.read_text(encoding="utf-8"))

        for node, _json_ptr in walk_schema_nodes(schema):
            # Must have both title and patternProperties
            if "title" not in node or "patternProperties" not in node:
                continue

            patterns = _extract_patterns(node["patternProperties"])
            if not patterns:
                continue

            # Use x-python-class-name if present, else title
            title = node["title"]
            model_name = index.class_name_overrides.get(title, title)

            all_patterns[model_name] = PatternPropertyInfo(model_name=model_name, patterns=patterns)

    return all_patterns


def _extract_patterns(pattern_props: Any) -> dict[str, dict] | None:
    if not isinstance(pattern_props, dict) or not pattern_props:
        return None

    # Validate all patterns compile
    for pattern_str in pattern_props.keys():
        try:
            re.compile(pattern_str)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern '{pattern_str}': {e}") from e

    return pattern_props


def generate_pattern_class_code(model_name: str, patterns: dict[str, dict]) -> str:
    """
    Generate __pattern_properties__ class variable code.

    Returns Python code string WITHOUT indentation (will be indented by AST).
    """
    lines = ["__pattern_properties__ = {"]

    for pattern_str, schema in patterns.items():
        # Escape for raw string
        escaped = pattern_str.replace("\\", "\\\\")
        schema_json = json.dumps(schema, separators=(",", ": "))
        lines.append(f"    re.compile(r'{escaped}'): {schema_json},")

    lines.append("}")

    return "\n".join(lines)
