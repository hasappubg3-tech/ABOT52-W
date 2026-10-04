"""Custom emoji metadata must survive cloning button trees."""
import unittest
from collections import defaultdict
from unittest.mock import patch

from bot import data_access


def _matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in expected):
                return False
        elif isinstance(expected, dict):
            if "$ne" in expected and document.get(key) == expected["$ne"]:
                return False
        elif document.get(key) != expected:
            return False
    return True


class Cursor(list):
    def sort(self, fields):
        for key, direction in reversed(fields):
            super().sort(key=lambda item: item.get(key, 0), reverse=direction < 0)
        return self


class Collection:
    def __init__(self, records=()):
        self.records = [dict(record) for record in records]

    def find(self, query):
        return Cursor(dict(record) for record in self.records if _matches(record, query))

    def find_one(self, query):
        return next(iter(self.find(query)), None)

    def insert_one(self, document):
        self.records.append(dict(document))

    def update_one(self, query, update):
        record = self.find_one(query)
        if record:
            target = next(item for item in self.records if item.get("id") == record.get("id"))
            target.update(update.get("$set", {}))


class CloneCustomEmojiTests(unittest.TestCase):
    def test_cloning_menu_preserves_custom_emoji_on_root_and_nested_buttons(self):
        original = [
            {"id": 1, "parent_id": None, "type": "menu", "label": "📘 القسم",
             "label_emojis": {"📘": "emoji-root"}, "ord": 1},
            {"id": 2, "parent_id": 1, "type": "menu", "label": "📚 مادة",
             "label_emojis": {"📚": "emoji-menu-child"}, "ord": 1},
            {"id": 3, "parent_id": 2, "type": "content", "label": "🧪 ملزمة",
             "label_emojis": {"🧪": "emoji-deep-child"}, "ord": 1},
            {"id": 4, "parent_id": 1, "type": "compound", "label": "📂 ملفات",
             "label_emojis": {"📂": "emoji-compound"}, "ord": 2},
            {"id": 5, "parent_id": 4, "type": "content", "label": "📄 ملف",
             "label_emojis": {"📄": "emoji-compound-child"}, "ord": 1},
        ]
        collections = defaultdict(Collection)
        collections["buttons"] = Collection(original)
        next_id = 100

        def allocate(_collection):
            nonlocal next_id
            next_id += 1
            return next_id

        with patch.object(data_access, "_col",
                          side_effect=lambda name: collections[name]), \
             patch.object(data_access, "_next_id", side_effect=allocate):
            cloned_root_id = data_access.clone_btn(1, None)

        by_parent_and_label = {
            (record.get("parent_id"), record["label"]): record
            for record in collections["buttons"].records
        }
        cloned_root = next(
            record for record in collections["buttons"].records
            if record.get("id") == cloned_root_id
        )
        self.assertEqual(cloned_root["label_emojis"], {"📘": "emoji-root"})

        cloned_menu = by_parent_and_label[(cloned_root_id, "📚 مادة")]
        self.assertEqual(cloned_menu["label_emojis"], {"📚": "emoji-menu-child"})
        cloned_deep_child = by_parent_and_label[(cloned_menu["id"], "🧪 ملزمة")]
        self.assertEqual(cloned_deep_child["label_emojis"], {"🧪": "emoji-deep-child"})

        cloned_compound = by_parent_and_label[(cloned_root_id, "📂 ملفات")]
        self.assertEqual(cloned_compound["label_emojis"], {"📂": "emoji-compound"})
        cloned_compound_child = by_parent_and_label[(cloned_compound["id"], "📄 ملف")]
        self.assertEqual(
            cloned_compound_child["label_emojis"], {"📄": "emoji-compound-child"}
        )

    def test_cloning_legacy_button_keeps_legacy_emoji_fallback(self):
        collections = defaultdict(Collection)
        collections["buttons"] = Collection([
            {"id": 1, "parent_id": None, "type": "menu", "label": "القسم", "ord": 1},
        ])
        next_id = 100

        def allocate(_collection):
            nonlocal next_id
            next_id += 1
            return next_id

        with patch.object(data_access, "_col",
                          side_effect=lambda name: collections[name]), \
             patch.object(data_access, "_next_id", side_effect=allocate):
            cloned_id = data_access.clone_btn(1, None)

        clone = next(
            record for record in collections["buttons"].records
            if record.get("id") == cloned_id
        )
        self.assertNotIn("label_emojis", clone)


if __name__ == "__main__":
    unittest.main()