"""
Schema index for SLAS - handles URI resolution and reference tracking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urldefrag

import typer


def resolve_pointer(doc: dict | list, pointer: str) -> Any:
    """Resolve a JSON Pointer in a document."""
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


def resolve_uri(base: str, ref: str) -> Tuple[str, str]:
    """Returns (abs_uri_without_fragment, fragment) for ref resolved against base."""
    abs_ = urljoin(base, ref)
    nofrag, frag = urldefrag(abs_)
    return nofrag, ("#" + frag) if frag else ""


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
    """Index of all schemas, their nodes, and references."""
    
    docs: Dict[str, dict] = field(default_factory=dict)
    anchors: Dict[str, Any] = field(default_factory=dict)  # uri#anchor -> node
    pointers: Dict[str, Any] = field(default_factory=dict)  # uri#/pointer -> node
    subschema_bases: Dict[int, str] = field(default_factory=dict)  # id(node) -> base URI
    origin_files: Dict[str, Path] = field(default_factory=dict)  # doc_uri -> source file
    
    @classmethod
    def load(cls, paths: List[Path], base_url: Optional[str] = None) -> SchemaIndex:
        """Load schemas from paths and build the index."""
        index = cls()
        
        for path in sorted(paths):
            if path.suffix != ".json":
                continue
            
            try:
                with path.open("r", encoding="utf-8") as f:
                    doc = json.load(f)
            except json.JSONDecodeError:
                continue
            
            # Determine document URI
            if "$id" in doc:
                doc_uri = doc["$id"]
            elif base_url:
                # Use the provided base URL
                rel_path = path.name
                doc_uri = urljoin(base_url, rel_path)
            else:
                # Mint a stable URI
                doc_uri = f"lithify:///{path.stem}.json"
            
            # Store document
            index.docs[doc_uri] = doc
            index.origin_files[doc_uri] = path
            
            # Index the document tree
            index._index_tree(doc, doc_uri, doc_uri)
        
        return index
    
    def _index_tree(self, node: Any, doc_uri: str, base_uri: str, pointer: str = "") -> None:
        """Recursively index a schema tree."""
        if not isinstance(node, dict):
            return
        
        # Handle nested $id - it creates a new resolvable document
        if "$id" in node and pointer:
            # Nested $id resets the base
            nested_uri = urljoin(base_uri, node["$id"])
            # Store this as a new resolvable document
            self.docs[nested_uri] = node
            # Update base for this subtree
            base_uri = nested_uri
        
        self.subschema_bases[id(node)] = base_uri
        
        # Index by pointer
        if pointer:
            pointer_uri = doc_uri + "#" + pointer
            self.pointers[pointer_uri] = node
        
        # Index by anchor
        if "$anchor" in node:
            anchor_uri = doc_uri + "#" + node["$anchor"]
            self.anchors[anchor_uri] = node
        
        # Recurse into known schema locations
        if "$defs" in node:
            for name, subschema in node["$defs"].items():
                self._index_tree(subschema, doc_uri, base_uri, f"{pointer}/$defs/{name}")
        
        if "definitions" in node:
            for name, subschema in node["definitions"].items():
                self._index_tree(subschema, doc_uri, base_uri, f"{pointer}/definitions/{name}")
        
        if "properties" in node:
            for name, subschema in node["properties"].items():
                # Escape special characters in property names
                escaped = name.replace("~", "~0").replace("/", "~1")
                self._index_tree(subschema, doc_uri, base_uri, f"{pointer}/properties/{escaped}")
        
        # Handle arrays and other containers
        for key in ["items", "prefixItems", "contains", "additionalProperties", 
                    "if", "then", "else", "not"]:
            if key in node:
                self._index_tree(node[key], doc_uri, base_uri, f"{pointer}/{key}")
        
        for key in ["allOf", "anyOf", "oneOf"]:
            if key in node and isinstance(node[key], list):
                for i, subschema in enumerate(node[key]):
                    self._index_tree(subschema, doc_uri, base_uri, f"{pointer}/{key}/{i}")
    
    def node_for(self, uri: str) -> Optional[dict]:
        """Resolve a URI to a schema node."""
        doc_uri, fragment = urldefrag(uri)
        
        # Get the document
        if doc_uri not in self.docs:
            return None
        doc = self.docs[doc_uri]
        
        if not fragment:
            return doc
        
        # Try anchor first
        anchor_uri = doc_uri + "#" + fragment
        if anchor_uri in self.anchors:
            return self.anchors[anchor_uri]
        
        # Try pointer
        if fragment.startswith("/"):
            pointer_uri = doc_uri + "#" + fragment
            if pointer_uri in self.pointers:
                return self.pointers[pointer_uri]
            
            # Try direct resolution
            try:
                return resolve_pointer(doc, fragment)
            except (KeyError, IndexError, TypeError):
                return None
        
        return None
    
    def refs_from(self, node_id: NodeId) -> List[str]:
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
                    # Resolve relative to base
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
    
    def exportables(self, doc_uri: str) -> List[Tuple[NodeId, str, str]]:
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
        
        # Top-level schema (if it has a title)
        if "title" in doc:
            exports.append((
                NodeId(doc_uri, ""),
                doc["title"],
                module_name
            ))
        
        # $defs entries
        if "$defs" in doc:
            for name in doc["$defs"]:
                exports.append((
                    NodeId(doc_uri, f"#/$defs/{name}"),
                    name,
                    module_name
                ))
        
        # Legacy definitions
        if "definitions" in doc:
            for name in doc["definitions"]:
                exports.append((
                    NodeId(doc_uri, f"#/definitions/{name}"),
                    name,
                    module_name
                ))
        
        return exports
