"""Temporary website-to-bot handoff status checks; no live Telegram requests."""
import os
import unittest
from unittest.mock import patch

from website import app as web


class HandoffCollection:
    def __init__(self):
        self.records = {}
        self.indexes = []

    def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))

    def insert_one(self, record):
        self.records[record["_id"]] = dict(record)

    def find_one(self, query, projection=None):
        return self.records.get(query["_id"])


class TelegramHandoffRouteTests(unittest.TestCase):
    def setUp(self):
        self.collection = HandoffCollection()
        self.button = {"id": 10, "type": "content", "label": "ملزمة الفيزياء"}
        self.items = [
            {"id": 31, "button_id": 10, "type": "document", "file_id": "file-id"}
        ]
        self.patches = [
            patch.dict(os.environ, {"MONGODB_URI": "mongodb://unused.invalid/botdb"}),
            patch.object(web, "_btn", side_effect=lambda bid: self.button if bid == 10 else None),
            patch.object(web, "_items", return_value=self.items),
            patch.object(
                web, "_col",
                side_effect=lambda name: self.collection
                if name == "telegram_download_handoffs"
                else AssertionError(f"Unexpected collection: {name}"),
            ),
        ]
        for mocked in self.patches:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = web.create_app().test_client()

    def test_create_and_poll_a_file_handoff(self):
        response = self.client.post(
            "/api/telegram-download-handoffs",
            json={"target": "10_31"},
        )
        self.assertEqual(response.status_code, 201)
        result = response.get_json()
        token = result["telegram_url"].split("start=handoff_", 1)[1]
        self.assertEqual(len(token), 16)
        self.assertIn(f"/api/telegram-download-handoffs/{token}", result["status_url"])
        self.assertEqual(self.collection.records[token]["button_id"], 10)
        self.assertEqual(self.collection.records[token]["item_id"], 31)
        self.assertEqual(self.collection.records[token]["status"], "pending")

        self.collection.records[token]["status"] = "delivered"
        status = self.client.get(result["status_url"])
        self.assertEqual(status.get_json(), {"status": "delivered"})
        self.assertEqual(status.headers["Cache-Control"], "no-store")

    def test_handoffs_reject_invalid_targets_and_unrelated_items(self):
        self.assertEqual(
            self.client.post("/api/telegram-download-handoffs", json={"target": "bad"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/telegram-download-handoffs",
                json={"target": "10_999"},
            ).status_code,
            404,
        )

    def test_expired_and_malformed_status_tokens_do_not_leak_records(self):
        response = self.client.post(
            "/api/telegram-download-handoffs",
            json={"target": "10_31"},
        )
        status_url = response.get_json()["status_url"]
        token = status_url.rsplit("/", 1)[-1]
        self.collection.records[token]["expires_at"] = web.datetime(2000, 1, 1)
        self.assertEqual(self.client.get(status_url).get_json(), {"status": "expired"})
        self.assertEqual(
            self.client.get("/api/telegram-download-handoffs/invalid").status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
