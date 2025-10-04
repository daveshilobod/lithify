"""
Utility functions for lithify.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import typer


def write_if_changed(path: Path, content: str) -> bool:
    """
    Write content to path only if it differs from existing content.
    
    Returns True if the file was written/changed, False if unchanged.
    """
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return False
    
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return True


def require_deps() -> None:
    """Check that required dependencies are installed."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        typer.secho("Missing dependency: PyYAML. Install with: pip install pyyaml", fg=typer.colors.RED)
        raise typer.Exit(1)
    
    try:
        import datamodel_code_generator  # noqa: F401
    except ImportError:
        typer.secho(
            "Missing dependency: datamodel-code-generator. Install with: pip install 'datamodel-code-generator[http]'",
            fg=typer.colors.RED
        )
        raise typer.Exit(1)
    
    try:
        import pydantic  # noqa: F401
    except ImportError:
        typer.secho("Missing dependency: pydantic. Install with: pip install pydantic", fg=typer.colors.RED)
        raise typer.Exit(1)


def write_manifest(
    package_dir: Path,
    *,
    mutability: str,
    immutable_hints: bool,
    use_frozendict: bool,
    from_attributes: bool,
    verbose: int = 0
) -> None:
    """Write manifest.json with package metadata."""
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
