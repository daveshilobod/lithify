# src/lithify/slas_alias_generator.py
"""
Alias synthesis for SLAS - generates type aliases for scalar types.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .slas_classifier import (
    classify_shape,
    get_number_constraints,
    get_string_constraints,
    union_scalar_pattern,
)
from .slas_schema_index import NodeId, SchemaIndex
from .utils import walk_schema_nodes


def _normalize_module_stem(stem: str) -> str:
    """
    Remove numbered prefix from module stem.

    Examples:
        "01_user" → "user"
        "user" → "user"
        "02_common_types" → "common_types"
    """
    if "_" in stem and stem.split("_", 1)[0].isdigit():
        return stem.split("_", 1)[1]
    return stem


def _determine_module(index: SchemaIndex, type_name: str) -> str:
    """Determine which module a type should go in based on origin schema."""
    for doc_uri, doc in index.docs.items():
        if "title" in doc:
            title = doc["title"]
            actual_name = index.class_name_overrides.get(title, title)
            if actual_name == type_name:
                origin_file = index.origin_files[doc_uri]
                return origin_file.stem

        # Check $defs
        if "$defs" in doc:
            for def_name in doc["$defs"]:
                actual_name = index.class_name_overrides.get(def_name, def_name)
                if actual_name == type_name:
                    origin_file = index.origin_files[doc_uri]
                    return origin_file.stem

    return "common_types"  # fallback


def generate_alias_code(name: str, schema: dict, shape: str, base_class: str = "BaseModel") -> str:
    """Generate Python code for a type alias."""

    if shape == "scalar_str":
        constraints = get_string_constraints(schema)
        if constraints:
            args = []
            if "pattern" in constraints:
                # Raw string preserves regex patterns without double escaping
                pat = constraints["pattern"].replace('"', '\\"')
                args.append(f'pattern=r"{pat}"')
            if "min_length" in constraints:
                args.append(f'min_length={constraints["min_length"]}')
            if "max_length" in constraints:
                args.append(f'max_length={constraints["max_length"]}')

            return f'{name} = Annotated[str, StringConstraints({", ".join(args)})]\n'
        else:
            return f"{name} = str\n"

    elif shape == "union_scalar_str":
        branches = schema.get("oneOf", [])
        pattern = union_scalar_pattern(branches)
        if pattern:
            # Raw string preserves regex patterns without double escaping
            pat = pattern.replace('"', '\\"')
            return f'{name} = Annotated[str, StringConstraints(pattern=r"{pat}")]\n'
        else:
            return f"{name} = str\n"

    elif shape == "scalar_number":
        # Number with constraints
        type_str = "int" if schema.get("type") == "integer" else "float"
        constraints = get_number_constraints(schema)
        if constraints:
            args = []
            for key, val in constraints.items():
                args.append(f"{key}={val}")

            return f'{name} = Annotated[{type_str}, Field({", ".join(args)})]\n'
        else:
            return f"{name} = {type_str}\n"

    elif shape == "enum_str":
        # String enum becomes Literal type for exact value matching
        if "enum" in schema:
            values = schema["enum"]
            literals = ", ".join(repr(v) for v in values)
            return f"{name} = Literal[{literals}]\n"
        elif "const" in schema:
            return f"{name} = Literal[{repr(schema['const'])}]\n"
        else:
            return f"{name} = str\n"

    else:
        return f"# {name}: unsupported shape {shape}\n"


def generate_alias_module(
    module_name: str,
    aliases: list[tuple[str, dict, str]],  # (name, schema, shape)
    package_name: str,
    base_class: str = "BaseModel",
    use_frozendict: bool = False,
) -> str:
    """Generate a Python module with type aliases."""

    # Deterministic ordering prevents unnecessary diffs in generated code
    aliases = sorted(aliases, key=lambda x: x[0])

    lines = []

    # Header
    lines.append('"""')
    lines.append(f"Type aliases from {module_name}")
    lines.append('"""')
    lines.append("")
    lines.append("from __future__ import annotations")
    lines.append("")

    # Imports
    lines.append("from typing import Annotated, Literal")
    lines.append("from pydantic import Field, StringConstraints")

    # Import resolution based on base class type
    if base_class == "FrozenModel":
        lines.append(f"from .frozen_base import {base_class}")
    elif base_class != "BaseModel":
        pass
    else:
        lines.append("from pydantic import BaseModel")

    lines.append("")

    # Export list for module interface
    all_names = [name for name, _, _ in aliases]
    all_str = "__all__ = [" + ", ".join(f"'{name}'" for name in all_names) + "]"
    lines.append(all_str)
    lines.append("")

    # Generate each alias
    for name, schema, shape in aliases:
        # Preserve schema descriptions as documentation
        if "description" in schema:
            lines.append(f'# {schema["description"]}')

        code = generate_alias_code(name, schema, shape, base_class)
        lines.append(code)

    return "\n".join(lines)


