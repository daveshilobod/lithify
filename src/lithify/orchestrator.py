# src/lithify/orchestrator.py
"""Generation orchestrator - coordinates the entire model generation pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .bases import inject_base, rebase_generated_models
from .class_name_rewriter import rewrite_class_names
from .core import mirror_yaml_to_json, run_datamodel_codegen, validate_schema_consistency
from .enums import FormatChoice, Mutability, OutputMode
from .formatting import format_path
from .packaging import generate_init_file
from .sanitizer import cleanup_temp_dir, sanitize_tree
from .slas_alias_generator import emit_alias_modules
from .slas_allof_processor import InlineAllOfInfo
from .slas_field_mapper import build_field_map
from .slas_rewriter import rewrite_all_modules
from .slas_schema_index import SchemaIndex
from .transforms import rewrite_type_hints_ast, wrap_all_docstrings
from .validation import validate_deep_frozen_models, validate_frozen_models, validate_mutable_models
from .workspace import copy_selected, staging_dir


@dataclass(slots=True)
class GenerationConfig:
    schemas: Path
    json_out: Path | None
    models_out: Path
    package_name: str
    exclude: list[str] | None
    mutability: Mutability
    base_url: str | None
    block_remote_refs: bool
    custom_ref_resolver: str | None
    immutable_hints: bool
    use_frozendict: bool
    from_attributes: bool
    partial: bool
    clean_first: bool
    check: bool
    verbose: int

    output_mode: OutputMode = OutputMode.clean
    fmt: FormatChoice = FormatChoice.auto
    no_rewrite: bool = False
    dry_run: bool = False
    lenient_allof: bool = False


@dataclass(slots=True)
class GenerationState:
    safe_json_dir: Path | None = None
    schema_index: SchemaIndex | None = None
    field_map: dict[str, Any] | None = None
    temp_alias_dir: Path | None = None
    modules_created: dict[str, list[str]] | None = None
    package_dir: Path | None = None
    base_symbol: str | None = None
    import_module: str | None = None
    inline_allofs: list[InlineAllOfInfo] | None = None
    pattern_properties_map: dict[str, Any] | None = None


class Reporter(Protocol):
    def task(self, label: str): ...
    def info(self, msg: str): ...


class SimpleReporter:
    def task(self, label: str):
        print(label)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def info(self, msg: str):
        print(msg)


@dataclass(slots=True)
class GenerationResult:
    package_dir: Path
    mutability: Mutability

    def human_summary(self) -> str:
        mode_descriptions = {
            Mutability.mutable: "standard mutable",
            Mutability.frozen: "shallow frozen (Pydantic's frozen=True)",
            Mutability.deep_frozen: "deeply immutable (lithified)",
        }
        mode = mode_descriptions[self.mutability]
        return f"[OK] Generated {mode} models in: {self.package_dir}"


def run_generation(cfg: GenerationConfig, r: Reporter) -> GenerationResult:
    _normalize_options(cfg, r)

    if cfg.dry_run:
        r.info(f"[plan] schemas={cfg.schemas}")
        r.info(f"[plan] models_out={cfg.models_out}")
        r.info(f"[plan] package_name={cfg.package_name}")
        r.info(f"[plan] output_mode={cfg.output_mode.value}")
        r.info(f"[plan] format={cfg.fmt.value}")
        r.info(f"[plan] rewrite={'no' if cfg.no_rewrite else 'yes'}")
        r.info(f"[plan] staging={'temp' if cfg.output_mode == OutputMode.clean else 'disabled'}")

        return GenerationResult(package_dir=cfg.models_out / cfg.package_name, mutability=cfg.mutability)

    if cfg.clean_first:
        _clean_outputs(cfg, r)

    st = GenerationState()

    use_staging = cfg.output_mode == OutputMode.clean

    with staging_dir(enabled=use_staging) as stage:
        if cfg.json_out is not None:
            work_json_out = cfg.json_out
            json_is_temp = False
        else:
            import tempfile

            json_temp_dir = Path(tempfile.mkdtemp(prefix="lithify_json_"))
            work_json_out = json_temp_dir
            json_is_temp = True

        if use_staging:
            work_models_out = stage / "models"
        else:
            work_models_out = cfg.models_out

        resolver = None
        if cfg.custom_ref_resolver:
            try:
                from .resolver import load_resolver

                resolver = load_resolver(cfg.custom_ref_resolver)
                if cfg.verbose >= 1:
                    r.info(f"[resolver] Loaded custom $ref resolver from {cfg.custom_ref_resolver}")
            except Exception as e:
                raise RuntimeError(f"Failed to load custom $ref resolver: {e}") from e

        with r.task("Preparing schemas..."):
            work_json_out.mkdir(parents=True, exist_ok=True)
            written = mirror_yaml_to_json(
                cfg.schemas,
                work_json_out,
                cfg.base_url,
                exclude=cfg.exclude,
                custom_ref_resolver=resolver,
                verbose=cfg.verbose,
            )
            if not written:
                raise RuntimeError("No YAML or JSON schema files found")

            # Sanitize names and refs BEFORE indexing
            st.safe_json_dir, _ = sanitize_tree(work_json_out, verbose=cfg.verbose)

            validate_schema_consistency(
                st.safe_json_dir, block_remote_refs=cfg.block_remote_refs, base_url=cfg.base_url, verbose=cfg.verbose
            )

            st.schema_index = SchemaIndex.load(list(st.safe_json_dir.rglob("*.json")), cfg.base_url)

        # allOf collapse before SLAS prevents aliases for structures that will merge
        with r.task("Collapsing allOf scalar refinements..."):
            from .slas_allof_processor import process_allof_collapse

            st.safe_json_dir, st.inline_allofs = process_allof_collapse(
                st.safe_json_dir,
                st.schema_index,
                strict=not cfg.lenient_allof,
                verbose=cfg.verbose,
            )

            # Re-index after collapsing (schemas were modified)
            st.schema_index = SchemaIndex.load(list(st.safe_json_dir.rglob("*.json")), cfg.base_url)

        with r.task("Detecting patternProperties..."):
            from .pattern_properties import detect_all_pattern_properties

            st.pattern_properties_map = detect_all_pattern_properties(st.safe_json_dir, st.schema_index)
            if st.pattern_properties_map and cfg.verbose >= 1:
                r.info(f"[pattern] Found {len(st.pattern_properties_map)} models with patternProperties")

        with r.task("Generating SLAS aliases..."):
            base_class = (
                "MutableBase"
                if cfg.mutability == Mutability.mutable
                else "FrozenBase"
                if cfg.mutability == Mutability.frozen
                else "FrozenModel"
            )
            st.temp_alias_dir = st.safe_json_dir.parent / "_slas_aliases"
            st.temp_alias_dir.mkdir(exist_ok=True)

            ref_map, st.modules_created = emit_alias_modules(
                st.schema_index,
                st.temp_alias_dir,
                cfg.package_name,
                base_class,
                cfg.use_frozendict,
                cfg.verbose,
                json_dir=st.safe_json_dir,
            )

            if st.inline_allofs:
                from .slas_alias_generator import emit_inline_allof_aliases

                inline_ref_map, inline_modules = emit_inline_allof_aliases(
                    st.inline_allofs, st.temp_alias_dir, cfg.package_name, cfg.verbose
                )
                ref_map.update(inline_ref_map)

                for module_name, aliases in inline_modules.items():
                    if module_name in st.modules_created:
                        st.modules_created[module_name].extend(aliases)
                    else:
                        st.modules_created[module_name] = aliases

            st.field_map = build_field_map(
                st.schema_index, ref_map, st.safe_json_dir, cfg.verbose, package_name=cfg.package_name
            )
            _remove_scalar_defs_if_needed(st, cfg, r)

        with r.task("Generating models with DCG..."):
            try:
                st.package_dir = run_datamodel_codegen(
                    st.safe_json_dir, work_models_out, cfg.package_name, partial=cfg.partial, verbose=cfg.verbose
                )
            finally:
                cleanup_temp_dir(st.safe_json_dir)
            st.package_dir.mkdir(parents=True, exist_ok=True)
            _install_aliases(st, r)

        with r.task(f"Applying {cfg.mutability.value} mode..."):
            st.base_symbol, st.import_module = inject_base(
                st.package_dir,
                mode=cfg.mutability.value,
                use_frozendict=cfg.use_frozendict,
                from_attributes=cfg.from_attributes,
                verbose=cfg.verbose,
            )
            rebase_generated_models(st.package_dir, st.base_symbol, st.import_module, verbose=cfg.verbose)

        if st.schema_index and st.schema_index.class_name_overrides:
            with r.task("Applying class name overrides..."):
                rewrite_class_names(st.package_dir, st.schema_index.class_name_overrides, cfg.verbose)

        if st.pattern_properties_map:
            with r.task("Applying patternProperties validation..."):
                from .pattern_properties_rewriter import rewrite_models_for_patterns
                from .pattern_validated_base import PATTERN_VALIDATED_BASE_SOURCE

                pattern_base_file = st.package_dir / "pattern_validated_base.py"
                pattern_base_file.write_text(PATTERN_VALIDATED_BASE_SOURCE)

                modified_count = 0
                for py_file in st.package_dir.glob("*.py"):
                    if py_file.name.startswith("_") or py_file.name in {
                        "__init__.py",
                        "frozen_base.py",
                        "pattern_validated_base.py",
                    }:
                        continue

                    if rewrite_models_for_patterns(py_file, st.pattern_properties_map, cfg.verbose):
                        modified_count += 1

                if cfg.verbose >= 1:
                    r.info(f"[pattern] Modified {modified_count} model files")

        if not cfg.no_rewrite:
            if st.field_map:
                with r.task("Rewriting type hints to aliases..."):
                    rewrite_all_modules(st.package_dir, st.field_map, cfg.verbose)

            if cfg.mutability == Mutability.deep_frozen and cfg.immutable_hints:
                with r.task("Rewriting for immutability..."):
                    rewrite_type_hints_ast(st.package_dir, verbose=cfg.verbose)

        with r.task("Re-wrapping docstrings..."):
            wrap_all_docstrings(st.package_dir, verbose=cfg.verbose)

        with r.task("Validating models..."):
            if cfg.mutability == Mutability.mutable:
                validate_mutable_models(st.package_dir, verbose=cfg.verbose)
            elif cfg.mutability == Mutability.frozen:
                validate_frozen_models(st.package_dir, st.base_symbol, verbose=cfg.verbose)
            else:
                validate_deep_frozen_models(st.package_dir, st.base_symbol, verbose=cfg.verbose)

        _generate_init(st, cfg, r)

        if use_staging:
            final_package_dir = cfg.models_out / cfg.package_name
            with r.task("Copying Python files to final location..."):
                copied = copy_selected(st.package_dir, final_package_dir, ["**/*.py"])
                r.info(f"Copied {len(copied)} Python files to {final_package_dir}")
            st.package_dir = final_package_dir

        with r.task("Formatting generated code..."):
            format_msg = format_path(st.package_dir, cfg.fmt, dry_run=False)
            r.info(format_msg)

        if json_is_temp:
            import shutil

            shutil.rmtree(work_json_out, ignore_errors=True)

        return GenerationResult(package_dir=st.package_dir, mutability=cfg.mutability)


def _normalize_options(cfg: GenerationConfig, r: Reporter) -> None:
    if cfg.immutable_hints and cfg.mutability != Mutability.deep_frozen:
        r.info("--immutable-hints only applies with --mutability=deep-frozen")
        cfg.immutable_hints = False
    if cfg.use_frozendict and cfg.mutability != Mutability.deep_frozen:
        r.info("--use-frozendict only applies with --mutability=deep-frozen")
        cfg.use_frozendict = False
    if cfg.from_attributes and cfg.mutability == Mutability.deep_frozen:
        r.info("--from-attributes may not work well with deep-frozen models")


def _clean_outputs(cfg: GenerationConfig, r: Reporter) -> None:
    import shutil

    if cfg.json_out and cfg.json_out.exists():
        shutil.rmtree(cfg.json_out)
    pkg_dir = cfg.models_out / cfg.package_name
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)


def _remove_scalar_defs_if_needed(st: GenerationState, cfg: GenerationConfig, r: Reporter) -> None:
    if not st.modules_created:
        return
    assert st.safe_json_dir is not None, "safe_json_dir must be set"
    from .slas_schema_processor import remove_scalar_defs

    with r.task("Preprocessing schemas for DCG..."):
        remove_scalar_defs(st.safe_json_dir, st.modules_created, cfg.verbose)
        # DCG requires at least one file to create the package directory
        remaining = list(st.safe_json_dir.rglob("*.json"))
        if not remaining:
            (st.safe_json_dir / "_placeholder.json").write_text(
                '{"$schema":"https://json-schema.org/draft/2020-12/schema","title":"Placeholder","type":"object"}'
            )


def _install_aliases(st: GenerationState, r: Reporter) -> None:
    if not st.modules_created:
        return
    assert st.temp_alias_dir is not None, "temp_alias_dir must be set"
    assert st.package_dir is not None, "package_dir must be set"
    import shutil

    with r.task("Installing type aliases..."):
        for f in st.temp_alias_dir.glob("*.py"):
            if f.name.startswith("_"):
                continue
            shutil.copy2(f, st.package_dir / f.name)
        refmap = st.temp_alias_dir / "_slas_ref_map.json"
        if refmap.exists():
            shutil.copy2(refmap, st.package_dir / "_slas_ref_map.json")
        shutil.rmtree(st.temp_alias_dir)


def _install_inline_allof_aliases(inline_allofs: list, package_dir: Path, package_name: str, verbose: int) -> dict:
    """
    Appends allOf aliases to DCG-generated model files.

    Returns ref_map of JSON pointer -> FQN for field mapper.
    """
    from collections import defaultdict

    from .slas_alias_generator import _class_to_module_name, generate_alias_code
    from .slas_classifier import classify_shape

    if not inline_allofs:
        return {}

    ref_map = {}

    # Group by module
    by_module = defaultdict(list)
    for info in inline_allofs:
        module_name = _class_to_module_name(info.parent_class, info.origin_file)
        alias_name = f"{info.parent_class}_{info.property_name}"
        by_module[module_name].append((alias_name, info))

    # Append to each model file
    for module_name, aliases in by_module.items():
        model_file = package_dir / f"{module_name}.py"
        if not model_file.exists():
            if verbose >= 1:
                print(f"[inline-allof] Warning: {model_file} not found, skipping")
            continue

        content = model_file.read_text(encoding="utf-8")

        # Add imports if needed
        if "StringConstraints" not in content:
            lines = content.split("\n")
            import_idx = 0
            for i, line in enumerate(lines):
                if line.startswith("from pydantic import"):
                    if "StringConstraints" not in line:
                        lines[i] = line.rstrip() + ", StringConstraints"
                    import_idx = i
                    break
                elif line.startswith("import ") or line.startswith("from "):
                    import_idx = i
            else:
                for i, line in enumerate(lines):
                    if line.startswith("from __future__"):
                        import_idx = i + 1
                        break

            if import_idx > 0 and "StringConstraints" not in content:
                lines.insert(import_idx + 1, "from pydantic import StringConstraints")

            content = "\n".join(lines)

        alias_code = "\n\n# Inline allOf aliases\n"
        for alias_name, info in aliases:
            shape = classify_shape(info.merged_schema)
            desc = info.merged_schema.get("description", "")
            if desc:
                alias_code += f"\n# {desc}\n"
            alias_code += generate_alias_code(alias_name, info.merged_schema, shape)

            fqn = f"{package_name}.{module_name}.{alias_name}"
            ref_map[info.json_pointer] = fqn

        model_file.write_text(content + alias_code, encoding="utf-8")

        if verbose >= 2:
            print(f"[inline-allof] Appended {len(aliases)} aliases to {module_name}.py")

    return ref_map


def _generate_init(st: GenerationState, cfg: GenerationConfig, r: Reporter) -> None:
    assert st.package_dir is not None, "package_dir must be set"
    with r.task("Generating __init__.py..."):
        generate_init_file(st.package_dir, verbose=cfg.verbose)
