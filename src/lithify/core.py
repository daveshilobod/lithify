# src/lithify/core.py
"""
Core schema processing functionality.

Handles YAML→JSON mirroring, $ref rewriting, and validation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer

Json = dict[str, Any] | list[Any] | str | int | float | bool | None


_NUMERIC_COMPOSITES = ("allOf", "anyOf", "oneOf")
_OBJECT_FIELDS = ("properties", "patternProperties", "dependentSchemas")
_ARRAY_FIELDS = ("items", "prefixItems", "contains")
_META_FIELDS = ("if", "then", "else", "not", "additionalProperties")


def _infer_type(value: Any) -> str | None:
    if value is None:
        return "null"
    elif isinstance(value, bool):  # Must check before int!
        return "boolean"
    elif isinstance(value, int | Decimal):
        # Check if Decimal has fractional part
        if isinstance(value, Decimal):
            exp = value.as_tuple().exponent
            # Handle special values (NaN, Infinity have non-int exponents)
            if not isinstance(exp, int) or exp < 0:
                return "number"
        return "integer"
    elif isinstance(value, float | Decimal):
        return "number"
    elif isinstance(value, str):
        return "string"
    elif isinstance(value, list):
        return "array"
    elif isinstance(value, dict):
        return "object"
    return None


def _maybe_rewrite_const(node: dict[str, Any]) -> None:
    """Rewrite const to handle float limitation in Python's Literal type."""
    if "const" not in node:
        return

    const_val = node.pop("const")

    # Python's Literal type doesn't support float - use min/max constraints instead
    if isinstance(const_val, float) and not isinstance(const_val, bool):
        node["type"] = "number"
        node["minimum"] = const_val
        node["maximum"] = const_val
    else:
        if "enum" in node:
            if const_val not in node["enum"]:
                node["enum"].append(const_val)
        else:
            node["enum"] = [const_val]

        if "type" not in node:
            inferred_type = _infer_type(const_val)
            if inferred_type:
                node["type"] = inferred_type


def _walk(node: Json) -> None:
    if isinstance(node, dict):
        _maybe_rewrite_const(node)

        # Single-value float enums have same Literal[float] issue as const
        if (
            "enum" in node
            and isinstance(node["enum"], list)
            and len(node["enum"]) == 1
            and isinstance(node["enum"][0], float)
            and not isinstance(node["enum"][0], bool)
        ):
            f = node.pop("enum")[0]
            node["type"] = "number"
            node["minimum"] = f
            node["maximum"] = f

        for key in _NUMERIC_COMPOSITES + _META_FIELDS:
            if key in node:
                _walk(node[key])

        for key in _OBJECT_FIELDS:
            if key in node and isinstance(node[key], dict):
                for sub in node[key].values():
                    _walk(sub)

        for key in _ARRAY_FIELDS:
            if key in node:
                _walk(node[key])

        for key in ("definitions", "$defs", "components", "schemas"):
            if key in node and isinstance(node[key], dict):
                for sub in node[key].values():
                    _walk(sub)

    elif isinstance(node, list):
        for item in node:
            _walk(item)


def rewrite_const_to_enum(root_schema: dict[str, Any]) -> dict[str, Any]:
    """
    Fix const and single-value enum issues for DCG compatibility.

    Transforms:
    - Float const → min/max constraints (avoids Literal[float] which Python doesn't support)
    - Single-float enum → min/max constraints
    - Other const → single-value enum (becomes Literal[...])

    This works around two issues:
    1. DCG crashes on float const values
    2. Python's Literal type doesn't support float values
    """
    _walk(root_schema)
    return root_schema


rewrite_float_const_to_enum = rewrite_const_to_enum


