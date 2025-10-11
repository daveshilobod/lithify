# src/lithify/transforms.py
"""
AST-based type hint transformations for deep immutability.
"""

from __future__ import annotations

import ast
import textwrap
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
                    # Add Ellipsis to make it variable-length: tuple[T, ...]
                    if isinstance(node.slice, ast.Tuple):
                        node.slice.elts.append(ast.Constant(value=...))
                    else:
                        node.slice = ast.Tuple(elts=[node.slice, ast.Constant(value=...)], ctx=ast.Load())
                elif sym in {"Set", "set"}:
                    node.value = _to_name("frozenset")
                elif sym in {"Dict", "dict", "MutableMapping"}:
                    node.value = _to_name("Mapping")
                return node

        if isinstance(val, ast.Name):
            sym = val.id
            if sym in {"List", "list"}:
                node.value = _to_name("tuple")
                # Add Ellipsis to make it variable-length: tuple[T, ...]
                if isinstance(node.slice, ast.Tuple):
                    node.slice.elts.append(ast.Constant(value=...))
                else:
                    node.slice = ast.Tuple(elts=[node.slice, ast.Constant(value=...)], ctx=ast.Load())
            elif sym in {"Set", "set"}:
                node.value = _to_name("frozenset")
            elif sym in {"Dict", "dict", "MutableMapping"}:
                node.value = _to_name("Mapping")
            return node

        return node


def _ensure_mapping_import(src: str) -> str:
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
            names = sorted({n for n in names if n})
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


class DocstringWrapper(ast.NodeTransformer):
    def __init__(self, width: int = 79):
        # Set width for textwrap, leaving room for indentation and quotes
        self.width = width

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        # First, visit children to handle nested classes
        self.generic_visit(node)

        # ast.get_docstring is the canonical way to get a docstring
        docstring = ast.get_docstring(node, clean=True)

        if not docstring:
            return node

        cleaned_docstring = " ".join(docstring.strip().split())

        if len(cleaned_docstring) < self.width:
            return node

        wrapped_lines = textwrap.wrap(cleaned_docstring, width=self.width)
        new_docstring_str = "\n".join(wrapped_lines)

        # Create a new docstring node. For Python 3.8+, this is ast.Constant
        new_docstring_node = ast.Expr(value=ast.Constant(value=new_docstring_str))

        # The docstring is always the first element in the body if it exists.
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            node.body[0] = new_docstring_node

        return node


def wrap_all_docstrings(package_dir: Path, verbose: int = 0) -> None:
    changed = 0
    # Use rglob to catch all python files, even in subdirectories
    for py_file in package_dir.rglob("*.py"):
        if py_file.name.startswith(("_", "__")):
            continue

        original_source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(original_source)
        except SyntaxError:
            continue

        transformer = DocstringWrapper()
        new_tree = transformer.visit(tree)
        ast.fix_missing_locations(new_tree)

        new_source = ast.unparse(new_tree)

        if new_source != original_source:
            py_file.write_text(new_source, encoding="utf-8")
            changed += 1

    if verbose >= 1:
        typer.echo(f"[docs] Re-wrapped docstrings in {changed} files.")
