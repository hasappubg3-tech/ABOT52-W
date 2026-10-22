"""Model-answer titles use the selected file's real ancestry and metadata."""
import os
import unittest
from collections import defaultdict
from unittest.mock import patch

from website import app as web
from website.model_answer_titles import answer_context, format_answer_title
from website.test_seo import SeoCollection


STAGE = "الصف السادس العلمي"


def context(*details):
    return answer_context([
        "السادس العلمي", "الاجوبة النموذجية لكل السنوات", "الكيمياء", *details,
    ])


class AnswerTitleTests(unittest.TestCase):
    def test_requested_format_from_path_without_caption(self):
        self.assertEqual(format_answer_title(context("2025", "دور اول"), [], STAGE),
                         "الأجوبة النموذجية لأسئلة مادة الكيمياء 2025 الدور الأول للصف السادس العلمي")

    def test_biological_and_applied_branches_are_not_inferred_from_grade(self):
        for raw, expected in [("احيائي", "أحيائي"), ("تطبيقي", "تطبيقي")]:
            title = format_answer_title(context("2017", "دور ثاني " + raw), [], STAGE)
            self.assertIn("2017 الدور الثاني " + expected + " للصف", title)
        plain = format_answer_title(context("2025", "دور اول"), ["الصف السادس العلمي"], STAGE)
        self.assertNotIn("علمي للصف", plain)
        self.assertNotIn("أحيائي", plain)
        self.assertNotIn("تطبيقي", plain)

    def test_all_special_details_are_preserved(self):
        for detail in ("نازحين", "خارج القطر", "أدبي", "موهوبين", "محاولات",
                       "تكميلي", "موصل", "نسخة خاصة"):
            with self.subTest(detail=detail):
                title = format_answer_title(context("2019", "دور اول " + detail), [], STAGE)
                self.assertIn("الدور الأول " + detail + " للصف", title)

    def test_missing_round_is_omitted_not_invented(self):
        title = format_answer_title(context("2026"), ["الاجوبة النموذجية"], STAGE)
        self.assertEqual(title, "الأجوبة النموذجية لأسئلة مادة الكيمياء 2026 للصف السادس العلمي")
        self.assertNotIn("غير محدد", title)

    def test_missing_year_is_omitted(self):
        self.assertEqual(
            format_answer_title(context("دور ثالث نازحين"), [], STAGE),
            "الأجوبة النموذجية لأسئلة مادة الكيمياء الدور الثالث نازحين للصف السادس العلمي",
        )

    def test_path_facts_win_over_conflicting_caption(self):
        title = format_answer_title(
            context("2017", "دور اول احيائي"),
            ["الاجوبة النموذجية 2025 الدور الثاني تطبيقي"], STAGE,
        )
        self.assertIn("2017 الدور الأول أحيائي", title)
        self.assertNotIn("2025", title)
        self.assertNotIn("الثاني", title)
        self.assertNotIn("تطبيقي", title)

    def test_caption_and_filename_fill_only_missing_facts(self):
        title = format_answer_title(context("2025"), ["جواب كيمياء الدور الثاني تطبيقي.pdf"], STAGE)
        self.assertIn("2025 الدور الثاني تطبيقي", title)

    def test_arabic_digits_and_repeated_round_word(self):
        title = format_answer_title(context("٢٠٢٥", "الدور الدور الأول"), [], STAGE)
        self.assertIn("2025 الدور الأول للصف", title)

    def test_preliminary_and_supplementary_rounds(self):
        self.assertIn("الدور التمهيدي تطبيقي", format_answer_title(
            context("2020", "تمهيدي تطبيقي"), [], STAGE
        ))
        self.assertIn("الدور الثالث تكميلي", format_answer_title(
            context("2020", "دور ثالث تكميلي"), [], STAGE
        ))

    def test_ordinary_study_notes_are_not_reformatted(self):
        self.assertIsNone(answer_context(["السادس العلمي", "الملازم", "الكيمياء", "ملزمة"]))
        self.assertIsNone(format_answer_title(None, ["ملزمة الكيمياء 2025"], STAGE))


class AnswerTitleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.db = defaultdict(SeoCollection)
        for button in [
            {"id": 1, "parent_id": None, "type": "menu", "label": "السادس العلمي"},
            {"id": 2, "parent_id": 1, "type": "menu", "label": "الأجوبة النموذجية"},
            {"id": 3, "parent_id": 2, "type": "menu", "label": "الكيمياء"},
            {"id": 4, "parent_id": 3, "type": "menu", "label": "2025"},
            {"id": 5, "parent_id": 4, "type": "content", "label": "دور اول احيائي"},
        ]:
            self.db["buttons"].insert_one(button)
        self.db["content_items"].insert_one({
            "id": 1, "button_id": 5, "type": "file", "file_id": "answer-file",
            "caption": "الاجوبة النموذجية",
        })
        for mocked in [
            patch.dict(os.environ, {"MONGODB_URI": "mongodb://unused.invalid/test"}),
            patch.object(web, "_col", side_effect=lambda name: self.db[name]),
            patch.object(web, "_search_index_cache", {"expires": 0, "records": None}),
        ]:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = web.create_app().test_client()
        self.expected = "الأجوبة النموذجية لأسئلة مادة الكيمياء 2025 الدور الأول أحيائي للصف السادس العلمي"

    def test_category_detail_metadata_search_and_latest_use_same_title(self):
        self.assertIn(self.expected, self.client.get("/cat/4").get_data(as_text=True))
        response = self.client.get("/attachment/answer-file", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn(self.expected, page)
        self.assertIn("<title>" + self.expected, page)
        self.assertEqual(web._latest_notes(8)[0]["display_label"], self.expected)
        self.assertEqual(web._search_content("كيمياء 2025 احيائي")[0]["display_label"], self.expected)
        self.assertEqual(web._note_display_name(self.db["buttons"].find_one({"id": 5})), self.expected)

    def test_existing_arabic_permalink_is_not_changed_by_display_format(self):
        web._search_index_records()
        self.assertEqual(web._attachment_url("answer-file"), "/attachment/الاجوبة-النموذجية")
        self.assertEqual(self.client.get(web._attachment_url("answer-file")).status_code, 200)

    def test_hidden_answer_does_not_reappear_in_search_or_latest(self):
        self.db["content_items"].update_one({"id": 1}, {"$set": {"website_hidden": True}})
        self.assertEqual(web._latest_notes(8), [])
        self.assertEqual(web._search_content("كيمياء 2025"), [])


if __name__ == "__main__":
    unittest.main()
