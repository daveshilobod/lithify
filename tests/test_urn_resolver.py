from pathlib import Path

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "urn_allof_dotted"

if not FIXTURE_ROOT.exists():
    raise RuntimeError(f"Fixture directory not found: {FIXTURE_ROOT}")

URN_MAP = {
    "urn:test:types:v1": FIXTURE_ROOT / "types.v1.schema.json",
    "urn:test:entity:v1": FIXTURE_ROOT / "entity.v1.schema.json",
}


def resolve_test_urn(urn: str) -> Path:
    base_urn = urn.split("#")[0]

    if base_urn not in URN_MAP:
        raise KeyError(f"Unknown test URN: {base_urn}")

    path = URN_MAP[base_urn]
    if not path.exists():
        raise FileNotFoundError(f"Fixture file not found: {path}")

    return path