def generate_ref_map(
    aliases: list[tuple[NodeId, str, str]],  # (node_id, name, module)
    package_name: str,
) -> dict[str, str]:
    """Generate a mapping from schema URIs to Python FQNs."""
    ref_map = {}

    for node_id, name, module in aliases:
        fqn = f"{package_name}.{module}.{name}"
        ref_map[node_id.uri] = fqn

    return ref_map


def emit_alias_modules(
    index: SchemaIndex,
    output_dir: Path,
    package_name: str,
    base_class: str = "BaseModel",
    use_frozendict: bool = False,
    verbose: int = 0,
    json_dir: Path = None,  # Added for nsint scanning
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Emit alias modules for all scalar types.

    NEW: Conditionally generates NsInt if *_ns fields detected.

    Returns:
        - ref_map: URI -> Python FQN mapping
        - modules_created: module_name -> list of type names
    """

    from .nsint_generator import generate_nsint_code
    from .nsint_mapper import scan_all_schemas_for_ns_fields
    from .slas_allof_processor import generate_validator_code

    ref_map = {}
    modules_created = defaultdict(list)

    all_refs = set()
    for doc_uri in index.docs:
        doc = index.docs[doc_uri]
        for node, _ in walk_schema_nodes(doc):
            if "$ref" in node:
                all_refs.add(index.resolve_ref(node["$ref"], doc_uri))

    # Group exportables by origin module
    by_module = defaultdict(list)

    for doc_uri in index.docs:
        exports = index.exportables(doc_uri)

        for node_id, name, module in exports:
            node = index.node_for(node_id.uri)
            if not node:
                continue

            # If the node is a $def with a title, only generate an alias if it's referenced.
            if node_id.fragment.startswith("#/$defs/") and "title" in node and node_id.uri not in all_refs:
                continue

            shape = classify_shape(node)

            # Scalar types become type aliases, complex types handled elsewhere
            if shape in {"scalar_str", "scalar_number", "enum_str", "union_scalar_str"}:
                by_module[module].append((name, node, shape, node_id))
                modules_created[module].append(name)

    output_dir.mkdir(parents=True, exist_ok=True)

    for module_name, aliases in by_module.items():
        if not aliases:
            continue

        alias_data = [(name, schema, shape) for name, schema, shape, _ in aliases]
        code = generate_alias_module(module_name, alias_data, package_name, base_class, use_frozendict)

        module_path = output_dir / f"{module_name}.py"
        module_path.write_text(code, encoding="utf-8")

        if verbose:
            print(f"[SLAS] Generated {len(aliases)} aliases in {module_path}")

        for name, _, _, node_id in aliases:
            fqn = f"{package_name}.{module_name}.{name}"
            ref_map[node_id.uri] = fqn

    # NEW: Check for schemas with _validator_patterns (fallback strategy)
    validators_needed = {}

    for _doc_uri, doc in index.docs.items():
        for node, _json_ptr in walk_schema_nodes(doc):
            if "_validator_patterns" not in node:
                continue

            if "title" not in node:
                continue

            title = node["title"]
            type_name = index.class_name_overrides.get(title, title)
            patterns = node["_validator_patterns"]

            validators_needed[type_name] = patterns

    # Generate validator functions and integrate into aliases
    for type_name, patterns in validators_needed.items():
        validator_func_code = generate_validator_code(patterns, type_name)
        validator_func_name = f"validate_{type_name.lower()}"

        # Write validator + alias to module
        module_content = f'''
"""Type alias with multi-pattern validation for {type_name}."""

from typing import Annotated
from pydantic import BeforeValidator
import re

{validator_func_code}

{type_name} = Annotated[
    str,
    BeforeValidator({validator_func_name})
]
'''

        # Determine which module this belongs to
        origin_module = _determine_module(index, type_name)
        module_file = output_dir / f"{origin_module}.py"

        # Append or create module
        if module_file.exists():
            existing = module_file.read_text()
            module_file.write_text(existing + "\n" + module_content)
        else:
            module_file.write_text(module_content)

        if verbose >= 2:
            print(f"[allof-validator] Generated BeforeValidator for {type_name}")

    # NEW: Check if NsInt generation is needed
    if json_dir and scan_all_schemas_for_ns_fields(json_dir):
        common_types_module = _determine_common_types_module(index, package_name)
        common_types_file = output_dir / f"{common_types_module}.py"

        if common_types_file.exists():
            content = common_types_file.read_text(encoding="utf-8")
        else:
            content = _generate_module_header(common_types_module)

        # Ensure required imports for NsInt
        if "from pydantic import BeforeValidator, PlainSerializer" not in content:
            content += "\nfrom pydantic import BeforeValidator, PlainSerializer"

        nsint_code = generate_nsint_code()
        content += f"\n\n{nsint_code}"

        common_types_file.write_text(content, encoding="utf-8")

        if verbose >= 1:
            print(f"[nsint] Generated NsInt in {common_types_module}.py")

        if common_types_module not in modules_created:
            modules_created[common_types_module].append("NsInt")

    return ref_map, dict(modules_created)


def _determine_common_types_module(index: SchemaIndex, package_name: str) -> str:
    """
    Determine which module should contain common types.

    Always uses "common_types" to keep all base types in one canonical location.

    Returns module name (without .py extension).
    """
    return "common_types"


def emit_inline_allof_aliases(
    inline_allofs: list[Any],  # List[InlineAllOfInfo]
    output_dir: Path,
    package_name: str,
    verbose: int = 0,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """
    Generate type aliases for inline allOf properties.

    Creates synthetic aliases named `ParentClass_property` and writes
    them to `{module}_types.py` to avoid collision with DCG-generated
    model files.

    Example:
        Given schema with inline allOf:

        >>> schema = {
        ...     "title": "User",
        ...     "properties": {
        ...         "user_id": {
        ...             "allOf": [
        ...                 {"type": "string", "minLength": 1},
        ...                 {"type": "string", "maxLength": 50}
        ...             ]
        ...         }
        ...     }
        ... }

        Generates `user_types.py` containing:

        >>> # User_user_id = Annotated[str, StringConstraints(
        >>> #     min_length=1, max_length=50
        >>> # )]

        And ref_map entry:

        >>> ref_map = {
        ...     "#/properties/user_id": "models.user_types.User_user_id"
        ... }

    Args:
        inline_allofs: List of InlineAllOfInfo from process_allof_collapse
        output_dir: Temporary directory for alias modules
        package_name: Package name for FQN generation
        verbose: Verbosity level (0=silent, 1=summary, 2=detailed)

    Returns:
        Tuple of:
        - ref_map: Map of JSON pointer → Python FQN
        - modules_created: module_name → list of type names
    """

    ref_map = {}
    modules_created = defaultdict(list)

    # Group by origin module (parent class)
    by_module = defaultdict(list)

    for info in inline_allofs:
        # Generate synthetic alias name: ParentClass_property
        alias_name = f"{info.parent_class}_{info.property_name}"

        # Determine module from origin file
        module_name = _class_to_module_name(info.parent_class, info.origin_file)

        by_module[module_name].append((alias_name, info.merged_schema, info))

    # Generate aliases in each module
    for module_name, aliases in by_module.items():
        module_file = output_dir / f"{module_name}.py"

        # Read existing module or create header
        if module_file.exists():
            content = module_file.read_text(encoding="utf-8")
        else:
            content = _generate_module_header(module_name)

        # Add each inline alias
        for alias_name, schema, info in aliases:
            # Classify the schema shape
            shape = classify_shape(schema)

            # Generate description comment
            desc = schema.get("description", "")
            if desc:
                content += f"\n# {desc}\n"
            else:
                content += f"\n# Inline allOf from {info.parent_class}.{info.property_name}\n"

            # Generate the alias code
            alias_code = generate_alias_code(alias_name, schema, shape)
            content += alias_code

            # Track in ref map
            fqn = f"{package_name}.{module_name}.{alias_name}"
            ref_map[info.json_pointer] = fqn

            # Track module creation
            modules_created[module_name].append(alias_name)

            if verbose >= 2:
                print(f"[inline-allof] {alias_name} → {module_name}.py")

        # Write back
        module_file.write_text(content, encoding="utf-8")

    # Summary at verbose >= 1
    if verbose >= 1 and inline_allofs:
        print(f"[inline-allof] Generated {len(inline_allofs)} inline alias(es)")

    return ref_map, dict(modules_created)


def _class_to_module_name(class_name: str, origin_file: Path) -> str:
    """
    Determine module name from class name and origin file.

    For inline allOf aliases, use a separate module to avoid
    collision with DCG-generated model files.

    Example: "user.json" → "user_types" (not "user")
    """
    stem = _normalize_module_stem(origin_file.stem)

    # Append _types suffix to avoid collision with model files
    return f"{stem}_types"


def _generate_module_header(module_name: str) -> str:
    """
    Generate standard module header for alias modules.
    """
    return f'''"""Type aliases from {module_name}"""

from __future__ import annotations

from typing import Annotated, Literal
from pydantic import Field, StringConstraints

'''
