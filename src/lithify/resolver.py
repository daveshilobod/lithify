# src/lithify/resolver.py
"""Custom $ref resolution plugin system.

Enables resolution of non-standard URI schemes (urn:, pkg:, etc.) in JSON Schema $refs.
"""

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol


class RefResolver(Protocol):
    """Protocol for custom $ref resolution.

    Enables resolution of custom URI schemes that aren't handled by
    standard JSON Schema $ref mechanics.

    Examples:
        - URN schemes: urn:company:schema:v1
        - Package refs: pkg://internal/common/types
        - Monorepo paths: repo://shared/schemas/user.json
    """

    def __call__(self, ref: str) -> Path:
        """Resolve custom $ref to absolute path of schema file.

        Args:
            ref: The $ref value (may include JSON pointer after #)

        Returns:
            Absolute path to the schema file

        Raises:
            Any exception to signal resolution failure
        """
        ...


# Singleton cache prevents redundant module loads when resolver used across multiple schema files
_resolver_cache: Callable[[str], Path] | None = None


def load_resolver(resolver_spec: str) -> Callable[[str], Path]:
    """Load a custom $ref resolver.

    Args:
        resolver_spec: Either 'module.path:function' or '/file/path.py:function'

    Returns:
        Resolver function

    Raises:
        ValueError: If spec is invalid or resolver not found
        FileNotFoundError: If file path doesn't exist

    Examples:
        >>> resolver = load_resolver('my.module:resolve_urn')
        >>> resolver = load_resolver('/schemas/resolver.py:resolve_pkg')
    """
    global _resolver_cache
    if _resolver_cache is not None:
        return _resolver_cache

    if ":" not in resolver_spec:
        raise ValueError(f"Resolver spec must be 'module:function' or 'path.py:function', " f"got: {resolver_spec}")

    module_or_path, func_name = resolver_spec.rsplit(":", 1)

    if "/" in module_or_path or module_or_path.endswith(".py"):
        module = _load_module_from_file(module_or_path)
    else:
        try:
            module = importlib.import_module(module_or_path)
        except ImportError as e:
            raise ValueError(f"Could not import module '{module_or_path}': {e}") from e

    if not hasattr(module, func_name):
        raise ValueError(f"Module '{module_or_path}' has no function '{func_name}'")

    resolver: Callable[[str], Path] = getattr(module, func_name)

    if not callable(resolver):
        raise ValueError(f"Resolver '{func_name}' is not callable (type: {type(resolver).__name__})")

    _resolver_cache = resolver
    return resolver


def _load_module_from_file(file_path: str) -> Any:
    """Load a Python module from a file path.

    Args:
        file_path: Path to .py file

    Returns:
        Loaded module

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file is not a .py file
    """
    path = Path(file_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Resolver file not found: {path}")

    if path.suffix != ".py":
        raise ValueError(f"Resolver file must be .py, got: {path.suffix}")

    spec = importlib.util.spec_from_file_location("custom_resolver", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load spec from {path}")

    module = importlib.util.module_from_spec(spec)
    # sys.modules registration required before exec - allows relative imports within loaded module
    sys.modules["custom_resolver"] = module
    spec.loader.exec_module(module)

    return module
