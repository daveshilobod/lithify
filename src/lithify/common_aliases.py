# src/lithify/common_aliases.py
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer


def _ns_from_json(v):
    if isinstance(v, bool):
        raise ValueError("nanosecond timestamps must be integers, not boolean")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    raise ValueError("nanosecond timestamps must be decimal digits (string) or int")


# In memory: int. On the wire (JSON): decimal string.
NsInt = Annotated[
    int,
    BeforeValidator(_ns_from_json),
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
]
