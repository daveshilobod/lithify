# tests/test_inline_allof_regression.py

from lithify.enums import FormatChoice, Mutability, OutputMode
from lithify.orchestrator import GenerationConfig, SimpleReporter, run_generation


class TestPR02Regression:
    def test_full_pipeline_with_inline_allof(self, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "User",
            "type": "object",
            "properties": {
                "user_id": {
                    "description": "User identifier",
                    "allOf": [{"type": "string", "minLength": 1}, {"type": "string", "maxLength": 50}],
                },
                "username": {"type": "string", "minLength": 3, "maxLength": 20},
            },
            "required": ["user_id", "username"],
        }

        schema_file = schemas_dir / "user.yaml"
        import yaml

        schema_file.write_text(yaml.dump(schema))

        models_out = tmp_path / "models"

        cfg = GenerationConfig(
            schemas=schemas_dir,
            json_out=None,
            models_out=models_out,
            package_name="test_models",
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

        package_dir = models_out / "test_models"
        assert package_dir.exists()

        user_model_file = package_dir / "user.py"
        assert user_model_file.exists()

        user_types_file = package_dir / "user_types.py"
        assert user_types_file.exists(), "Inline allOf aliases should be in user_types.py"

        user_model_content = user_model_file.read_text()
        user_types_content = user_types_file.read_text()

        assert "User_user_id" in user_types_content, "Alias should be in user_types.py"
        assert "min_length=1" in user_types_content
        assert "max_length=50" in user_types_content

        assert "from .user_types import User_user_id" in user_model_content, "Model should import the alias"
        assert "user_id: User_user_id" in user_model_content, "Model should use the alias type"

        assert "username" in user_model_content
        assert "User_username" not in user_types_content, "No alias for regular fields"

    def test_no_regression_on_regular_fields(self, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Product",
            "type": "object",
            "properties": {"name": {"type": "string"}, "price": {"type": "number"}, "in_stock": {"type": "boolean"}},
            "required": ["name", "price"],
        }

        schema_file = schemas_dir / "product.yaml"
        import yaml

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

        product_file = package_dir / "product.py"
        assert product_file.exists()

        content = product_file.read_text()

        assert "class Product" in content
        assert "name:" in content
        assert "price:" in content
        assert "in_stock:" in content

        assert "Product_name" not in content
        assert "Product_price" not in content

    def test_no_regression_on_named_defs(self, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Record",
            "$defs": {
                "UUID": {
                    "title": "UUID",
                    "type": "string",
                    "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-7][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                }
            },
            "properties": {"id": {"$ref": "#/$defs/UUID"}, "name": {"type": "string"}},
            "required": ["id", "name"],
        }

        schema_file = schemas_dir / "record.yaml"
        import yaml

        schema_file.write_text(yaml.dump(schema))

        models_out = tmp_path / "models"

        cfg = GenerationConfig(
            schemas=schemas_dir,
            json_out=None,
            models_out=models_out,
            package_name="test_models",
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

        record_file = package_dir / "record.py"
        assert record_file.exists()

        content = record_file.read_text()

        assert "class Record" in content
        assert "id:" in content
        assert "name:" in content

        assert "[0-9a-f]{8}" in content

        assert "Record_id" not in content
