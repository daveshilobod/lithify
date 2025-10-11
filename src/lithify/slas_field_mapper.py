# src/lithify/slas_field_mapper.py
"""
Field mapping for SLAS - tracks which model fields should use aliases.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .slas_schema_index import SchemaIndex, resolve_uri


@dataclass
class FieldTarget:
    model_name: str
    field_name: str
    alias_fqn: str
    slot: str  # "self", "list_item", "dict_value", etc.

    @property
    def field_key(self) -> str:
        return f"{self.model_name}.{self.field_name}"


def sanitize_field_name(name: str) -> str:
    import re

    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    name = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", name)
    name = name.lower()

    name = re.sub(r"[^a-z0-9_]", "_", name)

    if name and name[0].isdigit():
        name = f"field_{name}"

    if name in {"class", "def", "return", "from", "import", "type"}:
        name = f"{name}_"

    return name or "field"


def build_field_map(
    index: SchemaIndex,
    ref_map: dict[str, str],
    json_dir: Path,
    verbose: int = 0,
    package_name: str = "",  # Added for NsInt
) -> dict[str, FieldTarget]:
    from .nsint_mapper import detect_ns_fields
    from .slas_alias_generator import _determine_common_types_module

    field_map = {}
    for doc_uri, doc in index.docs.items():
        if not isinstance(doc, dict) or ("properties" not in doc and "patternProperties" not in doc):
            continue

        model_name = doc.get("title")
        if not model_name:
            # Fallback to generate a model name from the URI
            stem = doc_uri.split("/")[-1].replace(".json", "")
            model_name = "".join(word.capitalize() for word in stem.split("_"))

        if model_name in index.class_name_overrides:
            model_name = index.class_name_overrides[model_name]
            if verbose >= 2:
                print(f"[field-map] Using override class name: {model_name}")

        def find_refs_recursive(
            sub_schema: dict, prop_name: str, slot: str, _doc_uri: str = doc_uri, _model_name: str = model_name
        ):
            if not isinstance(sub_schema, dict):
                return

            if "$ref" in sub_schema:
                base_uri = index.subschema_bases.get(id(sub_schema), _doc_uri)
                ref = sub_schema["$ref"]

                abs_uri, frag = resolve_uri(base_uri, ref)
                full_uri = abs_uri + (frag or "")

                # Direct lookup in ref_map - works for both file-based and URN refs
                alias_fqn = ref_map.get(full_uri)

                if alias_fqn:
                    field_name = sanitize_field_name(prop_name)
                    target = FieldTarget(model_name=_model_name, field_name=field_name, alias_fqn=alias_fqn, slot=slot)
                    field_map[target.field_key] = target
                    if verbose >= 2:
                        print(f"[field-map] {target.field_key} -> {target.alias_fqn} (slot={slot})")

            if "items" in sub_schema:
                find_refs_recursive(sub_schema["items"], prop_name, "list_item")
            if "additionalProperties" in sub_schema:
                find_refs_recursive(sub_schema["additionalProperties"], prop_name, "dict_value")
            for key in ["oneOf", "anyOf", "allOf"]:
                if key in sub_schema and isinstance(sub_schema[key], list):
                    for item in sub_schema[key]:
                        find_refs_recursive(item, prop_name, "union_member")

        for prop_name, prop_schema in doc.get("properties", {}).items():
            # Check if this property's JSON pointer has a direct mapping (for inline allOf)
            prop_json_ptr = f"#/properties/{prop_name}"
            if prop_json_ptr in ref_map:
                alias_fqn = ref_map[prop_json_ptr]
                field_name = sanitize_field_name(prop_name)
                target = FieldTarget(model_name=model_name, field_name=field_name, alias_fqn=alias_fqn, slot="self")
                field_map[target.field_key] = target
                if verbose >= 2:
                    print(f"[field-map] {target.field_key} -> {target.alias_fqn} (inline allOf)")
            else:
                find_refs_recursive(prop_schema, prop_name, "self")

    common_types_module = _determine_common_types_module(index, package_name)
    for schema_file in sorted(json_dir.glob("*.json")):
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        if "title" not in schema:
            continue

        title = schema["title"]
        class_name = index.class_name_overrides.get(title, title)
        ns_fields = detect_ns_fields(schema)

        for field_name, _json_ptr in ns_fields:
            key = f"{class_name}.{field_name}"
            target = FieldTarget(
                model_name=class_name,
                field_name=field_name,
                alias_fqn=f"{package_name}.{common_types_module}.NsInt",
                slot="self",
            )
            field_map[key] = target
            if verbose >= 2:
                print(f"[nsint] Mapped {key} -> {target.alias_fqn}")

    if verbose >= 1:
        print(f"[field-map] Found {len(field_map)} fields that need aliases")

    return field_map
