# tests/test_nsint_regression.py

import yaml

from lithify.enums import FormatChoice, Mutability, OutputMode
from lithify.orchestrator import GenerationConfig, SimpleReporter, run_generation


class TestNsIntRegression:
    def test_full_pipeline_with_ns_fields(self, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "ForensicEvent",
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-7][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                },
                "timestamp_ns": {
                    "description": "Event timestamp in nanoseconds since epoch",
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
                "ingest_ns": {
                    "description": "Ingestion timestamp in nanoseconds",
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
                "message": {"type": "string"},
            },
            "required": ["event_id", "timestamp_ns", "message"],
        }

        schema_file = schemas_dir / "forensic_event.yaml"
        schema_file.write_text(yaml.dump(schema))

        models_out = tmp_path / "models"

        cfg = GenerationConfig(
            schemas=schemas_dir,
            json_out=None,
            models_out=models_out,
            package_name="security_models",
            exclude=None,
            mutability=Mutability.deep_frozen,
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
            lenient_allof=False,
        )

        reporter = SimpleReporter()
        run_generation(cfg, reporter)

        package_dir = models_out / "security_models"
        assert package_dir.exists()

        common_types_file = package_dir / "common_types.py"
        assert common_types_file.exists(), "common_types.py should exist"

        common_types_content = common_types_file.read_text()
        assert "NsInt = Annotated[" in common_types_content, "NsInt should be generated"
        assert "BeforeValidator" in common_types_content
        assert "PlainSerializer" in common_types_content
        assert "def _ns_from_json(v):" in common_types_content

        model_file = package_dir / "forensic_event.py"
        assert model_file.exists()

        model_content = model_file.read_text()

        assert "class ForensicEvent" in model_content
        assert "timestamp_ns:" in model_content
        assert "ingest_ns:" in model_content

        assert "from .common_types import NsInt" in model_content, "Should import from generated .common_types"
        assert "from lithify.common_aliases" not in model_content, "Must NOT import from lithify.common_aliases"

        assert "timestamp_ns: NsInt" in model_content
        assert "ingest_ns: NsInt" in model_content

    def test_no_regression_without_ns_fields(self, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "SimpleEvent",
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "timestamp": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["event_id", "message"],
        }

        schema_file = schemas_dir / "simple_event.yaml"
        schema_file.write_text(yaml.dump(schema))

        models_out = tmp_path / "models"

        cfg = GenerationConfig(
            schemas=schemas_dir,
            json_out=None,
            models_out=models_out,
            package_name="test_models",
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
            verbose=0,
            output_mode=OutputMode.clean,
            fmt=FormatChoice.none,
            no_rewrite=False,
            dry_run=False,
            lenient_allof=False,
        )

        reporter = SimpleReporter()
        run_generation(cfg, reporter)

        package_dir = models_out / "test_models"
        assert package_dir.exists()

        model_file = package_dir / "simple_event.py"
        assert model_file.exists()

        common_types_file = package_dir / "common_types.py"
        if common_types_file.exists():
            content = common_types_file.read_text()
            assert "NsInt" not in content, "NsInt should NOT be generated"

    def test_no_regression_on_existing_aliases(self, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        schema1 = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "User",
            "$defs": {
                "UUID": {
                    "title": "UUID",
                    "type": "string",
                    "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-7][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                }
            },
            "properties": {"user_id": {"$ref": "#/$defs/UUID"}, "username": {"type": "string"}},
            "required": ["user_id", "username"],
        }

        schema2 = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Activity",
            "properties": {
                "user_id": {
                    "type": "string",
                    "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-7][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                },
                "timestamp_ns": {"type": "string", "pattern": "^[0-9]+$"},
                "action": {"type": "string"},
            },
            "required": ["user_id", "timestamp_ns", "action"],
        }

        (schemas_dir / "user.yaml").write_text(yaml.dump(schema1))
        (schemas_dir / "activity.yaml").write_text(yaml.dump(schema2))

        models_out = tmp_path / "models"

        cfg = GenerationConfig(
            schemas=schemas_dir,
            json_out=None,
            models_out=models_out,
            package_name="app_models",
            exclude=None,
            mutability=Mutability.deep_frozen,
            base_url=None,
            block_remote_refs=False,
            custom_ref_resolver=None,
            immutable_hints=False,
            use_frozendict=False,
            from_attributes=False,
            partial=False,
            clean_first=False,
            check=False,
            verbose=1,
            output_mode=OutputMode.clean,
            fmt=FormatChoice.none,
            no_rewrite=False,
            dry_run=False,
            lenient_allof=False,
        )

        reporter = SimpleReporter()
        run_generation(cfg, reporter)

        package_dir = models_out / "app_models"
        assert (package_dir / "user.py").exists()
        assert (package_dir / "activity.py").exists()

        common_types_file = package_dir / "common_types.py"
        assert common_types_file.exists(), "common_types.py should exist"

        common_types_content = common_types_file.read_text()

        assert "NsInt" in common_types_content, "NsInt should be in common_types"
        assert "BeforeValidator" in common_types_content, "NsInt should use BeforeValidator"

        activity_content = (package_dir / "activity.py").read_text()
        assert "class Activity" in activity_content
        assert "timestamp_ns:" in activity_content

        assert "NsInt" in activity_content, "Activity should use NsInt type"
        assert "from .common_types import NsInt" in activity_content, "Should import NsInt"

        user_content = (package_dir / "user.py").read_text()
        assert "class User" in user_content
        assert "user_id:" in user_content

    def test_nsint_wrong_pattern_ignored(self, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "EdgeCase",
            "type": "object",
            "properties": {
                "timestamp_ns": {
                    "type": "string",
                    "pattern": "^[0-9a-f]+$",
                },
                "other_ns": {"type": "integer"},
                "namespace": {
                    "type": "string",
                    "pattern": "^[0-9]+$",
                },
            },
        }

        schema_file = schemas_dir / "edge_case.yaml"
        schema_file.write_text(yaml.dump(schema))

        models_out = tmp_path / "models"

        cfg = GenerationConfig(
            schemas=schemas_dir,
            json_out=None,
            models_out=models_out,
            package_name="edge_models",
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
            verbose=0,
            output_mode=OutputMode.clean,
            fmt=FormatChoice.none,
            no_rewrite=False,
            dry_run=False,
            lenient_allof=False,
        )

        reporter = SimpleReporter()
        run_generation(cfg, reporter)

        package_dir = models_out / "edge_models"
        common_types_file = package_dir / "common_types.py"

        if common_types_file.exists():
            content = common_types_file.read_text()
            assert "NsInt" not in content, "NsInt should NOT be generated for wrong patterns"
