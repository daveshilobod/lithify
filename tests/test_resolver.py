# tests/test_resolver.py

from pathlib import Path

import pytest

from lithify import resolver as resolver_module
from lithify.resolver import load_resolver


def test_load_resolver_from_module(tmp_path):
    resolver_module._resolver_cache = None

    resolver_file = tmp_path / "test_resolver.py"
    resolver_file.write_text(
        "from pathlib import Path\n"
        "\n"
        "def my_resolver(ref: str) -> Path:\n"
        "    # Simple unconditional return for testing\n"
        '    return Path("/test/schema.json")\n'
    )

    resolver = load_resolver(f"{resolver_file}:my_resolver")

    result = resolver("anything")
    assert result == Path("/test/schema.json")
    assert isinstance(result, Path)


def test_load_resolver_invalid_spec():
    resolver_module._resolver_cache = None

    with pytest.raises(ValueError, match="Resolver spec must be"):
        load_resolver("invalid_spec")


def test_load_resolver_missing_file():
    resolver_module._resolver_cache = None

    with pytest.raises(FileNotFoundError):
        load_resolver("/nonexistent/file.py:resolver")


def test_load_resolver_non_python_file(tmp_path):
    resolver_module._resolver_cache = None

    bad_file = tmp_path / "resolver.txt"
    bad_file.write_text("not python")

    with pytest.raises(ValueError, match="must be .py"):
        load_resolver(f"{bad_file}:resolver")


def test_load_resolver_missing_function(tmp_path):
    resolver_module._resolver_cache = None

    resolver_file = tmp_path / "resolver.py"
    resolver_file.write_text("# No functions here")

    with pytest.raises(ValueError, match="has no function"):
        load_resolver(f"{resolver_file}:nonexistent")


def test_load_resolver_not_callable(tmp_path):
    resolver_module._resolver_cache = None

    resolver_file = tmp_path / "resolver.py"
    resolver_file.write_text("not_a_function = 42")

    with pytest.raises(ValueError, match="not callable"):
        load_resolver(f"{resolver_file}:not_a_function")


def test_resolver_caching(tmp_path):
    from lithify import resolver as resolver_module

    resolver_module._resolver_cache = None

    resolver_file = tmp_path / "resolver.py"
    resolver_file.write_text(
        "from pathlib import Path\n" "\n" "def my_resolver(ref: str) -> Path:\n" '    return Path("/test.json")\n'
    )

    resolver1 = load_resolver(f"{resolver_file}:my_resolver")

    resolver2 = load_resolver(f"{resolver_file}:my_resolver")

    assert resolver1 is resolver2
    assert resolver_module._resolver_cache is not None
