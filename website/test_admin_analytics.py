import hashlib
import hmac
import json
import os
import time
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

from website import app as web


class MemoryCollection:
    def __init__(self, rows=()):
        self.rows = {row.get("_id", str(i)): dict(row) for i, row in enumerate(rows)}

    def create_index(self, *args, **kwargs):
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        rows = list(self.rows.values())
        date_filter = query.get("date", {})
        if isinstance(date_filter, dict) and "$gte" in date_filter:
            rows = [row for row in rows if row.get("date", "") >= date_filter["$gte"]]
        return rows

    def find_one(self, query=None, projection=None):
        query = query or {}
        return next(
            (row for row in self.rows.values()
             if all(row.get(key) == value for key, value in query.items())),
            None,
        )

    def update_one(self, query, update, upsert=False):
        key = query["_id"]
        row = self.rows.get(key)
        if row is None:
            if not upsert:
                return
            row = {"_id": key}
            row.update(update.get("$setOnInsert", {}))
            self.rows[key] = row
        for field, value in update.get("$inc", {}).items():
            row[field] = row.get(field, 0) + value


def signed_init_data(bot_token, user_id, auth_date=None):
    auth_date = auth_date or int(time.time())
    values = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": user_id}, separators=(",", ":")),
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(
        secret, check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(values)


class TrafficSourceTests(unittest.TestCase):
    def setUp(self):
        self.app = web.create_app()

    def test_classifies_search_direct_and_tagged_telegram_without_storing_queries(self):
        with self.app.test_request_context("/", base_url="https://alameer-iq.com"):
            self.assertEqual(web._classify_traffic_source(), ("direct", "", ""))
        with self.app.test_request_context(
            "/", base_url="https://alameer-iq.com",
            headers={"Referer": "https://www.google.com/search?q=private+query"},
        ):
            self.assertEqual(web._classify_traffic_source(), ("search", "", ""))
        with self.app.test_request_context(
            "/?utm_source=telegram&utm_medium=bot&utm_campaign=search_button",
            base_url="https://alameer-iq.com",
        ):
            self.assertEqual(
                web._classify_traffic_source(),
                ("telegram", "bot", "search_button"),
            )

    def test_one_visit_is_recorded_per_thirty_minutes_of_inactivity(self):
        collection = MemoryCollection()
        clock = [100_000]
        with patch.object(web, "_col", return_value=collection), \
                patch.object(web, "_ensure_traffic_index"), \
                patch.object(web.time, "time", side_effect=lambda: clock[0]), \
                self.app.test_request_context("/", base_url="https://alameer-iq.com"):
            web._record_traffic_visit()
            clock[0] += 100
            web._record_traffic_visit()
            clock[0] += 1801
            web._record_traffic_visit()
        self.assertEqual(sum(row.get("visits", 0) for row in collection.rows.values()), 2)
        self.assertTrue(all("referrer" not in row for row in collection.rows.values()))


class DownloadDisplayTests(unittest.TestCase):
    def test_site_counter_is_available_only_under_material_folders(self):
        buttons = {
            1: {"id": 1, "type": "content", "parent_id": 2},
            2: {"id": 2, "type": "menu", "label": "📚 الكتب"},
            3: {"id": 3, "type": "content", "parent_id": 4},
            4: {"id": 4, "type": "menu", "label": "الأجوبة النموذجية"},
        }
        self.assertTrue(web._website_study_material_path(buttons[1], buttons))
        self.assertFalse(web._website_study_material_path(buttons[3], buttons))

        with patch.object(web, "_bot_download_display_counts", return_value={41: 12}):
            self.assertEqual(
                web._attachment_download_count_from_record(
                    {"download_tracking": True}, 41
                ),
                12,
            )
            self.assertIsNone(
                web._attachment_download_count_from_record(
                    {"download_tracking": False}, 41
                )
            )


class OwnerStatsAuthTests(unittest.TestCase):
    def setUp(self):
        self.bot_token = "test-bot-token"
        self.app = web.create_app()
        self.client = self.app.test_client()

    def test_stats_api_requires_owner_signed_telegram_data(self):
        with patch.dict(os.environ, {
            "MONGODB_URI": "mongodb://unused.invalid/test",
            "SUPER_ADMIN_ID": "12345",
        }), patch.object(web, "BOT_TOKEN", self.bot_token):
            denied = self.client.get(
                "/api/admin/stats/summary", base_url="https://alameer-iq.com"
            )
            self.assertEqual(denied.status_code, 401)

            invalid = signed_init_data(self.bot_token, 99999)
            rejected = self.client.post(
                "/api/admin/stats/auth", json={"initData": invalid},
                base_url="https://alameer-iq.com",
            )
            self.assertEqual(rejected.status_code, 403)

            valid = signed_init_data(self.bot_token, 12345)
            accepted = self.client.post(
                "/api/admin/stats/auth", json={"initData": valid},
                base_url="https://alameer-iq.com",
            )
            self.assertEqual(accepted.status_code, 200)

            rows = {
                "website_traffic_daily": MemoryCollection([
                    {"date": time.strftime("%Y-%m-%d", time.gmtime()),
                     "source": "search", "visits": 3},
                    {"date": time.strftime("%Y-%m-%d", time.gmtime()),
                     "source": "direct", "visits": 2},
                ]),
                "item_download_stats": MemoryCollection([
                    {"item_id": 41, "count": 6},
                ]),
                "content_items": MemoryCollection([
                    {"id": 41, "button_id": 7, "file_name": "ملزمة الكيمياء"},
                ]),
                "buttons": MemoryCollection([
                    {"id": 7, "label": "الملازم"},
                ]),
            }
            with patch.object(web, "_col", side_effect=lambda name: rows[name]):
                summary = self.client.get(
                    "/api/admin/stats/summary", base_url="https://alameer-iq.com"
                )
            self.assertEqual(summary.status_code, 200)
            data = summary.get_json()
            self.assertEqual(data["total_visits"], 5)
            self.assertEqual(data["total_bot_downloads"], 6)
            self.assertEqual(data["top_downloads"][0]["downloads"], 6)

    def test_tampered_and_stale_init_data_are_rejected(self):
        now = int(time.time())
        stale = signed_init_data(self.bot_token, 12345, now - 601)
        tampered = signed_init_data(self.bot_token, 12345) + "x"
        with patch.dict(os.environ, {"SUPER_ADMIN_ID": "12345"}), \
                patch.object(web, "BOT_TOKEN", self.bot_token):
            self.assertFalse(web._verify_owner_webapp_init_data(stale))
            self.assertFalse(web._verify_owner_webapp_init_data(tampered))


if __name__ == "__main__":
    unittest.main()
