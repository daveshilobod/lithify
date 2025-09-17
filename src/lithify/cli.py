#!/usr/bin/env python3
"""
Lithify CLI - Generate Pydantic v2 models from JSON Schema with configurable mutability.

Three modes:
- mutable (default): Standard Pydantic models for 90% of use cases
- frozen: Pydantic's frozen=True for configuration objects
- deep-frozen: Recursively immutable for caching, thread safety, and event sourcing
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Optional

import typer

from .enums import Mutability, OutputMode, FormatChoice
from .core import mirror_yaml_to_json, validate_schema_consistency
from .utils import require_deps
from .orchestrator import GenerationConfig, run_generation, SimpleReporter


app = typer.Typer(
    name="lithify",
    help="Turn JSON Schemas into Pydantic models - from mutable to rock-solid immutable.",
    add_completion=False,
    
)



def _verbosity_callback(value: int):
    return max(0, min(value, 3))


@app.command()
def info() -> None:
    """Show information about mutability modes and use cases."""
    print("Lithify Mutability Modes")
    print("-" * 80)
    print(f"{'Mode':<15} {'Attributes':<15} {'Containers':<15} {'Hashable':<10} {'Use Cases'}")
    print("-" * 80)
    print(f"{'mutable':<15} {'✅ Mutable':<15} {'✅ Mutable':<15} {'❌ No':<10} {'APIs, DTOs, ORMs, builders, forms'}")
    print(f"{'frozen':<15} {'❌ Immutable':<15} {'⚠️  Mutable':<15} {'⚠️  Partial':<10} {'Config objects, value objects'}")
    print(f"{'deep-frozen':<15} {'❌ Immutable':<15} {'❌ Immutable':<15} {'✅ Yes':<10} {'Caching, thread safety, event sourcing'}")
    print("-" * 80)
    print("\nExamples:")
    print("  # Standard API models (default)")
    print("  lithify generate --schemas ./api --package-name models")
    print("\n  # Configuration objects")
    print("  lithify generate --schemas ./config --package-name config --mutability frozen")
    print("\n  # Event sourcing models (deep immutability))")
    print("  lithify generate --schemas ./audit --package-name audit --mutability deep-frozen --immutable-hints")
    print("\nLithify: because sometimes your models need to be set in stone.")


@app.command()
def diagnose() -> None:
    """Check environment and dependencies."""
    print("🔍 Lithify Environment Check\n")
    
    deps = {
        "yaml": "PyYAML",
        "datamodel_code_generator": "datamodel-code-generator",
        "pydantic": "Pydantic",
    }
    
    print("Dependencies")
    print("-" * 50)
    print(f"{'Package':<30} {'Status':<10} {'Version'}")
    print("-" * 50)
    
    for module, name in deps.items():
        try:
            m = __import__(module)
            version = getattr(m, "__version__", "unknown")
            print(f"{name:<30} {'✅':<10} {version}")
        except ImportError:
            print(f"{name:<30} {'❌':<10} {'not installed'}")
    print(f"\nPython: {sys.version}")


@app.command()
def clean(
    json_out: Path = typer.Option(..., "--json-out", help="Directory with mirrored JSON schemas"),
    models_out: Path = typer.Option(..., "--models-out", help="Root directory for generated models"),
    package_name: str = typer.Option(..., "--package-name", help="Generated package name"),
) -> None:
    """Remove generated JSON and model packages."""
    removed = []
    
    if json_out.exists():
        shutil.rmtree(json_out)
        removed.append(f"JSON schemas at {json_out}")
    
    pkg_dir = models_out / package_name
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
        removed.append(f"Package at {pkg_dir}")
    
    if removed:
        print("🧹 Cleaned:")
        for item in removed:
            print(f"  - {item}")
    else:
        print("Nothing to clean")


@app.command()
def validate(
    schemas: Path = typer.Option(..., "--schemas", exists=True, help="Root directory of schemas"),
    json_out: Optional[Path] = typer.Option(None, "--json-out", help="Directory for JSON schemas (if not specified, uses temp)"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Remote schema base URL to rewrite"),
    block_remote_refs: bool = typer.Option(False, "--block-remote-refs", help="Treat http(s) $refs as errors"),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, callback=_verbosity_callback),
) -> None:
    """Validate schemas: YAML→JSON mirror, $ref rewriting and resolution."""
    require_deps()
    

    print("Mirroring schemas...")
    json_out.mkdir(parents=True, exist_ok=True)
    written = mirror_yaml_to_json(schemas, json_out, base_url, verbose=verbose)

    print("Validating $refs...")
    validate_schema_consistency(json_out, block_remote_refs=block_remote_refs, base_url=base_url, verbose=verbose)
    
    print(f"✅ Validated {len(written)} schemas")


@app.command()
def generate(
    schemas: Path = typer.Option(..., "--schemas", exists=True, help="Root directory of schemas"),
    json_out: Optional[Path] = typer.Option(None, "--json-out", help="Directory for JSON schemas (if not specified, uses temp)"),
    models_out: Path = typer.Option(..., "--models-out", help="Root directory for generated models"),
    package_name: str = typer.Option(..., "--package-name", help="Generated package name"),
    

    mutability: Mutability = typer.Option(
        Mutability.mutable,
        "--mutability",
        help="Model mutability: mutable (default), frozen (Pydantic's frozen=True), deep-frozen (recursive immutability)",
        case_sensitive=False,
    ),

    base_url: Optional[str] = typer.Option(None, "--base-url", help="Remote schema base URL to rewrite"),
    block_remote_refs: bool = typer.Option(False, "--block-remote-refs", help="Treat http(s) $refs as errors"),

    immutable_hints: bool = typer.Option(
        False,
        "--immutable-hints",
        help="Rewrite type hints to immutable variants (only with --mutability=deep-frozen)"
    ),
    use_frozendict: bool = typer.Option(
        False,
        "--use-frozendict",
        help="Use hashable FrozenDict (only with --mutability=deep-frozen)"
    ),

    from_attributes: bool = typer.Option(
        False,
        "--from-attributes",
        help="Enable from_attributes for ORM compatibility (Pydantic v2)"
    ),

    partial: bool = typer.Option(
        False,
        "--partial",
        help="Make all fields Optional (useful for PATCH endpoints)"
    ),

    output_mode: OutputMode = typer.Option(
        OutputMode.clean,
        "--output-mode",
        help="clean: stage in temp, copy only .py files; debug: write everything in place",
        case_sensitive=False,
    ),
    fmt: FormatChoice = typer.Option(
        FormatChoice.auto,
        "--format",
        help="Code formatter: auto|ruff|black|none",
        case_sensitive=False,
    ),
    no_rewrite: bool = typer.Option(
        False,
        "--no-rewrite",
        help="Skip post-generation rewrite steps (useful for debugging)"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show plan without writing anything"
    ),

    clean_first: bool = typer.Option(False, "--clean", help="Remove existing outputs before generation"),
    check: bool = typer.Option(False, "--check", help="Check if models need regeneration (exit 1 if changes needed)"),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, callback=_verbosity_callback),
) -> None:
    """
    Generate Pydantic models with configurable mutability.
    
    Examples:
        
        # Standard mutable models (most common)
        lithify generate --schemas ./schemas --models-out ./models --package-name api_models
        
        # Configuration objects  
        lithify generate --schemas ./schemas --models-out ./models --package-name config --mutability frozen
        
        # Event sourcing systems
        lithify generate --schemas ./schemas --models-out ./models --package-name audit --mutability deep-frozen --immutable-hints
    """
    require_deps()
    

    cfg = GenerationConfig(
        schemas=schemas,
        json_out=json_out,
        models_out=models_out,
        package_name=package_name,
        mutability=mutability,
        base_url=base_url,
        block_remote_refs=block_remote_refs,
        immutable_hints=immutable_hints,
        use_frozendict=use_frozendict,
        from_attributes=from_attributes,
        partial=partial,
        clean_first=clean_first,
        check=check,
        verbose=verbose,
        output_mode=output_mode,
        fmt=fmt,
        no_rewrite=no_rewrite,
        dry_run=dry_run,
    )
    

    reporter = SimpleReporter()
    
    try:
        result = run_generation(cfg, reporter)
    except RuntimeError as e:
        print(f"❌ {e}")
        raise typer.Exit(1)
    

    print(f"\n{result.human_summary()}")
    
    if mutability == Mutability.mutable:
        print("Models can be modified after creation (standard Pydantic behavior)")
    elif mutability == Mutability.frozen:
        print("Attributes are immutable, but list/dict contents can still be modified")
    else:
        print("Models are lithified - recursively immutable with frozen containers")


if __name__ == "__main__":
    app()