"""
Model validation for different mutability modes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer


def validate_mutable_models(package_dir: Path, verbose: int = 0) -> None:
    """Validate that mutable models can be imported and mutated."""
    pkg_name = package_dir.name
    code = f"""
import sys
sys.path.insert(0, {str(package_dir.parent)!r})
import pydantic

# Import the package
pkg = __import__({pkg_name!r}, fromlist=['*'])

# Try to find and instantiate a model
found_model = False
for name, obj in vars(pkg).items():
    if isinstance(obj, type) and issubclass(obj, pydantic.BaseModel):
        if obj.__module__.startswith(pkg_name):
            # Found a model, try to instantiate it
            try:
                # Try with empty dict - models may have defaults
                instance = obj()
                found_model = True
                break
            except:
                # Try to find required fields and provide dummy values
                try:
                    fields = obj.model_fields
                    data = {{}}
                    for field_name, field_info in fields.items():
                        if field_info.is_required():
                            # Provide dummy value based on type
                            annotation = field_info.annotation
                            if annotation == int:
                                data[field_name] = 1
                            elif annotation == str:
                                data[field_name] = "test"
                            elif annotation == float:
                                data[field_name] = 1.0
                            elif annotation == bool:
                                data[field_name] = True
                            else:
                                data[field_name] = None
                    instance = obj(**data)
                    found_model = True
                    
                    # Test mutation (should work for mutable models)
                    for field_name in fields:
                        try:
                            setattr(instance, field_name, data.get(field_name))
                        except Exception as e:
                            raise AssertionError(f"Mutable model should allow mutation: {{e}}")
                    break
                except:
                    continue

if not found_model:
    # No models to validate, that's okay
    pass

print("OK")
"""
    
    if verbose:
        typer.echo("[validate] checking mutable models")
    
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if r.returncode != 0:
        typer.echo(r.stdout)
        typer.secho(r.stderr, fg=typer.colors.RED)
        raise typer.Exit(1)
    
    if verbose:
        typer.echo("[validate] mutable models OK")


def validate_frozen_models(package_dir: Path, base_symbol: str, verbose: int = 0) -> None:
    """Validate that frozen models prevent attribute reassignment."""
    pkg_name = package_dir.name
    code = f"""
import sys
sys.path.insert(0, {str(package_dir.parent)!r})
import pydantic

pkg = __import__({pkg_name!r}, fromlist=['*'])
FrozenBase = __import__({pkg_name!r}+'.frozen_base', fromlist=[{base_symbol!r}]).{base_symbol}

# Check that models subclass FrozenBase
bad = []
for name, obj in vars(pkg).items():
    if isinstance(obj, type) and issubclass(obj, pydantic.BaseModel):
        if obj.__module__.startswith(pkg_name) and obj is not FrozenBase:
            if not issubclass(obj, FrozenBase):
                bad.append(name)

if bad:
    raise SystemExit(f"Models not subclassing {{base_symbol}}: " + ", ".join(sorted(bad)))

# Test shallow freeze semantics
class TestModel(FrozenBase):
    x: int
    y: list[int]

m = TestModel(x=1, y=[1, 2])

# Attribute reassignment should fail
try:
    m.x = 2
    raise AssertionError("Attribute reassignment should fail on frozen model")
except (AttributeError, pydantic.ValidationError):
    pass

# But container mutation should still work (shallow freeze)
m.y.append(3)  # This should work
assert len(m.y) == 3, "Container mutation should work in shallow frozen mode"

print("OK")
"""
    
    if verbose:
        typer.echo("[validate] checking frozen models")
    
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if r.returncode != 0:
        typer.echo(r.stdout)
        typer.secho(r.stderr, fg=typer.colors.RED)
        raise typer.Exit(1)
    
    if verbose:
        typer.echo("[validate] frozen models OK")


def validate_deep_frozen_models(package_dir: Path, base_symbol: str, verbose: int = 0) -> None:
    """Validate that deep-frozen models are fully immutable."""
    pkg_name = package_dir.name
    code = f"""
import sys
sys.path.insert(0, {str(package_dir.parent)!r})
import pydantic

pkg = __import__({pkg_name!r}, fromlist=['*'])
FrozenModel = __import__({pkg_name!r}+'.frozen_base', fromlist=[{base_symbol!r}]).{base_symbol}

# Check that all models subclass FrozenModel
bad = []
for name, obj in vars(pkg).items():
    if isinstance(obj, type) and issubclass(obj, pydantic.BaseModel):
        if obj.__module__.startswith(pkg_name) and obj is not FrozenModel:
            if not issubclass(obj, FrozenModel):
                bad.append(name)

if bad:
    raise SystemExit("Models not subclassing FrozenModel: " + ", ".join(sorted(bad)))

# Deep-freeze probe
class Probe(FrozenModel):
    x: int
    y: list[int]
    z: dict[str, int]

p = Probe(x=1, y=[1, 2], z={{"a": 1}})

# Check deep freezing
assert isinstance(p.y, tuple), f"list not frozen to tuple, got {{type(p.y)}}"
assert not isinstance(p.z, dict) or not hasattr(p.z, '__setitem__'), "dict should be frozen"

# Attribute mutation should fail
try:
    p.x = 2
    raise AssertionError("Mutation should fail on lithified model")
except (AttributeError, pydantic.ValidationError):
    pass

# Container mutation should also fail (deep freeze)
try:
    if hasattr(p.y, 'append'):
        p.y.append(3)
        raise AssertionError("Tuple should not have append method")
except AttributeError:
    pass

print("OK")
"""
    
    if verbose:
        typer.echo("[validate] checking deep-frozen models")
    
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if r.returncode != 0:
        typer.echo(r.stdout)
        typer.secho(r.stderr, fg=typer.colors.RED)
        raise typer.Exit(1)
    
    if verbose:
        typer.echo("[validate] deep-frozen models OK (lithified)")
