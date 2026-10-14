"""Strict helpers for reading memory inventories without hiding failures."""

from collections.abc import Mapping
import json
import os
import urllib.request


QDRANT_SCROLL = "http://{host}:{port}/collections/mem0_local/points/scroll".format(
    host=os.environ.get("MEM0_QDRANT_HOST", "localhost"),
    port=os.environ.get("MEM0_QDRANT_PORT", "6333"),
)
SCROLL_PAGE_SIZE = 10_000
SCROLL_MAX_PAGES = 1_000


class MemoryReadError(RuntimeError):
    """The memory store could not provide a trustworthy complete inventory."""


def normalize_get_all_response(response):
    """Normalize supported Mem0 ``get_all`` shapes and reject ambiguous data."""
    if isinstance(response, Mapping):
        if "results" not in response:
            raise MemoryReadError("get_all returned an object without a results list")
        rows = response["results"]
    elif isinstance(response, list):
        rows = response
    else:
        raise MemoryReadError("get_all returned an unsupported response shape")

    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise MemoryReadError("get_all results must be a list of memory objects")
    return [dict(row) for row in rows]


def read_inventory(owner, scope=None, agent=None, memory_type=None,
                   include_superseded=True, timeout=10):
    """Read a complete, owner-scoped inventory by following Qdrant cursors.

    Mem0's ``get_all`` can silently cap results without a continuation token,
    so it is not safe for inventory operations that may supersede or update
    records. Any malformed response, missing owner field, transport failure,
    repeated cursor, or page-limit overflow aborts the whole read.
    """
    if not owner:
        raise MemoryReadError("an owner is required to read a memory inventory")

    must = [{"key": "user_id", "match": {"value": owner}}]
    if scope:
        must.append({"key": "run_id", "match": {"value": scope}})
    if agent:
        must.append({"key": "agent_id", "match": {"value": agent}})
    if memory_type:
        must.append({"key": "type", "match": {"value": memory_type}})

    rows = []
    offset = None
    seen_offsets = set()
    for _ in range(SCROLL_MAX_PAGES):
        body = {"limit": SCROLL_PAGE_SIZE, "with_payload": True,
                "filter": {"must": must}}
        if offset is not None:
            body["offset"] = offset
        request = urllib.request.Request(
            QDRANT_SCROLL,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=timeout).read()
            result = json.loads(response)["result"]
            points = result["points"]
            next_offset = result["next_page_offset"]
        except Exception as exc:
            raise MemoryReadError(f"unable to read complete memory inventory: {exc}") from exc

        if not isinstance(points, list):
            raise MemoryReadError("Qdrant returned a non-list points collection")
        for point in points:
            if not isinstance(point, Mapping) or "id" not in point:
                raise MemoryReadError("Qdrant returned a malformed memory point")
            point_id = point["id"]
            if isinstance(point_id, bool) or not isinstance(point_id, (str, int)) or not str(point_id):
                raise MemoryReadError("Qdrant returned a memory point with an invalid id")
            payload = point.get("payload")
            if not isinstance(payload, Mapping):
                raise MemoryReadError("Qdrant returned a memory point without payload")
            metadata = payload.get("metadata", {})
            if metadata is None:
                metadata = {}
            if not isinstance(metadata, Mapping):
                raise MemoryReadError("Qdrant returned malformed memory metadata")
            memory = payload.get("data")
            if not isinstance(memory, str):
                raise MemoryReadError("Qdrant returned a memory point without string data")
            row_owner = payload.get("user_id") or metadata.get("user_id")
            if not isinstance(row_owner, str) or not row_owner:
                raise MemoryReadError("Qdrant returned a memory point without owner metadata")
            if row_owner != owner:
                continue
            if scope and payload.get("run_id") != scope:
                continue
            if agent and payload.get("agent_id") != agent:
                continue
            if memory_type and payload.get("type") != memory_type:
                continue
            raw_status = payload.get("status")
            if raw_status is None:
                raw_status = metadata.get("status")
            if raw_status is not None and not isinstance(raw_status, str):
                raise MemoryReadError("Qdrant returned a memory point with invalid status")
            status = raw_status or "active"
            created_at = payload.get("created_at", "")
            if created_at is None:
                created_at = ""
            if not isinstance(created_at, str):
                raise MemoryReadError("Qdrant returned a memory point with invalid created_at")
            if not include_superseded and status == "superseded":
                continue
            rows.append({
                "id": str(point_id),
                "memory": memory,
                "user_id": row_owner,
                "run_id": payload.get("run_id"),
                "agent_id": payload.get("agent_id"),
                "status": status,
                "created_at": created_at,
                "metadata": dict(metadata),
            })

        if next_offset is None:
            return rows
        if isinstance(next_offset, bool) or not isinstance(next_offset, (str, int)) or not str(next_offset):
            raise MemoryReadError("Qdrant returned an invalid scroll cursor")
        try:
            cursor_key = json.dumps(next_offset, sort_keys=True)
        except TypeError as exc:
            raise MemoryReadError("Qdrant returned an invalid scroll cursor") from exc
        if cursor_key in seen_offsets:
            raise MemoryReadError("Qdrant repeated a scroll cursor; refusing an incomplete inventory")
        seen_offsets.add(cursor_key)
        offset = next_offset

    raise MemoryReadError(f"memory inventory exceeded the {SCROLL_MAX_PAGES}-page safety limit")
