"""Website-only regression tests; no Telegram requests or database writes."""
import os
import unittest
from unittest.mock import Mock, patch

from website import app as web

REAL_BUTTON_LOOKUP = web._btn
REAL_CHILDREN_LOOKUP = web._children
REAL_SEARCH_INDEX = web._search_index_records


class SortedRows(list):
    def sort(self, keys):
        for key, direction in reversed(keys):
            super().sort(key=lambda row: row.get(key, 0), reverse=direction < 0)
        return self


class EmptyAttachmentCollection:
    def find(self, query, *args):
        if "file_id" not in query:
            raise AssertionError("Unexpected database access")
        return SortedRows()


class IndependentNotesTests(unittest.TestCase):
    def setUp(self):
        self.group = {
            "id": 10, "parent_id": 5, "type": "content",
            "label": "ملزمة الفيزياء 2026",
        }
        self.parent = {"id": 5, "parent_id": 1, "type": "menu", "label": "الفيزياء"}
        self.files = [
            {"id": 1, "button_id": 10, "type": "document",
             "file_id": "first-pdf", "caption": "ملزمة الفيزياء الجزء الأول 2025"},
            {"id": 2, "button_id": 10, "type": "file",
             "file_id": "second-pdf", "caption": "ملزمة الفيزياء الجزء الثاني 2026"},
        ]
        normalized_items = [
            web._normalize_search_text(web._search_item_text(item)) for item in self.files
        ]
        normalized_label = web._normalize_search_text(self.group["label"])

        def no_database_access(name):
            if name == "content_items":
                return EmptyAttachmentCollection()
            raise AssertionError("Unexpected database access")

        self.record = {
            "button": self.group, "items": self.files, "label": self.group["label"],
            "normalized_label": normalized_label,
            "normalized_item_texts": normalized_items,
            "aggregate_normalized": " ".join([normalized_label, *normalized_items]),
        }
        patches = [
            patch.dict(os.environ, {"MONGODB_URI": "mongodb://unused.invalid/botdb"}),
            patch.object(web, "_search_index_cache", {"expires": 0, "records": None}),
            patch.object(web, "_btn", side_effect=lambda bid: {
                10: self.group, 5: self.parent,
            }.get(bid)),
            patch.object(web, "_children", return_value=[self.group]),
            patch.object(web, "_items", return_value=self.files),
            patch.object(web, "_search_index_records", return_value=[self.record]),
            patch.object(web, "_file_url", return_value=None),
            patch.object(web, "_breadcrumb", return_value=[]),
            patch.object(web, "_rating", return_value={"count": 0, "avg": 0, "stars": ""}),
            patch.object(web, "_feedback_context", return_value={
                "rating": {"count": 0, "avg": 0, "stars": ""},
                "comments": [], "user_rating": None, "guest_name": "", "shared": False,
            }),
            patch.object(web, "_has_content_media", return_value=False),
            patch.object(web, "_col", side_effect=no_database_access),
        ]
        for mocked in patches:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = web.create_app().test_client()

    def test_category_lists_every_file_as_a_separate_detail_link(self):
        response = self.client.get("/cat/5")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('href="/attachment/first-pdf"', page)
        self.assertIn('href="/attachment/second-pdf"', page)
        self.assertNotIn('href="/note/10"', page)
        self.assertNotIn('href="/file/', page)
        self.assertNotIn("ملفات الملزمة", page)

    def test_requested_homepage_and_footer_copy(self):
        with patch.object(web, "_children", return_value=[
            {"id": 1, "parent_id": None, "type": "menu", "label": "السادس العلمي"},
        ]), patch.object(web, "_latest_notes", return_value=[]):
            page = self.client.get("/").get_data(as_text=True)
        self.assertIn("كل ما يحتاجه الطالب", page)
        self.assertIn("اختار مرحلتك الدراسية...", page)
        self.assertIn("جميع الملازم والكتب متاحة مجاناً عبر التلگرام", page)
        self.assertIn("افتح البوت على التلگرام", page)
        self.assertIn('href="/static/img/site-icon.png"', page)
        self.assertNotIn("ملازم وكتب دراسية مجانية لجميع الصفوف", page)
        self.assertNotIn("الصفوف الدراسية", page)

    def test_site_icon_is_a_small_png_from_the_header_logo(self):
        response = self.client.get("/static/img/site-icon.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        self.assertTrue(response.data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertLess(len(response.data), 20_000)
        response.close()

    def test_attachment_and_feedback_use_requested_copy(self):
        with patch.object(web, "_feedback_context", return_value={
            "rating": {"count": 0, "avg": 0, "stars": ""},
            "comments": [], "user_rating": None, "guest_name": "", "shared": True,
        }):
            page = self.client.get("/attachment/first-pdf").get_data(as_text=True)
        self.assertIn("اضغط زر التحميل لتحميل الملف من التلگرام بشكل مباشر.", page)
        self.assertIn("اختر عدداً من النجوم لإرسال تقييمك.", page)
        self.assertIn("التعليقات", page)
        self.assertNotIn("التقييمات والتعليقات مشتركة بين صفحات المحتوى", page)
        self.assertNotIn("دون الحاجة إلى كتابة اسم", page)
        self.assertNotIn("تعليقات القراء", page)

    def test_old_multi_file_note_link_opens_independent_cards(self):
        response = self.client.get("/note/10")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/cat/10")
        page = self.client.get(response.location).get_data(as_text=True)
        self.assertIn('href="/attachment/first-pdf"', page)
        self.assertIn('href="/attachment/second-pdf"', page)
        self.assertNotIn("ملفات الملزمة", page)

    def test_old_single_file_note_link_opens_that_file(self):
        with patch.object(web, "_items", return_value=self.files[:1]):
            response = self.client.get("/note/10")
        self.assertEqual(response.location, "/attachment/first-pdf")

    def test_duplicate_file_records_do_not_create_duplicate_cards(self):
        with patch.object(web, "_items", return_value=[self.files[0], self.files[0]]):
            response = self.client.get("/note/10")
            page = self.client.get("/cat/5").get_data(as_text=True)
        self.assertEqual(response.location, "/attachment/first-pdf")
        self.assertEqual(page.count('href="/attachment/first-pdf"'), 1)

    def test_selected_file_is_separate_from_similar_files(self):
        response = self.client.get("/attachment/first-pdf")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        detail, similar = page.split("ملفات مشابهة", 1)
        self.assertIn(web._bot_download_url(10, 1), detail)
        self.assertIn('data-telegram-handoff-target="10_1"', detail)
        self.assertIn('data-telegram-handoff-url="/api/telegram-download-handoffs"', detail)
        self.assertNotIn(web._bot_download_url(10), detail)
        self.assertNotIn('src="/file/', detail)
        self.assertNotIn("second-pdf", detail)
        self.assertIn('href="/attachment/second-pdf"', similar)
        self.assertNotIn('href="/attachment/first-pdf"', similar)
        self.assertNotIn("ملفات الملزمة", page)

    def test_search_opens_independent_detail_pages(self):
        results = self.client.get("/api/search?q=فيزياء").get_json()
        self.assertEqual(
            {result["url"] for result in results},
            {"/attachment/first-pdf", "/attachment/second-pdf"},
        )

    def test_home_latest_files_already_use_independent_pages(self):
        results = web._latest_notes()
        self.assertEqual(
            {result["url"] for result in results},
            {"/attachment/first-pdf", "/attachment/second-pdf"},
        )

    def test_group_year_does_not_override_file_year(self):
        title = web._attachment_display_title(self.files[0], 1, self.group["label"])
        self.assertIn("2025", title)
        self.assertNotIn("2026", title)

    def test_attachment_title_includes_teacher_before_year_and_part(self):
        item = {
            **self.files[0],
            "caption": "ملزمة الكيمياء الجزء 2 2026\nللأستاذ علي حيدر",
        }
        self.assertEqual(
            web._attachment_display_title(item, 1, "📌ملزمة 2027📌"),
            "ملزمة الكيمياء للأستاذ علي حيدر 2026 الجزء 2",
        )
        self.assertEqual(
            web._attachment_search_subtitle(item, "📌ملزمة 2027📌"),
            "ملزمة",
        )

    def test_teacher_already_in_caption_is_not_duplicated(self):
        item = {
            **self.files[0],
            "caption": "ملزمة الكيمياء للأستاذ علي حيدر 2026 الجزء 2",
        }
        self.assertEqual(
            web._attachment_display_title(item, 1, "ملزمة 2027"),
            "ملزمة الكيمياء للأستاذ علي حيدر 2026 الجزء 2",
        )

    def test_legacy_note_title_uses_the_same_teacher_order(self):
        item = {
            **self.files[0],
            "caption": "ملزمة الكيمياء الجزء 2 2026\nللأستاذ علي حيدر",
        }
        self.assertEqual(
            web._note_display_name(self.group, [item]),
            "ملزمة الكيمياء للأستاذ علي حيدر 2026 الجزء 2",
        )

    def test_download_through_bot_does_not_depend_on_preview_availability(self):
        response = self.client.get("/attachment/first-pdf")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("تحميل من التلكرام", page)
        self.assertIn(web._bot_download_url(10, 1), page)
        self.assertNotIn("معاينة الملف غير متاحة", page)
        self.assertNotIn("فتح رسالة الملف", page)
        web._file_url.assert_not_called()
        self.assertIn('href="/attachment/second-pdf"', page)
        self.assertNotIn('<iframe class="pdf-embed"', page)

    def test_each_file_in_one_button_has_its_own_telegram_download_target(self):
        for item in self.files:
            page = self.client.get(f"/attachment/{item['file_id']}").get_data(as_text=True)
            detail = page.split("ملفات مشابهة", 1)[0]
            self.assertIn(f"?start=file_10_{item['id']}", detail)
            self.assertNotIn("?start=btn_10", detail)
            self.assertIn("اضغط زر التحميل لتحميل الملف من التلگرام بشكل مباشر", detail)

    def test_text_only_download_links_keep_the_legacy_button_payload(self):
        self.assertEqual(web._bot_download_url(10),
                         f"https://t.me/{web.BOT_USERNAME}?start=btn_10")

    def test_old_direct_document_links_redirect_only_to_the_bot(self):
        for item_id, file_id in enumerate(("first-pdf", "second-pdf"), start=1):
            response = self.client.get(f"/file/{file_id}")
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, web._bot_download_url(10, item_id))
        web._file_url.assert_not_called()

    def test_unknown_direct_file_links_are_not_proxied(self):
        self.assertEqual(self.client.get("/file/hidden-pdf").status_code, 404)
        web._file_url.assert_not_called()

    def test_visible_gallery_photos_still_work_without_exposing_api_urls(self):
        photo = {"type": "photo", "file_id": "visible-photo"}
        records = [{**self.record, "items": [*self.files, photo]}]
        upstream = Mock()
        upstream.headers = {"Content-Type": "image/jpeg"}
        upstream.iter_content.return_value = [b"image-bytes"]
        with patch.object(web, "_search_index_records", return_value=records), \
                patch.object(web, "_file_url", return_value="https://unused.invalid/image"), \
                patch.object(web._req, "get", return_value=upstream):
            response = self.client.get("/file/visible-photo")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b"image-bytes")
            self.assertNotIn("Location", response.headers)
            upstream.close.assert_called_once()

    def test_pdf_cannot_be_downloaded_through_a_photo_record(self):
        records = [{**self.record, "items": [
            *self.files, {"type": "photo", "file_id": "mislabelled-pdf"},
        ]}]
        upstream = Mock()
        upstream.headers = {"Content-Type": "application/pdf"}
        with patch.object(web, "_search_index_records", return_value=records), \
                patch.object(web, "_file_url", return_value="https://unused.invalid/pdf"), \
                patch.object(web._req, "get", return_value=upstream):
            response = self.client.get("/file/mislabelled-pdf")
        self.assertEqual(response.status_code, 404)
        upstream.close.assert_called_once()

    def test_unknown_file_is_not_accessible(self):
        self.assertEqual(self.client.get("/attachment/unknown-pdf").status_code, 404)

    def test_no_similar_files_has_empty_state(self):
        with patch.object(web, "_similar_attachments", return_value=[]):
            page = self.client.get("/attachment/first-pdf").get_data(as_text=True)
        self.assertIn("ملفات مشابهة", page)
        self.assertIn("لا توجد ملفات مشابهة حالياً", page)

    def test_text_only_note_stays_accessible_without_grouped_attachments(self):
        with patch.object(web, "_items", return_value=[
            {"type": "text", "content": "شرح الفيزياء"},
        ]):
            response = self.client.get("/note/10")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertNotIn("ملفات الملزمة", page)
        self.assertIn('data-telegram-handoff-target="10"', page)
        self.assertIn("/static/js/telegram-handoff.js", page)

    def test_student_groups_and_their_content_are_hidden_from_site_surfaces(self):
        buttons = {
            1: {"id": 1, "parent_id": None, "type": "menu",
                "label": "السادس العلمي", "ord": 1},
            2: {"id": 2, "parent_id": 1, "type": "content",
                "label": "گــــروب طــــلاب الــــسادس", "ord": 1},
            3: {"id": 3, "parent_id": 1, "type": "content",
                "label": "ملزمة الاحياء", "ord": 2},
            4: {"id": 4, "parent_id": 2, "type": "content",
                "label": "ملزمة كيمياء", "ord": 1},
        }
        docs = {
            2: {"id": 2, "button_id": 2, "type": "text",
                "content": "مجموعة طلاب السادس"},
            3: {"id": 3, "button_id": 3, "type": "file",
                "file_id": "school-note", "content": "ملزمة الاحياء"},
            4: {"id": 4, "button_id": 4, "type": "file",
                "file_id": "group-child-note", "content": "ملزمة كيمياء"},
        }
        button_collection = Mock()
        button_collection.find.side_effect = lambda query, *args: SortedRows([
            row for row in buttons.values()
            if all(
                (row.get(key) != condition["$ne"])
                if isinstance(condition, dict) and "$ne" in condition
                else row.get(key) == condition
                for key, condition in query.items()
            )
        ])
        button_collection.find_one.side_effect = (
            lambda query, projection=None: buttons.get(query["id"])
        )
        item_collection = Mock()
        item_collection.find.side_effect = lambda query, *args: SortedRows([
            row for row in docs.values()
            if all(row.get(key) == value for key, value in query.items()
                   if not isinstance(value, dict))
        ])
        database = {
            "buttons": button_collection,
            "content_items": item_collection,
            "website_index_state": Mock(find_one=Mock(return_value=None)),
        }

        with patch.object(web, "_col", side_effect=lambda name: database[name]), \
                patch.object(web, "_search_index_records", REAL_SEARCH_INDEX), \
                patch.object(web, "_btn", REAL_BUTTON_LOOKUP), \
                patch.object(web, "_children", REAL_CHILDREN_LOOKUP), \
                patch.object(web, "_search_index_cache", {"expires": 0, "records": None}):
            visible = web._search_index_records()
            self.assertEqual([record["button"]["id"] for record in visible], [3])
            self.assertEqual([button["id"] for button in web._children(1)], [3])
            self.assertIsNone(web._btn(2))
            self.assertIsNone(web._btn(4))
            self.assertEqual(web._btn(3)["id"], 3)

            self.assertEqual(self.client.get("/cat/2").status_code, 404)
            self.assertEqual(self.client.get("/note/2").status_code, 404)


if __name__ == "__main__":
    unittest.main()