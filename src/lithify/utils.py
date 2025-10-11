# src/lithify/utils.py

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import typer


def walk_schema_nodes(schema: Any, json_ptr: str = "#") -> Iterator[tuple[dict, str]]:
    """
    Walk all schema nodes in a JSON Schema document.

    Foundation for allOf collapse, pattern detection, and schema transformations.
    Yields (node, json_pointer) for every dict that could contain schema keywords.

    Excluded keywords (intentional):
    - unevaluatedProperties/Items: Pydantic v2 unsupported
    - propertyNames: Niche validation, adds complexity without sufficient benefit
    - $dynamicRef/$dynamicAnchor: Advanced recursion not yet implemented

    Args:
        schema: Root schema or any node
        json_ptr: Current JSON Pointer path

    Yields:
        (node_dict, json_pointer_string) tuples
    """
    if not isinstance(schema, dict):
        return

    # Yield current node
    yield (schema, json_ptr)

    # Properties
    if "properties" in schema and isinstance(schema["properties"], dict):
        for name, prop_schema in schema["properties"].items():
            escaped = name.replace("~", "~0").replace("/", "~1")
            yield from walk_schema_nodes(prop_schema, f"{json_ptr}/properties/{escaped}")

    # Pattern properties
    if "patternProperties" in schema and isinstance(schema["patternProperties"], dict):
        for pattern, pattern_schema in schema["patternProperties"].items():
            escaped = pattern.replace("~", "~0").replace("/", "~1")
            yield from walk_schema_nodes(pattern_schema, f"{json_ptr}/patternProperties/{escaped}")

    # Additional properties
    if "additionalProperties" in schema and isinstance(schema["additionalProperties"], dict):
        yield from walk_schema_nodes(schema["additionalProperties"], f"{json_ptr}/additionalProperties")

    # Items
    if "items" in schema:
        if isinstance(schema["items"], dict):
            yield from walk_schema_nodes(schema["items"], f"{json_ptr}/items")
        elif isinstance(schema["items"], list):
            for i, item_schema in enumerate(schema["items"]):
                yield from walk_schema_nodes(item_schema, f"{json_ptr}/items/{i}")

    # Prefix items
    if "prefixItems" in schema and isinstance(schema["prefixItems"], list):
        for i, item_schema in enumerate(schema["prefixItems"]):
            yield from walk_schema_nodes(item_schema, f"{json_ptr}/prefixItems/{i}")

    # Contains
    if "contains" in schema and isinstance(schema["contains"], dict):
        yield from walk_schema_nodes(schema["contains"], f"{json_ptr}/contains")

    # Combiners
    for keyword in ["allOf", "anyOf", "oneOf"]:
        if keyword in schema and isinstance(schema[keyword], list):
            for i, branch in enumerate(schema[keyword]):
                yield from walk_schema_nodes(branch, f"{json_ptr}/{keyword}/{i}")

    # Conditionals
    for keyword in ["if", "then", "else", "not"]:
        if keyword in schema and isinstance(schema[keyword], dict):
            yield from walk_schema_nodes(schema[keyword], f"{json_ptr}/{keyword}")

    # Dependent schemas
    if "dependentSchemas" in schema and isinstance(schema["dependentSchemas"], dict):
        for name, dep_schema in schema["dependentSchemas"].items():
            escaped = name.replace("~", "~0").replace("/", "~1")
            yield from walk_schema_nodes(dep_schema, f"{json_ptr}/dependentSchemas/{escaped}")

    # Definitions
    for def_key in ["$defs", "definitions"]:
        if def_key in schema and isinstance(schema[def_key], dict):
            for name, def_schema in schema[def_key].items():
                yield from walk_schema_nodes(def_schema, f"{json_ptr}/{def_key}/{name}")


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return False

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return True


def require_deps() -> None:
    try:
        import yaml  # noqa: F401
    except ImportError:
        typer.secho("Missing dependency: PyYAML. Install with: pip install pyyaml", fg=typer.colors.RED)
        raise typer.Exit(1) from None

    try:
        import datamodel_code_generator  # noqa: F401
    except ImportError:
        typer.secho(
            "Missing dependency: datamodel-code-generator. Install with: pip install 'datamodel-code-generator[http]'",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from None

    try:
        import pydantic  # noqa: F401
    except ImportError:
        typer.secho("Missing dependency: pydantic. Install with: pip install pydantic", fg=typer.colors.RED)
        raise typer.Exit(1) from None


def write_manifest(
    package_dir: Path,
    *,
    mutability: str,
    immutable_hints: bool,
    use_frozendict: bool,
    from_attributes: bool,
    verbose: int = 0,
) -> None:
    cls_index: dict[str, list[str]] = {}
    class_re = re.compile(r"^class\s+(\w+)\([^)]+\):", re.MULTILINE)

    for py in package_dir.glob("*.py"):
        if py.name in {"__init__.py", "frozen_base.py", "mutable_base.py", "frozendict.py"}:
            continue
        text = py.read_text(encoding="utf-8")
        classes = class_re.findall(text)
        if classes:
            cls_index[py.name] = classes

    manifest = {
        "package": package_dir.name,
        "mutability": mutability,
        "options": {
            "immutable_hints": immutable_hints,
            "use_frozendict": use_frozendict,
            "from_attributes": from_attributes,
        },
        "files": cls_index,
    }

    out = package_dir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    if verbose:
        typer.echo(f"[manifest] wrote {out}")


def write_py_typed(package_dir: Path) -> None:
    """Write py.typed marker for PEP 561 compliance."""
    (package_dir / "py.typed").write_text("", encoding="utf-8")
