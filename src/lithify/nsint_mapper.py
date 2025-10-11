# src/lithify/nsint_mapper.py
from __future__ import annotations

import json
from pathlib import Path


def detect_ns_fields(schema: dict) -> list[tuple[str, str]]:
    ns_fields = []
    for name, prop_schema in schema.get("properties", {}).items():
        if name.endswith("_ns") and prop_schema.get("type") == "string" and prop_schema.get("pattern") == "^[0-9]+$":
            json_ptr = f"#/properties/{name}"
            ns_fields.append((name, json_ptr))
    return ns_fields


def scan_all_schemas_for_ns_fields(json_dir: Path) -> bool:
    for schema_file in json_dir.glob("*.json"):
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        if detect_ns_fields(schema):
            return True
    return False
