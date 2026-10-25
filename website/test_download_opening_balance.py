"""One-shot opening balances never replace actual successful-delivery counts."""
from collections import defaultdict
from unittest import TestCase
from unittest.mock import patch

from pymongo.errors import DuplicateKeyError

from scripts import seed_download_opening_balances as seed
from website.test_feedback import Collection


class MigrationCollection(Collection):
    def create_index(self, *args, **kwargs):
        pass

    def insert_one(self, document):
        if "_id" in document and self.find_one({"_id": document["_id"]}):
            raise DuplicateKeyError("duplicate migration")
        return super().insert_one(document)

    def update_one(self, query, update, upsert=False):
        if upsert and self.find_one(query) is None:
            self.docs.append({**query, **update.get("$setOnInsert", {})})
        return super().update_one(query, update, upsert)

    def bulk_write(self, operations, ordered=True):
        for operation in operations:
            self.update_one(operation._filter, operation._doc, operation._upsert)


class DownloadOpeningBalanceTests(TestCase):
    def test_rating_multiplier_range_scope_and_twin_deduplication(self):
        buttons = [
            {"id": 1, "type": "menu", "label": "السادس العلمي"},
            {"id": 2, "type": "menu", "label": "الملازم", "parent_id": 1},
            {"id": 3, "type": "content", "parent_id": 2},
            {"id": 4, "type": "menu", "label": "الأجوبة النموذجية", "parent_id": 1},
            {"id": 5, "type": "content", "parent_id": 4},
            {"id": 6, "type": "menu", "label": "الملازم", "deleted": 1},
            {"id": 7, "type": "content", "parent_id": 6},
        ]
        items = [
            {"id": 10, "button_id": 3, "type": "file", "file_id": "first"},
            {"id": 11, "button_id": 3, "type": "document", "file_id": "second"},
            {"id": 12, "button_id": 3, "type": "file", "file_id": "first-twin"},
            {"id": 13, "button_id": 5, "type": "file", "file_id": "answer"},
            {"id": 14, "button_id": 3, "type": "file", "file_id": "hidden", "website_hidden": True},
            {"id": 15, "button_id": 3, "type": "text"},
            {"id": 16, "button_id": 7, "type": "file", "file_id": "deleted-path"},
        ]
        rating_for = lambda button, item: {"count": 3 if item["id"] == 10 else 0}
        for random_value, expected in ((0, [300, 100]), (200, [900, 300])):
            with self.subTest(random_value=random_value):
                targets = seed.build_targets(
                    buttons, items, [{"a": 10, "b": 12}], rating_for,
                    randbelow=lambda upper: random_value,
                )
                self.assertEqual([target["item_id"] for target in targets], [10, 11])
                self.assertEqual([target["opening_count"] for target in targets], expected)

    def test_retry_and_rerun_do_not_duplicate_seed_or_target_new_files(self):
        db = defaultdict(MigrationCollection)
        db["item_download_stats"].docs = [{
            "item_id": 10, "count": 5, "display_count": 4, "display_updated_at": 10000,
        }]
        targets = [
            {"item_id": 10, "rating_count": 3, "opening_count": 600},
            {"item_id": 11, "rating_count": 0, "opening_count": 150},
        ]
        with patch.object(seed, "prepare_targets", return_value=targets) as prepare:
            self.assertFalse(seed.run(db, apply=True)["already_completed"])
            prepare.assert_called_once()
        existing = db["item_download_stats"].find_one({"item_id": 10})
        self.assertEqual(existing["count"], 5)
        self.assertEqual(existing["display_count"], 4)
        self.assertEqual(existing["opening_count"], 600)
        self.assertEqual(db["item_download_stats"].find_one({"item_id": 11})["count"], 0)

        # Simulate a crash after rows committed but before the completion marker.
        db["data_migrations"].docs[0]["status"] = "pending"
        db["item_download_stats"].update_one({"item_id": 10}, {"$inc": {"count": 1}})
        with patch.object(seed, "prepare_targets", side_effect=AssertionError("must not replan")):
            seed.run(db, apply=True)
            self.assertTrue(seed.run(db, apply=True)["already_completed"])
        self.assertEqual(db["item_download_stats"].find_one({"item_id": 10})["count"], 6)
        self.assertEqual(db["item_download_stats"].find_one({"item_id": 10})["opening_count"], 600)
        self.assertEqual(len(db["item_download_stats"].docs), 2)
        self.assertIsNone(db["item_download_stats"].find_one({"item_id": 99}))

    def test_dry_run_does_not_write_any_balance_or_migration(self):
        db = defaultdict(MigrationCollection)
        targets = [{"item_id": 10, "rating_count": 1, "opening_count": 200}]
        with patch.object(seed, "prepare_targets", return_value=targets):
            summary = seed.run(db)
        self.assertFalse(summary["applied"])
        self.assertEqual(db["data_migrations"].docs, [])
        self.assertEqual(db["item_download_stats"].docs, [])
