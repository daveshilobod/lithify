
# tests/test_transforms.py
""" 
Transform tests.
"""

import ast
import sys
import tempfile
from pathlib import Path

import pytest

from lithify.transforms import TypeHintTransformer, rewrite_type_hints_ast
from lithify.slas_rewriter import FieldRewriter
from lithify.slas_field_mapper import FieldTarget


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestTransforms:
    """Test AST type hint transformations."""
    
    def test_type_hint_transformer_basic(self):
        """Test basic type hint transformations."""
        transformer = TypeHintTransformer()
        
        assert hasattr(transformer, 'visit')
        
        code = "from typing import List\nx: List[str] = []"
        tree = ast.parse(code)
        transformed = transformer.visit(tree)
        assert transformed is not None
    
    def test_rewrite_type_hints_in_file(self, temp_dir):
        """Test type hint rewriting in a Python file."""
        code = '''from typing import List, Dict, Set, Optional

class TestModel:
    items: List[str]
    mapping: Dict[str, int]
    tags: Set[str]
    optional: Optional[List[int]]
'''
        test_file = temp_dir / "test_model.py"
        test_file.write_text(code)
        
        rewrite_type_hints_ast(temp_dir)
        
        result = test_file.read_text()
        
        assert "tuple[" in result or "Tuple[" in result
        assert "frozenset[" in result or "Frozenset[" in result
        assert "Mapping[" in result
        assert "from typing import" in result
        assert "Mapping" in result


class TestASTRewriter:
    """Test AST-based type hint rewriting."""
    
    def test_rewriter_basic_types(self):
        """Test rewriting basic type annotations."""
        source = """
from pydantic import BaseModel
from typing import Optional

class Probe(BaseModel):
    version: str
    identifier: str
    optional_id: Optional[str]
    nullable_id: str | None
"""
        
        field_map = {
            "Probe.version": FieldTarget(
                "Probe", "version",
                "test_package.common_types.VersionString", "self"
            ),
            "Probe.identifier": FieldTarget(
                "Probe", "identifier",
                "test_package.common_types.IdHex16Or32", "self"
            ),
            "Probe.optional_id": FieldTarget(
                "Probe", "optional_id",
                "test_package.common_types.UuidLower", "self"
            ),
            "Probe.nullable_id": FieldTarget(
                "Probe", "nullable_id",
                "test_package.common_types.IdHex16", "self"
            ),
        }
        
        tree = ast.parse(source)
        rewriter = FieldRewriter(field_map)
        new_tree = rewriter.visit(tree)
        
        assert len(rewriter.imports_needed) > 0
        
        new_code = ast.unparse(new_tree) if sys.version_info >= (3, 9) else str(new_tree)
        
        assert "VersionString" in new_code
        assert "IdHex16Or32" in new_code
        assert "Optional[UuidLower]" in new_code or "UuidLower | None" in new_code
        assert "IdHex16 | None" in new_code
    
    def test_rewriter_containers(self):
        """Test rewriting container type annotations."""
        source = """
from pydantic import BaseModel

class Event(BaseModel):
    ids: list[str]
    tags: set[str]
    metadata: dict[str, str]
    pair: tuple[str, str]
"""
        
        field_map = {
            "Event.ids": FieldTarget(
                "Event", "ids",
                "test_package.common_types.IdHex16", "list_item"
            ),
            "Event.tags": FieldTarget(
                "Event", "tags",
                "test_package.common_types.NonEmptyString", "set_item"
            ),
            "Event.metadata": FieldTarget(
                "Event", "metadata",
                "test_package.common_types.NonEmptyString", "dict_value"
            ),
            "Event.pair": FieldTarget(
                "Event", "pair",
                "test_package.common_types.VersionString", "tuple_items"
            ),
        }
        
        tree = ast.parse(source)
        rewriter = FieldRewriter(field_map)
        new_tree = rewriter.visit(tree)
        
        new_code = ast.unparse(new_tree) if sys.version_info >= (3, 9) else str(new_tree)
        
        assert "list[IdHex16]" in new_code
        assert "set[NonEmptyString]" in new_code
        assert "dict[str, NonEmptyString]" in new_code
        assert "tuple[str, str]" in new_code
