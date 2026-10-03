"""Feedback regressions with an isolated in-memory Mongo-shaped store."""
import copy
import os
import re
import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import patch

from pymongo.errors import DuplicateKeyError, PyMongoError
from website import app as web
from website import feedback

REAL_ATTACHMENT_LOOKUP = web._find_visible_attachment


def matches(document, query):
    for key, value in query.items():
        if key == "$or":
            if not any(matches(document, branch) for branch in value):
                return False
        elif isinstance(value, dict):
            if "$lte" in value and document.get(key, float("inf")) > value["$lte"]:
                return False
            if "$exists" in value and (key in document) != value["$exists"]:
                return False
            if "$ne" in value and document.get(key) == value["$ne"]:
                return False
        elif document.get(key) != value:
            return False
    return True


class Cursor(list):
    def sort(self, fields):
        for field, direction in reversed(fields):
            super().sort(key=lambda doc: doc.get(field, 0), reverse=direction < 0)
        return self


class Collection:
    def __init__(self):
        self.docs = []

    def find(self, query, projection=None):
        docs = [copy.deepcopy(d) for d in self.docs if matches(d, query)]
        if projection:
            docs = [{key: value for key, value in doc.items()
                     if projection.get(key, key == "_id")} for doc in docs]
        return Cursor(docs)

    def find_one(self, query):
        return next(iter(self.find(query)), None)

    def insert_one(self, document):
        if "target_type" in document and self.find_one({
            k: document[k] for k in ("target_type", "target_id", "user_id")
        }):
            raise DuplicateKeyError("duplicate comment")
        self.docs.append(copy.deepcopy(document))

    def update_one(self, query, update, upsert=False):
        found = next((d for d in self.docs if matches(d, query)), None)
        if found is None:
            if not upsert:
                return
            if "_id" in query and any(d.get("_id") == query["_id"] for d in self.docs):
                raise DuplicateKeyError("duplicate key")
            found = {k: v for k, v in query.items() if not k.startswith("$")}
            self.docs.append(found)
        found.update(update.get("$set", {}))
        for key, amount in update.get("$inc", {}).items():
            found[key] = found.get(key, 0) + amount

    def find_one_and_update(self, query, update, upsert=False, return_document=False):
        self.update_one(query, update, upsert)
        return self.find_one({k: v for k, v in query.items() if k != "$or"})

    def delete_one(self, query):
        found = next((d for d in self.docs if matches(d, query)), None)
        if found is not None:
            self.docs.remove(found)
        return SimpleNamespace(deleted_count=int(found is not None))

    def delete_many(self, query):
        self.docs = [d for d in self.docs if not matches(d, query)]

    def aggregate(self, pipeline):
        docs = self.find(pipeline[0]["$match"])
        if not docs:
            return []
        result = {"_id": None}
        for name, expr in pipeline[1]["$group"].items():
            if name == "_id":
                continue
            result[name] = (len(docs) if "$sum" in expr else
                            sum(d["rating"] for d in docs) / len(docs))
        return [result]


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.db = defaultdict(Collection)
        self.button = {"id": 12, "type": "content", "label": "ملزمة الفيزياء",
                       "unified_rating": 1}
        self.item = {"id": 22, "button_id": 12, "type": "file", "file_id": "test-pdf"}
        self.selected = {"button": self.button, "item": self.item, "index": 0,
                         "record": {"label": "ملزمة الفيزياء"}}
        self.db["btn_twins"].insert_one({"a": 10, "b": 12})
        self.db["item_twins"].insert_one({"a": 20, "b": 22})
        for mocked in [
            patch.dict(os.environ, {"MONGODB_URI": "mongodb://unused.invalid/test",
                                    "SESSION_SECRET": "test-key-only"}),
            patch.object(web, "_col", side_effect=lambda name: self.db[name]),
            patch.object(web, "_find_visible_attachment",
                         side_effect=lambda fid: self.selected if fid == "test-pdf" else None),
            patch.object(web, "_similar_attachments", return_value=[]),
        ]:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.app = web.create_app()
        self.app.testing = True
        self.client = self.app.test_client()

    def open(self, client=None):
        return (client or self.client).get("/attachment/test-pdf")

    def post(self, action, client=None, **data):
        client = client or self.client
        with client.session_transaction() as state:
            data.setdefault("csrf_token", state.get("feedback_csrf", ""))
        return client.post(f"/feedback/attachment/test-pdf/{action}", data=data)

    def bot(self):
        from bot import data_access
        return data_access

    def test_rating_from_website_is_readable_by_bot_and_uses_canonical_button(self):
        self.open()
        response = self.post("rate", rating="5")
        self.assertEqual(response.status_code, 303)
        with patch.object(self.bot(), "_col", side_effect=lambda name: self.db[name]):
            self.assertEqual(self.bot().get_btn_rating_summary(12), {"count": 1, "avg": 5.0})
        record = self.db["button_ratings"].docs[0]
        self.assertEqual(record["button_id"], 10)
        self.assertLess(record["user_id"], 0)
        self.assertLess(abs(record["user_id"]), 2**63)

    def test_bot_comment_and_rating_are_visible_on_website(self):
        with patch.object(self.bot(), "_col", side_effect=lambda name: self.db[name]), \
                patch.object(self.bot(), "get_mongo_db", return_value=self.db):
            self.bot().save_btn_rating(12, 123, 4)
            self.bot().save_comment("btn", 12, 123, "قارئ من البوت", "تعليق البوت")
        page = self.open().get_data(as_text=True)
        self.assertIn("تعليق البوت", page)
        self.assertIn("قارئ من البوت", page)
        self.assertIn("4.0", page)
        self.assertNotIn("حذف تعليقي", page)

    def test_guest_comment_uses_bot_counter_and_is_readable_by_bot(self):
        self.db["_counters"].insert_one({"_id": "comments", "seq": 44})
        self.open()
        self.post("comment", display_name="زائر", text="تعليق الموقع")
        with patch.object(self.bot(), "_col", side_effect=lambda name: self.db[name]):
            comments = self.bot().get_comments("btn", 12)
        self.assertEqual(comments[0]["id"], 45)
        self.assertEqual(comments[0]["text"], "تعليق الموقع")
        self.assertEqual(comments[0]["display_name"], "زائر")

    def test_non_unified_file_uses_item_ratings_and_comments_with_twins(self):
        self.button["unified_rating"] = 0
        self.open()
        self.post("rate", rating="3")
        self.post("comment", display_name="قارئ", text="تعليق ملف")
        with patch.object(self.bot(), "_col", side_effect=lambda name: self.db[name]):
            self.assertEqual(self.bot().get_item_rating_summary(22), {"count": 1, "avg": 3.0})
            self.assertEqual(self.bot().get_comments("item", 22)[0]["target_id"], 20)
        self.assertFalse(self.db["button_ratings"].docs)

    def test_csrf_invalid_rating_and_empty_name_cannot_write(self):
        self.open()
        for values in [{"rating": "5", "csrf_token": "invalid"},
                       {"rating": "5", "csrf_token": "غير صالح"},
                       {"rating": "6"}, {"rating": "bad"}]:
            self.post("rate", **values)
        self.post("comment", display_name=" ", text="نص")
        self.post("comment", display_name="اسم", text=" ")
        self.post("comment", display_name="اسم", text="x" * 2001)
        self.assertFalse(self.db["button_ratings"].docs)
        self.assertFalse(self.db["comments"].docs)

    def test_one_comment_per_guest_and_rating_can_be_updated(self):
        self.open()
        self.post("comment", display_name="اسم", text="أول")
        self.post("comment", display_name="اسم", text="ثاني")
        self.assertEqual(len(self.db["comments"].docs), 1)
        self.post("rate", rating="2")
        self.db["website_feedback_limits"].docs.clear()
        self.post("rate", rating="5")
        self.assertEqual(len(self.db["button_ratings"].docs), 1)
        self.assertEqual(self.db["button_ratings"].docs[0]["rating"], 5)

    def test_rate_limit_blocks_rapid_submissions(self):
        self.open()
        self.post("rate", rating="2")
        response = self.post("rate", rating="5")
        self.assertEqual(self.db["button_ratings"].docs[0]["rating"], 2)
        self.assertEqual(response.status_code, 400)
        self.assertIn("انتظر قليلاً", response.get_data(as_text=True))

    def test_guests_are_separate_and_only_owner_can_delete(self):
        self.open()
        self.post("comment", display_name="اسم", text="تعليقي")
        cid = self.db["comments"].docs[0]["id"]
        other = self.app.test_client()
        self.open(other)
        self.post("delete", other, comment_id=cid)
        self.assertEqual(len(self.db["comments"].docs), 1)
        self.post("delete", comment_id=cid)
        self.assertFalse(self.db["comments"].docs)

    def test_html_escaped_cookie_persistent_and_detail_not_cached(self):
        response = self.open()
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("Expires=", response.headers["Set-Cookie"])
        self.assertIn("Secure", response.headers["Set-Cookie"])
        self.assertIn("SameSite=None", response.headers["Set-Cookie"])
        self.assertIn("Partitioned", response.headers["Set-Cookie"])
        self.post("comment", display_name="<script>name</script>", text="<script>alert(1)</script>")
        page = self.open().get_data(as_text=True)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("تحميل من التلكرام", page)

    def test_missing_attachment_unknown_action_and_database_failure(self):
        self.assertEqual(self.client.post("/feedback/attachment/missing/rate").status_code, 404)
        self.assertEqual(self.post("invalid").status_code, 404)
        self.open()
        with patch.object(feedback, "submit", side_effect=PyMongoError("test only")):
            response = self.post("rate", rating="5")
        self.assertEqual(response.status_code, 503)
        self.assertIn("تعذّر حفظ المشاركة", response.get_data(as_text=True))

    def test_no_cookie_submission_shows_error_without_silent_redirect(self):
        page = self.open().get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        no_cookies = self.app.test_client(use_cookies=False)
        response = no_cookies.post("/feedback/attachment/test-pdf/comment", data={
            "csrf_token": csrf, "display_name": "قارئ", "text": "لن يُحفظ",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("انتهت صلاحية النموذج", response.get_data(as_text=True))
        self.assertNotIn("Location", response.headers)
        self.assertFalse(self.db["comments"].docs)

    def test_real_search_projection_old_feedback_and_both_submission_directions(self):
        """Do NOT stub the index/lookup: this is where production lost its flag."""
        self.button["parent_id"] = 1
        self.db["buttons"].insert_one({"id": 1, "parent_id": None, "type": "menu",
                                      "label": "السادس العلمي"})
        self.db["buttons"].insert_one(self.button)
        self.db["content_items"].insert_one(self.item)
        with patch.object(self.bot(), "_col", side_effect=lambda name: self.db[name]), \
                patch.object(self.bot(), "get_mongo_db", return_value=self.db):
            self.bot().save_btn_rating(12, 123, 4)
            self.bot().save_comment("btn", 12, 123, "قارئ البوت", "تعليق سابق من البوت")
        with patch.object(web, "_find_visible_attachment", REAL_ATTACHMENT_LOOKUP), \
                patch.object(web, "_search_index_cache", {"expires": 0, "records": None}):
            selected = web._find_visible_attachment("test-pdf")
            self.assertEqual(selected["button"]["unified_rating"], 1)
            page = self.open().get_data(as_text=True)
            self.assertIn("تعليق سابق من البوت", page)
            self.assertIn("4.0", page)
            # Extract the actual form token, rather than inventing session state.
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
            for action, values in [
                ("rate", {"rating": "5"}),
                ("comment", {"display_name": "قارئ الموقع", "text": "تعليق جديد من الموقع"}),
            ]:
                response = self.client.post(
                    f"/feedback/attachment/test-pdf/{action}",
                    data={**values, "csrf_token": csrf}, follow_redirects=True,
                )
                self.assertEqual(response.status_code, 200)
            with patch.object(self.bot(), "_col", side_effect=lambda name: self.db[name]):
                self.assertEqual(self.bot().get_btn_rating_summary(12),
                                 {"count": 2, "avg": 4.5})
                comments = self.bot().get_comments("btn", 12)
            self.assertEqual({c["text"] for c in comments},
                             {"تعليق سابق من البوت", "تعليق جديد من الموقع"})
            self.assertFalse(self.db["item_ratings"].docs)


if __name__ == "__main__":
    unittest.main()