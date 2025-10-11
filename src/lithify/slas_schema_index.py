# src/lithify/slas_schema_index.py
"""Index mapping schema URIs to documents, nodes, and inter-schema references."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin

import typer


def resolve_pointer(doc: dict | list, pointer: str) -> Any:
    """RFC 6901 JSON Pointer resolution. Handles ~0/~1 escaping and array/object traversal."""
    if pointer == "" or pointer == "#":
        return doc
    if pointer.startswith("#"):
        pointer = pointer[1:]
    if pointer.startswith("/"):
        parts = pointer.split("/")[1:]
    else:
        parts = pointer.split("/")

    def unescape(token: str) -> str:
        return token.replace("~1", "/").replace("~0", "~")

    cur = doc
    for raw in parts:
        tok = unescape(raw)
        if isinstance(cur, list):
            idx = int(tok)
            cur = cur[idx]
        elif isinstance(cur, dict):
            cur = cur[tok]
        else:
            raise KeyError(f"Cannot dereference {tok!r} in non-container {type(cur)}")
    return cur


def resolve_uri(base: str, ref: str) -> tuple[str, str]:
    """Returns (abs_uri_without_fragment, fragment) for ref resolved against base."""
    abs_ = urljoin(base, ref)
    nofrag, frag = urldefrag(abs_)
    return nofrag, ("#" + frag) if frag else ""


def extract_class_name_override(schema: dict) -> str | None:
    return schema.get("x-python-class-name")


@dataclass(frozen=True)
class NodeId:
    """Identifies a node in the schema graph."""

    doc_uri: str
    fragment: str = ""  # "" or "#/..."

    @property
    def uri(self) -> str:
        return self.doc_uri + self.fragment


@dataclass
class SchemaIndex:
    """URI-based schema resolution. Maps document URIs to schemas, anchors to nodes, pointers to subschemas."""

    docs: dict[str, dict[Any, Any]] = field(default_factory=dict)
    anchors: dict[str, dict[Any, Any]] = field(default_factory=dict)  # uri#anchor -> node
    pointers: dict[str, dict[Any, Any]] = field(default_factory=dict)  # uri#/pointer -> node
    subschema_bases: dict[int, str] = field(default_factory=dict)  # id(node) -> base URI
    origin_files: dict[str, Path] = field(default_factory=dict)  # doc_uri -> source file
    class_name_overrides: dict[str, str] = field(default_factory=dict)  # title -> x-python-class-name
    base_url: str | None = None

    @classmethod
    def load(cls, paths: list[Path], base_url: str | None = None) -> SchemaIndex:
        index = cls()
        index.base_url = base_url

        for path in sorted(paths):
            if path.suffix != ".json":
                continue

            try:
                with path.open("r", encoding="utf-8") as f:
                    doc = json.load(f)
            except json.JSONDecodeError:
                continue

            if "$id" in doc:
                doc_uri = doc["$id"]
            elif base_url:
                rel_path = path.name
                doc_uri = urljoin(base_url, rel_path)
            else:
                # lithify:/// pseudo-scheme allows refs without base_url while maintaining URI resolution semantics
                doc_uri = f"lithify:///{path.stem}.json"

            index.docs[doc_uri] = doc
            index.origin_files[doc_uri] = path
            index._index_tree(doc, doc_uri, doc_uri)

        return index

    def resolve_ref(self, ref: str, context_doc_uri: str) -> str:
        # Custom handling for lithify:/// pseudo-scheme
        # urljoin() doesn't understand lithify:/// correctly
        if context_doc_uri.startswith("lithify:///"):
            if ref.startswith(("#", "http://", "https://", "file://", "lithify:///")):
                return urljoin(context_doc_uri, ref)

            ref_path = ref.lstrip("./")

            if "#" in ref_path:
                path, fragment = ref_path.split("#", 1)
                return f"lithify:///{path}#{fragment}"
            return f"lithify:///{ref_path}"

        return urljoin(context_doc_uri, ref)

    def _index_tree(self, node: Any, doc_uri: str, base_uri: str, pointer: str = "") -> None:
        if not isinstance(node, dict):
            return

        if "title" in node:
            override = extract_class_name_override(node)
            if override:
                from .validation import validate_class_name_override

                origin_file = self.origin_files.get(doc_uri)
                if origin_file:
                    try:
                        validate_class_name_override(override, origin_file)
                    except ValueError as e:
                        typer.secho(str(e), fg=typer.colors.RED)
                        raise typer.Exit(1) from e
                self.class_name_overrides[node["title"]] = override

        # JSON Schema Draft 2020-12: nested $id establishes new base URI for all relative refs in subtree
        if "$id" in node and pointer:
            nested_uri = urljoin(base_uri, node["$id"])
            self.docs[nested_uri] = node
            base_uri = nested_uri

        self.subschema_bases[id(node)] = base_uri

        if pointer:
            pointer_uri = doc_uri + "#" + pointer
            self.pointers[pointer_uri] = node

        if "$anchor" in node:
            anchor_uri = doc_uri + "#" + node["$anchor"]
            self.anchors[anchor_uri] = node

        if "$defs" in node:
            for name, subschema in node["$defs"].items():
                self._index_tree(subschema, doc_uri, base_uri, f"{pointer}/$defs/{name}")

        if "definitions" in node:
            for name, subschema in node["definitions"].items():
                self._index_tree(subschema, doc_uri, base_uri, f"{pointer}/definitions/{name}")

        if "properties" in node:
            for name, subschema in node["properties"].items():
                # RFC 6901: escape ~ and / in JSON Pointer tokens (~ becomes ~0, / becomes ~1)
                escaped = name.replace("~", "~0").replace("/", "~1")
                self._index_tree(subschema, doc_uri, base_uri, f"{pointer}/properties/{escaped}")

        for key in ["items", "prefixItems", "contains", "additionalProperties", "if", "then", "else", "not"]:
            if key in node:
                self._index_tree(node[key], doc_uri, base_uri, f"{pointer}/{key}")

        for key in ["allOf", "anyOf", "oneOf"]:
            if key in node and isinstance(node[key], list):
                for i, subschema in enumerate(node[key]):
                    self._index_tree(subschema, doc_uri, base_uri, f"{pointer}/{key}/{i}")

    def node_for(self, uri: str) -> dict[Any, Any] | None:
        # Custom handling for lithify:/// pseudo-scheme
        # urldefrag() mangles lithify:/// (three slashes) to lithify:/ (one slash)
        if uri.startswith("lithify:///"):
            if "#" in uri:
                doc_uri, fragment = uri.split("#", 1)
            else:
                doc_uri = uri
                fragment = ""
        else:
            doc_uri, fragment = urldefrag(uri)

        if doc_uri not in self.docs:
            return None
        doc = self.docs[doc_uri]

        if not fragment:
            return doc

        # JSON Schema spec: $anchor takes precedence over JSON Pointer for fragment resolution
        anchor_uri = doc_uri + "#" + fragment
        if anchor_uri in self.anchors:
            return self.anchors[anchor_uri]

        if fragment.startswith("/"):
            pointer_uri = doc_uri + "#" + fragment
            if pointer_uri in self.pointers:
                return self.pointers[pointer_uri]

            try:
                return resolve_pointer(doc, fragment)
            except (KeyError, IndexError, TypeError):
                return None

        return None

    def refs_from(self, node_id: NodeId) -> list[str]:
        """Find all $refs from a node and resolve them to absolute URIs."""
        node = self.node_for(node_id.uri)
        if not node:
            return []

        refs = []
        base = self.subschema_bases.get(id(node), node_id.doc_uri)

        def visit(obj: Any) -> None:
            if isinstance(obj, dict):
                if "$ref" in obj:
                    ref = obj["$ref"]
                    abs_uri, frag = resolve_uri(base, ref)
                    full_uri = abs_uri + (frag or "")
                    refs.append(full_uri)

                for v in obj.values():
                    visit(v)
            elif isinstance(obj, list):
                for v in obj:
                    visit(v)

        visit(node)
        return refs

    def doc_uri_for_path(self, path: Path) -> str | None:
        """Find the document URI for a given file path.

        Provides reverse lookup from filesystem path to the document URI
        that was assigned during index loading. This ensures consistency
        when the allOf processor needs to resolve refs using the same
        URIs that the index uses internally.

        Args:
            path: File system path to look up

        Returns:
            Document URI (e.g., lithify:///entity_v1.json) if found, None otherwise

        Example:
            >>> path = Path("/tmp/safe_dir/entity_v1.json")
            >>> index.doc_uri_for_path(path)
            'lithify:///entity_v1.json'
        """
        path_resolved = path.resolve()

        for doc_uri, origin_path in self.origin_files.items():
            if origin_path.resolve() == path_resolved:
                return doc_uri

        return None

    def exportables(self, doc_uri: str) -> list[tuple[NodeId, str, str]]:
        """Get exportable symbols from a document.

        Returns: List of (node_id, symbol_name, origin_module_name)
        """
        if doc_uri not in self.docs:
            return []

        doc = self.docs[doc_uri]
        origin_file = self.origin_files[doc_uri]

        stem = origin_file.stem
        if "_" in stem and stem.split("_", 1)[0].isdigit():
            module_name = stem.split("_", 1)[1]
        else:
            module_name = stem

        exports = []

        if "title" in doc:
            exports.append((NodeId(doc_uri, ""), doc["title"], module_name))

        if "$defs" in doc:
            for name in doc["$defs"]:
                exports.append((NodeId(doc_uri, f"#/$defs/{name}"), name, module_name))

        # JSON Schema pre-Draft-07: 'definitions' was renamed to '$defs' in Draft-07+
        if "definitions" in doc:
            for name in doc["definitions"]:
                exports.append((NodeId(doc_uri, f"#/definitions/{name}"), name, module_name))

        return exports
