# src/lithify/packaging.py
"""
Generates __init__.py with categorized imports.
Ordering: base classes -> type aliases -> models to satisfy Python import dependencies.
"""

from pathlib import Path


def generate_init_file(package_dir: Path, verbose: int = 0) -> None:
    """Generate __init__.py with categorized imports: base classes, type aliases, models.
    Ordering prevents import errors when models depend on base classes or type aliases.
    """
    py_files = sorted([f.stem for f in package_dir.glob("*.py") if f.stem != "__init__" and not f.stem.startswith("_")])

    if not py_files:
        return

    base_files = [f for f in py_files if "base" in f.lower()]
    type_files = [f for f in py_files if f in ["common_types", "aliases"]]
    model_files = [f for f in py_files if f not in base_files + type_files]

    init_lines = [
        '"""',
        "Generated Pydantic models from JSON schemas.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]

    if base_files:
        init_lines.append("# Base classes")
        for f in base_files:
            init_lines.append(f"from . import {f}")
        init_lines.append("")

    if type_files:
        init_lines.append("# Type aliases")
        for f in type_files:
            init_lines.append(f"from . import {f}")
        init_lines.append("")

    if model_files:
        init_lines.append("# Models")
        for f in sorted(model_files):
            init_lines.append(f"from . import {f}")
        init_lines.append("")

    init_lines.append("__all__ = [")
    for f in sorted(py_files):
        init_lines.append(f'    "{f}",')
    init_lines.append("]")
    init_lines.append("")

    (package_dir / "__init__.py").write_text("\n".join(init_lines))

    if verbose:
        print(f"Generated __init__.py with {len(py_files)} modules")
