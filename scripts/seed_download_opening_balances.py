"""Set a fixed, owner-requested opening balance once for existing material files.

No startup hook runs this script. The persisted migration snapshot is reused on
retries, so neither future ratings nor newly added files receive another seed.
Actual successful-delivery counters are never modified.
"""
import argparse
import json
import secrets
import sys
import time
from pathlib import Path

from bson import BSON
from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from website import app as web

MIGRATION_ID = "material_download_opening_balance"


def build_targets(buttons, items, twins, rating_for, randbelow=secrets.randbelow):
    visible = {
        button["id"]: button for button in buttons
        if not button.get("deleted") and not button.get("hidden")
        and not web._is_bot_only_entry(button)
    }
    canonical = {}
    for pair in twins:
        left, right = pair.get("a"), pair.get("b")
        if left is not None and right is not None:
            canonical[left] = canonical[right] = min(left, right)
    targets, seen = [], set()
    for item in sorted(items, key=lambda row: row.get("id") or 0):
        button = visible.get(item.get("button_id"))
        if (not button or button.get("type") != "content"
                or item.get("id") is None or not item.get("file_id")
                or item.get("type") not in {"file", "document"}
                or item.get("website_hidden")
                or not web._has_visible_ancestors(button, visible)
                or not web._website_study_material_path(button, visible)):
            continue
        iid = canonical.get(item["id"], item["id"])
        if iid in seen:
            continue
        seen.add(iid)
        count = max(0, int(rating_for(button, item).get("count") or 0))
        targets.append({
            "item_id": iid,
            "rating_count": count,
            "opening_count": max(1, count) * (100 + randbelow(201)),
        })
    return targets


def prepare_targets(db):
    buttons = list(db["buttons"].find({}, {
        "id": 1, "parent_id": 1, "type": 1, "label": 1,
        "deleted": 1, "hidden": 1, "unified_rating": 1,
    }))
    items = list(db["content_items"].find({}, {
        "id": 1, "button_id": 1, "type": 1, "file_id": 1, "website_hidden": 1,
    }))
    twins = list(db["item_twins"].find({}, {"a": 1, "b": 1}))
    return build_targets(buttons, items, twins, web._attachment_card_rating)


def apply_opening_balances(db, snapshot):
    stats = db["item_download_stats"]
    stats.create_index([("item_id", 1)], unique=True)
    targets = snapshot["targets"]
    for offset in range(0, len(targets), 400):
        operations = []
        for target in targets[offset:offset + 400]:
            iid = target["item_id"]
            operations.extend([
                UpdateOne({"item_id": iid}, {"$setOnInsert": {
                    "item_id": iid, "count": 0, "display_count": 0,
                    "display_updated_at": snapshot["created_at"],
                }}, upsert=True),
                UpdateOne(
                    {"item_id": iid, "opening_balance_migration": {"$exists": False}},
                    {"$set": {
                        "opening_count": target["opening_count"],
                        "opening_rating_count": target["rating_count"],
                        "opening_balance_migration": MIGRATION_ID,
                        "opening_created_at": snapshot["created_at"],
                    }},
                ),
            ])
        stats.bulk_write(operations, ordered=True)


def run(db, apply=False):
    migrations = db["data_migrations"]
    snapshot = migrations.find_one({"_id": MIGRATION_ID})
    if snapshot is None:
        snapshot = {
            "_id": MIGRATION_ID, "created_at": int(time.time()),
            "source": "owner_requested_opening_balance",
            "status": "pending", "targets": prepare_targets(db),
        }
        if not snapshot["targets"]:
            raise ValueError("No eligible material files found; no balances were written.")
        if len(BSON.encode(snapshot)) > 15_000_000:
            raise ValueError("Migration snapshot is too large; no balances were written.")
        if apply:
            try:
                migrations.insert_one(snapshot)
            except DuplicateKeyError:
                snapshot = migrations.find_one({"_id": MIGRATION_ID})
                if snapshot is None:
                    raise
    already_completed = snapshot["status"] == "complete"
    if apply and not already_completed:
        apply_opening_balances(db, snapshot)
        migrations.update_one(
            {"_id": MIGRATION_ID},
            {"$set": {"status": "complete", "completed_at": int(time.time())}},
        )
    return {
        "files": len(snapshot["targets"]),
        "rated_files": sum(target["rating_count"] > 0 for target in snapshot["targets"]),
        "opening_total": sum(target["opening_count"] for target in snapshot["targets"]),
        "applied": apply,
        "already_completed": already_completed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(web._get_mongo(), apply=args.apply), ensure_ascii=False))


if __name__ == "__main__":
    main()
