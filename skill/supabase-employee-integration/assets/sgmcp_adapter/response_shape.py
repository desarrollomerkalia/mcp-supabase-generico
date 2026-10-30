"""Bounded structural diagnostics: never inspect or return individual row values.

Used only for admin-facing debugging (see routes.diagnostic_authorizer in
integration wiring) - it tells an administrator the *shape* of what sgmcp
returned without ever leaking a cell value.
"""

from __future__ import annotations

import json


_CONTAINERS = {
    "structuredContent", "data", "result", "results", "rows", "records",
    "registros", "datos", "items", "pagination", "paginacion", "meta", "metadata",
}
_COUNTERS = {
    "total", "total_count", "count", "totalRecords", "total_registros",
    "total_filtrado", "returned_count", "returned", "total_returned",
    "limit", "limite", "page", "pagina", "offset", "size", "page_size",
}
_FIELDS = _CONTAINERS | _COUNTERS | {
    "content", "text", "type", "jsonrpc", "isError", "success", "ok", "error",
    "has_more", "hasMore", "next_page", "nextPage", "status", "message",
}


def _kind(value):
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "other"


def summarize_response(value, *, table=None, record_hint_fields=frozenset()):
    """``record_hint_fields`` should be the union of all columns declared in
    the employee's ConnectionConfig - it is the generic replacement for a
    hardcoded, business-specific list of "this dict is probably a row"
    field names."""
    nodes = 0

    def describe(item, depth=0):
        nonlocal nodes
        nodes += 1
        result = {"kind": _kind(item)}
        if depth > 4 or nodes > 24:
            return {**result, "truncated": True}
        if isinstance(item, list):
            # Array entries may be records: types and cardinality only.
            return {**result, "length": len(item), "sampled_item_types": sorted({_kind(row) for row in item[:3]})}
        if not isinstance(item, dict):
            return result
        if ("id" in item and "jsonrpc" not in item) or record_hint_fields.intersection(item):
            return {**result, "record_values_omitted": True}
        keys = sorted(key for key in item if isinstance(key, str) and key in _FIELDS)
        result["fields"] = {key: _kind(item[key]) for key in keys}
        result["other_field_count"] = len(item) - len(keys)
        children = {}
        for key in keys:
            child = item[key]
            if key in _CONTAINERS and isinstance(child, (dict, list)):
                children[key] = describe(child, depth + 1)
        content = item.get("content")
        if isinstance(content, list):
            blocks = []
            for block in content[:3]:
                node = {"kind": _kind(block)}
                if isinstance(block, dict):
                    block_type = block.get("type")
                    node["block_type"] = block_type if isinstance(block_type, str) and block_type in {"text", "image", "audio", "resource", "resource_link"} else "other"
                    text = block.get("text")
                    if block_type == "text" and isinstance(text, str):
                        if len(text) > 2 * 1024 * 1024:
                            node["json_state"] = "too_large"
                        else:
                            try:
                                decoded = json.loads(text)
                            except (ValueError, RecursionError):
                                node["json_state"] = "invalid"
                            else:
                                node["json_state"] = "decoded"
                                node["decoded_shape"] = describe(decoded, depth + 1)
                blocks.append(node)
            result["content"] = {"block_count": len(content), "blocks": blocks, "truncated": len(content) > 3}
        if children:
            result["children"] = children
        return result

    report = {"schema": "sgmcp_read_shape_v1", "origin": "data_decoder", "totals_verified": False, "shape": describe(value)}
    if isinstance(table, str):
        report["table"] = table
    return report
