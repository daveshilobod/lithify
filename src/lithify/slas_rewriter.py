"""
AST rewriter for SLAS - rewrites type hints to use aliases.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set

from .slas_field_mapper import FieldTarget


class FieldRewriter(ast.NodeTransformer):
    """AST transformer to replace type hints with aliases."""
    
    def __init__(self, field_targets: Dict[str, FieldTarget]):
        self.field_targets = field_targets
        self.imports_needed: Dict[str, Set[str]] = defaultdict(set)
        self._stack: List[ast.AST] = []
        self._current_class: Optional[str] = None
    
    def visit_Module(self, node: ast.Module) -> ast.Module:
        """Process module and add imports."""
        # First pass: collect needed imports
        self.generic_visit(node)
        
        # Build import statements
        new_imports = []
        for module, names in sorted(self.imports_needed.items()):
            # Split module path to get the actual import
            # e.g., "generated.common_types.SemVer" -> from generated.common_types import SemVer
            parts = module.rsplit(".", 1)
            if len(parts) == 2:
                from_module, name = parts

                if from_module.startswith("generated."):
                    from_module = "." + from_module.split(".", 1)[1]
                
                import_node = ast.ImportFrom(
                    module=from_module,
                    names=[ast.alias(name=name, asname=None)],
                    level=0 if not from_module.startswith(".") else 1
                )
                new_imports.append(import_node)
        
        if new_imports:
            # Insert after docstring and existing imports
            insert_at = 0
            
            # Skip docstring if present
            if (node.body and 
                isinstance(node.body[0], ast.Expr) and 
                isinstance(node.body[0].value, (ast.Str, ast.Constant))):
                insert_at = 1
            
            # Skip existing imports
            while insert_at < len(node.body):
                stmt = node.body[insert_at]
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    insert_at += 1
                else:
                    break
            
            # Insert new imports
            node.body[insert_at:insert_at] = new_imports
        
        return node
    
    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        """Track current class context."""
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
        """Process field annotations."""
        if not self._current_class:
            return node
        

        if not isinstance(node.target, ast.Name):
            return node
        
        field_key = f"{self._current_class}.{node.target.id}"
        target = self.field_targets.get(field_key)
        
        if not target:
            return node
        
        # Rewrite the annotation
        new_ann = self._rewrite_annotation(node.annotation, target)
        if new_ann is not None:
            node.annotation = new_ann
            
            # Track import needed
            parts = target.alias_fqn.rsplit(".", 1)
            if len(parts) == 2:
                self.imports_needed[target.alias_fqn].add(parts[1])
        
        return node
    
    def _rewrite_annotation(self, ann: ast.expr, target: FieldTarget) -> Optional[ast.expr]:
        """Rewrite a type annotation to use an alias."""
        
        # Get the alias name from FQN
        alias_name = target.alias_fqn.rsplit(".", 1)[-1]
        
        def make_alias():
            return ast.Name(id=alias_name, ctx=ast.Load())
        
        # Handle different annotation patterns
        
        # Direct type: str -> Alias
        if isinstance(ann, ast.Name):
            if ann.id == "str" and target.slot == "self":
                return make_alias()
            elif ann.id in {"int", "float"} and target.slot == "self":

                return make_alias()
        
        # Optional[str] or str | None
        elif isinstance(ann, ast.Subscript):
            if isinstance(ann.value, ast.Name):
                # Optional[...]
                if ann.value.id == "Optional":
                    inner = self._rewrite_annotation(ann.slice, 
                                                    FieldTarget(target.model_name, 
                                                              target.field_name,
                                                              target.alias_fqn, 
                                                              "self"))
                    if inner:
                        return ast.Subscript(
                            value=ann.value,
                            slice=inner,
                            ctx=ast.Load()
                        )
                
                # List[str], Set[str]
                elif ann.value.id in {"List", "list", "Set", "set"}:
                    if target.slot in {"self", "list_item", "set_item"}:
                        inner = make_alias()
                        return ast.Subscript(
                            value=ann.value,
                            slice=inner,
                            ctx=ast.Load()
                        )
                
                # Dict[str, str] - replace value type only
                elif ann.value.id in {"Dict", "dict"}:
                    if isinstance(ann.slice, ast.Tuple) and len(ann.slice.elts) == 2:
                        key_type, val_type = ann.slice.elts
                        if target.slot in {"self", "dict_value"}:
                            new_val = make_alias()
                            return ast.Subscript(
                                value=ann.value,
                                slice=ast.Tuple(
                                    elts=[key_type, new_val],
                                    ctx=ast.Load()
                                ),
                                ctx=ast.Load()
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
                                value=ann.value,
                                slice=ast.Tuple(elts=new_elts, ctx=ast.Load()),
                                ctx=ast.Load()
                            )
        
        # PEP 604 union: str | None
        elif isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
            left = self._rewrite_annotation(ann.left, target)
            right = ann.right
            
            if left and left != ann.left:
                return ast.BinOp(left=left, op=ast.BitOr(), right=right)
        
        return None


def rewrite_module_with_aliases(
    module_path: Path,
    field_map: Dict[str, FieldTarget],
    verbose: int = 0
) -> bool:
    """Rewrite a generated module to use aliases.
    
    Returns True if any changes were made.
    """
    # Read the module
    code = module_path.read_text(encoding="utf-8")
    
    # Parse AST
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        if verbose:
            print(f"[rewriter] Failed to parse {module_path}: {e}")
        return False
    
    # Filter field map to only entries for classes in this module
    module_classes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            module_classes.add(node.name)
    
    # Filter field targets
    relevant_targets = {
        key: target for key, target in field_map.items()
        if target.model_name in module_classes
    }
    
    if not relevant_targets:
        return False
    
    # Rewrite the AST
    rewriter = FieldRewriter(relevant_targets)
    new_tree = rewriter.visit(tree)
    
    # Check if any imports were added (indicates changes were made)
    if not rewriter.imports_needed:
        return False
    
    # Generate new code
    if sys.version_info >= (3, 9):
        new_code = ast.unparse(new_tree)
    else:
        # Fallback for older Python
        import astor
        new_code = astor.to_source(new_tree)
    
    # Write back
    module_path.write_text(new_code, encoding="utf-8")
    
    if verbose:
        print(f"[rewriter] Updated {module_path.name} with {len(rewriter.imports_needed)} alias imports")
    
    return True


def rewrite_all_modules(
    package_dir: Path,
    field_map: Dict[str, FieldTarget],
    verbose: int = 0
) -> int:
    """Rewrite all modules in a package to use aliases.
    
    Returns the number of modules modified.
    """
    modified_count = 0
    
    for py_file in sorted(package_dir.rglob("*.py")):
        # Skip special files
        if py_file.name.startswith("_"):
            continue
        if py_file.name in {"mutable_base.py", "frozen_base.py", "bases.py"}:
            continue
        if py_file.parent.name == "defs":
    
            continue
        
        # Skip alias modules
        if "_slas_" in py_file.name:
            continue
        
        if rewrite_module_with_aliases(py_file, field_map, verbose):
            modified_count += 1
    
    if verbose and modified_count:
        print(f"[rewriter] Modified {modified_count} modules to use aliases")
    
    return modified_count
