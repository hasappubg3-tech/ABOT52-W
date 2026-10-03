"""Sitemap regressions with no network requests or shared database writes."""
import os
import unittest
from collections import defaultdict
from unittest.mock import patch
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from website import app as web
from website.test_feedback import Collection


NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class SitemapTests(unittest.TestCase):
    def setUp(self):
        self.db = defaultdict(Collection)
        buttons = [
            {"id": 1, "parent_id": None, "type": "menu", "label": "السادس العلمي"},
            {"id": 2, "parent_id": 1, "type": "menu", "label": "ملازم"},
            {"id": 3, "parent_id": 2, "type": "content", "label": "ملزمة الفيزياء"},
            {"id": 4, "parent_id": 2, "type": "content", "label": "ملخص الكيمياء"},
            {"id": 5, "parent_id": 1, "type": "menu", "label": "گــــروب الطلاب"},
            {"id": 6, "parent_id": 5, "type": "content", "label": "ملزمة مخفية"},
            {"id": 7, "parent_id": 1, "type": "menu", "label": "كتب", "hidden": 1},
            {"id": 8, "parent_id": 7, "type": "content", "label": "كتاب مخفي"},
            {"id": 9, "parent_id": 1, "type": "content", "label": "كتاب محذوف",
             "deleted": 1},
            {"id": 10, "parent_id": 999, "type": "content", "label": "ملزمة يتيمة"},
            {"id": 11, "parent_id": 2, "type": "content", "label": "كتاب مكرر"},
            {"id": 12, "parent_id": 1, "type": "content", "label": "كروب طلاب السادس"},
        ]
        for button in buttons:
            self.db["buttons"].insert_one(button)
        for item in [
            {"id": 1, "button_id": 3, "type": "file", "file_id": "first-file"},
            {"id": 2, "button_id": 3, "type": "document", "file_id": "second&file"},
            {"id": 3, "button_id": 11, "type": "file", "file_id": "first-file"},
            {"id": 4, "button_id": 6, "type": "file", "file_id": "hidden-file"},
        ]:
            self.db["content_items"].insert_one(item)
        for mocked in [
            patch.dict(os.environ, {"MONGODB_URI": "mongodb://unused.invalid/test",
                                    "SITE_URL": "http://wrong-host.invalid"}),
            patch.object(web, "_col", side_effect=lambda name: self.db[name]),
            patch.object(web, "_search_index_cache", {"expires": 0, "records": None}),
        ]:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = web.create_app().test_client()

    def parse(self):
        response = self.client.get("/sitemap.xml", base_url="http://preview.invalid")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/xml")
        self.assertIn("charset=utf-8", response.content_type)
        self.assertTrue(response.data.startswith(b"<?xml"))
        root = ET.fromstring(response.data)
        self.assertEqual(root.tag, "{%s}urlset" % NS["s"])
        return response, root

    def test_valid_xml_all_page_urls_use_the_fixed_https_domain(self):
        _, root = self.parse()
        nodes = root.findall("s:url", NS)
        self.assertGreater(len(nodes), 1)
        for node in nodes:
            location = node.find("s:loc", NS).text
            parsed = urlsplit(location)
            self.assertEqual(parsed.scheme, "https")
            self.assertEqual(parsed.netloc, "alameer-iq.com")
            self.assertTrue(parsed.path.startswith("/"))
            self.assertIn(node.find("s:changefreq", NS).text,
                          {"daily", "weekly", "monthly"})
            self.assertTrue(0 <= float(node.find("s:priority", NS).text) <= 1)

    def test_independent_pages_escaping_deduplication_and_hidden_entries(self):
        response, root = self.parse()
        locations = [node.text for node in root.findall("s:url/s:loc", NS)]
        self.assertEqual(len(locations), len(set(locations)))
        self.assertEqual(set(locations), {
            "https://alameer-iq.com/",
            "https://alameer-iq.com/cat/1",
            "https://alameer-iq.com/cat/2",
            "https://alameer-iq.com/attachment/first-file",
            "https://alameer-iq.com/attachment/second&file",
            "https://alameer-iq.com/note/4",
        })
        self.assertIn(b"second&amp;file", response.data)

    def test_robots_advertises_the_same_https_sitemap(self):
        response = self.client.get("/robots.txt", base_url="http://preview.invalid")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Sitemap: https://alameer-iq.com/sitemap.xml",
                      response.get_data(as_text=True))

    def test_empty_database_still_produces_valid_homepage_sitemap(self):
        self.db.clear()
        _, root = self.parse()
        self.assertEqual([node.text for node in root.findall("s:url/s:loc", NS)],
                         ["https://alameer-iq.com/"])


if __name__ == "__main__":
    unittest.main()