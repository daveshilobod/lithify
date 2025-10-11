# src/lithify/slas_allof_processor.py
import json
import warnings
from dataclasses import dataclass
from pathlib import Path

from .slas_schema_index import SchemaIndex
from .utils import walk_schema_nodes


@dataclass
class ValidationError:
    """Structured validation error with context."""

    file: str
    json_pointer: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.json_pointer}\n  {self.message}"


@dataclass
class InlineAllOfInfo:
    """Info about an inline allOf that was collapsed."""

    property_name: str
    parent_class: str  # From x-python-class-name or title
    merged_schema: dict
    json_pointer: str
    origin_file: Path


def _is_inline_property_allof(node: dict, json_ptr: str, parent_class: str | None) -> bool:
    """
    Determine if an allOf is an inline property refinement.

    Criteria:
    - No title attribute (not a named type)
    - In a /properties/ path (not a $def)
    - Has a parent class context

    Args:
        node: The schema node containing allOf
        json_ptr: JSON pointer to the node
        parent_class: Parent class name (from schema title or override)

    Returns:
        True if this is an inline property allOf that needs a synthetic alias
    """
    return "title" not in node and "/properties/" in json_ptr and parent_class is not None


def _extract_property_name(json_pointer: str) -> str:
    """
    Extract property name from JSON pointer.

    Handles:
    - Simple: "#/properties/user_id" → "user_id"
    - Nested: "#/$defs/Foo/properties/bar" → "bar"

    Args:
        json_pointer: JSON pointer string

    Returns:
        Property name

    Raises:
        ValueError: If pointer doesn't contain /properties/<name>
    """
    parts = json_pointer.split("/")

    # Find last occurrence of /properties/ for nested cases
    last_properties_idx = -1
    for i, part in enumerate(parts):
        if part == "properties":
            last_properties_idx = i

    if last_properties_idx >= 0 and last_properties_idx + 1 < len(parts):
        return parts[last_properties_idx + 1]

    raise ValueError(
        f"Cannot extract property name from JSON pointer: {json_pointer}\n"
        f"Expected format: .../properties/<property_name>/..."
    )


def is_allof_refinement(schema: dict) -> bool:
    """
    Check if schema is an allOf (structure only).

    Returns True if:
    - Has 'allOf' key
    - allOf is a list
    - Has 2+ branches

    Does NOT resolve refs or validate types - single responsibility.
    """
    if "allOf" not in schema:
        return False

    branches = schema["allOf"]
    return isinstance(branches, list) and len(branches) >= 2


def resolve_allof_branches(
    branches: list,
    index: SchemaIndex,
    json_pointer: str,
    doc_uri: str,
) -> list[dict]:
    """
    Resolve all $refs in branches, detect circular references.

    Raises ValueError if circular refs detected.
    """
    visited_nodes = set()
    resolved = []

    for i, branch in enumerate(branches):
        if "$ref" in branch:
            ref_uri = branch["$ref"]

            # Resolve the reference against the document's URI
            full_ref_uri = index.resolve_ref(ref_uri, doc_uri)

            node = index.node_for(full_ref_uri)
            if not node:
                raise ValueError(f"Cannot resolve $ref in allOf[{i}]: {ref_uri}")

            if id(node) in visited_nodes:
                raise ValueError(f"Circular reference detected in allOf[{i}]: {ref_uri}")
            visited_nodes.add(id(node))

            resolved.append(node)
        else:
            resolved.append(branch)

    if not resolved:
        raise ValueError(f"allOf has no resolvable branches at {json_pointer}")

    return resolved


def validate_scalar_types(branches: list[dict], json_pointer: str) -> str:
    """
    Validate all branches are compatible scalar types.

    Returns the single scalar type if valid.
    Raises ValueError if mixed types or non-scalar.
    """
    scalar_types = {"string", "number", "integer", "boolean"}
    types_seen = set()

    for i, branch in enumerate(branches):
        if "type" in branch:
            t = branch["type"]
            if t not in scalar_types:
                raise ValueError(
                    f"Branch {i} has non-scalar type '{t}' in allOf.\n"
                    f"allOf refinement only works with scalar types: {scalar_types}"
                )
            types_seen.add(t)

    if not types_seen:
        raise ValueError(
            f"allOf at {json_pointer} has no type information.\n"
            f"Cannot determine if scalar refinement is applicable."
        )

    if len(types_seen) > 1:
        raise ValueError(
            f"allOf at {json_pointer} mixes incompatible scalar types: {types_seen}\n"
            f"All branches must have the same type for refinement."
        )

    return types_seen.pop()


