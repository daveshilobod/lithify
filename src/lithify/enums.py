# src/lithify/enums.py
from enum import Enum


class OutputMode(str, Enum):
    clean = "clean"
    debug = "debug"


class FormatChoice(str, Enum):
    auto = "auto"
    ruff = "ruff"
    black = "black"
    none = "none"


class Mutability(str, Enum):
    mutable = "mutable"
    frozen = "frozen"
    deep_frozen = "deep-frozen"
