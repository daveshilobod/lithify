# src/lithify/slas_rewriter.py
"""
AST rewriter for SLAS - rewrites type hints to use aliases.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from .slas_field_mapper import FieldTarget


class FieldRewriter(ast.NodeTransformer):
    def __init__(self, field_targets: dict[str, FieldTarget], depth: int, package_name: str):
        self.field_targets = field_targets
        self.depth = depth
        self.package_name = package_name
        self.imports_needed: dict[str, set[str]] = defaultdict(set)
        self._stack: list[ast.AST] = []
        self._current_class: str | None = None

    def visit_Module(self, node: ast.Module) -> ast.Module:
        self.generic_visit(node)

        new_imports = []
        for module, _names in sorted(self.imports_needed.items()):
            # e.g., "generated.common_types.SemVer" -> from generated.common_types import SemVer
            parts = module.rsplit(".", 1)
            if len(parts) == 2:
                from_module, name = parts

                if from_module.startswith(self.package_name + "."):
                    from_module = from_module[len(self.package_name) + 1 :]

                # Level 1 is '.', Level 2 is '..', etc.
                level = self.depth + 1

                import_node = ast.ImportFrom(module=from_module, names=[ast.alias(name=name, asname=None)], level=level)
                new_imports.append(import_node)

        if new_imports:
            insert_at = 0

            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Str | ast.Constant)
            ):
                insert_at = 1

            while insert_at < len(node.body):
                stmt = node.body[insert_at]
                if isinstance(stmt, ast.Import | ast.ImportFrom):
                    insert_at += 1
                else:
                    break

            node.body[insert_at:insert_at] = new_imports

        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        old_class = self._current_class
        self._current_class = node.name
        self._stack.append(node)

        try:
            self.generic_visit(node)
        finally:
            self._stack.pop()
            self._current_class = old_class

        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AnnAssign:
        if not self._current_class:
            return node

        if not isinstance(node.target, ast.Name):
            return node

        field_key = f"{self._current_class}.{node.target.id}"
        target = self.field_targets.get(field_key)

        if not target:
            return node

        new_ann = self._rewrite_annotation(node.annotation, target)
        if new_ann is not None:
            node.annotation = new_ann

            parts = target.alias_fqn.rsplit(".", 1)
            if len(parts) == 2:
                self.imports_needed[target.alias_fqn].add(parts[1])

        return node

    def _rewrite_annotation(self, ann: ast.expr, target: FieldTarget) -> ast.expr | None:
        alias_name = target.alias_fqn.rsplit(".", 1)[-1]

        def make_alias():
            return ast.Name(id=alias_name, ctx=ast.Load())

        # Direct type: str -> Alias
        if isinstance(ann, ast.Name):
            if ann.id == "str" and target.slot == "self":
                return make_alias()
            elif ann.id in {"int", "float"} and target.slot == "self":
                return make_alias()

        # Subscript patterns (Optional, List, Annotated, etc.)
        elif isinstance(ann, ast.Subscript):
            if isinstance(ann.value, ast.Name):
                # Handles DCG-generated fields with inline constraints from collapsed allOf
                if ann.value.id == "Annotated" and target.slot == "self":
                    return make_alias()

                # Optional[...]
                if ann.value.id == "Optional":
                    inner = self._rewrite_annotation(
                        ann.slice, FieldTarget(target.model_name, target.field_name, target.alias_fqn, "self")
                    )
                    if inner:
                        return ast.Subscript(value=ann.value, slice=inner, ctx=ast.Load())

                # List[str], Set[str]
                elif ann.value.id in {"List", "list", "Set", "set"}:
                    if target.slot in {"self", "list_item", "set_item"}:
                        inner = make_alias()
                        return ast.Subscript(value=ann.value, slice=inner, ctx=ast.Load())

                # Dict[str, str] - replace value type only
                elif ann.value.id in {"Dict", "dict"}:
                    if isinstance(ann.slice, ast.Tuple) and len(ann.slice.elts) == 2:
                        key_type, val_type = ann.slice.elts
                        if target.slot in {"self", "dict_value"}:
                            new_val = make_alias()
                            return ast.Subscript(
                                value=ann.value,
                                slice=ast.Tuple(elts=[key_type, new_val], ctx=ast.Load()),
                                ctx=ast.Load(),
                            )

                # Union[...]
                elif ann.value.id == "Union":
                    if isinstance(ann.slice, ast.Tuple):
                        new_elts = []
                        for elt in ann.slice.elts:
                            if isinstance(elt, ast.Name) and elt.id == "str":
                                new_elts.append(make_alias())
                            else:
                                new_elts.append(elt)

                        if new_elts != ann.slice.elts:
                            return ast.Subscript(
                                value=ann.value, slice=ast.Tuple(elts=new_elts, ctx=ast.Load()), ctx=ast.Load()
                            )

        # PEP 604 union: str | None
        elif isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
            left = self._rewrite_annotation(ann.left, target)
            right = ann.right

            if left and left != ann.left:
                return ast.BinOp(left=left, op=ast.BitOr(), right=right)

        return None


def rewrite_module_with_aliases(
    module_path: Path, field_map: dict[str, FieldTarget], depth: int, package_name: str, verbose: int = 0
) -> bool:
    """Rewrite a generated module to use aliases.

    Returns True if any changes were made.
    """
    code = module_path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        if verbose:
            print(f"[rewriter] Failed to parse {module_path}: {e}")
        return False

    module_classes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            module_classes.add(node.name)

    relevant_targets = {key: target for key, target in field_map.items() if target.model_name in module_classes}

    if not relevant_targets:
        return False

    rewriter = FieldRewriter(relevant_targets, depth, package_name)
    new_tree = rewriter.visit(tree)

    # Return early if no aliases were substituted
    if not rewriter.imports_needed:
        return False

    new_code = ast.unparse(new_tree)
    module_path.write_text(new_code, encoding="utf-8")

    if verbose:
        print(f"[rewriter] Updated {module_path.name} with {len(rewriter.imports_needed)} alias imports")

    return True


def rewrite_all_modules(package_dir: Path, field_map: dict[str, FieldTarget], verbose: int = 0) -> int:
    """Rewrite all modules in a package to use aliases.

    Returns the number of modules modified.
    """
    modified_count = 0
    package_name = package_dir.name

    for py_file in sorted(package_dir.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        if py_file.name in {"mutable_base.py", "frozen_base.py", "bases.py"}:
            continue
        if py_file.parent.name == "defs":
            continue

        if "_slas_" in py_file.name:
            continue

        relative_path = py_file.relative_to(package_dir)
        depth = len(relative_path.parts) - 1

        if rewrite_module_with_aliases(py_file, field_map, depth, package_name, verbose):
            modified_count += 1

    if verbose and modified_count:
        print(f"[rewriter] Modified {modified_count} modules to use aliases")

    return modified_count
