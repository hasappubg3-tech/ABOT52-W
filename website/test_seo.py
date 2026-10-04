"""Automatic metadata regressions, without shared database writes."""
import json
import os
import re
import unittest
from collections import defaultdict
from html import unescape
from unittest.mock import patch

from website import app as web
from website.test_feedback import Collection


class SeoCollection(Collection):
    def find_one(self, query, projection=None):
        return next(iter(self.find(query, projection)), None)


class SeoTests(unittest.TestCase):
    def setUp(self):
        self.db = defaultdict(SeoCollection)
        for button in [
            {"id": 1, "parent_id": None, "type": "menu", "label": "السادس العلمي"},
            {"id": 2, "parent_id": 1, "type": "menu", "label": "ملازم"},
            {"id": 3, "parent_id": 2, "type": "menu", "label": "الكيمياء"},
            {"id": 4, "parent_id": 3, "type": "content", "label": "ملزمة"},
            {"id": 5, "parent_id": None, "type": "menu", "label": "الثالث المتوسط"},
            {"id": 6, "parent_id": 5, "type": "menu", "label": "ملازم"},
            {"id": 7, "parent_id": 3, "type": "content", "label": "ملخص الكيمياء"},
        ]:
            self.db["buttons"].insert_one(button)
        self.db["content_items"].insert_one({
            "id": 1, "button_id": 4, "type": "file", "file_id": "chemistry",
            "caption": "ملزمة الكيمياء الفصل الأول\nللاستاذ أحمد سالم\nسنة الإصدار 2026",
        })
        for mocked in [
            patch.dict(os.environ, {"MONGODB_URI": "mongodb://unused.invalid/test"}),
            patch.object(web, "_col", side_effect=lambda name: self.db[name]),
            patch.object(web, "_search_index_cache", {"expires": 0, "records": None}),
        ]:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = web.create_app().test_client()

    def page(self, path):
        response = self.client.get(path, base_url="http://preview.invalid")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def metadata(self, html, name):
        match = re.search(rf'<meta name="{name}" content="([^"]*)"', html)
        return unescape(match.group(1)) if match else None

    def test_category_titles_include_subject_and_grade_without_changing_heading(self):
        html = self.page("/cat/3")
        title = re.search(r"<title>(.*?)</title>", html).group(1)
        self.assertIn("الكيمياء", title)
        self.assertIn("السادس العلمي", title)
        self.assertIn('<h1 class="page-title">الكيمياء</h1>', html)
        first = re.search(r"<title>(.*?)</title>", self.page("/cat/2")).group(1)
        second = re.search(r"<title>(.*?)</title>", self.page("/cat/6")).group(1)
        self.assertNotEqual(first, second)

    def test_descriptions_are_file_specific_and_only_in_the_head(self):
        html = self.page("/attachment/chemistry")
        description = self.metadata(html, "description")
        for term in ["الكيمياء", "أحمد سالم", "2026", "الفصل الأول", "السادس العلمي"]:
            self.assertIn(term, description)
        self.assertNotIn(description, html.split("<body>", 1)[1])
        self.assertNotIn('style="display:none"', html)

    def test_fixed_https_canonicals_ignore_proxy_host_and_query_parameters(self):
        for path in ["/", "/cat/3", "/attachment/chemistry", "/note/7"]:
            html = self.page(path + "?utm_source=test")
            expected = "https://alameer-iq.com" + path
            self.assertIn(f'<link rel="canonical" href="{expected}">', html)
            self.assertIn(f'<meta property="og:url" content="{expected}">', html)
            self.assertNotIn("preview.invalid", html)
            self.assertIsNone(self.metadata(html, "robots"))

    def test_search_is_noindex_but_still_accessible_and_not_blocked_by_robots(self):
        for path in ["/search", "/search?q=كيمياء"]:
            html = self.page(path)
            self.assertEqual(self.metadata(html, "robots"), "noindex,follow")
            self.assertNotIn('rel="canonical"', html)
        robots = self.client.get("/robots.txt").get_data(as_text=True)
        self.assertNotIn("Disallow: /search", robots)
        self.assertNotIn("/search", self.page("/sitemap.xml"))

    def test_breadcrumb_json_matches_existing_navigation_and_canonical_urls(self):
        html = self.page("/cat/3")
        raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', html).group(1)
        data = json.loads(raw)
        self.assertEqual(data["@type"], "BreadcrumbList")
        self.assertEqual([x["name"] for x in data["itemListElement"]],
                         ["الرئيسية", "السادس العلمي", "ملازم", "الكيمياء"])
        self.assertEqual(data["itemListElement"][-1]["item"],
                         "https://alameer-iq.com/cat/3")
        html = self.page("/attachment/chemistry")
        data = json.loads(re.search(
            r'<script type="application/ld\+json">(.*?)</script>', html
        ).group(1))
        self.assertEqual(len(data["itemListElement"]), 2)
        self.assertEqual(data["itemListElement"][-1]["item"],
                         "https://alameer-iq.com/attachment/chemistry")

    def test_future_materials_get_metadata_and_sitemap_entries_automatically(self):
        self.page("/sitemap.xml")
        self.db["content_items"].insert_one({
            "id": 2, "button_id": 4, "type": "file", "file_id": "new-file",
            "caption": "ملزمة الكيمياء الجزء الثاني\nللاستاذ حسين علي\nسنة الإصدار 2027",
        })
        # Simulate the normal expiry of the read-only content cache.
        web._search_index_cache["expires"] = 0
        html = self.page("/attachment/new-file")
        description = self.metadata(html, "description")
        for term in ["حسين علي", "2027", "الجزء الثاني", "السادس العلمي"]:
            self.assertIn(term, description)
        self.assertNotIn("أحمد سالم", description)
        self.assertNotIn("2026", description)
        self.assertIn("https://alameer-iq.com/attachment/new-file",
                      self.page("/sitemap.xml"))


if __name__ == "__main__":
    unittest.main()