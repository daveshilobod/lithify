"""
Base class injection for different mutability modes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Tuple

import typer



MUTABLE_BASE_TEMPLATE = '''"""
Standard mutable Pydantic models.
"""

from pydantic import BaseModel, ConfigDict

class MutableBase(BaseModel):
    """Standard mutable Pydantic model with validation."""
    model_config = ConfigDict(
        validate_assignment=True,
        extra='forbid',
        from_attributes={from_attributes},
    )
'''


FROZEN_BASE_TEMPLATE = '''"""
Pydantic's built-in frozen models (shallow immutability).
"""

from pydantic import BaseModel, ConfigDict

class FrozenBase(BaseModel):
    """Pydantic's frozen=True model. Attributes are immutable but containers are not."""
    model_config = ConfigDict(
        frozen=True,
        extra='forbid',
        from_attributes={from_attributes},
    )
'''


DEEP_FROZEN_BASE_TEMPLATE = '''"""
Lithified base for Pydantic v2 models with deep immutability.

Containers are frozen recursively:
- list → tuple
- set  → frozenset
- dict → {dict_impl}
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict
{maybe_mappingproxy_import}{maybe_frozendict_import}

def _deep_freeze(value: Any) -> Any:
    """Recursively freeze mutable containers."""
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    if isinstance(value, tuple):
        return tuple(_deep_freeze(v) for v in value)
    if isinstance(value, frozenset):
        return frozenset(_deep_freeze(v) for v in value)
    if isinstance(value, list):
        return tuple(_deep_freeze(v) for v in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(v) for v in value)
    if isinstance(value, dict):
        frozen_items = {{k: _deep_freeze(v) for k, v in value.items()}}
        return {dict_ctor}(frozen_items)
    return value


class FrozenModel(BaseModel):
    """Lithified model - deeply immutable for data integrity."""
    model_config = ConfigDict(
        frozen=True,
        extra='ignore',
        validate_assignment=True,
        from_attributes={from_attributes},
    )
    
    def model_post_init(self, __context) -> None:
        """Deep-freeze after Pydantic v2 initialization."""
        frozen_values = {{k: _deep_freeze(getattr(self, k)) for k in self.model_fields}}
        for k, v in frozen_values.items():
            object.__setattr__(self, k, v)
    
    def __setattr__(self, name: str, value: Any) -> None:
        """Block attribute setting after initialization."""
        if hasattr(self, '__pydantic_fields_set__'):
            raise AttributeError(f"Cannot modify lithified model attribute '{{name}}'")
        super().__setattr__(name, value)
    
    def __delattr__(self, name: str) -> None:
        """Block attribute deletion."""
        raise AttributeError(f"Cannot delete lithified model attribute '{{name}}'")
'''


from .frozendict import FROZENDICT_SOURCE


def inject_base(
    package_dir: Path,
    mode: Literal["mutable", "frozen", "deep-frozen"],
    *,
    use_frozendict: bool = False,
    from_attributes: bool = False,
    verbose: int = 0
) -> Tuple[str, str]:
    """
    Inject the appropriate base class based on mutability mode.
    
    Returns: (base_symbol, import_module) tuple
    """
    if mode == "mutable":
        source = MUTABLE_BASE_TEMPLATE.format(from_attributes=from_attributes)
        base_file = "mutable_base.py"
        base_symbol = "MutableBase"
        import_module = "mutable_base"
        
    elif mode == "frozen":
        source = FROZEN_BASE_TEMPLATE.format(from_attributes=from_attributes)
        base_file = "frozen_base.py"
        base_symbol = "FrozenBase"
        import_module = "frozen_base"
        
    else:  # deep-frozen
        base_file = "frozen_base.py"
        base_symbol = "FrozenModel"
        import_module = "frozen_base"
        

        if use_frozendict:
            (package_dir / "frozendict.py").write_text(FROZENDICT_SOURCE, encoding="utf-8")
            source = DEEP_FROZEN_BASE_TEMPLATE.format(
                dict_impl="FrozenDict",
                maybe_mappingproxy_import="",
                maybe_frozendict_import="from .frozendict import FrozenDict\n",
                dict_ctor="FrozenDict",
                from_attributes=from_attributes,
            )
        else:
            source = DEEP_FROZEN_BASE_TEMPLATE.format(
                dict_impl="read-only MappingProxyType",
                maybe_mappingproxy_import="from types import MappingProxyType\n",
                maybe_frozendict_import="",
                dict_ctor="MappingProxyType",
                from_attributes=from_attributes,
            )
    

    (package_dir / base_file).write_text(source, encoding="utf-8")
    if verbose >= 1:
        typer.echo(f"[inject] wrote {package_dir / base_file} for {mode} mode")
    
    return base_symbol, import_module


def rebase_generated_models(
    package_dir: Path,
    base_symbol: str,
    import_module: str,
    verbose: int = 0
) -> None:
    """
    Rebase all generated models to use the injected base class.
    """
    changed = 0
    basemodel_re = re.compile(r"\(BaseModel\)")
    
    for py in package_dir.glob("*.py"):
        if py.name in {"__init__.py", "mutable_base.py", "frozen_base.py", "frozendict.py"}:
            continue
        
        original_text = py.read_text(encoding="utf-8")
        

        import_line = f"from .{import_module} import {base_symbol}"
        
        if import_line not in original_text:
            lines = original_text.splitlines(keepends=True)
            

            last_future_idx = -1
            for i, line in enumerate(lines):
                if 'from __future__ import' in line:
                    last_future_idx = i
            

            if last_future_idx >= 0:

                lines.insert(last_future_idx + 1, import_line + '\n')
            else:

                lines.insert(0, import_line + '\n')
            
            text = ''.join(lines)
        else:
            text = original_text
        

        new_text = basemodel_re.sub(f"({base_symbol})", text)
        
        if new_text != original_text:
            py.write_text(new_text, encoding="utf-8")
            changed += 1
    
    if verbose >= 1:
        typer.echo(f"[inject] rebased {changed} files to {base_symbol}")
