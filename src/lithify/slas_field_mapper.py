"""
Field mapping for SLAS - tracks which model fields should use aliases.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .slas_schema_index import SchemaIndex, resolve_uri
from .sanitizer import safe_module_slug


@dataclass
class FieldTarget:
    """Information about a field that should use an alias."""
    model_name: str
    field_name: str
    alias_fqn: str
    slot: str  # "self", "list_item", "dict_value", etc.
    
    @property
    def field_key(self) -> str:
        return f"{self.model_name}.{self.field_name}"


def sanitize_field_name(name: str) -> str:
    """Convert JSON property name to Python field name."""
    import re

    name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    name = re.sub(r'([a-z\d])([A-Z])', r'\1_\2', name)
    name = name.lower()
    

    name = re.sub(r'[^a-z0-9_]', '_', name)
    

    if name and name[0].isdigit():
        name = f'field_{name}'
    

    if name in {'class', 'def', 'return', 'from', 'import', 'type'}:
        name = f'{name}_'
    
    return name or 'field'


def build_field_map(
    index: SchemaIndex,
    ref_map: Dict[str, str],
    json_dir: Path,
    verbose: int = 0
) -> Dict[str, FieldTarget]:
    """Build a map of model fields that should use aliases by traversing the index."""
    field_map = {}
    for doc_uri, doc in index.docs.items():
        if not isinstance(doc, dict) or "properties" not in doc:
            continue

        model_name = doc.get("title")
        if not model_name:
            # Fallback to generate a model name from the URI
            stem = doc_uri.split("/")[-1].replace(".json", "")
            model_name = "".join(word.capitalize() for word in stem.split("_"))

        def find_refs_recursive(sub_schema: dict, prop_name: str, slot: str):
            if not isinstance(sub_schema, dict):
                return

            if "$ref" in sub_schema:
                base_uri = index.subschema_bases.get(id(sub_schema), doc_uri)
                ref = sub_schema["$ref"]
                
                # Handle file references specially - need to resolve to the target's $id
                if ref.startswith("./") or ref.startswith("../"):
                    # This is a relative file reference
                    # First resolve to get the file path
                    abs_uri, frag = resolve_uri(base_uri, ref)
                    
                    # Check if we have a document loaded from this resolved path
                    # We need to find the document by its actual $id, not the file path
                    target_doc_uri = None
                    for loaded_uri, loaded_doc in index.docs.items():
                        # Check if this document was loaded from a file that matches
                        origin_file = index.origin_files.get(loaded_uri)
                        if origin_file:
                            # Check if the resolved URI matches this file
                            if abs_uri.endswith(origin_file.name):
                                target_doc_uri = loaded_uri
                                break
                    
                    if target_doc_uri:
                        # Use the document's actual $id as the base
                        full_uri = target_doc_uri + (frag or "")
                    else:
                        # Fallback to the resolved URI
                        full_uri = abs_uri + (frag or "")
                else:
                    # Not a file reference, resolve normally
                    abs_uri, frag = resolve_uri(base_uri, ref)
                    full_uri = abs_uri + (frag or "")

                alias_fqn = None
                if full_uri in ref_map:
                    alias_fqn = ref_map[full_uri]
                else:
                    # Fallback for sanitized filenames
                    alt_uri = full_uri.replace('.json#', '.schema.json#') if '.json#' in full_uri else ''
                    if alt_uri and alt_uri in ref_map:
                        alias_fqn = ref_map[alt_uri]
                
                if alias_fqn:
                    field_name = sanitize_field_name(prop_name)
                    target = FieldTarget(
                        model_name=model_name,
                        field_name=field_name,
                        alias_fqn=alias_fqn,
                        slot=slot
                    )
                    field_map[target.field_key] = target
                    if verbose >= 2:
                        print(f"[field-map] {target.field_key} -> {target.alias_fqn} (slot={slot})")

            # Recurse into nested structures
            if "items" in sub_schema:
                find_refs_recursive(sub_schema["items"], prop_name, "list_item")
            if "additionalProperties" in sub_schema:
                find_refs_recursive(sub_schema["additionalProperties"], prop_name, "dict_value")
            for key in ["oneOf", "anyOf", "allOf"]:
                if key in sub_schema and isinstance(sub_schema[key], list):
                    for item in sub_schema[key]:
                        find_refs_recursive(item, prop_name, "union_member")

        for prop_name, prop_schema in doc.get("properties", {}).items():
            find_refs_recursive(prop_schema, prop_name, "self")

    if verbose >= 1:
        print(f"[field-map] Found {len(field_map)} fields that need aliases")

    return field_map
