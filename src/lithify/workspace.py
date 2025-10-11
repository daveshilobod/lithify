# src/lithify/workspace.py
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def staging_dir(enabled: bool):
    if not enabled:
        yield None
        return
    tmp = tempfile.mkdtemp(prefix="lithify_workspace_")
    try:
        yield Path(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def copy_selected(src_dir: Path, dst_dir: Path, patterns: Iterable[str]) -> list[Path]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for pattern in patterns:
        for p in src_dir.rglob(pattern):
            rel = p.relative_to(src_dir)
            target = dst_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            copied.append(target)
    return copied
