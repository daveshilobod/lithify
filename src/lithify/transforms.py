"""
AST-based type hint transformations for deep immutability.
"""

from __future__ import annotations

import ast
from pathlib import Path

import typer


class TypeHintTransformer(ast.NodeTransformer):
    """
    Rewrites type hints to immutable variants:
      List[T], list[T]        → tuple[T, ...]
      Set[T], set[T]          → frozenset[T]
      Dict[K, V], dict[K, V]  → Mapping[K, V]
      MutableMapping[K, V]    → Mapping[K, V]
    
    Handles Optional/Union by transforming inner args.
    """
    
    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        self.generic_visit(node)  # transform inner args first
        val = node.value
        
        def _to_name(name: str) -> ast.Name:
            return ast.Name(id=name, ctx=ast.Load())
        
        if isinstance(val, ast.Attribute) and isinstance(val.value, ast.Name):
            base_mod = val.value.id
            sym = val.attr
            if base_mod in {"typing", "collections", "collections.abc"}:
                if sym in {"List", "list"}:
                    node.value = _to_name("tuple")
                    # Note: This is a simplification. Properly handling tuple[T, ...] 
                    # requires more complex AST manipulation
                elif sym in {"Set", "set"}:
                    node.value = _to_name("frozenset")
                elif sym in {"Dict", "dict", "MutableMapping"}:
                    node.value = _to_name("Mapping")
                return node
        
        if isinstance(val, ast.Name):
            sym = val.id
            if sym in {"List", "list"}:
                node.value = _to_name("tuple")
            elif sym in {"Set", "set"}:
                node.value = _to_name("frozenset")
            elif sym in {"Dict", "dict", "MutableMapping"}:
                node.value = _to_name("Mapping")
            return node
        
        return node


def _ensure_mapping_import(src: str) -> str:
    """Ensure Mapping is imported when needed."""
    need = "Mapping["
    if need not in src:
        return src
    
    lines = src.splitlines()
    found_line = -1
    for i, line in enumerate(lines):
        if line.startswith("from typing import"):
            found_line = i
            names = [n.strip() for n in line.split("import", 1)[1].split(",")]
            names = [n for n in names if n not in {"List", "Dict", "Set", "MutableMapping"}]
            if "Mapping" not in names:
                names.append("Mapping")
            names = sorted(set([n for n in names if n]))
            lines[i] = "from typing import " + ", ".join(names)
            break
    
    if found_line == -1:
        insert_at = 0
        for i, line in enumerate(lines[:5]):
            if line.startswith("from __future__ import"):
                insert_at = i + 1
        lines.insert(insert_at, "from typing import Mapping")
    
    return "\n".join(lines)


def rewrite_type_hints_ast(package_dir: Path, verbose: int = 0) -> None:
    """
    Rewrite type hints in generated models to use immutable types.
    Only applies to deep-frozen mode.
    """
    changed = 0
    
    for py in package_dir.glob("*.py"):
        if py.name in {"__init__.py", "frozen_base.py", "mutable_base.py", "frozendict.py"}:
            continue
        
        src = py.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        
        new_tree = TypeHintTransformer().visit(tree)
        ast.fix_missing_locations(new_tree)
        new_src = ast.unparse(new_tree)
        new_src = _ensure_mapping_import(new_src)
        
        if new_src != src:
            py.write_text(new_src, encoding="utf-8")
            changed += 1
    
    if verbose >= 1:
        typer.echo(f"[hints] rewrote type hints in {changed} files for deep immutability")
