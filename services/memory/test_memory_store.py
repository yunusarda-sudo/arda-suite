"""Qdrant inventory pagination and isolation contract tests."""

import json
import unittest
from unittest.mock import patch

import memory_store


class _Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body


def _point(point_id, owner="owner-a", scope="project-a", status="active"):
    return {
        "id": point_id,
        "payload": {
            "user_id": owner,
            "run_id": scope,
            "agent_id": "agent-a",
            "type": "fact",
            "data": f"memory {point_id}",
            "status": status,
        },
    }


class TestReadInventory(unittest.TestCase):
    @patch("memory_store.urllib.request.urlopen")
    def test_reads_multiple_pages_and_follows_cursor(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _Response({"result": {"points": [_point("one")], "next_page_offset": "cursor-2"}}),
            _Response({"result": {"points": [_point("two")], "next_page_offset": None}}),
        ]

        rows = memory_store.read_inventory(owner="owner-a", scope="project-a")

        self.assertEqual([row["id"] for row in rows], ["one", "two"])
        self.assertEqual(mock_urlopen.call_count, 2)
        first = json.loads(mock_urlopen.call_args_list[0].args[0].data)
        second = json.loads(mock_urlopen.call_args_list[1].args[0].data)
        self.assertEqual(first["filter"]["must"], [
            {"key": "user_id", "match": {"value": "owner-a"}},
            {"key": "run_id", "match": {"value": "project-a"}},
        ])
        self.assertNotIn("offset", first)
        self.assertEqual(second["offset"], "cursor-2")

    @patch("memory_store.urllib.request.urlopen")
    def test_enforces_owner_and_scope_isolation_even_if_page_contains_other_rows(self, mock_urlopen):
        mock_urlopen.return_value = _Response({"result": {
            "points": [
                _point("owned", owner="owner-a", scope="project-a"),
                _point("other-owner", owner="owner-b", scope="project-a"),
                _point("other-scope", owner="owner-a", scope="project-b"),
            ],
            "next_page_offset": None,
        }})

        rows = memory_store.read_inventory(owner="owner-a", scope="project-a")

        self.assertEqual([row["id"] for row in rows], ["owned"])

    @patch("memory_store.urllib.request.urlopen")
    def test_rejects_malformed_page_and_point_shapes(self, mock_urlopen):
        malformed = [
            {"result": {"points": "not a list", "next_page_offset": None}},
            {"result": {"points": ["not a point"], "next_page_offset": None}},
            {"result": {"points": [{"id": "missing-payload"}], "next_page_offset": None}},
            {"result": {"points": [_point("missing-owner", owner=None)], "next_page_offset": None}},
            {"result": {"points": [{"id": "no-data", "payload": {"user_id": "owner-a"}}], "next_page_offset": None}},
            {"result": {"points": [{"id": "bad-metadata", "payload": {"user_id": "owner-a", "data": "text", "metadata": []}}], "next_page_offset": None}},
            {"result": {"points": [{"id": "", "payload": {"user_id": "owner-a", "data": "text"}}], "next_page_offset": None}},
            {"result": {"points": [{"id": "bad-time", "payload": {"user_id": "owner-a", "data": "text", "created_at": 123}}], "next_page_offset": None}},
            {"result": {"points": [{"id": "bad-status", "payload": {"user_id": "owner-a", "data": "text", "status": []}}], "next_page_offset": None}},
            {"result": {"points": [{
                "id": "malformed-metadata",
                "payload": {"user_id": "owner-a", "metadata": ["not", "an", "object"]},
            }], "next_page_offset": None}},
        ]
        for response in malformed:
            with self.subTest(response=response):
                mock_urlopen.return_value = _Response(response)
                with self.assertRaises(memory_store.MemoryReadError):
                    memory_store.read_inventory(owner="owner-a")

    @patch("memory_store.urllib.request.urlopen")
    def test_rejects_missing_cursor_field_instead_of_assuming_inventory_is_complete(self, mock_urlopen):
        mock_urlopen.return_value = _Response({"result": {"points": [_point("one")]}})

        with self.assertRaises(memory_store.MemoryReadError):
            memory_store.read_inventory(owner="owner-a")

    @patch("memory_store.urllib.request.urlopen")
    def test_rejects_repeated_cursor(self, mock_urlopen):
        page = {"result": {"points": [_point("one")], "next_page_offset": "same"}}
        mock_urlopen.side_effect = [_Response(page), _Response(page)]

        with self.assertRaises(memory_store.MemoryReadError):
            memory_store.read_inventory(owner="owner-a")

    @patch("memory_store.urllib.request.urlopen")
    def test_wraps_backend_failure(self, mock_urlopen):
        mock_urlopen.side_effect = OSError("Qdrant unavailable")

        with self.assertRaises(memory_store.MemoryReadError) as raised:
            memory_store.read_inventory(owner="owner-a")

        self.assertIn("Qdrant unavailable", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
