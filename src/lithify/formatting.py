# lithify/formatting.py
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
from .enums import FormatChoice

def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def format_path(path: Path, choice: FormatChoice, dry_run: bool = False) -> str:
    """
    Returns a human string describing what ran or would run.
    Raises CalledProcessError on nonzero exit when not dry-run.
    """
    if choice == FormatChoice.none:
        return "format: skipped"

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
        return f"format: would run {' '.join(cmd)}"
    subprocess.run(cmd, check=True)
    return f"format: ran {label}"
