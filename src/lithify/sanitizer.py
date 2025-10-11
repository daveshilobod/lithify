# src/lithify/sanitizer.py
"""
Schema file name sanitization and $ref rewriting.

Ensures generated Python modules have valid names by:
1. Mapping unsafe names (01_user.json) to safe names (user.json or _01_user.json)
2. Rewriting all $refs to use the safe names
3. Creating a sanitized temp directory for codegen
"""

from __future__ import annotations

import json
import keyword
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .core import rewrite_float_const_to_enum

VALID_START = re.compile(r"[A-Za-z_]")
VALID_CHARS = re.compile(r"[^0-9A-Za-z_]")
RESERVED = set(keyword.kwlist)


def safe_module_slug(stem: str) -> str:
    """
    Turn an arbitrary file stem (e.g., '01_user') into a valid Python module name.
    - Strip .schema suffix if present (finding.v1.schema -> finding.v1)
    - Strip numeric prefix if present (01_user -> user)
    - Prefix with '_' if result starts with a digit
    - Replace invalid chars with '_'
    - Avoid keywords
    """
    name = stem

    if name.endswith(".schema"):
        name = name[:-7]

    if name and name[0].isdigit() and "_" in name:
        name = name.split("_", 1)[1]

    if name and not VALID_START.match(name[0]):
        name = "_" + name

    name = VALID_CHARS.sub("_", name)

    if name in RESERVED:
        name = f"{name}_mod"

    name = re.sub(r"_{2,}", "_", name)

    return name or "_mod"


def build_filename_map(src_root: Path) -> dict[str, str]:
    """
    Map '01_user.json' → 'user.json' or '_01_user.json' deterministically.
    If multiple sanitized collisions arise, suffix with increasing numbers.
    """
    taken: set[str] = set()
    mapping: dict[str, str] = {}

    for jf in sorted(src_root.rglob("*.json")):
        if not jf.is_file():
            continue

        stem = jf.stem
        slug = safe_module_slug(stem)
        candidate = slug + ".json"

        if stem.split("_", 1)[0].isdigit() and "_" in stem:
            unprefixed = safe_module_slug(stem.split("_", 1)[1]) + ".json"
            if unprefixed not in taken:
                candidate = unprefixed

        base = candidate[:-5]
        i = 1
        while candidate in taken:
            candidate = f"{base}_{i}.json"
            i += 1

        taken.add(candidate)
        mapping[jf.name] = candidate

    return mapping


def _rewrite_refs(node: Any, name_map: dict[str, str]) -> Any:
    """Recursively rewrite $refs to use sanitized filenames.

    Skips custom URI schemes (urn:, pkg:, etc.) that were already resolved.
    """
    if isinstance(node, dict):
        new = {}
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str) and not v.startswith("#"):
                # Custom URI schemes must have been resolved already - skip rewriting
                if ":" in v and not v.startswith(("./", "../", "http://", "https://")):
                    new[k] = v
                    continue

                base, frag = (v.split("#", 1) + [""])[:2]
                base = base.lstrip("./")

                if base in name_map:
                    v = "./" + name_map[base] + (("#" + frag) if frag else "")

            new[k] = _rewrite_refs(v, name_map)
        return new

    if isinstance(node, list):
        return [_rewrite_refs(x, name_map) for x in node]

    return node


def sanitize_tree(json_root: Path, verbose: int = 0) -> tuple[Path, dict[str, str]]:
    """
    Copy json_root → temp_dir with safe file names and rewritten $refs.
    Returns (temp_dir, file_map).
    """
    tmp = Path(tempfile.mkdtemp(prefix="lithify_"))
    name_map = build_filename_map(json_root)

    if verbose >= 2:
        import typer

        typer.echo(f"[sanitize] Created temp dir: {tmp}")
        typer.echo("[sanitize] File mappings:")
        for orig, safe in name_map.items():
            if orig != safe:
                typer.echo(f"  {orig} → {safe}")

    for src in sorted(json_root.rglob("*.json")):
        if not src.is_file():
            continue

        rel = src.relative_to(json_root)
        safe_name = name_map[src.name]
        dst = tmp / rel.parent / safe_name
        dst.parent.mkdir(parents=True, exist_ok=True)

        data = json.loads(src.read_text(encoding="utf-8"))
        data = _rewrite_refs(data, name_map)

        if "$id" in data and isinstance(data["$id"], str):
            for original_name, sanitized_name in name_map.items():
                if data["$id"].endswith(original_name):
                    data["$id"] = data["$id"].replace(original_name, sanitized_name)
                    break

        # Custom scheme $ids force SchemaIndex to use URNs instead of lithify:/// URIs - strip them
        if "$id" in data and isinstance(data["$id"], str):
            id_value = data["$id"]
            if ":" in id_value and not id_value.startswith(("http://", "https://")):
                del data["$id"]

        data = rewrite_float_const_to_enum(data)
        dst.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

        if verbose >= 3:
            import typer

            typer.echo(f"[sanitize] {src.name} → {safe_name}")

    return tmp, name_map


def cleanup_temp_dir(temp_dir: Path) -> None:
    if temp_dir.exists() and str(temp_dir).startswith(tempfile.gettempdir()):
        shutil.rmtree(temp_dir, ignore_errors=True)
