# tests/test_class_name_rewriter.py

import tempfile
from pathlib import Path

import pytest

from lithify.class_name_rewriter import (
    ClassNameRewriter,
    build_rename_map,
    rewrite_class_names,
)
from lithify.enums import FormatChoice, Mutability, OutputMode
from lithify.orchestrator import GenerationConfig, SimpleReporter, run_generation
from lithify.slas_schema_index import extract_class_name_override


def test_extract_override_from_schema():
    schema = {"title": "UserProfileV1", "x-python-class-name": "UserProfile", "type": "object"}

    assert extract_class_name_override(schema) == "UserProfile"


def test_no_override_returns_none():
    schema = {"title": "User", "type": "object"}
    assert extract_class_name_override(schema) is None


def test_build_rename_map():
    with tempfile.TemporaryDirectory() as tmpdir:
        package_dir = Path(tmpdir)

        test_file = package_dir / "models.py"
        test_file.write_text(
            "from pydantic import BaseModel\n\n" "class UserProfileV1(BaseModel):\n" "    user_id: str\n"
        )

        overrides = {"UserProfileV1": "UserProfile"}
        rename_map = build_rename_map(package_dir, overrides, verbose=0)

        assert rename_map == {"UserProfileV1": "UserProfile"}


def test_build_rename_map_with_sanitization():
    with tempfile.TemporaryDirectory() as tmpdir:
        package_dir = Path(tmpdir)

        test_file = package_dir / "models.py"
        test_file.write_text(
            "from pydantic import BaseModel\n\n" "class User_Profile_V1(BaseModel):\n" "    user_id: str\n"
        )

        overrides = {"User-Profile-V1": "UserProfile"}
        rename_map = build_rename_map(package_dir, overrides, verbose=0)

        assert rename_map == {"User_Profile_V1": "UserProfile"}


def test_class_name_rewriter_renames_class():
    import ast

    code = """
class OldName(BaseModel):
    field: str
"""

    tree = ast.parse(code)
    rewriter = ClassNameRewriter({"OldName": "NewName"})
    new_tree = rewriter.visit(tree)

    assert rewriter.changed
    class_def = new_tree.body[0]
    assert isinstance(class_def, ast.ClassDef)
    assert class_def.name == "NewName"


def test_class_name_rewriter_renames_references():
    import ast

    code = """
class OldName(BaseModel):
    pass

class Other(BaseModel):
    ref: OldName
"""

    tree = ast.parse(code)
    rewriter = ClassNameRewriter({"OldName": "NewName"})
    new_tree = rewriter.visit(tree)

    assert rewriter.changed
    # Check both class and reference renamed
    new_code = ast.unparse(new_tree)
    assert "class NewName" in new_code
    assert "ref: NewName" in new_code
    assert "OldName" not in new_code


def test_class_name_rewriter_renames_imports():
    import ast

    code = "from .models import OldName\n"

    tree = ast.parse(code)
    rewriter = ClassNameRewriter({"OldName": "NewName"})
    new_tree = rewriter.visit(tree)

    assert rewriter.changed
    new_code = ast.unparse(new_tree)
    assert "NewName" in new_code
    assert "OldName" not in new_code


def test_class_name_rewriter_renames_base_classes():
    import ast

    code = """
class OldName(BaseModel):
    pass

class Child(OldName):
    extra: str
"""

    tree = ast.parse(code)
    rewriter = ClassNameRewriter({"OldName": "NewName"})
    new_tree = rewriter.visit(tree)

    assert rewriter.changed
    new_code = ast.unparse(new_tree)
    assert "class NewName(BaseModel)" in new_code
    assert "class Child(NewName)" in new_code
    assert "OldName" not in new_code


def test_rewrite_class_names_integration():
    with tempfile.TemporaryDirectory() as tmpdir:
        package_dir = Path(tmpdir)

        (package_dir / "user.py").write_text(
            "from pydantic import BaseModel\n\n" "class UserProfileV1(BaseModel):\n" "    user_id: str\n"
        )

        (package_dir / "other.py").write_text(
            "from .user import UserProfileV1\n\n" "def get_profile() -> UserProfileV1:\n" "    pass\n"
        )

        overrides = {"UserProfileV1": "UserProfile"}
        modified = rewrite_class_names(package_dir, overrides, verbose=0)

        assert modified == 2

        user_content = (package_dir / "user.py").read_text()
        assert "class UserProfile(BaseModel)" in user_content
        assert "UserProfileV1" not in user_content

        other_content = (package_dir / "other.py").read_text()
        assert "from .user import UserProfile" in other_content
        assert "-> UserProfile:" in other_content
        assert "UserProfileV1" not in other_content


def test_rewrite_class_names_skips_base_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        package_dir = Path(tmpdir)

        (package_dir / "__init__.py").write_text("from .models import OldName")
        (package_dir / "mutable_base.py").write_text("class OldName(BaseModel): pass")
        (package_dir / "frozen_base.py").write_text("class OldName(BaseModel): pass")
        (package_dir / "frozendict.py").write_text("class OldName: pass")

        (package_dir / "models.py").write_text("class OldName(BaseModel): pass")

        overrides = {"OldName": "NewName"}
        modified = rewrite_class_names(package_dir, overrides, verbose=0)

        assert modified == 1

        assert "OldName" in (package_dir / "__init__.py").read_text()
        assert "OldName" in (package_dir / "mutable_base.py").read_text()
        assert "OldName" in (package_dir / "frozen_base.py").read_text()
        assert "OldName" in (package_dir / "frozendict.py").read_text()

        assert "NewName" in (package_dir / "models.py").read_text()
        assert "OldName" not in (package_dir / "models.py").read_text()


def test_rewrite_class_names_empty_overrides():
    with tempfile.TemporaryDirectory() as tmpdir:
        package_dir = Path(tmpdir)
        (package_dir / "models.py").write_text("class Test(BaseModel): pass")

        modified = rewrite_class_names(package_dir, {}, verbose=0)
        assert modified == 0


@pytest.mark.integration
def test_end_to_end_with_override(tmp_path):
    fixture_dir = Path(__file__).parent / "fixtures" / "class_name_override"
    if not fixture_dir.exists():
        pytest.skip("Fixture schemas not available")

    models_out = tmp_path / "models"

    cfg = GenerationConfig(
        schemas=fixture_dir,
        json_out=None,
        models_out=models_out,
        package_name="test_overrides",
        exclude=None,
        mutability=Mutability.mutable,
        base_url=None,
        block_remote_refs=False,
        custom_ref_resolver=None,
        immutable_hints=False,
        use_frozendict=False,
        from_attributes=False,
        partial=False,
        clean_first=False,
        check=False,
        verbose=2,
        output_mode=OutputMode.clean,
        fmt=FormatChoice.none,
        no_rewrite=False,
        dry_run=False,
    )

    reporter = SimpleReporter()
    result = run_generation(cfg, reporter)

    package_dir = result.package_dir

    py_files = list(package_dir.glob("*.py"))
    model_files = [f for f in py_files if f.name not in {"__init__.py", "mutable_base.py"}]
    assert len(model_files) > 0

    content = model_files[0].read_text()

    assert "class UserProfile(" in content
    assert "UserProfileV1" not in content

    assert "class Preferences(" in content
    assert "PreferencesV1" not in content