def extract_uuid_version_set(pattern: str) -> set[str] | None:
    """
    Extract version character set from UUID pattern third group.

    UUID format: 8-4-4-4-12 hex digits
    Third group contains version nibble

    Examples:
        "...-[1-5][0-9a-f]{3}-..." → {'1','2','3','4','5'}
        "...-7[0-9a-f]{3}-..."      → {'7'}
        "...-[0-9a-f]{4}-..."       → None (no version constraint)

    Returns None if not a recognizable UUID pattern.
    """
    clean = pattern.lstrip("^").rstrip("$")

    hyphen_positions = []
    bracket_depth = 0
    brace_depth = 0

    for i, char in enumerate(clean):
        if char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
        elif char == "-" and bracket_depth == 0 and brace_depth == 0:
            hyphen_positions.append(i)

    if len(hyphen_positions) != 4:
        return None

    start = hyphen_positions[1] + 1
    end = hyphen_positions[2]
    third_group = clean[start:end]

    if third_group.startswith("["):
        close_bracket = third_group.find("]")
        if close_bracket == -1:
            return None

        class_content = third_group[1:close_bracket]

        if len(class_content) == 3 and class_content[1] == "-":
            start_char = class_content[0]
            end_char = class_content[2]
            if start_char.isdigit() and end_char.isdigit():
                return {str(i) for i in range(int(start_char), int(end_char) + 1)}

        if all(c.isdigit() for c in class_content):
            return set(class_content)

        if class_content in ["0-9a-f", "a-f0-9", "0-9a-fA-F"]:
            return None

    elif len(third_group) > 0 and third_group[0].isdigit():
        return {third_group[0]}

    return None


def try_uuid_pattern_specialization(patterns: list[str], json_pointer: str) -> str | None:
    """
    Detect UUID version specialization and return specialized pattern.

    Strategy:
    1. Extract version sets from both patterns
    2. Compute intersection
    3. Error if empty (unsatisfiable)
    4. Return pattern with smaller set (more specific)

    Returns:
        Specialized pattern if one is subset of other, else None

    Raises:
        ValueError if patterns conflict (empty intersection)
    """
    if len(patterns) != 2:
        return None

    p1, p2 = patterns
    v1 = extract_uuid_version_set(p1)
    v2 = extract_uuid_version_set(p2)

    # Not UUID patterns or no version constraints
    if v1 is None or v2 is None:
        return None

    intersection = v1 & v2

    if not intersection:
        raise ValueError(
            f"UUID pattern conflict at {json_pointer}:\n"
            f"  Base allows versions: {sorted(v1)}\n"
            f"  Refinement requires: {sorted(v2)}\n"
            f"  Intersection: empty (unsatisfiable)\n"
            f"  Example: Cannot refine UUID v1-5 to require v7"
        )

    if v2.issubset(v1) and v2 != v1:
        return p2
    elif v1.issubset(v2) and v1 != v2:
        return p1
    else:
        return p1


