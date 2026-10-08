"""Real search-index and both endpoint regressions, without database writes."""
import os
import unittest
from collections import defaultdict
from unittest.mock import patch

from website import app as web
from website import search
from website.test_feedback import Collection


class FlexibleSearchTests(unittest.TestCase):
    def setUp(self):
        self.db = defaultdict(Collection)
        for button in [
            {"id": 1, "parent_id": None, "type": "menu", "label": "السادس العلمي"},
            {"id": 2, "parent_id": 1, "type": "content", "label": "ملزمة الفيزياء 2026"},
            {"id": 3, "parent_id": 1, "type": "content", "label": "ملزمة الكيمياء"},
            {"id": 4, "parent_id": 1, "type": "content", "label": "ملزمة حسين ناصر"},
            {"id": 5, "parent_id": 1, "type": "content", "label": "ملخص الرياضيات علي سالم"},
            {"id": 6, "parent_id": 1, "type": "content", "label": "كتاب عبد الله"},
            {"id": 7, "parent_id": 1, "type": "content", "label": "ملزمة محجوبة", "hidden": 1},
            {"id": 8, "parent_id": 1, "type": "menu", "label": "كروب الطلاب"},
            {"id": 9, "parent_id": 8, "type": "content", "label": "ملزمة حسين الهاشمي"},
            {"id": 10, "parent_id": 1, "type": "content", "label": "ملزمة كلاميات"},
            {"id": 11, "parent_id": 1, "type": "content",
             "label": "ملزمة اختبار الإخفاء على الموقع"},
        ]:
            self.db["buttons"].insert_one(button)
        for item in [
            {"id": 1, "button_id": 2, "type": "file", "file_id": "physics",
             "caption": "ملزمة الفيزياء 2026\nللاستاذ حسين الهاشمي"},
            {"id": 2, "button_id": 2, "type": "file", "file_id": "other-teacher",
             "caption": "ملزمة الفيزياء 2025\nللاستاذ احمد محمود"},
            {"id": 3, "button_id": 3, "type": "file", "file_id": "chemistry",
             "caption": "ملزمة الكيمياء للأستاذ حسن الهاشمي"},
            {"id": 4, "button_id": 7, "type": "file", "file_id": "hidden",
             "caption": "ملزمة حسين الهاشمي"},
            {"id": 5, "button_id": 9, "type": "file", "file_id": "bot-only"},
            {"id": 7, "button_id": 10, "type": "file", "file_id": "incidental-word",
             "caption": "ملزمة كلاميات الكيمياء للاستاذ احمد"},
            {"id": 8, "button_id": 11, "type": "file",
             "file_id": "website-hidden", "website_hidden": True,
             "caption": "ملزمة الفيزياء للأستاذ جاسم الزبيدي"},
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

    def results(self, query):
        response = self.client.get("/api/search", query_string={"q": query})
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_taa_marbuta_and_plural_variants(self):
        expected = {r["url"] for r in self.results("ملزمة")}
        self.assertTrue(expected)
        self.assertEqual(expected, {r["url"] for r in self.results("ملزمه")})
        self.assertEqual(expected, {r["url"] for r in self.results("ملازم")})

    def test_extra_words_do_not_prevent_teacher_match(self):
        for query in [
            "حسين الهاشمي",
            "اريد ملزمه للاستاذ حسين الهاشمي ممكن تحميل pdf",
            "حسين الهاشمي كلام اضافي غير موجود",
            "الهاشمي حسين",
            "اريد ملزمه حسين الهاشمي مع كلام اضافي",
        ]:
            with self.subTest(query=query):
                self.assertEqual(self.results(query)[0]["url"], "/attachment/physics")

    def test_typos_and_transposed_letters(self):
        for query in ["حسني الهاشمي", "حسين الهاشمى", "حسين الهاشم", "حسين الهاشممي"]:
            with self.subTest(query=query):
                self.assertEqual(self.results(query)[0]["url"], "/attachment/physics")

    def test_normalization_diacritics_kashida_digits_and_articles(self):
        self.assertEqual(
            self.results("مُلَزَّمَه الـفِيزياء ٢٠٢٦")[0]["url"],
            "/attachment/physics",
        )
        self.assertTrue(self.results("فيزياء"))
        self.assertTrue(self.results("حس"))
        self.assertTrue(self.results("كتاب عبدالله"))
        self.assertEqual(self.results("علي")[0]["url"], "/note/5")

    def test_full_match_ranks_before_partial_and_fuzzy_names(self):
        results = self.results("حسين الهاشمي")
        self.assertEqual(results[0]["url"], "/attachment/physics")
        self.assertIn("/attachment/chemistry", [r["url"] for r in results])
        self.assertIn("/note/4", [r["url"] for r in results])

    def test_exact_single_name_precedes_fuzzy_name(self):
        urls = [r["url"] for r in self.results("حسين")]
        self.assertLess(urls.index("/attachment/physics"), urls.index("/attachment/chemistry"))

    def test_no_cross_sibling_teacher_matches_or_note_fallback(self):
        urls = [r["url"] for r in self.results("حسين الهاشمي")]
        self.assertNotIn("/attachment/other-teacher", urls)
        self.assertNotIn("/note/2", urls)

    def test_hidden_and_bot_only_content_stays_hidden(self):
        urls = [r["url"] for r in self.results("حسين الهاشمي")]
        self.assertNotIn("/attachment/hidden", urls)
        self.assertNotIn("/attachment/bot-only", urls)

    def test_website_hidden_item_is_absent_from_all_public_entry_points(self):
        self.assertEqual(self.results("جاسم الزبيدي"), [])
        self.assertEqual(web._independent_notes({
            "id": 11, "type": "content", "label": "ملزمة اختبار الإخفاء",
        }), [])
        with patch.object(web, "_btn", return_value={
            "id": 11, "type": "content", "label": "ملزمة اختبار الإخفاء",
        }):
            self.assertEqual(self.client.get("/note/11").status_code, 404)
            self.assertEqual(self.client.get("/cat/11").status_code, 404)
        self.assertEqual(
            self.client.get("/attachment/website-hidden").status_code, 404
        )

    def test_website_visibility_revision_invalidates_the_cached_search_index(self):
        first = web._search_index_records()
        self.db["website_index_state"].insert_one({
            "_id": "content_visibility", "revision": 1,
        })
        second = web._search_index_records()
        self.assertIsNot(first, second)

    def test_ancestor_grade_is_searchable(self):
        self.assertIn("/attachment/physics", [
            r["url"] for r in self.results("فيزياء السادس العلمي")
        ])

    def test_unknown_queries_do_not_return_every_generic_note(self):
        for query in ["", "!!!", "اريد ممكن عن تحميل", "زززززززز", "ملزمة زززززززز"]:
            with self.subTest(query=query):
                self.assertEqual(self.results(query), [])

    def test_years_do_not_fuzzy_match_or_leak_from_siblings(self):
        self.assertEqual([r["url"] for r in self.results("2025")], ["/attachment/other-teacher"])
        self.assertEqual([r["url"] for r in self.results("2026")], ["/attachment/physics"])
        self.assertEqual(self.results("2099"), [])

    def test_full_results_page_uses_the_same_flexible_search(self):
        response = self.client.get("/search", query_string={
            "q": "اريد ملزمه حسين الهاشمي كلام اضافي",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/attachment/physics"', response.get_data(as_text=True))

    def test_deduplication_limits_and_cached_index(self):
        self.db["content_items"].insert_one({
            "id": 6, "button_id": 3, "type": "file", "file_id": "physics",
            "caption": "ملزمة الفيزياء حسين الهاشمي",
        })
        self.assertEqual([r["url"] for r in self.results("حسين الهاشمي")].count(
            "/attachment/physics"
        ), 1)
        self.assertEqual(len(web._search_content("ملزمة", limit=2)), 2)
        first = web._search_index_records()
        self.assertIs(first, web._search_index_records())
        self.assertTrue(all("search_documents" in record for record in first))

    def test_typo_distance_is_bounded(self):
        self.assertEqual(search._similarity("فيزياء", "كيمياء"), 0)
        self.assertEqual(search._similarity("2026", "2025"), 0)
        self.assertEqual(search._similarity("سن", "حسن"), 0)
        self.assertGreater(search._similarity("حسني", "حسين"), 0)


if __name__ == "__main__":
    unittest.main()