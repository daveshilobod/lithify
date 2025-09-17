"""
Alias synthesis for SLAS - generates type aliases for scalar types.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .slas_schema_index import SchemaIndex, NodeId
from .slas_classifier import (
    classify_shape,
    get_string_constraints,
    get_number_constraints,
    union_scalar_pattern,
    is_scalar_str,
)


def generate_alias_code(
    name: str, 
    schema: dict,
    shape: str,
    base_class: str = "BaseModel"
) -> str:
    """Generate Python code for a type alias."""
    
    if shape == "scalar_str":
        # String with constraints
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
        # Union of string patterns
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
    aliases: List[Tuple[str, dict, str]],  # (name, schema, shape)
    package_name: str,
    base_class: str = "BaseModel",
    use_frozendict: bool = False
) -> str:
    """Generate a Python module with type aliases."""
    
    # Deterministic ordering prevents unnecessary diffs in generated code
    aliases = sorted(aliases, key=lambda x: x[0])
    
    lines = []
    
    # Header
    lines.append('"""')
    lines.append(f'Type aliases from {module_name}')
    lines.append('"""')
    lines.append('')
    lines.append('from __future__ import annotations')
    lines.append('')
    
    # Imports
    lines.append('from typing import Annotated, Literal')
    lines.append('from pydantic import Field, StringConstraints')
    
    # Import resolution based on base class type
    if base_class == "FrozenModel":
        lines.append(f'from .frozen_base import {base_class}')
    elif base_class != "BaseModel":
        pass
    else:
        lines.append('from pydantic import BaseModel')
    
    lines.append('')
    
    # Export list for module interface
    all_names = [name for name, _, _ in aliases]
    all_str = "__all__ = [" + ", ".join(f"'{name}'" for name in all_names) + "]"
    lines.append(all_str)
    lines.append('')
    
    # Generate each alias
    for name, schema, shape in aliases:
        # Preserve schema descriptions as documentation
        if "description" in schema:
            lines.append(f'# {schema["description"]}')
        
        code = generate_alias_code(name, schema, shape, base_class)
        lines.append(code)
    
    return "\n".join(lines)


def generate_ref_map(
    aliases: List[Tuple[NodeId, str, str]],  # (node_id, name, module)
    package_name: str
) -> Dict[str, str]:
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
    verbose: int = 0
) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """Emit alias modules for all scalar types.
    
    Returns:
        - ref_map: URI -> Python FQN mapping
        - modules_created: module_name -> list of type names
    """
    ref_map = {}
    modules_created = defaultdict(list)
    
    # Group exportables by origin module
    by_module = defaultdict(list)
    
    for doc_uri in index.docs:
        exports = index.exportables(doc_uri)
        
        for node_id, name, module in exports:

            node = index.node_for(node_id.uri)
            if not node:
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
        code = generate_alias_module(
            module_name,
            alias_data,
            package_name,
            base_class,
            use_frozendict
        )
        

        module_path = output_dir / f"{module_name}.py"
        module_path.write_text(code, encoding="utf-8")
        
        if verbose:
            print(f"[SLAS] Generated {len(aliases)} aliases in {module_path}")
        

        for name, _, _, node_id in aliases:
            fqn = f"{package_name}.{module_name}.{name}"
            ref_map[node_id.uri] = fqn
    

    
    return ref_map, dict(modules_created)