def merge_constraints(branches: list[dict], scalar_type: str, json_pointer: str, strict: bool = True) -> dict:
    """
    Merge allOf branches into a single constrained scalar schema.

    Strategy:
    1. Try UUID pattern specialization (preferred)
    2. Fall back to collecting all patterns for BeforeValidator
    3. Merge other constraints (lengths, bounds, enums)
    4. Validate satisfy-ability if strict=True

    Returns merged schema dict with possible _validator_patterns key.
    Raises ValueError if constraints are unsatisfiable in strict mode.
    """
    merged = {"type": scalar_type}

    patterns = [b["pattern"] for b in branches if "pattern" in b]
    if patterns:
        try:
            specialized = try_uuid_pattern_specialization(patterns, json_pointer)
            if specialized:
                merged["pattern"] = specialized
            else:
                merged["_validator_patterns"] = patterns
                merged["pattern"] = patterns[0]
        except ValueError as e:
            if strict:
                raise
            else:
                warnings.warn(f"Pattern conflict (lenient mode): {e}", UserWarning, stacklevel=2)
                merged["pattern"] = patterns[0]

    min_lengths = [b["minLength"] for b in branches if "minLength" in b]
    max_lengths = [b["maxLength"] for b in branches if "maxLength" in b]
    if min_lengths:
        merged["minLength"] = max(min_lengths)
    if max_lengths:
        merged["maxLength"] = min(max_lengths)

    minimums = [b["minimum"] for b in branches if "minimum" in b]
    maximums = [b["maximum"] for b in branches if "maximum" in b]
    if minimums:
        merged["minimum"] = max(minimums)
    if maximums:
        merged["maximum"] = min(maximums)

    enums = [set(b["enum"]) for b in branches if "enum" in b]
    if enums:
        intersection = set.intersection(*enums)
        if not intersection:
            msg = f"allOf enum intersection is empty (unsatisfiable).\n" f"Branch enums: {[sorted(e) for e in enums]}"
            if strict:
                raise ValueError(msg)
            else:
                warnings.warn(msg, UserWarning, stacklevel=2)
                merged["enum"] = sorted(enums[0])
        else:
            merged["enum"] = sorted(intersection)

    for b in branches:
        if "format" in b:
            merged["format"] = b["format"]
            break

    return merged


def validate_satisfiability(schema: dict, json_pointer: str, strict: bool = True) -> None:
    """
    Validate that merged constraints don't create impossible conditions.

    Raises ValueError if unsatisfiable in strict mode.
    Warns if lenient mode.
    """
    errors = []

    if "minLength" in schema and "maxLength" in schema:
        if schema["minLength"] > schema["maxLength"]:
            errors.append(
                f"minLength={schema['minLength']} > maxLength={schema['maxLength']}\n"
                f"  No string can satisfy both constraints"
            )

    if "minimum" in schema and "maximum" in schema:
        if schema["minimum"] > schema["maximum"]:
            errors.append(
                f"minimum={schema['minimum']} > maximum={schema['maximum']}\n"
                f"  No number can satisfy both constraints"
            )

    if "exclusiveMinimum" in schema and "exclusiveMaximum" in schema:
        if schema["exclusiveMinimum"] >= schema["exclusiveMaximum"]:
            errors.append(
                f"exclusiveMinimum={schema['exclusiveMinimum']} >= "
                f"exclusiveMaximum={schema['exclusiveMaximum']}\n"
                f"  No number can satisfy both constraints"
            )

    if errors:
        msg = f"Impossible constraints at {json_pointer}:\n  " + "\n  ".join(errors)
        if strict:
            raise ValueError(msg)
        else:
            warnings.warn(msg, UserWarning, stacklevel=2)


