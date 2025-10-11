# tests/test_cli_generate.py
from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer.testing


def _require_cli():
    cli = importlib.import_module("lithify.cli")
    enums = importlib.import_module("lithify.enums")
    formatting = importlib.import_module("lithify.formatting")
    orchestrator = importlib.import_module("lithify.orchestrator")
    return SimpleNamespace(cli=cli, enums=enums, formatting=formatting, orchestrator=orchestrator)


def _stub_generation(monkeypatch, package_name="models_pkg"):
    """
    Patch lithify.orchestrator.run_generation.

    The stub writes:
      out_dir/<package_name>/__init__.py
      out_dir/<package_name>/user.py
      out_dir/manifest.json                      # deliberately "junk"
      out_dir/debug_map.json                     # deliberately "junk"

    It returns the package dir, matching the CLI's expectation.
    """
    mods = _require_cli()

    calls = {"rewrites": 0}

    def fake_apply_rewrites(_generated_root: Path):
        calls["rewrites"] += 1
        # no-op

    def fake_run_generation(cfg, reporter):
        calls["configs"] = getattr(calls, "configs", [])
        calls["configs"].append(cfg)

        if cfg.dry_run:
            reporter.info(f"[plan] schemas={cfg.schemas}")
            reporter.info(f"[plan] models_out={cfg.models_out}")
            reporter.info(f"[plan] output_mode={cfg.output_mode.value}")
            reporter.info(f"[plan] format={cfg.fmt.value}")
            reporter.info(f"[plan] rewrite={'no' if cfg.no_rewrite else 'yes'}")
            reporter.info(f"[plan] staging={'temp' if cfg.output_mode.value == 'clean' else 'disabled'}")
            return mods.orchestrator.GenerationResult(
                package_dir=cfg.models_out / cfg.package_name, mutability=cfg.mutability
            )

        pkg = cfg.models_out / cfg.package_name
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "user.py").write_text("class User: ...\n", encoding="utf-8")

        if cfg.output_mode.value == "debug":
            (cfg.models_out / "manifest.json").write_text("{}", encoding="utf-8")
            (cfg.models_out / "debug_map.json").write_text("{}", encoding="utf-8")

        if not cfg.no_rewrite:
            fake_apply_rewrites(None)

        from lithify.formatting import format_path

        format_path(pkg, cfg.fmt, dry_run=False)

        return mods.orchestrator.GenerationResult(package_dir=pkg, mutability=cfg.mutability)

    monkeypatch.setattr(mods.orchestrator, "run_generation", fake_run_generation, raising=True)
    monkeypatch.setattr(mods.cli, "run_generation", fake_run_generation, raising=True)
    return calls


def _stub_formatter_to_noop(monkeypatch):
    mods = _require_cli()
    recorded = []

    def fake_format_path(path: Path, choice, dry_run: bool = False) -> str:
        recorded.append((str(path), getattr(choice, "value", str(choice))))
        return f"format: simulated {getattr(choice, 'value', str(choice))}"

    monkeypatch.setattr(mods.formatting, "format_path", fake_format_path, raising=True)
    return recorded


def test_generate_dry_run_prints_plan_and_creates_no_files(tmp_path: Path, monkeypatch):
    mods = _require_cli()
    runner = typer.testing.CliRunner()

    _stub_generation(monkeypatch, package_name="pkg")
    _stub_formatter_to_noop(monkeypatch)

    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "minimal.json").write_text("{}", encoding="utf-8")
    models_out = tmp_path / "out"

    result = runner.invoke(
        mods.cli.app,
        [
            "generate",
            "--schemas",
            str(schemas),
            "--json-out",
            str(tmp_path / "json"),
            "--models-out",
            str(models_out),
            "--package-name",
            "pkg",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    out = result.stdout

    assert "[plan] schemas=" in out
    assert "[plan] models_out=" in out
    assert "[plan] output_mode=clean" in out
    assert "[plan] format=auto" in out
    assert "[plan] rewrite=yes" in out
    assert "[plan] staging=temp" in out

    assert not models_out.exists()


def test_output_mode_clean_copies_only_py(tmp_path: Path, monkeypatch):
    mods = _require_cli()
    runner = typer.testing.CliRunner()

    rewrite_calls = _stub_generation(monkeypatch, package_name="genpkg")
    formatted = _stub_formatter_to_noop(monkeypatch)

    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "s.json").write_text("{}", encoding="utf-8")
    models_out = tmp_path / "clean_out"

    result = runner.invoke(
        mods.cli.app,
        [
            "generate",
            "--schemas",
            str(schemas),
            "--json-out",
            str(tmp_path / "json"),
            "--models-out",
            str(models_out),
            "--package-name",
            "genpkg",
            "--output-mode",
            "clean",
        ],
    )

    assert result.exit_code == 0, result.stdout

    pkg = models_out / "genpkg"
    assert (pkg / "user.py").exists()
    assert (pkg / "__init__.py").exists()
    assert not (models_out / "manifest.json").exists()
    assert not (models_out / "debug_map.json").exists()

    assert rewrite_calls["rewrites"] == 1

    assert formatted and formatted[0][0] == str(pkg)


def test_output_mode_debug_keeps_intermediates(tmp_path: Path, monkeypatch):
    mods = _require_cli()
    runner = typer.testing.CliRunner()

    _stub_generation(monkeypatch, package_name="genpkg")
    formatted = _stub_formatter_to_noop(monkeypatch)

    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "s.json").write_text("{}", encoding="utf-8")
    models_out = tmp_path / "debug_out"

    result = runner.invoke(
        mods.cli.app,
        [
            "generate",
            "--schemas",
            str(schemas),
            "--json-out",
            str(tmp_path / "json"),
            "--models-out",
            str(models_out),
            "--package-name",
            "genpkg",
            "--output-mode",
            "debug",
        ],
    )

    assert result.exit_code == 0, result.stdout

    pkg = models_out / "genpkg"
    assert (pkg / "user.py").exists()
    assert (models_out / "manifest.json").exists()
    assert (models_out / "debug_map.json").exists()

    assert formatted and formatted[0][0] == str(pkg)


