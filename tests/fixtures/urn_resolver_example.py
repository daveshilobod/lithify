# tests/fixtures/urn_resolver_example.py
"""URN resolver for test fixtures."""

from pathlib import Path

FIXTURE_ROOT = Path(__file__).parent

URN_MAP = {
    "urn:example:common:v1": FIXTURE_ROOT / "urn_with_override/common_types.json",
    "urn:example:record:v1": FIXTURE_ROOT / "urn_with_override/system_record.json",
}


def resolve_urn(urn: str) -> Path:
    """Resolve URN to fixture file path."""
    if urn not in URN_MAP:
        raise KeyError(f"Unknown URN: {urn}")
    return URN_MAP[urn]