def process_allof_collapse(
    json_dir: Path, index: SchemaIndex, strict: bool = True, verbose: int = 0
) -> tuple[Path, list[InlineAllOfInfo]]:
    """
    Collapse allOf scalar refinements with fixpoint iteration.

    Repeats until no more collapses occur, handling nested $def dependencies.
    Tracks inline allOf (those without title in properties).

    Args:
        json_dir: Directory with JSON schemas (modified in place)
        index: Schema index for $ref resolution
        strict: Fail on unsatisfiable constraints
        verbose: Verbosity level

    Returns:
        (json_dir, inline_allofs) tuple

    Raises:
        RuntimeError if errors in strict mode

    Note: json_dir is the safe working copy (st.safe_json_dir), so in-place
    modification is safe even if errors occur mid-process.
    """
    max_iterations = 10
    total_collapsed = 0
    inline_allofs = []

    for iteration in range(max_iterations):
        if verbose >= 2:
            print(f"[allof] Iteration {iteration + 1}")

        errors = []
        iteration_collapsed = 0

        for schema_file in sorted(json_dir.glob("*.json")):
            schema = json.loads(schema_file.read_text(encoding="utf-8"))
            modified = False

            # Look up the doc_uri that the index uses for this file.
            # CRITICAL: Must use the same URI that the index uses internally.
            # Creating a file:/// URI with schema_file.as_uri() will fail because
            # the index might have minted a lithify:/// URI or used a base_url.
            # See doc_uri_for_path() docstring for details.
            doc_uri = index.doc_uri_for_path(schema_file)
            if not doc_uri:
                if verbose >= 1:
                    print(f"[allof-warn] File {schema_file.name} not in index, skipping")
                continue

            schema_title = schema.get("title")
            if schema_title:
                parent_class = index.class_name_overrides.get(schema_title, schema_title)
            else:
                parent_class = None

            for node, json_ptr in walk_schema_nodes(schema):
                if not is_allof_refinement(node):
                    continue

                try:
                    branches = resolve_allof_branches(node["allOf"], index, json_ptr, doc_uri)
                    scalar_type = validate_scalar_types(branches, json_ptr)

                    merged = merge_constraints(branches, scalar_type, json_ptr, strict)

                    validate_satisfiability(merged, json_ptr, strict)

                    if _is_inline_property_allof(node, json_ptr, parent_class):
                        try:
                            property_name = _extract_property_name(json_ptr)
                            inline_allofs.append(
                                InlineAllOfInfo(
                                    property_name=property_name,
                                    parent_class=parent_class,
                                    merged_schema=dict(merged),
                                    json_pointer=json_ptr,
                                    origin_file=schema_file,
                                )
                            )
                            if verbose >= 2:
                                print(f"[allof] Tracked inline: {parent_class}.{property_name}")
                        except ValueError:
                            pass

                    node.clear()
                    node.update(merged)
                    modified = True
                    iteration_collapsed += 1

                    if verbose >= 2:
                        print(f"[allof] Collapsed {schema_file.name}:{json_ptr}")

                except ValueError as e:
                    error = ValidationError(file=schema_file.name, json_pointer=json_ptr, message=str(e))
                    errors.append(error)
                    if verbose >= 1:
                        print(f"[allof-error] {error}")

            if modified:
                with schema_file.open("w", encoding="utf-8") as f:
                    json.dump(schema, f, indent=2, ensure_ascii=False, sort_keys=True)

        total_collapsed += iteration_collapsed

        if iteration_collapsed == 0:
            if verbose >= 1:
                print(f"[allof] Fixpoint reached after {iteration + 1} iteration(s)")
            break

        index = SchemaIndex.load(list(json_dir.rglob("*.json")), index.base_url)

    else:
        if iteration_collapsed > 0:
            warnings.warn(
                f"Max iterations ({max_iterations}) reached with {iteration_collapsed} "
                f"remaining collapses. Some allOf may remain unprocessed.",
                UserWarning,
                stacklevel=2,
            )

    if verbose >= 1:
        print(f"[allof] Total collapsed: {total_collapsed}")
        if inline_allofs:
            print(f"[allof] Tracked {len(inline_allofs)} inline allOf(s)")

    if errors and strict:
        raise RuntimeError(
            f"allOf processing encountered {len(errors)} error(s):\n\n" + "\n\n".join(str(e) for e in errors)
        )

    return json_dir, inline_allofs


def generate_validator_code(patterns: list[str], type_name: str) -> str:
    """
    Generate Pydantic BeforeValidator code for multiple pattern validation.

    Used when pattern specialization is not possible (fallback strategy).

    Args:
        patterns: List of regex patterns that must ALL match (AND semantics)
        type_name: Name of the type being validated (for error messages)

    Returns:
        Python code string for the validator function
    """
    escaped_patterns = [p.replace("\\", "\\\\").replace('"', '\\"') for p in patterns]

    validator_code = f'''
def validate_{type_name.lower()}(value: str) -> str:
    """Validate value matches all required patterns (AND semantics)."""
    if not isinstance(value, str):
        raise ValueError(f"{{value!r}} must be a string")

    patterns = {escaped_patterns!r}
    for pattern in patterns:
        if not re.match(pattern, value):
            raise ValueError(
                f"{{value!r}} does not match required pattern: {{pattern}}"
            )
    return value
'''
    return validator_code
