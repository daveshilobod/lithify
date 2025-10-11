# src/lithify/class_name_rewriter.py
"""Renames generated classes when schemas specify x-python-class-name."""

from __future__ import annotations

import ast
from pathlib import Path

import typer


def build_rename_map(package_dir: Path, overrides: dict[str, str], verbose: int = 0) -> dict[str, str]:
    """DCG may sanitize schema titles, so we scan for actual class names."""
    rename_map: dict[str, str] = {}

    for py_file in package_dir.rglob("*.py"):
        if py_file.name in {"__init__.py", "mutable_base.py", "frozen_base.py", "frozendict.py"}:
            continue

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for title, desired_name in overrides.items():
                        if node.name == title or node.name == title.replace("-", "_"):
                            rename_map[node.name] = desired_name
                            if verbose >= 2:
                                typer.echo(f"[rename-map] {node.name} -> {desired_name}")
        except SyntaxError:
            continue

    return rename_map


class ClassNameRewriter(ast.NodeTransformer):
    def __init__(self, rename_map: dict[str, str]):
        self.rename_map = rename_map
        self.changed = False

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        if node.name in self.rename_map:
            node.name = self.rename_map[node.name]
            self.changed = True
        return self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if node.id in self.rename_map:
            node.id = self.rename_map[node.id]
            self.changed = True
        return node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
        if node.names:
            for alias in node.names:
                if alias.name in self.rename_map:
                    alias.name = self.rename_map[alias.name]
                    self.changed = True
        return node


def rewrite_class_names(package_dir: Path, overrides: dict[str, str], verbose: int = 0) -> int:
    if not overrides:
        if verbose >= 1:
            typer.echo("[class-rename] No overrides specified")
        return 0

    rename_map = build_rename_map(package_dir, overrides, verbose)

    if not rename_map:
        if verbose >= 1:
            typer.echo("[class-rename] No matching classes found")
        return 0

    modified = 0

    for py_file in package_dir.rglob("*.py"):
        if py_file.name in {"__init__.py", "mutable_base.py", "frozen_base.py", "frozendict.py"}:
            continue

        original_text = py_file.read_text(encoding="utf-8")

        try:
            tree = ast.parse(original_text)
            rewriter = ClassNameRewriter(rename_map)
            new_tree = rewriter.visit(tree)

            if rewriter.changed:
                new_text = ast.unparse(new_tree)
                py_file.write_text(new_text, encoding="utf-8")
                modified += 1

                if verbose >= 1:
                    typer.echo(f"[class-rename] rewrote {py_file.relative_to(package_dir)}")

        except SyntaxError as e:
            typer.secho(f"[class-rename] syntax error in {py_file}: {e}", fg=typer.colors.RED)
            continue

    if verbose >= 1:
        typer.echo(f"[class-rename] modified {modified} files with {len(rename_map)} renames")

    return modified