def load_yaml_safe(path: Path) -> Any:
    import yaml

    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def dump_json(path: Path, data: Any) -> None:
    """Write JSON with deterministic output (sorted keys)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)


def build_schema_map(yaml_root: Path) -> dict[str, str]:
    """
    Map schema file names used in refs to the mirrored JSON target names.
    Handles numbered prefixes and .schema.json → .json.

    Example:
      04_identity.yaml → "identity.schema.json" -> "04_identity.json"
                          "identity.json"       -> "04_identity.json"
    """
    schema_map: dict[str, str] = {}
    for src in list(yaml_root.rglob("*.yaml")) + list(yaml_root.rglob("*.yml")):
        if not src.is_file():
            continue
        name = src.stem
        target = f"{name}.json"
        unprefixed = name.split("_", 1)[1] if "_" in name and name.split("_", 1)[0].isdigit() else name
        schema_map[f"{unprefixed}.schema.json"] = target
        schema_map[f"{unprefixed}.json"] = target
        schema_map[f"./{unprefixed}.schema.json"] = target
        schema_map[f"./{unprefixed}.json"] = target
    return schema_map


def _rewrite_single_ref(ref: str, base_url: str | None, schema_map: dict[str, str]) -> str:
    original = ref

    def _normalize(fname: str) -> str:
        return fname[2:] if fname.startswith("./") else fname

    def split_frag(s: str) -> tuple[str, str]:
        if "#" in s:
            base, frag = s.split("#", 1)
            return base, "#" + frag
        return s, ""

    if base_url and ref.startswith(base_url):
        remainder = ref[len(base_url) :]
        base, frag = split_frag(remainder)
        key = _normalize(base)
        mapped = schema_map.get(key)
        if not mapped:
            mapped = base.replace(".schema.json", ".json")
        new = mapped + frag
        if not new.startswith("."):
            new = "./" + new
        return new

    if ref.startswith("./") or ref.endswith(".json") or ".schema.json" in ref:
        base, frag = split_frag(ref)
        key = _normalize(base)

        mapped = schema_map.get(key)
        if not mapped:
            mapped = key.replace(".schema.json", ".json")
        new = mapped + frag
        if not new.startswith("."):
            new = "./" + new
        return new

    return original


def rewrite_remote_refs(data: Any, schema_map: dict[str, str], base_url: str | None) -> Any:
    """Convert remote refs (and .schema.json) to local file refs using the schema map."""
    if isinstance(data, dict):
        new = {}
        for k, v in data.items():
            if k == "$ref" and isinstance(v, str):
                new[k] = _rewrite_single_ref(v, base_url, schema_map)
            else:
                new[k] = rewrite_remote_refs(v, schema_map, base_url)
        return new
    if isinstance(data, list):
        return [rewrite_remote_refs(x, schema_map, base_url) for x in data]
    return data


def _rewrite_custom_refs(
    data: Any,
    json_root: Path,
    current_file: Path,
    resolver: Any,
    resolutions: dict[str, Path],
    verbose: int = 0,
) -> Any:
    """Rewrite custom $refs to relative file paths. Resolutions dict tracks mappings for audit."""
    if isinstance(data, dict):
        new = {}
        for k, v in data.items():
            if k == "$ref" and isinstance(v, str):
                if not v.startswith(("#", "./", "../", "http://", "https://")) and ":" in v:
                    ref_base, _, pointer = v.partition("#")

                    try:
                        resolved_abs = resolver(ref_base)
                        if not isinstance(resolved_abs, Path):
                            resolved_abs = Path(resolved_abs)

                        if ref_base not in resolutions:
                            resolutions[ref_base] = resolved_abs

                        if not resolved_abs.exists():
                            raise FileNotFoundError(f"Resolved path does not exist: {resolved_abs}")

                        # Resolved files outside json_root must be copied in to avoid out-of-tree refs
                        try:
                            rel_to_root = resolved_abs.relative_to(json_root)
                            target_path = json_root / rel_to_root
                        except ValueError:
                            target_path = json_root / resolved_abs.name

                            if not target_path.exists():
                                target_path.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(resolved_abs, target_path)

                                # Copied files may contain custom refs - must process recursively
                                if resolver:
                                    copied_data = json.loads(target_path.read_text(encoding="utf-8"))
                                    copied_data = _rewrite_custom_refs(
                                        copied_data, json_root, target_path, resolver, resolutions, verbose
                                    )
                                    with target_path.open("w", encoding="utf-8") as f:
                                        json.dump(copied_data, f, indent=2, ensure_ascii=False, sort_keys=True)

                                if verbose >= 2:
                                    typer.echo(f"[custom-ref-copy] {resolved_abs} -> {target_path}")

                        rel_path = os.path.relpath(target_path, current_file.parent)
                        rel_path = rel_path.replace(os.sep, "/")  # JSON requires forward slashes!!

                        if not rel_path.startswith(("./", "../")):
                            rel_path = "." + "/" + rel_path

                        if pointer:
                            rel_path = f"{rel_path}#{pointer}"

                        new[k] = rel_path

                        if verbose >= 2:
                            typer.echo(f"[custom-ref] {v} → {new[k]}")

                    except Exception as e:
                        raise ValueError(f"Failed to resolve custom $ref '{v}' in {current_file}: {e}") from e
                else:
                    new[k] = v
            else:
                new[k] = _rewrite_custom_refs(v, json_root, current_file, resolver, resolutions, verbose)
        return new
    elif isinstance(data, list):
        return [_rewrite_custom_refs(item, json_root, current_file, resolver, resolutions, verbose) for item in data]
    else:
        return data


def _should_exclude(path: Path, yaml_root: Path, exclude_patterns: list[str] | None) -> bool:
    if not exclude_patterns:
        return False

    try:
        rel_path = path.relative_to(yaml_root)
    except ValueError:
        return False

    for part in rel_path.parts:
        if part in exclude_patterns:
            return True

    return False


def mirror_yaml_to_json(
    yaml_root: Path,
    json_root: Path,
    base_url: str | None,
    exclude: list[str] | None = None,
    custom_ref_resolver: Any | None = None,
    verbose: int = 0,
) -> list[Path]:
    """Mirror YAML/JSON schemas to json_root, rewriting remote refs and applying custom resolver if provided."""
    written: list[Path] = []
    schema_map = build_schema_map(yaml_root)
    custom_resolutions: dict[str, Path] = {}

    for src in list(yaml_root.rglob("*.yaml")) + list(yaml_root.rglob("*.yml")):
        if not src.is_file():
            continue

        if _should_exclude(src, yaml_root, exclude):
            if verbose >= 2:
                typer.echo(f"[excluded] {src.relative_to(yaml_root)}")
            continue

        rel = src.relative_to(yaml_root)
        dst = (json_root / rel).with_suffix(".json")
        data = load_yaml_safe(src)
        data = rewrite_remote_refs(data, schema_map, base_url)

        if custom_ref_resolver:
            data = _rewrite_custom_refs(data, json_root, dst, custom_ref_resolver, custom_resolutions, verbose)

        data = rewrite_const_to_enum(data)  # Fix datamodel-code-generator const issues
        dump_json(dst, data)
        written.append(dst)
        if verbose >= 2:
            typer.echo(f"[yaml→json] {src} -> {dst}")

    # Skip files already in json_root to avoid infinite recursion
    for src in yaml_root.rglob("*.json"):
        if not src.is_file():
            continue

        try:
            src.relative_to(json_root)
            continue
        except ValueError:
            pass

        if _should_exclude(src, yaml_root, exclude):
            if verbose >= 2:
                typer.echo(f"[excluded] {src.relative_to(yaml_root)}")
            continue

        rel = src.relative_to(yaml_root)
        dst = json_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(src.read_text(encoding="utf-8"))
        data = rewrite_remote_refs(data, schema_map, base_url)

        if custom_ref_resolver:
            data = _rewrite_custom_refs(data, json_root, dst, custom_ref_resolver, custom_resolutions, verbose)

        data = rewrite_const_to_enum(data)  # Fix datamodel-code-generator const issues
        dump_json(dst, data)
        written.append(dst)
        if verbose >= 2:
            typer.echo(f"[json→json] {src} -> {dst}")

    if custom_ref_resolver and custom_resolutions and verbose >= 1:
        typer.echo(f"[custom-refs] Resolved {len(custom_resolutions)} unique custom $refs")
        if verbose >= 2:
            for ref, path in sorted(custom_resolutions.items()):
                typer.echo(f"  {ref} → {path}")

    return written


def _iter_refs(node: Any) -> Iterable[str]:
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str):
            yield node["$ref"]
        for v in node.values():
            yield from _iter_refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_refs(v)


def validate_schema_consistency(
    json_root: Path,
    block_remote_refs: bool = False,
    base_url: str | None = None,
    verbose: int = 0,
) -> None:
    """
    Ensure that all file refs resolve under json_root. Remote refs warn or error.
    """
    problems: list[str] = []
    warnings: list[str] = []

    # Resolve json_root to handle symlinks (/var -> /private/var on macOS)
    json_root = json_root.resolve()

    for jf in sorted(json_root.rglob("*.json")):
        try:
            schema = json.loads(jf.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problems.append(f"[invalid-json] {jf.relative_to(json_root)}: {e}")
            continue

        for ref in _iter_refs(schema):
            if ref.startswith("#"):
                continue
            if ref.startswith("http://") or ref.startswith("https://"):
                msg = f"[remote-ref] {jf.relative_to(json_root)}: {ref}"
                if block_remote_refs:
                    problems.append(msg)
                else:
                    warnings.append(msg)
                continue

            base = ref.split("#", 1)[0]
            candidate = (jf.parent / base).resolve()
            if not candidate.exists():
                rel_target = candidate
                try:
                    rel_target = candidate.relative_to(json_root)
                except Exception:
                    pass
                problems.append(f"[missing-ref] {jf.relative_to(json_root)}: '{ref}' -> '{rel_target}' not found")
            elif not candidate.is_file():
                problems.append(f"[bad-ref] {jf.relative_to(json_root)}: '{ref}' -> '{candidate}' is not a file")

            elif json_root not in candidate.resolve().parents and candidate.resolve() != json_root:
                problems.append(
                    f"[out-of-tree-ref] {jf.relative_to(json_root)}: '{ref}' -> '{candidate}' outside '{json_root}'"
                )

    for w in warnings:
        typer.secho(f"Warning: {w}", fg=typer.colors.YELLOW)
    if problems:
        for p in problems:
            typer.secho(f"Error: {p}", fg=typer.colors.RED)
        raise typer.Exit(1)
    if verbose:
        typer.echo("[validate] schema $refs OK")


def run_datamodel_codegen(
    json_root: Path, models_out_dir: Path, package_name: str, partial: bool = False, verbose: int = 0
) -> Path:
    package_dir = models_out_dir / package_name
    package_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "datamodel_code_generator",
        "--input",
        str(json_root),
        "--input-file-type",
        "jsonschema",
        "--output",
        str(package_dir),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.11",
        "--use-standard-collections",
        "--use-double-quotes",
        "--disable-timestamp",
        "--reuse-model",
        "--collapse-root-models",
        "--snake-case-field",
        "--field-constraints",
        "--use-field-description",
        "--use-default",
        "--use-annotated",
        "--strict-nullable",
        "--enum-field-as-literal",
        "one",  # Literal[...] for single-value enums
        "--use-title-as-name",
    ]

    if partial:
        cmd.append("--use-default-kwarg")

    if verbose:
        typer.echo("[generator] " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        typer.echo(result.stdout)
        typer.secho(result.stderr, fg=typer.colors.RED)
        raise typer.Exit(1)

    (package_dir / "__init__.py").touch(exist_ok=True)
    return package_dir
