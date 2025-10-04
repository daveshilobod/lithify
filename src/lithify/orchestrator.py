"""Generation orchestrator - coordinates the entire model generation pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, Protocol, List

from .enums import Mutability, OutputMode, FormatChoice
from .core import mirror_yaml_to_json, validate_schema_consistency, run_datamodel_codegen
from .sanitizer import sanitize_tree, cleanup_temp_dir
from .bases import inject_base, rebase_generated_models
from .transforms import rewrite_type_hints_ast, wrap_all_docstrings
from .validation import validate_mutable_models, validate_frozen_models, validate_deep_frozen_models
from .slas_schema_index import SchemaIndex
from .slas_alias_generator import emit_alias_modules
from .slas_field_mapper import build_field_map
from .slas_rewriter import rewrite_all_modules
from .packaging import generate_init_file
from .workspace import staging_dir, copy_selected
from .formatting import format_path

@dataclass(slots=True)
class GenerationConfig:
    schemas: Path
    json_out: Optional[Path]  # Now optional
    models_out: Path
    package_name: str
    mutability: Mutability
    base_url: Optional[str]
    block_remote_refs: bool
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

@dataclass(slots=True)
class GenerationState:
    safe_json_dir: Optional[Path] = None
    schema_index: Optional[SchemaIndex] = None
    field_map: Optional[Dict[str, Any]] = None
    temp_alias_dir: Optional[Path] = None
    modules_created: Optional[Dict[str, List[str]]] = None
    package_dir: Optional[Path] = None
    base_symbol: Optional[str] = None
    import_module: Optional[str] = None

class Reporter(Protocol):
    """Progress reporting interface."""
    def task(self, label: str): ...
    def info(self, msg: str): ...


class SimpleReporter:
    """Simple console reporter without Rich."""
    
    def task(self, label: str):
        """Start a new task (context manager)."""
        print(label)
        return self
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass
    
    def info(self, msg: str):
        """Print an info message."""
        print(msg)


@dataclass(slots=True)
class GenerationResult:
    package_dir: Path
    mutability: Mutability
    
    def human_summary(self) -> str:
        mode_descriptions = {
            Mutability.mutable: "standard mutable",
            Mutability.frozen: "shallow frozen (Pydantic's frozen=True)",
            Mutability.deep_frozen: "deeply immutable (lithified)"
        }
        mode = mode_descriptions[self.mutability]
        return f"✨ Generated {mode} models in: {self.package_dir}"

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
    

    use_staging = (cfg.output_mode == OutputMode.clean)
    
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

        with r.task("Preparing schemas..."):
            work_json_out.mkdir(parents=True, exist_ok=True)
            written = mirror_yaml_to_json(cfg.schemas, work_json_out, cfg.base_url, verbose=cfg.verbose)
            if not written:
                raise RuntimeError("No YAML or JSON schema files found")
            
            # Sanitize names and refs BEFORE indexing
            st.safe_json_dir, _ = sanitize_tree(work_json_out, verbose=cfg.verbose)
            
            validate_schema_consistency(st.safe_json_dir, block_remote_refs=cfg.block_remote_refs, base_url=cfg.base_url, verbose=cfg.verbose)
            
            st.schema_index = SchemaIndex.load(list(st.safe_json_dir.rglob("*.json")), cfg.base_url)

        with r.task("Generating SLAS aliases..."):
            base_class = "MutableBase" if cfg.mutability == Mutability.mutable else "FrozenBase" if cfg.mutability == Mutability.frozen else "FrozenModel"
            st.temp_alias_dir = st.safe_json_dir.parent / "_slas_aliases"
            st.temp_alias_dir.mkdir(exist_ok=True)
            ref_map, st.modules_created = emit_alias_modules(st.schema_index, st.temp_alias_dir, cfg.package_name, base_class, cfg.use_frozendict, cfg.verbose)
            st.field_map = build_field_map(st.schema_index, ref_map, st.safe_json_dir, cfg.verbose)
            _remove_scalar_defs_if_needed(st, cfg, r)

        with r.task("Generating models with DCG..."):
            try:
                st.package_dir = run_datamodel_codegen(st.safe_json_dir, work_models_out, cfg.package_name, partial=cfg.partial, verbose=cfg.verbose)
            finally:
                cleanup_temp_dir(st.safe_json_dir)
            st.package_dir.mkdir(parents=True, exist_ok=True)
            _install_aliases(st, r)

        with r.task(f"Applying {cfg.mutability.value} mode..."):
            st.base_symbol, st.import_module = inject_base(st.package_dir, mode=cfg.mutability.value, use_frozendict=cfg.use_frozendict, from_attributes=cfg.from_attributes, verbose=cfg.verbose)
            rebase_generated_models(st.package_dir, st.base_symbol, st.import_module, verbose=cfg.verbose)


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
    if pkg_dir.exists(): shutil.rmtree(pkg_dir)

def _remove_scalar_defs_if_needed(st: GenerationState, cfg: GenerationConfig, r: Reporter) -> None:
    if not st.modules_created: return
    from .slas_schema_processor import remove_scalar_defs
    with r.task("Preprocessing schemas for DCG..."):
        remove_scalar_defs(st.safe_json_dir, st.modules_created, cfg.verbose)
        # DCG requires at least one file to create the package directory
        remaining = list(st.safe_json_dir.rglob("*.json"))
        if not remaining:
            (st.safe_json_dir / "_placeholder.json").write_text('{"$schema":"https://json-schema.org/draft/2020-12/schema","title":"Placeholder","type":"object"}')

def _install_aliases(st: GenerationState, r: Reporter) -> None:
    if not st.modules_created: return
    import shutil
    with r.task("Installing type aliases..."):
        for f in st.temp_alias_dir.glob("*.py"):
            if f.name.startswith("_"): continue
            shutil.copy2(f, st.package_dir / f.name)
        refmap = st.temp_alias_dir / "_slas_ref_map.json"
        if refmap.exists():
            shutil.copy2(refmap, st.package_dir / "_slas_ref_map.json")
        shutil.rmtree(st.temp_alias_dir)

def _generate_init(st: GenerationState, cfg: GenerationConfig, r: Reporter) -> None:
    with r.task("Generating __init__.py..."):
        generate_init_file(st.package_dir, verbose=cfg.verbose)