def test_no_rewrite_flag_skips_apply_rewrites(tmp_path: Path, monkeypatch):
    mods = _require_cli()
    runner = typer.testing.CliRunner()

    calls = _stub_generation(monkeypatch, package_name="genpkg")
    _ = _stub_formatter_to_noop(monkeypatch)

    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "s.json").write_text("{}", encoding="utf-8")
    models_out = tmp_path / "out"

    result1 = runner.invoke(
        mods.cli.app,
        [
            "generate",
            "--schemas",
            str(schemas),
            "--json-out",
            str(tmp_path / "json"),
            "--models-out",
            str(models_out),
            "--package-name",
            "genpkg",
        ],
    )
    assert result1.exit_code == 0
    assert calls["rewrites"] == 1

    result2 = runner.invoke(
        mods.cli.app,
        [
            "generate",
            "--schemas",
            str(schemas),
            "--json-out",
            str(tmp_path / "json"),
            "--models-out",
            str(models_out),
            "--package-name",
            "genpkg",
            "--no-rewrite",
        ],
    )
    assert result2.exit_code == 0
    assert calls["rewrites"] == 1


def test_format_auto_prefers_ruff(monkeypatch, tmp_path: Path):
    mods = _require_cli()

    monkeypatch.setattr(mods.formatting, "_have", lambda c: c == "ruff", raising=True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

    msg = mods.formatting.format_path(tmp_path, mods.enums.FormatChoice.auto, dry_run=False)
    assert "ruff" in msg


def test_format_auto_falls_back_to_black(monkeypatch, tmp_path: Path):
    mods = _require_cli()

    monkeypatch.setattr(mods.formatting, "_have", lambda c: c == "black", raising=True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

    msg = mods.formatting.format_path(tmp_path, mods.enums.FormatChoice.auto, dry_run=False)
    assert "black" in msg


def test_format_none_skips(monkeypatch, tmp_path: Path):
    mods = _require_cli()

    def fail_run(*a, **k):
        raise AssertionError("formatting should have been skipped")

    monkeypatch.setattr(subprocess, "run", fail_run)
    msg = mods.formatting.format_path(tmp_path, mods.enums.FormatChoice.none, dry_run=False)
    assert "skipped" in msg


def test_generate_with_class_name_override(tmp_path: Path):
    from pathlib import Path

    from typer.testing import CliRunner

    mods = _require_cli()
    runner = CliRunner()

    fixture_dir = Path(__file__).parent / "fixtures" / "class_name_override"
    if not fixture_dir.exists():
        pytest.skip("Fixture schemas not available")

    models_out = tmp_path / "models"

    result = runner.invoke(
        mods.cli.app,
        [
            "generate",
            "--schemas",
            str(fixture_dir),
            "--models-out",
            str(models_out),
            "--package-name",
            "test_pkg",
            "--format",
            "none",
            "-v",
        ],
    )

    assert result.exit_code == 0, f"Generation failed: {result.stdout}"
    assert "Applying class name overrides" in result.stdout

    py_files = list((models_out / "test_pkg").glob("*.py"))
    model_files = [f for f in py_files if f.name not in {"__init__.py", "mutable_base.py"}]

    assert len(model_files) > 0, "No model files generated"
    content = model_files[0].read_text()
    assert "class UserProfile(" in content, "Class not renamed to UserProfile"
    assert "UserProfileV1" not in content, "Old class name still present"
