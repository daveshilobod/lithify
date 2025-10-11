# src/lithify/slas_schema_processor.py
"""
Schema processor for SLAS - removes scalar $defs that SLAS handles.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .slas_classifier import classify_shape


def remove_scalar_defs(json_dir: Path, modules_created: dict[str, list[str]], verbose: int = 0) -> None:
    """Remove scalar $defs from schemas since SLAS handles them.

    This prevents DCG from generating duplicate (and broken) types.
    """

    handled_types = set()
    for types in modules_created.values():
        handled_types.update(types)

    if verbose:
        print(f"[SLAS] Removing {len(handled_types)} scalar $defs from schemas")

    files_to_remove = []

    for json_file in json_dir.rglob("*.json"):
        if json_file.parent.name == "defs":
            continue

        try:
            with json_file.open("r", encoding="utf-8") as f:
                schema = json.load(f)
        except json.JSONDecodeError:
            continue

        modified = False
        had_only_scalar_defs = False

        if "$defs" in schema:
            original_count = len(schema["$defs"])
            remaining_defs = {}

            for name, defn in schema["$defs"].items():
                if name in handled_types:
                    modified = True
                    if verbose >= 2:
                        print(f"  Removing {name} from {json_file.name}")
                else:
                    shape = classify_shape(defn)
                    if shape in {"object", "array", "mixed", "unknown"}:
                        remaining_defs[name] = defn
                    else:
                        remaining_defs[name] = defn

            if remaining_defs:
                schema["$defs"] = remaining_defs
            else:
                del schema["$defs"]
                had_only_scalar_defs = True

            if verbose >= 2 and modified:
                print(f"  {json_file.name}: Removed {original_count - len(remaining_defs)} of {original_count} $defs")

        if "definitions" in schema:
            original_count = len(schema["definitions"])
            remaining_defs = {}

            for name, defn in schema["definitions"].items():
                if name in handled_types:
                    modified = True
                else:
                    shape = classify_shape(defn)
                    if shape in {"object", "array", "mixed", "unknown"}:
                        remaining_defs[name] = defn
                    else:
                        remaining_defs[name] = defn

            if remaining_defs:
                schema["definitions"] = remaining_defs
            else:
                del schema["definitions"]
                had_only_scalar_defs = True

        if had_only_scalar_defs:
            has_content = False
            for key in schema:
                if key not in {"$schema", "$id", "title", "type", "description", "additionalProperties"}:
                    has_content = True
                    break

            if "properties" in schema:
                has_content = True

            if not has_content and schema.get("type") == "object":
                files_to_remove.append(json_file)
                if verbose >= 2:
                    print(f"  Will remove empty schema file: {json_file.name}")
                continue

        if modified and json_file not in files_to_remove:
            with json_file.open("w", encoding="utf-8") as f:
                json.dump(schema, f, indent=2, sort_keys=True)

    # Second pass: remove references to files we're deleting
    if files_to_remove:
        removed_names = {f.name for f in files_to_remove}

        if verbose:
            print(f"[SLAS] Removing references to {len(removed_names)} empty schemas")

        for json_file in json_dir.rglob("*.json"):
            if json_file in files_to_remove or json_file.parent.name == "defs":
                continue

            try:
                with json_file.open("r", encoding="utf-8") as f:
                    schema = json.load(f)
            except json.JSONDecodeError:
                continue

            modified = _remove_refs_to_files(schema, removed_names)

            if modified:
                with json_file.open("w", encoding="utf-8") as f:
                    json.dump(schema, f, indent=2, sort_keys=True)
                if verbose >= 2:
                    print(f"  Updated refs in {json_file.name}")

    for json_file in files_to_remove:
        json_file.unlink()
        if verbose:
            print(f"[SLAS] Removed empty schema: {json_file.name}")

    if verbose:
        print("[SLAS] Schema preprocessing complete")


def _remove_refs_to_files(node: Any, removed_files: set[str]) -> bool:
    if isinstance(node, dict):
        modified = False

        if "$ref" in node and isinstance(node["$ref"], str):
            ref = node["$ref"]

            if "#" in ref:
                file_part, _ = ref.split("#", 1)
            else:
                file_part = ref

            file_part = file_part.lstrip("./")

            # Safe to replace with string type - we only remove files containing scalar string $defs
            if file_part in removed_files:
                del node["$ref"]
                node["type"] = "string"
                modified = True

        for key, value in list(node.items()):
            if key != "$ref":
                if _remove_refs_to_files(value, removed_files):
                    modified = True

        return modified

    elif isinstance(node, list):
        modified = False
        for item in node:
            if _remove_refs_to_files(item, removed_files):
                modified = True
        return modified

    return False


def create_slas_placeholder_schema(module_name: str, types: list[str]) -> dict:
    """Create a placeholder schema for SLAS-handled types.

    This creates a minimal schema that tells DCG "this module exists"
    but doesn't have any types to generate (since SLAS handled them).
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "".join(word.capitalize() for word in module_name.split("_")),
        "type": "object",
        "description": f"Type aliases handled by SLAS: {', '.join(sorted(types))}",
        "additionalProperties": False,
    }
