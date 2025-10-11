# tests/test_utils.py

import json
import time
from pathlib import Path

import pytest

from lithify.frozendict import FrozenDict
from lithify.utils import write_if_changed, write_manifest


class TestFrozenDict:
    def test_frozendict_hashing(self):
        d1 = FrozenDict({"a": 1, "b": 2, "c": 3})
        d2 = FrozenDict({"c": 3, "a": 1, "b": 2})
        d3 = FrozenDict({"b": 2, "c": 3, "a": 1})

        assert hash(d1) == hash(d2) == hash(d3)
        assert d1 == d2 == d3

        test_dict = {d1: "value"}
        assert test_dict[d2] == "value"

    def test_frozendict_nested(self):
        d1 = FrozenDict({"x": [1, 2], "y": {"nested": True}})
        d2 = FrozenDict({"y": {"nested": True}, "x": [1, 2]})

        assert hash(d1) == hash(d2)
        assert d1 == d2

    def test_frozendict_immutable(self):
        d = FrozenDict({"a": 1, "b": 2})

        assert not hasattr(d, "__setitem__")
        assert not hasattr(d, "__delitem__")
        assert not hasattr(d, "pop")
        assert not hasattr(d, "update")


class TestUtils:
    def test_write_if_changed_new_file(self, temp_dir):
        path = temp_dir / "new.txt"
        content = "Hello, world!"

        changed = write_if_changed(path, content)

        assert changed is True
        assert path.exists()
        assert path.read_text() == content

    def test_write_if_changed_same_content(self, temp_dir):
        path = temp_dir / "existing.txt"
        content = "Hello, world!"

        path.write_text(content)
        time.sleep(0.01)
        mtime_before = path.stat().st_mtime

        changed = write_if_changed(path, content)

        assert changed is False
        assert path.stat().st_mtime == mtime_before

    def test_write_if_changed_different_content(self, temp_dir):
        path = temp_dir / "existing.txt"

        path.write_text("Old content")

        changed = write_if_changed(path, "New content")

        assert changed is True
        assert path.read_text() == "New content"

    def test_write_manifest(self, temp_dir):
        write_manifest(
            temp_dir, mutability="deep-frozen", immutable_hints=True, use_frozendict=True, from_attributes=False
        )

        manifest_path = temp_dir / "manifest.json"
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text())
        assert "mutability" in manifest or "generator" in manifest


@pytest.fixture
def temp_dir():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)
