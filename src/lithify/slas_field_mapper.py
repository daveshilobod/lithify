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
    """Build a map of model fields that should use aliases.
    
    Args:
        index: Schema index with all nodes
        ref_map: URI -> Python FQN mapping for aliases
        json_dir: Directory with sanitized JSON schemas (what DCG will process)
        
    Returns:
        Mapping from "ModelName.field_name" to FieldTarget
    """
    field_map = {}
    
    # Process each JSON file that will be fed to DCG
    for json_file in sorted(json_dir.rglob("*.json")):
        if json_file.parent.name == "defs":
            continue
        
        try:
            with json_file.open("r", encoding="utf-8") as f:
                schema = json.load(f)
        except json.JSONDecodeError:
            continue
        

        if "title" in schema:
            model_name = schema["title"]
        else:

            model_name = safe_module_slug(json_file.stem)

            model_name = "".join(word.capitalize() for word in model_name.split("_"))
        

        doc_uri = None
        for uri, doc in index.docs.items():

            if doc == schema or (isinstance(doc, dict) and doc.get("$id") == schema.get("$id")):
                doc_uri = uri
                break
        
        if not doc_uri:

            for uri, path in index.origin_files.items():
                if path.name == json_file.name or path.stem == json_file.stem:
                    doc_uri = uri
                    break
        
        if not doc_uri:
            if verbose >= 2:
                print(f"[field-map] No doc_uri found for {json_file}")
            continue
        

        def process_property(prop_schema: Any, prop_name: str, slot: str = "self") -> None:
            if not isinstance(prop_schema, dict):
                return
            
            if "$ref" in prop_schema:
                ref = prop_schema["$ref"]

                abs_uri, frag = resolve_uri(doc_uri, ref)
                full_uri = abs_uri + (frag or "")
                
                if verbose >= 3:
                    print(f"[field-map] Found ref '{ref}' -> '{full_uri}'")
                    print(f"[field-map]   Looking for in ref_map: {full_uri in ref_map}")
                

                if full_uri in ref_map:
                    field_name = sanitize_field_name(prop_name)
                    target = FieldTarget(
                        model_name=model_name,
                        field_name=field_name,
                        alias_fqn=ref_map[full_uri],
                        slot=slot
                    )
                    field_map[target.field_key] = target
                    
                    if verbose >= 2:
                        print(f"[field-map] {target.field_key} -> {target.alias_fqn} (slot={slot})")
                else:
                    # Refs might point to common_types.json but ref_map has common_types.schema.json
                    alt_keys = []
                    

                    if ".json#" in full_uri and ".schema.json#" not in full_uri:
                        alt_key = full_uri.replace(".json#", ".schema.json#")
                        alt_keys.append(alt_key)
                    

                    parts = full_uri.split("/")
                    if parts:
                        filename = parts[-1].split("#")[0]
                        if "_" in filename and filename.split("_", 1)[0].isdigit():

                            unprefixed = filename.split("_", 1)[1]
                            alt_key = full_uri.replace(filename, unprefixed)
                            alt_keys.append(alt_key)

                            if ".json#" in alt_key:
                                alt_keys.append(alt_key.replace(".json#", ".schema.json#"))
                    

                    for alt_key in alt_keys:
                        if alt_key in ref_map:
                            field_name = sanitize_field_name(prop_name)
                            target = FieldTarget(
                                model_name=model_name,
                                field_name=field_name,
                                alias_fqn=ref_map[alt_key],
                                slot=slot
                            )
                            field_map[target.field_key] = target
                            
                            if verbose >= 2:
                                print(f"[field-map] {target.field_key} -> {target.alias_fqn} (slot={slot}) [via alt key]")
                            break
                    else:
                        if verbose >= 3:
                            print(f"[field-map]   Not found in ref_map, tried alt keys: {alt_keys}")
            

            if "items" in prop_schema:

                process_property(prop_schema["items"], prop_name, "list_item")
            
            if "additionalProperties" in prop_schema and isinstance(prop_schema["additionalProperties"], dict):

                process_property(prop_schema["additionalProperties"], prop_name, "dict_value")
            

            if "oneOf" in prop_schema:
                for branch in prop_schema["oneOf"]:
                    process_property(branch, prop_name, "union_member")
            
            if "anyOf" in prop_schema:
                for branch in prop_schema["anyOf"]:
                    process_property(branch, prop_name, "union_member")
        

        if "properties" in schema:
            for prop_name, prop_schema in schema["properties"].items():
                process_property(prop_schema, prop_name)
        

        if "patternProperties" in schema:
            for pattern, prop_schema in schema["patternProperties"].items():

                process_property(prop_schema, f"pattern_{pattern}", "dict_value")
    
    if verbose >= 1:
        if field_map:
            print(f"[field-map] Found {len(field_map)} fields that need aliases")
            if verbose >= 2:
                for key in sorted(field_map.keys())[:5]:
                    target = field_map[key]
                    print(f"[field-map]   {key} -> {target.alias_fqn}")
        else:
            print(f"[field-map] WARNING: No fields found that need aliases!")
            print(f"[field-map] ref_map has {len(ref_map)} entries")
            if verbose >= 2:
                print(f"[field-map] ref_map keys (first 5):")
                for key in list(ref_map.keys())[:5]:
                    print(f"[field-map]   {key}")
    

    field_map_path = json_dir.parent / "_slas_field_map.json"
    field_map_data = {
        key: {
            "model": target.model_name,
            "field": target.field_name,
            "alias": target.alias_fqn,
            "slot": target.slot
        }
        for key, target in field_map.items()
    }
    field_map_path.write_text(
        json.dumps(field_map_data, indent=2, sort_keys=True),
        encoding="utf-8"
    )
    
    return field_map
