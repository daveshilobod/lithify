# src/lithify/formatting.py
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .enums import FormatChoice


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def fix_linting(path: Path, dry_run: bool = False) -> str:
    if not _have("ruff"):
        return "lint-fix: skipped (ruff not found)"

    # Select common, safe-to-fix rules:
    # I: isort (import sorting)
    # UP: pyupgrade (modernize syntax, e.g., Optional[X] -> X | None)
    # F: pyflakes (unused imports, etc.)
    cmd = ["ruff", "check", str(path), "--fix", "--select", "I,UP,F", "--exit-zero"]

    if dry_run:
        return f"lint-fix: would run {' '.join(cmd)}"

    # Run fix, don't check exit code as it might be non-zero if unfixable errors remain.
    # --exit-zero ensures it exits 0 unless there's a catastrophic error.
    subprocess.run(cmd, check=False, capture_output=True)
    return "lint-fix: ran ruff check --fix"


def format_path(path: Path, choice: FormatChoice, dry_run: bool = False) -> str:
    if choice == FormatChoice.none:
        return "lint-fix: skipped\nformat: skipped"

    # First, run the linter to fix imports and other issues
    lint_msg = fix_linting(path, dry_run)

    if choice == FormatChoice.auto:
        choice = FormatChoice.ruff if _have("ruff") else FormatChoice.black

    if choice == FormatChoice.ruff:
        cmd = ["ruff", "format", str(path)]
        label = "ruff format"
    elif choice == FormatChoice.black:
        cmd = ["black", str(path)]
        label = "black"
    else:
        raise ValueError(f"Unknown format choice: {choice}")

    if dry_run:
        format_msg = f"format: would run {' '.join(cmd)}"
        return f"{lint_msg}\n{format_msg}"

    subprocess.run(cmd, check=True, capture_output=True)
    format_msg = f"format: ran {label}"
    return f"{lint_msg}\n{format_msg}"
