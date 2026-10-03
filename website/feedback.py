"""Website guest feedback using the bot's existing MongoDB record formats."""
import secrets
import time
from datetime import datetime, timezone

from flask import session
from pymongo.errors import DuplicateKeyError


def guest():
    # Telegram users have positive IDs. Signed cookies cannot select a bot identity.
    if "feedback_guest" not in session:
        session["feedback_guest"] = -(secrets.randbits(62) + 1)
        session.permanent = True
    if "feedback_csrf" not in session:
        session["feedback_csrf"] = secrets.token_urlsafe(32)
    return session["feedback_guest"]


def canonical_id(col, kind, target_id):
    twins = col("item_twins" if kind == "item" else "btn_twins")
    pair = twins.find_one({"$or": [{"a": target_id}, {"b": target_id}]})
    return min(pair["a"], pair["b"]) if pair else target_id


def target(button, item=None):
    if item and not button.get("unified_rating", 0):
        return "item", item["id"]
    return "btn", button["id"]


def rating_summary(col, kind, target_id):
    target_id = canonical_id(col, kind, target_id)
    field = "item_id" if kind == "item" else "button_id"
    records = list(col("item_ratings" if kind == "item" else "button_ratings").aggregate([
        {"$match": {field: target_id}},
        {"$group": {"_id": None, "count": {"$sum": 1}, "avg": {"$avg": "$rating"}}},
    ]))
    count = records[0]["count"] if records else 0
    avg = round(float(records[0].get("avg") or 0), 1) if records else 0.0
    return {"count": count, "avg": avg, "stars": "★" * round(avg) + "☆" * (5 - round(avg))}


def context(col, button, item=None):
    uid = guest()
    kind, tid = target(button, item)
    tid = canonical_id(col, kind, tid)
    collection = "item_ratings" if kind == "item" else "button_ratings"
    field = "item_id" if kind == "item" else "button_id"
    own_rating = col(collection).find_one({field: tid, "user_id": uid})
    comments = list(col("comments").find({"target_type": kind, "target_id": tid}))
    comments.sort(key=lambda c: (c.get("likes", 0) + c.get("dislikes", 0),
                                 c.get("created_at", 0)), reverse=True)
    for comment in comments:
        comment["owned"] = comment.get("user_id") == uid
        comment["created_label"] = datetime.fromtimestamp(
            comment.get("created_at", 0), timezone.utc
        ).strftime("%Y/%m/%d")
    return {
        "rating": rating_summary(col, kind, tid),
        "user_rating": own_rating.get("rating") if own_rating else None,
        "comments": comments,
        "guest_name": session.get("feedback_name", ""),
        "shared": bool(item and button.get("unified_rating", 0)),
    }


def _throttle(col, uid, action):
    now = time.time()
    delay = 3 if action == "rate" else 20
    try:
        col("website_feedback_limits").find_one_and_update(
            {"_id": f"{uid}:{action}", "$or": [
                {"until": {"$lte": now}}, {"until": {"$exists": False}},
            ]},
            {"$set": {"until": now + delay}},
            upsert=True,
        )
    except DuplicateKeyError:
        raise ValueError("انتظر قليلاً قبل إرسال مشاركة أخرى.") from None


def submit(col, button, item, action, form):
    uid = guest()
    token = form.get("csrf_token", "")
    if not isinstance(token, str) or not secrets.compare_digest(
        token.encode("utf-8"), session["feedback_csrf"].encode("utf-8")
    ):
        raise ValueError("انتهت صلاحية النموذج. حدّث الصفحة وحاول مرة أخرى.")
    kind, tid = target(button, item)
    tid = canonical_id(col, kind, tid)
    key = {"target_type": kind, "target_id": tid, "user_id": uid}
    now = int(time.time())
    if action == "rate":
        try:
            rating = int(form.get("rating", ""))
        except (ValueError, TypeError):
            raise ValueError("اختر تقييماً من 1 إلى 5.") from None
        if not 1 <= rating <= 5:
            raise ValueError("اختر تقييماً من 1 إلى 5.")
        _throttle(col, uid, action)
        field = "item_id" if kind == "item" else "button_id"
        col("item_ratings" if kind == "item" else "button_ratings").update_one(
            {field: tid, "user_id": uid},
            {"$set": {field: tid, "user_id": uid, "rating": rating, "rated_at": now}},
            upsert=True,
        )
        return "تم حفظ تقييمك."
    if action == "comment":
        name = form.get("display_name", "").strip()
        text = form.get("text", "").strip()
        if not name or len(name) > 80:
            raise ValueError("اكتب اسمك، بحد أقصى 80 حرفاً.")
        if not text or len(text) > 2000:
            raise ValueError("اكتب تعليقاً، بحد أقصى 2000 حرف.")
        if col("comments").find_one(key):
            raise ValueError("لديك تعليق بالفعل. احذفه أولاً إذا تريد كتابة تعليق آخر.")
        _throttle(col, uid, action)
        # Use the SAME atomic counter as the bot, not a website-local sequence.
        counter = col("_counters").find_one_and_update(
            {"_id": "comments"}, {"$inc": {"seq": 1}}, upsert=True, return_document=True,
        )
        try:
            col("comments").insert_one({
                **key, "id": counter["seq"], "display_name": name, "text": text,
                "likes": 0, "dislikes": 0, "created_at": now,
            })
        except DuplicateKeyError:
            raise ValueError("لديك تعليق بالفعل. حدّث الصفحة لعرضه.") from None
        session["feedback_name"] = name
        return "تم نشر تعليقك."
    if action == "delete":
        try:
            cid = int(form.get("comment_id", ""))
        except (ValueError, TypeError):
            raise ValueError("التعليق غير موجود.") from None
        deleted = col("comments").delete_one({**key, "id": cid})
        if not deleted.deleted_count:
            raise ValueError("لا يمكنك حذف هذا التعليق.")
        col("comment_reactions").delete_many({"comment_id": cid})
        return "تم حذف تعليقك."
    raise ValueError("طلب غير صالح.")