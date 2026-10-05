"""Visible category attachments must still open when absent from search results."""
import os
import unittest
from unittest.mock import patch

from website import app as web


class SortedRows(list):
    def sort(self, keys):
        for key, direction in reversed(keys):
            super().sort(key=lambda row: row.get(key, 0), reverse=direction < 0)
        return self


def _matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in expected):
                return False
        elif isinstance(expected, dict):
            if "$ne" in expected and document.get(key) == expected["$ne"]:
                return False
            if "$in" in expected and document.get(key) not in expected["$in"]:
                return False
        elif document.get(key) != expected:
            return False
    return True


class Collection:
    def __init__(self, records):
        self.records = records

    def find(self, query, *args):
        return SortedRows(
            record for record in self.records if _matches(record, query)
        )

    def find_one(self, query, *args):
        return next(iter(self.find(query)), None)


class VisibleAttachmentFallbackTests(unittest.TestCase):
    def setUp(self):
        self.buttons = [
            {"id": 1, "parent_id": None, "type": "menu", "label": "السادس العلمي"},
            {"id": 2, "parent_id": 1, "type": "menu", "label": "الفيزياء"},
            {"id": 3, "parent_id": 2, "type": "content",
             "label": "واجبات مؤيد سليم الجزء الثاني"},
        ]
        self.item = {
            "id": 30, "button_id": 3, "type": "file",
            "file_id": "visible-unindexed-file",
        }
        self.collections = {
            "buttons": Collection(self.buttons),
            "content_items": Collection([self.item]),
        }
        self.patches = [
            patch.dict(os.environ, {"MONGODB_URI": "mongodb://unused.invalid/botdb"}),
            patch.object(web, "_search_index_records", return_value=[]),
            patch.object(web, "_col", side_effect=lambda name: self.collections[name]),
            patch.object(web, "_items", return_value=[self.item]),
            patch.object(web, "_breadcrumb", return_value=[]),
            patch.object(web, "_similar_attachments", return_value=[]),
            patch.object(web, "_feedback_context", return_value={
                "rating": {"count": 0, "avg": 0, "stars": ""},
                "comments": [], "user_rating": None, "guest_name": "", "shared": False,
            }),
        ]
        for mocked in self.patches:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = web.create_app().test_client()

    def test_file_shown_in_category_opens_even_when_search_excludes_it(self):
        card = web._independent_notes(
            self.buttons[2], [self.item]
        )[0]
        self.assertEqual(card["url"], "/attachment/visible-unindexed-file")

        response = self.client.get(card["url"])
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "واجبات مؤيد سليم الجزء الثاني",
            response.get_data(as_text=True),
        )

    def test_bot_only_group_attachments_remain_hidden(self):
        self.buttons[1]["label"] = "گروب طلاب السادس"
        self.assertEqual(
            self.client.get("/attachment/visible-unindexed-file").status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
