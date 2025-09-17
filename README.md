# Lithify

**Turn JSON Schema into Pydantic v2 models. Works alongside datamodel-code-generator (DCG).**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Pydantic v2](https://img.shields.io/badge/pydantic-v2-green.svg)](https://docs.pydantic.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## TL;DR

If you use datamodel-code-generator and have JSON schemas with constrained scalar types (patterns, enums, ranges), Lithify ensures they become proper Pydantic v2 type aliases.

```bash
git clone https://github.com/daveshilobod/lithify.git
cd lithify
pip install -e .
lithify generate --schemas ./schemas --models-out ./models --package-name api
```

Your `UUID` patterns become `UUID` types. Your `SemVer` strings stay strings with validation. No wrapper classes.
Plus deep immutability for event sourcing and caching when you need it.

## The Problem

You define reusable scalar types in your JSON Schema `$defs` — UUIDs, semantic versions, hex digests:

```yaml
$defs:
  UUID:
    type: string
    pattern: "^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"

  SemVer:
    type: string
    pattern: "^(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)$"

  Sha256:
    type: string
    pattern: "^[0-9a-f]{64}$"

  Percentage:
    type: number
    minimum: 0
    maximum: 100

  Country:
    type: string
    enum: ["US", "CA", "UK", "JP"]
```

When you run datamodel-code-generator, these carefully defined types often become plain `str` or generate unexpected wrapper classes. DCG excels at handling complex nested objects and arrays - it's the industry standard for good reason. Lithify adds specialized handling for these scalar type constraints.

## What Lithify Does

Lithify works alongside DCG to give you proper Pydantic v2 type aliases for your constrained scalars. DCG handles the complex structural generation (nested objects, arrays, inheritance), while Lithify ensures your scalar constraints become real type aliases.

**Philosophy:**
- **No wrapper objects** — Strings stay strings, just with constraints
- **Deterministic output** — Same input always produces same output
- **Readable types** — Your `UUID` type hint says `UUID`, not `str`
- **Runtime transparency** — JSON serialization is unchanged

### Before (DCG alone)

```python
class Document(BaseModel):
    id: str              # should be UUID
    version: str         # should be SemVer
    checksum: str        # should be Sha256
    score: float         # should be Percentage
    country: str         # should be Country enum
    tags: list[str]      # should be list[NonEmptyString]
```

### After (Lithify + DCG)

```python
# models/common_types.py — generated once per schema of origin
from typing import Literal
from typing_extensions import Annotated
from pydantic import StringConstraints, Field

UUID = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")]
SemVer = Annotated[str, StringConstraints(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Percentage = Annotated[float, Field(ge=0, le=100)]
Country = Literal["US", "CA", "UK", "JP"]
NonEmptyString = Annotated[str, StringConstraints(min_length=1)]


# models/document.py — rewritten to use your aliases
from .common_types import UUID, SemVer, Sha256, Percentage, Country, NonEmptyString

class Document(BaseModel):
    id: UUID
    version: SemVer
    checksum: Sha256
    score: Percentage
    country: Country
    tags: list[NonEmptyString]
```

No wrapper objects. Strings stay strings with constraints.

## When to Use Lithify

**Use Lithify if you:**
- Already use datamodel-code-generator
- Have JSON schemas with scalar type definitions ($defs with patterns, ranges, enums)
- Need proper type hints for scalar constraints in your schemas
- Want readable type hints (`UUID` instead of `str`)
- Need deep immutability for event sourcing or caching

**Skip Lithify if you:**
- Only have simple objects without constrained scalars
- Don't use datamodel-code-generator
- Are satisfied with plain str/int/float types

## Installation

```bash
# From source
git clone https://github.com/daveshilobod/lithify.git
cd lithify
pip install -e .

# With code formatters (recommended)
pip install -e ".[formatting]"

# For development
pip install -e ".[dev]"
```

## More examples

### Email addresses and URLs

```yaml
# Schema
$defs:
  Email:
    type: string
    format: email
  HttpUrl:
    type: string
    format: uri
    pattern: "^https?://"
```

```python
# Generated aliases
Email = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")]
HttpUrl = Annotated[str, StringConstraints(pattern=r"^https?://")]

# Used in models
class User(BaseModel):
    email: Email
    website: HttpUrl | None
```

### Enums and string unions

```yaml
# Schema
$defs:
  LogLevel:
    enum: ["debug", "info", "warn", "error", "fatal"]

  IdFormat:
    oneOf:
      - type: string
        pattern: "^[0-9a-f]{16}$" # 64-bit hex
      - type: string
        pattern: "^[0-9a-f]{32}$" # 128-bit hex
```

```python
# Generated
LogLevel = Literal["debug", "info", "warn", "error", "fatal"]
IdFormat = Annotated[str, StringConstraints(pattern=r"^(?:[0-9a-f]{16}|[0-9a-f]{32})$")]

# Used in models
class Event(BaseModel):
    log_level: LogLevel
    trace_id: IdFormat  # accepts either 16 or 32 hex chars
```

_Note: Mixed-type unions (e.g., `int | str`) continue to use DCG's standard generation._

### Collections with constrained items

```yaml
# Schema
$defs:
  NonEmptyString:
    type: string
    minLength: 1

  Tag:
    type: string
    pattern: "^[a-z][a-z0-9-]*$"
    maxLength: 32

properties:
  tags:
    type: array
    items:
      $ref: "#/$defs/Tag"
  metadata:
    type: object
    additionalProperties:
      $ref: "#/$defs/NonEmptyString"
```

```python
# Generated
NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
Tag = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*$", max_length=32)]

# Used in models
class Resource(BaseModel):
    tags: list[Tag]
    metadata: dict[str, NonEmptyString]
```

### Numeric constraints

```yaml
# Schema
$defs:
  Latitude:
    type: number
    minimum: -90
    maximum: 90

  Longitude:
    type: number
    minimum: -180
    maximum: 180

  UnixTimestamp:
    type: integer
    minimum: 0

  Percentage:
    type: number
    minimum: 0
    maximum: 100
    multipleOf: 0.01 # two decimal places
```

```python
# Generated
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
UnixTimestamp = Annotated[int, Field(ge=0)]
Percentage = Annotated[float, Field(ge=0, le=100, multiple_of=0.01)]

# Used in models
class Location(BaseModel):
    lat: Latitude
    lon: Longitude
    accuracy: Percentage
    timestamp: UnixTimestamp
```

### Complex strings with combined constraints

```yaml
# Schema
$defs:
  Username:
    type: string
    pattern: "^[a-zA-Z][a-zA-Z0-9_-]*$"
    minLength: 3
    maxLength: 30

  StrongPassword:
    type: string
    minLength: 12
    maxLength: 128
    pattern: "^(?=.*[a-z])(?=.*[A-Z])(?=.*\\d)(?=.*[@$!%*?&])"

  PhoneNumber:
    type: string
    pattern: "^\\+?[1-9]\\d{1,14}$" # E.164 format
```

```python
# Generated
Username = Annotated[str, StringConstraints(
    pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$",
    min_length=3,
    max_length=30
)]

StrongPassword = Annotated[str, StringConstraints(
    pattern=r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])",
    min_length=12,
    max_length=128
)]

PhoneNumber = Annotated[str, StringConstraints(pattern=r"^\+?[1-9]\d{1,14}$")]
```

## Plus: Deep Immutability When You Need It

Beyond scalar type handling, Lithify offers three mutability modes. While most users will stick with the default `mutable` mode, if you need true immutability for event sourcing, caching, or thread safety, Lithify delivers:

```bash
# Standard mutable models (default)
lithify generate --schemas ./schemas --models-out ./models --package-name api

# Deep-frozen for event sourcing - recursively immutable
lithify generate --schemas ./schemas --models-out ./models --package-name events \
  --mutability deep-frozen
```

With `deep-frozen`, your models become truly immutable - lists become tuples, dicts become frozen, and everything is hashable. Perfect for audit logs, caching keys, or shared state in concurrent systems. See the [Mutability Modes](#mutability-modes) section for details.

```python
# With --mutability deep-frozen
class Config(FrozenModel):
    """Completely immutable - hashable and thread-safe."""
    version: SemVer
    features: list[FeatureFlag]  # Automatically converted to tuple
    settings: dict[str, ConfigValue]  # Automatically converted to frozendict

# After initialization, containers are immutable:
# - features is now a tuple
# - settings is now a frozendict
# Can be used as dict keys, in sets, etc.
configs: set[Config] = {config1, config2}
cache: dict[Config, Result] = {config: computed_result}
```

## Quick Start

```bash
# Basic usage (JSON files saved to temp)
lithify generate \
  --schemas ./schemas \
  --models-out ./models \
  --package-name api

# Keep JSON files for migration from YAML
lithify generate \
  --schemas ./schemas \
  --json-out ./schemas/json \
  --models-out ./models \
  --package-name api

# With remote refs and cleanup
lithify generate \
  --schemas ./schemas \
  --models-out ./models \
  --package-name api \
  --base-url https://api.example.com/schemas/ \
  --clean

# For PATCH endpoints (all fields optional)
lithify generate \
  --schemas ./schemas \
  --models-out ./models \
  --package-name api_patch \
  --partial

# Debug mode - keep all intermediate files for inspection
lithify generate \
  --schemas ./schemas \
  --json-out ./debug/json \
  --models-out ./models \
  --package-name api \
  --output-mode debug

# Dry run - see what would happen without executing
lithify generate \
  --schemas ./schemas \
  --models-out ./models \
  --package-name api \
  --dry-run
```

## Supported constraints

### Strings

- `pattern` — regex validation
- `minLength`, `maxLength` — length bounds
- `format` — for known formats like `date-time`
- `enum` — becomes `Literal[...]`

### Numbers

- `minimum`, `maximum` — inclusive bounds
- `exclusiveMinimum`, `exclusiveMaximum` — exclusive bounds
- `multipleOf` — value must be multiple of this

### Unions

- `oneOf` with scalar strings — compiles to single regex

## Guarantees

- One module per schema of origin with predictable imports.
- No wrapper types. Runtime JSON shape is unchanged.
- Scalar names preserved in type hints for IDEs and readability.
- Deterministic output and idempotent rewrites.

## Defaults and choices

- `format: date-time` remains a string by default. If you prefer `datetime`, add your own validators.
- Unions of scalar strings compile into a single noncapturing regex.

## FAQ

**Why not just use DCG alone?**
DCG is excellent and handles 90% of schema-to-model generation perfectly. Lithify just adds specialized handling for constrained scalar types that benefit from type aliases.

**Why not wrap scalars in classes?**
Wrapper classes require `.value` everywhere and change your JSON structure. Lithify's aliases keep the runtime behavior identical.

**Why not manually write the types?**
You could, but Lithify ensures consistency between your schemas and code, and handles complex unions automatically.

**Is this a DCG replacement?**
No, it's a companion tool. DCG does the heavy lifting; Lithify adds precision for scalar types.

## Limitations

- Mixed-type unions (e.g., `int | str`) continue to use DCG's standard generation.
- Advanced recursion (`$dynamicRef`, `$recursiveRef`, `$dynamicAnchor`) is not yet handled.
- Scalar types must have constraints (pattern, minLength, etc.) - plain `type: string` won't generate an alias.
- Python only, Pydantic v2 only.

## CLI options

```bash
lithify generate [OPTIONS]

Required:
  --schemas PATH          Root directory of schemas
  --models-out PATH       Root directory for generated models
  --package-name TEXT     Generated package name

Optional:
  --json-out PATH         Directory for JSON schemas (if not specified, uses temp)
  --base-url TEXT         Remote schema base URL to rewrite
  --block-remote-refs     Treat http(s) $refs as errors
  --partial               Make all fields Optional (PATCH endpoints)
  --clean                 Remove existing outputs before generation
  --check                 Exit 1 if regeneration needed (for CI/CD)
  --verbose, -v           Increase verbosity (repeat for more)

Workspace & Output:
  --output-mode [clean|debug]
                          clean: stage in temp, copy only .py files (default)
                          debug: write everything in place for inspection
  --format [auto|ruff|black|none]
                          Code formatter (default: auto-detect)
  --no-rewrite            Skip post-generation rewrite steps
  --dry-run               Show plan without writing anything

Mutability:
  --mutability [mutable|frozen|deep-frozen]
                          Default: mutable
  --immutable-hints       Use immutable type hints (deep-frozen only)
  --use-frozendict        Use FrozenDict (deep-frozen only)
  --from-attributes       Enable from_attributes (ORM compat)
```

## Mutability Modes

Lithify offers three mutability modes, solving a critical limitation in Pydantic's immutability model.

### The Problem: Shallow vs Deep Immutability

Pydantic's `frozen=True` prevents reassigning fields but doesn't freeze the contents of those fields. If a field contains a list or dict, you can still modify what's inside:

```python
# With Pydantic's frozen=True
class Event(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    tags: list[str]

event = Event(id="123", tags=["important"])
event.id = "456"           # ❌ Raises ValidationError - attribute is frozen
event.tags.append("urgent") # ✅ Works! The list is still mutable
event.tags[0] = "modified" # ✅ Works! Internal mutation is allowed
```

This breaks immutability guarantees needed for caching, thread safety, and event sourcing.

### Three Modes

#### 1. `mutable` (default)

Standard Pydantic models with full mutability.

```python
class User(MutableBase):
    name: str
    roles: list[str]

user = User(name="Alice", roles=["admin"])
user.name = "Bob"           # ✅ Works
user.roles.append("owner")  # ✅ Works
```

**Use for:** APIs, DTOs, ORMs, form handling, builders - anywhere you need to modify data after creation.

#### 2. `frozen`

Pydantic's `frozen=True` - attributes are immutable but containers are not.

```python
class Config(FrozenBase):
    version: str
    features: list[str]

config = Config(version="1.0", features=["auth"])
config.version = "2.0"        # ❌ Raises ValidationError
config.features.append("api") # ✅ Still works - shallow freeze only
```

**Use for:** Configuration objects, value objects where you only need top-level immutability.

#### 3. `deep-frozen`

Lithify's recursive immutability - everything is frozen all the way down.

```python
class AuditEvent(FrozenModel):
    id: str
    tags: list[str]           # Declared as list for convenience
    metadata: dict[str, str]  # Declared as dict for convenience

event = AuditEvent(
    id="123",
    tags=["important", "security"],  # Pass a normal list
    metadata={"user": "alice"}        # Pass a normal dict
)
# After initialization:
# - event.tags is a tuple
# - event.metadata is a frozendict (or MappingProxyType)
event.id = "456"                # ❌ Raises AttributeError
event.tags.append("urgent")     # ❌ AttributeError - tuples are immutable
event.metadata["key"] = "value" # ❌ TypeError - frozendict is immutable

# Fully hashable - can be used as dict keys or in sets
cache: dict[AuditEvent, Result] = {event: result}
events_set: set[AuditEvent] = {event1, event2}
```

**Use for:**

- **Event sourcing** - Events must be immutable historical records
- **Caching** - Objects used as cache keys must not change
- **Thread safety** - Shared data between threads must be immutable
- **Functional programming** - Pure functions need truly immutable inputs

### How Deep-Frozen Works

Lithify recursively converts mutable containers during model initialization:

- `list` → `tuple`
- `set` → `frozenset`
- `dict` → `frozendict` (with `--use-frozendict`) or `MappingProxyType`

This happens automatically in `model_post_init`, so you:
1. Declare fields with familiar mutable types (`list`, `dict`, `set`)
2. Pass normal mutable containers to the constructor
3. Get back deeply immutable instances

No need to manually annotate with `tuple` or import `frozendict` - Lithify handles the conversion.

### Command Examples

```bash
# Standard mutable models (default)
lithify generate --schemas ./schemas --models-out ./models --package-name api

# Configuration objects with shallow freeze
lithify generate --schemas ./schemas --models-out ./models --package-name config \
  --mutability frozen

# Event sourcing with deep immutability and hashable models
lithify generate --schemas ./schemas --models-out ./models --package-name events \
  --mutability deep-frozen --use-frozendict

# Deep-frozen with immutable type hints (tuple instead of List, etc.)
lithify generate --schemas ./schemas --models-out ./models --package-name audit \
  --mutability deep-frozen --immutable-hints
```

### Quick Reference

| Mode          | Attributes   | Containers   | Hashable   | Use Cases                  |
| ------------- | ------------ | ------------ | ---------- | -------------------------- |
| `mutable`     | ✅ Mutable   | ✅ Mutable   | ❌ No      | APIs, forms, ORMs          |
| `frozen`      | ❌ Immutable | ⚠️ Mutable   | ⚠️ Partial | Config objects             |
| `deep-frozen` | ❌ Immutable | ❌ Immutable | ✅ Yes     | Events, caching, threading |

## Automatic code formatting

Lithify automatically formats generated code using ruff or black (whichever is available):

```bash
# Auto-detect formatter (default)
lithify generate ... --format auto

# Use specific formatter
lithify generate ... --format ruff
lithify generate ... --format black

# Skip formatting
lithify generate ... --format none
```

## Troubleshooting

**"No YAML or JSON schema files found"**

- Check your `--schemas` path contains `.yaml` or `.json` files

**Generated models have `str` instead of your types**

- Ensure your `$defs` have constraints (pattern, minLength, format, etc.)
- Plain `type: string` without constraints won't generate an alias
- Numbers need constraints (min, max, multipleOf) to generate aliases

**Import errors in generated code**

- Check that schema filenames are valid Python identifiers
- Lithify sanitizes names automatically, but extreme cases may need manual adjustment

**Need to debug schema processing?**

- Use `--output-mode debug` to keep all intermediate files
- Add `-v` or `-vv` for verbose output
- Use `--dry-run` to see the execution plan without making changes

## Requirements

- Python >=3.11
- Pydantic >=2.5
- datamodel-code-generator >=0.25
- PyYAML >=6.0
- typer >=0.17
- email-validator >=2.0

## Contributing

If Lithify misses a case, open an issue with a minimal reproducible schema and the expected Python type surface. Include the exact DCG version and command you used.

## How it Works (Technical Details)

1. Converts YAML to JSON (preserving `$ref` structure).
2. Indexes your schemas and resolves `$ref`s with proper identity and JSON Pointer handling.
3. Classifies shapes: scalar strings (pattern, minLength, maxLength, format), numbers (min, max, multipleOf), enums, and unions.
4. Generates type aliases for scalar types with constraints.
5. Generates per-schema alias modules using `Annotated[str, StringConstraints(...)]`.
6. Runs DCG to generate the full object models.
7. Enhances the generated code by replacing basic type annotations with your type aliases, including `Optional[str]`, `list[str]`, and `dict[str, str]` on the value side.

## Credits

Lithify builds on top of the excellent [datamodel-code-generator](https://github.com/koxudaxi/datamodel-code-generator) by Koudai Aono (@koxudaxi). DCG does the heavy lifting of parsing JSON Schema and generating Pydantic models.

## License

MIT

---

Built in Hawai'i for deterministic builds, readable types, and schemas that match what you meant.
