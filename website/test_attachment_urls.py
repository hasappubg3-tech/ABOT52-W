"""Arabic permalink and legacy redirect regressions without external requests."""
import os
import unittest
from collections import defaultdict
from unittest.mock import patch
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from website import app as web
from website.attachment_urls import arabic_slug, allocate_slug
from website.test_feedback import Collection


class ArabicSlugTests(unittest.TestCase):
    def test_requested_arabic_format_preserves_hamza_and_teacher(self):
        self.assertEqual(
            arabic_slug("ملزمة الكيمياء للأستاذ حسين الهاشمي"),
            "ملزمة-الكيمياء-الأستاذ-حسين-الهاشمي",
        )

    def test_emoji_punctuation_and_diacritics_are_removed(self):
        self.assertEqual(arabic_slug("📌 مُلَزَّمة  الكيمياء / الأستاذ حسين 📌"),
                         "ملزمة-الكيمياء-الأستاذ-حسين")

    def test_collisions_and_repeated_file_ids(self):
        files, slugs = {}, {}
        first = allocate_slug("first", "ملزمة الكيمياء", files, slugs)
        second = allocate_slug("second", "ملزمة الكيمياء", files, slugs)
        self.assertNotEqual(first, second)
        self.assertTrue(second.startswith(first + "-"))
        self.assertEqual(allocate_slug("first", "عنوان في نسخة أخرى", files, slugs), first)
        self.assertEqual(slugs[second], "second")


class AttachmentPermalinkTests(unittest.TestCase):
    def setUp(self):
        self.db = defaultdict(Collection)
        for button in [
            {"id": 1, "type": "menu", "label": "السادس العلمي", "parent_id": None},
            {"id": 2, "type": "compound", "label": "حسين الهاشمي", "parent_id": 1},
            {"id": 3, "type": "content", "label": "ملزمة الكيمياء", "parent_id": 2},
        ]:
            self.db["buttons"].insert_one(button)
        for item in [
            {"id": 12, "button_id": 3, "type": "file", "file_id": "old-file-id",
             "caption": "ملزمة الكيمياء\nللأستاذ حسين الهاشمي"},
            {"id": 13, "button_id": 3, "type": "file", "file_id": "different-file-id",
             "caption": "ملزمة الكيمياء\nللأستاذ حسين الهاشمي"},
        ]:
            self.db["content_items"].insert_one(item)
        for mocked in [
            patch.dict(os.environ, {"MONGODB_URI": "mongodb://unused.invalid/test"}),
            patch.object(web, "_col", side_effect=lambda name: self.db[name]),
            patch.object(web, "_search_index_cache", {"expires": 0, "records": None}),
        ]:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = web.create_app().test_client()
        self.expected = "/attachment/ملزمة-الكيمياء-الأستاذ-حسين-الهاشمي"

    def test_old_file_id_redirects_once_to_requested_slug_and_slug_returns_200(self):
        response = self.client.get("/attachment/old-file-id")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(unquote(response.location), self.expected)
        page = self.client.get(self.expected)
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn(f'rel="canonical" href="https://alameer-iq.com{self.expected}"', html)
        self.assertIn(f'property="og:url" content="https://alameer-iq.com{self.expected}"', html)
        self.assertIn(web._bot_download_url(3, 12), html)

    def test_sitemap_lists_the_same_canonical_urls_and_no_file_ids(self):
        response = self.client.get("/sitemap.xml")
        root = ET.fromstring(response.data)
        locations = [node.text for node in root.findall(
            "{http://www.sitemaps.org/schemas/sitemap/0.9}url/"
            "{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        self.assertIn("https://alameer-iq.com" + self.expected, locations)
        self.assertIn("https://alameer-iq.com" + web._attachment_url("different-file-id"),
                      locations)
        self.assertEqual(len(locations), len(set(locations)))
        self.assertNotIn(b"old-file-id", response.data)
        self.assertNotIn(b"different-file-id", response.data)

    def test_duplicate_titles_resolve_to_the_correct_independent_files(self):
        first = self.client.get("/attachment/old-file-id")
        second = self.client.get("/attachment/different-file-id")
        self.assertNotEqual(first.location, second.location)
        page = self.client.get(second.location).get_data(as_text=True)
        self.assertIn(web._bot_download_url(3, 13), page)

    def test_hiding_collision_owner_does_not_reassign_its_url(self):
        first = web._attachment_url("old-file-id")
        second = web._attachment_url("different-file-id")
        self.db["content_items"].update_one({"id": 12}, {"$set": {"website_hidden": True}})
        web._search_index_cache["expires"] = 0
        self.assertEqual(web._attachment_url("different-file-id"), second)
        self.assertEqual(self.client.get(first).status_code, 404)
        self.assertEqual(self.client.get("/attachment/old-file-id").status_code, 404)
        self.assertEqual(self.client.get(second).status_code, 200)
        sitemap = ET.fromstring(self.client.get("/sitemap.xml").data)
        locations = [node.text for node in sitemap.iter(
            "{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        self.assertNotIn("https://alameer-iq.com" + first, locations)

    def test_teacher_is_taken_from_parent_when_old_file_has_no_teacher_caption(self):
        for item_id in (12, 13):
            self.db["content_items"].update_one(
                {"id": item_id}, {"$set": {"caption": "ملزمة الكيمياء"}})
        self.assertEqual(web._attachment_url("old-file-id"), self.expected)

    def test_new_files_appear_automatically_after_content_cache_refresh(self):
        self.client.get("/sitemap.xml")
        self.db["content_items"].insert_one({
            "id": 14, "button_id": 3, "type": "file", "file_id": "future-file",
            "caption": "ملزمة الكيمياء الجزء الثاني\nللأستاذ حسين الهاشمي",
        })
        web._search_index_cache["expires"] = 0
        response = self.client.get("/sitemap.xml")
        self.assertIn(web._attachment_url("future-file").encode(), response.data)
        self.assertEqual(self.client.get(web._attachment_url("future-file")).status_code, 200)


if __name__ == "__main__":
    unittest.main()
