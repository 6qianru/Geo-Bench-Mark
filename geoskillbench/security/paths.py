from __future__ import annotations

from pathlib import Path


def resolve_inside(base: str | Path, candidate: str | Path) -> Path:
    """Resolve candidate and require it to remain inside base."""
    base_path = Path(base).resolve()
    target = Path(candidate)
    if not target.is_absolute():
        target = base_path / target
    target = target.resolve()
    if not target.is_relative_to(base_path):
        raise ValueError(f"Path escapes allowed root: {candidate}")
    return target
