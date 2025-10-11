# tests/test_inline_allof.py

import json

import pytest

from lithify.slas_alias_generator import emit_inline_allof_aliases
from lithify.slas_allof_processor import (
    InlineAllOfInfo,
    _extract_property_name,
    _is_inline_property_allof,
    process_allof_collapse,
)
from lithify.slas_schema_index import SchemaIndex


class TestInlinePropertyPredicate:
    def test_identifies_inline_property_allof(self):
        node = {"allOf": [{"type": "string"}]}
        assert _is_inline_property_allof(node, "#/properties/user_id", "User") is True

    def test_rejects_titled_definition(self):
        node = {"title": "UserID", "allOf": [{"type": "string"}]}
        assert _is_inline_property_allof(node, "#/properties/user_id", "User") is False

    def test_rejects_non_property_path(self):
        node = {"allOf": [{"type": "string"}]}
        assert _is_inline_property_allof(node, "#/$defs/UserID", "User") is False

    def test_rejects_no_parent_class(self):
        node = {"allOf": [{"type": "string"}]}
        assert _is_inline_property_allof(node, "#/properties/user_id", None) is False


class TestPropertyNameExtraction:
    def test_extracts_from_simple_properties(self):
        assert _extract_property_name("#/properties/event_id") == "event_id"

    def test_extracts_from_nested_defs(self):
        assert _extract_property_name("#/$defs/ForensicEvent/properties/event_id") == "event_id"

    def test_extracts_last_property_in_nested(self):
        assert _extract_property_name("#/properties/foo/properties/bar") == "bar"

    def test_raises_on_invalid_pointer(self):
        with pytest.raises(ValueError, match="Cannot extract property name"):
            _extract_property_name("#/$defs/SomeType")


