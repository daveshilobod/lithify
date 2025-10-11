# src/lithify/pattern_properties_rewriter.py
"""AST-based rewriter for patternProperties model inheritance."""

from __future__ import annotations

import ast
from pathlib import Path

from .pattern_properties import PatternPropertyInfo, generate_pattern_class_code


class PatternPropertiesRewriter(ast.NodeTransformer):
    """
    Rewrite models to inherit from PatternValidatedModel.

    Changes:
    1. Inheritance: FrozenModel -> PatternValidatedModel
    2. Config: extra="forbid" -> extra="allow"
    3. Add __pattern_properties__ class variable
    """

    def __init__(self, pattern_info: dict[str, PatternPropertyInfo]):
        self.pattern_info = pattern_info
        self.modified = False

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        """Modify class if it needs pattern validation."""
        if node.name not in self.pattern_info:
            return node

        info = self.pattern_info[node.name]

        for base in node.bases:
            if isinstance(base, ast.Name) and base.id == "FrozenModel":
                base.id = "PatternValidatedModel"
                self.modified = True

        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == "model_config":
                        self._modify_config(item.value)

        pattern_code = generate_pattern_class_code(node.name, info.patterns)
        pattern_node = self._create_pattern_node(pattern_code)

        insert_idx = self._find_config_index(node.body) + 1
        node.body.insert(insert_idx, pattern_node)
        self.modified = True

        return node

    def _modify_config(self, config_node: ast.expr) -> None:
        """Change extra='forbid' to extra='allow' to enable patternProperties validation.
        PatternValidatedModel validates unknown fields against patterns; forbid would block this.
        """
        if not isinstance(config_node, ast.Call):
            return

        for keyword in config_node.keywords:
            if keyword.arg == "extra":
                if isinstance(keyword.value, ast.Constant):
                    if keyword.value.value == "forbid":
                        keyword.value.value = "allow"
                        self.modified = True

    def _find_config_index(self, body: list[ast.stmt]) -> int:
        for i, item in enumerate(body):
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == "model_config":
                        return i
        return 0

    def _create_pattern_node(self, pattern_code: str) -> ast.stmt:
        # Pattern code should be at column 0 for parsing
        module = ast.parse(pattern_code)
        return module.body[0]


def add_required_imports(tree: ast.Module) -> ast.Module:
    """Add PatternValidatedModel and re imports if not present.
    PatternValidatedModel inserted after FrozenModel import; re inserted after last import.
    """
    import_modules = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                import_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for alias in node.names:
                    import_modules.add(f"{node.module}.{alias.name}")

    # Add PatternValidatedModel import after FrozenModel
    if "pattern_validated_base.PatternValidatedModel" not in import_modules:
        for i, node in enumerate(tree.body):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "frozen_base"
                and any(alias.name == "FrozenModel" for alias in node.names)
            ):
                new_import = ast.ImportFrom(
                    module="pattern_validated_base",
                    names=[ast.alias(name="PatternValidatedModel", asname=None)],
                    level=1,
                )
                tree.body.insert(i + 1, new_import)
                break

    if "re" not in import_modules:
        last_import_idx = max(
            (i for i, node in enumerate(tree.body) if isinstance(node, ast.Import | ast.ImportFrom)), default=0
        )
        re_import = ast.Import(names=[ast.alias(name="re", asname=None)])
        tree.body.insert(last_import_idx + 1, re_import)

    return tree


def rewrite_models_for_patterns(
    model_file: Path, pattern_info: dict[str, PatternPropertyInfo], verbose: int = 0
) -> bool:
    """
    Rewrite models to use PatternValidatedModel.

    Returns True if modified.
    """
    source = model_file.read_text(encoding="utf-8")
    tree = ast.parse(source)

    rewriter = PatternPropertiesRewriter(pattern_info)
    new_tree = rewriter.visit(tree)

    if not rewriter.modified:
        return False

    new_tree = add_required_imports(new_tree)
    ast.fix_missing_locations(new_tree)

    new_source = ast.unparse(new_tree)
    model_file.write_text(new_source, encoding="utf-8")

    if verbose >= 2:
        models = list(pattern_info.keys())
        print(f"[pattern] Rewrote {model_file.name} for {models}")

    return True
