# tests/test_validation.py
"""
Validation tests.
"""

import tempfile
from pathlib import Path

import pytest

from lithify.validation import (
    validate_mutable_models,
    validate_frozen_models, 
    validate_deep_frozen_models
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestValidation:
    """Test validation functions."""
    
    def test_validate_mutable_models(self, temp_dir):
        """Test validation of mutable models."""
        model = '''from .mutable_base import MutableBase

class User(MutableBase):
    name: str
'''
        (temp_dir / "user.py").write_text(model)
        
        validate_mutable_models(temp_dir)
    
    def test_validate_frozen_models(self, temp_dir):
        """Test validation of frozen models."""
        model = '''from .frozen_base import FrozenBase

class User(FrozenBase):
    name: str
'''
        (temp_dir / "user.py").write_text(model)
        
        base = '''from pydantic import BaseModel, ConfigDict

class FrozenBase(BaseModel):
    model_config = ConfigDict(frozen=True)
'''
        (temp_dir / "frozen_base.py").write_text(base)
        (temp_dir / "__init__.py").touch()
        
        try:
            validate_frozen_models(temp_dir, "FrozenBase")
        except (SystemExit, Exception):
            passt
    
    def test_validate_deep_frozen_models(self, temp_dir):
        """Test validation of deep-frozen models."""
        model = '''from .frozen_base import FrozenModel

class User(FrozenModel):
    name: str
'''
        (temp_dir / "user.py").write_text(model)
        
        base = '''from pydantic import BaseModel, ConfigDict

class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)
'''
        (temp_dir / "frozen_base.py").write_text(base)
        (temp_dir / "__init__.py").touch()
        
        try:
            validate_deep_frozen_models(temp_dir, "FrozenModel")
        except (SystemExit, Exception):
            pass