class TestInlineAllOfTracking:
    def test_tracks_inline_allof_without_title(self, tmp_path):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Record",
            "properties": {
                "record_id": {
                    "description": "Unique record identifier",
                    "allOf": [
                        {
                            "type": "string",
                            "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-7][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                        },
                        {
                            "type": "string",
                            "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                        },
                    ],
                }
            },
        }

        schema_file = tmp_path / "record.json"
        schema_file.write_text(json.dumps(schema, indent=2))

        index = SchemaIndex.load([schema_file], None)
        json_dir, inline_allofs = process_allof_collapse(tmp_path, index, strict=True, verbose=0)

        assert len(inline_allofs) == 1
        info = inline_allofs[0]
        assert info.property_name == "record_id"
        assert info.parent_class == "Record"
        assert "pattern" in info.merged_schema
        assert "5" in info.merged_schema["pattern"]

    def test_ignores_titled_defs(self, tmp_path):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "CommonTypes",
            "$defs": {
                "UUIDv5": {
                    "title": "UUIDv5",
                    "allOf": [
                        {
                            "type": "string",
                            "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-7][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                        },
                        {
                            "type": "string",
                            "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                        },
                    ],
                }
            },
        }

        schema_file = tmp_path / "common_types.json"
        schema_file.write_text(json.dumps(schema, indent=2))

        index = SchemaIndex.load([schema_file], None)
        json_dir, inline_allofs = process_allof_collapse(tmp_path, index, strict=True, verbose=0)

        assert len(inline_allofs) == 0

    def test_tracks_with_class_name_override(self, tmp_path):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "ForensicEventV1",
            "x-python-class-name": "ForensicEvent",
            "properties": {
                "event_id": {
                    "allOf": [
                        {
                            "type": "string",
                            "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-7][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                        },
                        {
                            "type": "string",
                            "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                        },
                    ]
                }
            },
        }

        schema_file = tmp_path / "forensic_event_v1.json"
        schema_file.write_text(json.dumps(schema, indent=2))

        index = SchemaIndex.load([schema_file], None)
        json_dir, inline_allofs = process_allof_collapse(tmp_path, index, strict=True, verbose=0)

        assert len(inline_allofs) == 1
        assert inline_allofs[0].parent_class == "ForensicEvent"


class TestInlineAliasGeneration:
    def test_generates_synthetic_alias_name(self, tmp_path):
        origin_file = tmp_path / "record.json"
        origin_file.touch()

        info = InlineAllOfInfo(
            property_name="record_id",
            parent_class="Record",
            merged_schema={
                "type": "string",
                "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            },
            json_pointer="#/properties/record_id",
            origin_file=origin_file,
        )

        output_dir = tmp_path / "aliases"
        output_dir.mkdir()

        ref_map, modules_created = emit_inline_allof_aliases([info], output_dir, package_name="test_pkg", verbose=0)

        assert "#/properties/record_id" in ref_map
        assert "Record_record_id" in ref_map["#/properties/record_id"]

        module_file = output_dir / "record_types.py"
        assert module_file.exists()

        content = module_file.read_text()
        assert "Record_record_id" in content
        assert "StringConstraints" in content
        assert "pattern=" in content

    def test_places_in_parent_module(self, tmp_path):
        origin_file = tmp_path / "01_user.json"
        origin_file.touch()

        info = InlineAllOfInfo(
            property_name="user_id",
            parent_class="User",
            merged_schema={"type": "string", "minLength": 1, "maxLength": 50},
            json_pointer="#/properties/user_id",
            origin_file=origin_file,
        )

        output_dir = tmp_path / "aliases"
        output_dir.mkdir()

        ref_map, modules_created = emit_inline_allof_aliases([info], output_dir, package_name="models", verbose=0)

        module_file = output_dir / "user_types.py"
        assert module_file.exists()

        content = module_file.read_text()
        assert "User_user_id" in content

    def test_multiple_inline_allofs_same_module(self, tmp_path):
        origin_file = tmp_path / "record.json"
        origin_file.touch()

        infos = [
            InlineAllOfInfo(
                property_name="id",
                parent_class="Record",
                merged_schema={"type": "string", "minLength": 1},
                json_pointer="#/properties/id",
                origin_file=origin_file,
            ),
            InlineAllOfInfo(
                property_name="name",
                parent_class="Record",
                merged_schema={"type": "string", "maxLength": 100},
                json_pointer="#/properties/name",
                origin_file=origin_file,
            ),
        ]

        output_dir = tmp_path / "aliases"
        output_dir.mkdir()

        ref_map, modules_created = emit_inline_allof_aliases(infos, output_dir, package_name="models", verbose=0)

        module_file = output_dir / "record_types.py"
        assert module_file.exists()

        content = module_file.read_text()
        assert "Record_id" in content
        assert "Record_name" in content


class TestEndToEnd:
    def test_complete_workflow(self, tmp_path):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Document",
            "properties": {
                "doc_id": {
                    "description": "Document identifier",
                    "allOf": [{"type": "string", "minLength": 1}, {"type": "string", "maxLength": 50}],
                },
                "title": {
                    "description": "Document title",
                    "allOf": [{"type": "string", "minLength": 1}, {"type": "string", "maxLength": 200}],
                },
            },
        }

        schema_file = tmp_path / "document.json"
        schema_file.write_text(json.dumps(schema, indent=2))

        index = SchemaIndex.load([schema_file], None)
        json_dir, inline_allofs = process_allof_collapse(tmp_path, index, strict=True, verbose=0)

        assert len(inline_allofs) == 2

        output_dir = tmp_path / "aliases"
        output_dir.mkdir()

        ref_map, modules_created = emit_inline_allof_aliases(
            inline_allofs, output_dir, package_name="models", verbose=0
        )

        assert "#/properties/doc_id" in ref_map
        assert "#/properties/title" in ref_map

        module_file = output_dir / "document_types.py"
        assert module_file.exists()

        content = module_file.read_text()
        assert "Document_doc_id" in content
        assert "Document_title" in content
        assert "max_length=50" in content
        assert "max_length=200" in content
