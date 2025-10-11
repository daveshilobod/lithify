# src/lithify/nsint_generator.py
from __future__ import annotations

NSINT_TYPE_CODE = '''
def _ns_from_json(v):
    """Convert nanosecond timestamp from JSON string or int."""
    if isinstance(v, bool):
        raise ValueError("nanosecond timestamps must be integers, not boolean")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    raise ValueError("nanosecond timestamps must be decimal digits (string) or int")

# Nanosecond timestamp: int in memory, string in JSON
NsInt = Annotated[
    int,
    BeforeValidator(_ns_from_json),
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
]
'''


def needs_nsint_generation(schemas: list[dict]) -> bool:
    from .nsint_mapper import detect_ns_fields

    for schema in schemas:
        if detect_ns_fields(schema):
            return True
    return False


def generate_nsint_code() -> str:
    return NSINT_TYPE_CODE
