"""Website-only stage flattening and model-answer availability."""
import os
import re
import unittest
from collections import defaultdict
from unittest.mock import patch

from website import app as web
from website.test_seo import SeoCollection


class GradeNavigationTests(unittest.TestCase):
    def setUp(self):
        self.db = defaultdict(SeoCollection)
        for button in [
            {"id": 1, "parent_id": None, "type": "menu", "label": "المرحلة الابتدائية"},
            {"id": 2, "parent_id": 1, "type": "menu", "label": "السادس الابتدائي"},
            {"id": 3, "parent_id": None, "type": "menu", "label": "المرحلة المتوسطة"},
            {"id": 4, "parent_id": 3, "type": "menu", "label": "الثالث المتوسط"},
            {"id": 5, "parent_id": None, "type": "menu", "label": "السادس العلمي"},
            {"id": 16, "parent_id": 5, "type": "menu", "label": "أقسام أخرى"},
            {"id": 6, "parent_id": 16, "type": "menu", "label": "الأجوبة النموذجية"},
            {"id": 7, "parent_id": 6, "type": "menu", "label": "الكيمياء"},
            {"id": 8, "parent_id": 7, "type": "content", "label": "الدور الأول 2026"},
            {"id": 9, "parent_id": 4, "type": "menu", "label": "الملازم"},
            {"id": 10, "parent_id": 4, "type": "menu", "label": "أدوات أخرى"},
            {"id": 11, "parent_id": 1, "type": "menu", "label": "الأول الابتدائي", "hidden": 1},
            {"id": 12, "parent_id": 1, "type": "menu", "label": "الثاني الابتدائي", "deleted": 1},
            {"id": 13, "parent_id": None, "type": "menu", "label": "المرحلة الابتدائيه", "hidden": 1},
            {"id": 14, "parent_id": 13, "type": "menu", "label": "الثالث الابتدائي"},
        ]:
            self.db["buttons"].insert_one(button)
        self.db["content_items"].insert_one({
            "id": 1, "button_id": 8, "type": "file", "file_id": "answer-file",
            "caption": "حل الكيمياء الدور الأول",
        })
        for mocked in [
            patch.dict(os.environ, {"MONGODB_URI": "mongodb://unused.invalid/test"}),
            patch.object(web, "_col", side_effect=lambda name: self.db[name]),
            patch.object(web, "_search_index_cache", {"expires": 0, "records": None}),
        ]:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = web.create_app().test_client()

    def test_homepage_and_navigation_flatten_only_visible_grades(self):
        with patch.object(web, "_latest_notes", return_value=[]):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        cards = re.findall(r'<a href="/cat/(\d+)" class="cat-card">', html)
        self.assertEqual(cards, ["2", "4", "5"])
        self.assertNotIn("المرحلة الابتدائية", html)
        self.assertNotIn("المرحلة المتوسطة", html)
        self.assertEqual(self.db["buttons"].find_one({"id": 2})["parent_id"], 1)

    def test_new_grade_appears_on_next_request_without_a_restart(self):
        with patch.object(web, "_latest_notes", return_value=[]):
            self.client.get("/")
            self.db["buttons"].insert_one({
                "id": 15, "parent_id": 3, "type": "menu", "label": "الأول المتوسط",
            })
            html = self.client.get("/").get_data(as_text=True)
        self.assertIn('<a href="/cat/15" class="cat-card">', html)
        self.assertIn("الأول المتوسط", html)

    def test_nested_grade_uses_same_section_filter_as_root_grade(self):
        html = self.client.get("/cat/4").get_data(as_text=True)
        self.assertIn('href="/cat/9"', html)
        self.assertNotIn('href="/cat/10"', html)
        self.assertNotIn("المرحلة المتوسطة", html)

    def test_sixth_scientific_shows_real_answer_section(self):
        html = self.client.get("/cat/5").get_data(as_text=True)
        self.assertIn('href="/cat/6"', html)
        self.assertIn("الأجوبة النموذجية", html)
        html = self.client.get("/cat/6").get_data(as_text=True)
        self.assertIn('href="/cat/7"', html)
        response = self.client.get("/cat/7")
        self.assertEqual(response.status_code, 200)
        self.assertIn("الدور الأول", response.get_data(as_text=True))

    def test_answer_files_are_searchable_even_without_note_keyword(self):
        records = web._search_index_records()
        answer = next(record for record in records if record["button"]["id"] == 8)
        self.assertIn("الأجوبة النموذجية", answer["search_context"])

    def test_hidden_answers_remain_hidden(self):
        self.db["content_items"].update_one(
            {"id": 1}, {"$set": {"website_hidden": True}}
        )
        self.assertFalse(any(
            record["button"]["id"] == 8 for record in web._search_index_records()
        ))

    def test_hidden_answer_group_is_not_promoted_to_grade(self):
        self.db["buttons"].update_one({"id": 16}, {"$set": {"hidden": 1}})
        html = self.client.get("/cat/5").get_data(as_text=True)
        self.assertNotIn('href="/cat/6"', html)

    def test_attachment_fallback_still_resolves_files_under_stage_folders(self):
        self.db["buttons"].insert_one({
            "id": 18, "parent_id": 2, "type": "content", "label": "واجبات",
        })
        self.db["content_items"].insert_one({
            "id": 2, "button_id": 18, "type": "file", "file_id": "primary-file",
        })
        self.assertIsNotNone(web._find_visible_attachment("primary-file"))

    def test_model_answer_spelling_variants_are_supported(self):
        for label in ("الأجوبة النموذجية", "الاجوبه النموذجيه", "الـأجـوبـة النموذجية",
                      "الإجابات النموذجية"):
            with self.subTest(label=label):
                self.assertTrue(web._is_allowed_menu({"label": label}))
        self.assertFalse(web._is_allowed_menu({"label": "كروب الطلاب"}))

    def test_sitemap_lists_grades_but_not_bot_stage_wrappers(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        xml = response.get_data(as_text=True)
        self.assertNotIn("https://alameer-iq.com/cat/1<", xml)
        self.assertNotIn("https://alameer-iq.com/cat/3<", xml)
        self.assertIn("https://alameer-iq.com/cat/2<", xml)
        self.assertIn("https://alameer-iq.com/cat/6<", xml)


if __name__ == "__main__":
    unittest.main()
