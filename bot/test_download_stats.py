import unittest
from unittest.mock import patch

from bot import data_access


class MemoryStatsCollection:
    def __init__(self):
        self.rows = {}

    def update_one(self, query, update, upsert=False):
        item_id = query["item_id"]
        row = self.rows.get(item_id)
        if row is None:
            if not upsert:
                return
            row = {"item_id": item_id}
            row.update(update.get("$setOnInsert", {}))
            self.rows[item_id] = row
        for key, value in query.items():
            if key != "item_id" and row.get(key) != value:
                return
        for key, value in update.get("$inc", {}).items():
            row[key] = row.get(key, 0) + value
        row.update(update.get("$set", {}))

    def find_one(self, query):
        return self.rows.get(query["item_id"])


class DownloadStatsTests(unittest.TestCase):
    def test_opening_balance_is_fixed_while_actual_downloads_keep_increasing(self):
        collection = MemoryStatsCollection()
        collection.rows[41] = {
            "item_id": 41, "opening_count": 600,
            "count": 5, "display_count": 5, "display_updated_at": 10_000,
        }
        clock = [10_000]
        with patch.object(data_access, "_col", return_value=collection), \
                patch.object(data_access, "canonical_item_id", side_effect=lambda iid: iid), \
                patch.object(data_access._time, "time", side_effect=lambda: clock[0]):
            self.assertEqual(data_access.get_item_download_display_count(41), 605)
            self.assertEqual(data_access.increment_item_download_count(41), 605)
            self.assertEqual(data_access.get_item_download_total(41), 6)
            clock[0] += 3601
            self.assertEqual(data_access.get_item_download_display_count(41), 606)
        self.assertEqual(collection.rows[41]["opening_count"], 600)

    def test_public_snapshot_refreshes_no_more_than_hourly(self):
        collection = MemoryStatsCollection()
        clock = [10_000]
        with patch.object(data_access, "_col", return_value=collection), \
                patch.object(data_access, "canonical_item_id", side_effect=lambda iid: iid), \
                patch.object(data_access._time, "time", side_effect=lambda: clock[0]):
            self.assertEqual(data_access.increment_item_download_count(41), 1)
            clock[0] += 120
            self.assertEqual(data_access.increment_item_download_count(41), 1)
            self.assertEqual(data_access.get_item_download_total(41), 2)
            clock[0] += 3600
            self.assertEqual(data_access.get_item_download_display_count(41), 2)
            self.assertEqual(data_access.increment_item_download_count(41), 2)
            self.assertEqual(data_access.get_item_download_display_count(41), 2)

    def test_only_material_section_ancestors_enable_counting(self):
        buttons = {
            1: {"id": 1, "type": "content", "parent_id": 2},
            2: {"id": 2, "type": "menu", "label": "📚 الملازم والكتب"},
            3: {"id": 3, "type": "content", "parent_id": 4},
            4: {"id": 4, "type": "menu", "label": "الأجوبة النموذجية"},
            5: {"id": 5, "type": "content", "parent_id": 6},
            6: {"id": 6, "type": "menu", "label": "الملخصات"},
        }
        with patch.object(data_access, "get_btn", side_effect=buttons.get):
            self.assertTrue(data_access._is_study_material_button(1))
            self.assertFalse(data_access._is_study_material_button(3))
            self.assertTrue(data_access._is_study_material_button(5))


if __name__ == "__main__":
    unittest.main()
