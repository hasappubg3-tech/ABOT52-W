"""Pagination and server-rendered cards for the homepage's latest additions."""
import os
import unittest
from collections import defaultdict
from unittest.mock import patch

from website import app as web
from website.test_seo import SeoCollection


class LatestPaginationTests(unittest.TestCase):
    def setUp(self):
        self.db = defaultdict(SeoCollection)
        self.db["buttons"].insert_one({
            "id": 1, "parent_id": None, "type": "menu", "label": "السادس العلمي",
        })
        self.db["buttons"].insert_one({
            "id": 2, "parent_id": 1, "type": "menu", "label": "الملازم",
        })
        for index in range(18):
            button_id = index + 3
            self.db["buttons"].insert_one({
                "id": button_id, "parent_id": 2, "type": "content",
                "label": f"ملزمة {index:02d}", "created_at": 1000 + index,
            })
            self.db["content_items"].insert_one({
                "id": index + 1, "button_id": button_id, "type": "file",
                "file_id": f"latest-{index:02d}", "caption": "ملزمة الكيمياء",
                "created_at": 1000 + index,
            })
        for mocked in [
            patch.dict(os.environ, {"MONGODB_URI": "mongodb://unused.invalid/test"}),
            patch.object(web, "_col", side_effect=lambda name: self.db[name]),
            patch.object(web, "_search_index_cache", {"expires": 0, "records": None}),
            patch.object(web, "_latest_notes_cache", {
                "expires": 0, "revision": None, "source_stamp": None, "items": None,
            }),
        ]:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = web.create_app().test_client()

    def test_homepage_shows_first_eight_and_load_more_controls(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertEqual(page.count('class="note-card"'), 8)
        self.assertIn('id="latest-load-more"', page)
        self.assertIn("عرض المزيد", page)
        self.assertIn('data-next-offset="8"', page)

    def test_api_returns_ordered_non_overlapping_pages_and_terminal_page(self):
        first = self.client.get("/api/latest-additions?offset=0").get_json()
        second = self.client.get("/api/latest-additions?offset=8").get_json()
        third = self.client.get("/api/latest-additions?offset=16").get_json()

        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 8)
        self.assertEqual(first["html"].count('class="note-card"'), 8)
        self.assertIn("ملزمة 17", first["html"])
        self.assertIn("ملزمة 10", first["html"])

        self.assertTrue(second["has_more"])
        self.assertEqual(second["next_offset"], 16)
        self.assertIn("ملزمة 09", second["html"])
        self.assertIn("ملزمة 02", second["html"])
        self.assertNotIn("ملزمة 17", second["html"])

        self.assertFalse(third["has_more"])
        self.assertEqual(third["next_offset"], 18)
        self.assertEqual(third["html"].count('class="note-card"'), 2)
        self.assertIn("ملزمة 01", third["html"])
        self.assertIn("ملزمة 00", third["html"])

    def test_invalid_offsets_are_rejected(self):
        for value in ("bad", "-1", "10001"):
            with self.subTest(offset=value):
                response = self.client.get(f"/api/latest-additions?offset={value}")
                self.assertEqual(response.status_code, 400)

    def test_later_pages_reuse_the_sorted_snapshot(self):
        first = web._latest_notes(8)
        self.assertEqual(len(first), 8)
        with patch.object(web, "_build_latest_notes", side_effect=AssertionError(
            "the cached latest list should be reused"
        )):
            second = web._latest_notes(8, 8)
        self.assertEqual(len(second), 8)
        self.assertEqual(first[0]["file_id"], "latest-17")
        self.assertEqual(second[0]["file_id"], "latest-09")

    def test_new_upload_or_visibility_revision_refreshes_the_snapshot(self):
        web._latest_notes(8)
        refreshed = [{"id": 99, "display_label": "إضافة أحدث"}]
        with (
            patch.object(web, "_website_visibility_revision", return_value=1),
            patch.object(web, "_latest_source_stamp", return_value=("new-item", "new-button")),
            patch.object(web, "_build_latest_notes", return_value=refreshed) as rebuild,
        ):
            self.assertEqual(web._latest_notes(8), refreshed)
        rebuild.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
