# tests/test_bases.py

from pathlib import Path

import pytest


class TestBases:
    def test_inject_base_mutable(self, temp_dir):
        from lithify.bases import inject_base

        base_symbol, import_module = inject_base(temp_dir, mode="mutable", from_attributes=False)

        assert base_symbol == "MutableBase"
        assert import_module == "mutable_base"
        assert (temp_dir / "mutable_base.py").exists()

        content = (temp_dir / "mutable_base.py").read_text()
        assert "class MutableBase(BaseModel):" in content
        assert "validate_assignment=True" in content
        assert "from_attributes=False" in content

    def test_inject_base_frozen(self, temp_dir):
        from lithify.bases import inject_base

        base_symbol, import_module = inject_base(temp_dir, mode="frozen", from_attributes=True)

        assert base_symbol == "FrozenBase"
        assert import_module == "frozen_base"
        assert (temp_dir / "frozen_base.py").exists()

        content = (temp_dir / "frozen_base.py").read_text()
        assert "class FrozenBase(BaseModel):" in content
        assert "frozen=True" in content
        assert "from_attributes=True" in content

    def test_inject_base_deep_frozen(self, temp_dir):
        from lithify.bases import inject_base

        base_symbol, import_module = inject_base(
            temp_dir, mode="deep-frozen", use_frozendict=True, from_attributes=False
        )

        assert base_symbol == "FrozenModel"
        assert import_module == "frozen_base"
        assert (temp_dir / "frozen_base.py").exists()
        assert (temp_dir / "frozendict.py").exists()

        content = (temp_dir / "frozen_base.py").read_text()
        assert "class FrozenModel(BaseModel):" in content
        assert "_deep_freeze" in content
        assert "model_post_init" in content
        assert "from .frozendict import FrozenDict" in content

    def test_inject_base_deep_frozen_no_frozendict(self, temp_dir):
        from lithify.bases import inject_base

        base_symbol, import_module = inject_base(temp_dir, mode="deep-frozen", use_frozendict=False)

        assert base_symbol == "FrozenModel"
        assert not (temp_dir / "frozendict.py").exists()

        content = (temp_dir / "frozen_base.py").read_text()
        assert "from types import MappingProxyType" in content
        assert "MappingProxyType" in content

    def test_rebase_generated_models(self, temp_dir):
        from lithify.bases import rebase_generated_models

        model1 = """from pydantic import BaseModel

class User(BaseModel):
    name: str
"""
        model2 = """from pydantic import BaseModel

class Event(BaseModel):
    id: int
"""

        (temp_dir / "user.py").write_text(model1)
        (temp_dir / "event.py").write_text(model2)
        (temp_dir / "__init__.py").touch()

        rebase_generated_models(temp_dir, "FrozenBase", "frozen_base")

        user_content = (temp_dir / "user.py").read_text()
        assert "from .frozen_base import FrozenBase" in user_content
        assert "class User(FrozenBase):" in user_content

        event_content = (temp_dir / "event.py").read_text()
        assert "from .frozen_base import FrozenBase" in event_content
        assert "class Event(FrozenBase):" in event_content

        init_content = (temp_dir / "__init__.py").read_text()
        assert "FrozenBase" not in init_content


@pytest.fixture
def temp_dir():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def fixtures_dir():
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def common_types_schema(fixtures_dir):
    import yaml

    with open(fixtures_dir / "common_types.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def event_schema(fixtures_dir):
    import yaml

    with open(fixtures_dir / "event.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def temp_schemas_dir(tmp_path, common_types_schema, event_schema):
    import json

    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()

    common_types_path = schemas_dir / "00_common_types.json"
    common_types_path.write_text(json.dumps(common_types_schema, indent=2))

    event_path = schemas_dir / "01_event.json"
    event_path.write_text(json.dumps(event_schema, indent=2))

    return schemas_dir


class TestAliasGeneratorBaseClass:
    def test_no_mutable_base_import(self, temp_schemas_dir, tmp_path):
        from lithify.slas_alias_generator import generate_alias_module

        aliases = [
            ("UUID", {"type": "string", "pattern": "^[0-9a-f-]+$"}, "scalar_str"),
            ("SemVer", {"type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$"}, "scalar_str"),
        ]

        for base_class in ["BaseModel", "FrozenModel", "MutableBase", "CustomBase"]:
            code = generate_alias_module("test_module", aliases, "test_pkg", base_class, use_frozendict=False)

            assert "from typing import Annotated, Literal" in code
            assert "from pydantic import Field, StringConstraints" in code

            if base_class == "BaseModel":
                assert "from pydantic import BaseModel" in code
            elif base_class == "FrozenModel":
                assert "from .frozen_base import FrozenModel" in code
            else:
                assert "from .mutable_base" not in code
                assert "MutableBase" not in code

            assert "UUID = Annotated[str, StringConstraints(" in code
            assert "SemVer = Annotated[str, StringConstraints(" in code

    def test_alias_module_with_all_base_classes(self, temp_schemas_dir, tmp_path):
        from lithify.slas_alias_generator import emit_alias_modules
        from lithify.slas_schema_index import SchemaIndex

        index = SchemaIndex.load(list(temp_schemas_dir.glob("*.json")))

        output_dir = tmp_path / "output"

        for base_class in ["BaseModel", "FrozenModel", "MutableBase"]:
            output_dir_test = output_dir / base_class.lower()
            output_dir_test.mkdir(parents=True)

            ref_map, modules_created = emit_alias_modules(
                index, output_dir_test, "test_pkg", base_class, use_frozendict=False, verbose=0
            )

            for module_file in output_dir_test.glob("*.py"):
                content = module_file.read_text()

                assert "from .mutable_base" not in content, f"MutableBase import found in {module_file}"

                if base_class == "FrozenModel":
                    assert "from .frozen_base import FrozenModel" in content or "BaseModel" not in content
