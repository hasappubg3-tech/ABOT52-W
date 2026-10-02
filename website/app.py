"""
الموقع الإلكتروني لشبكة الامير التعليمية
Flask app — يعمل في process منفصل بجانب البوت
"""
import os
import re
import time
import logging
import unicodedata
from threading import Lock
import requests as _req
from flask import Flask, render_template, jsonify, request, redirect, abort, url_for, Response

BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Mdry7bot")
SITE_NAME    = "شبكة الامير التعليمية"
SITE_URL     = os.environ.get("SITE_URL", "").rstrip("/")

# ── إزالة الإيموجيات ────────────────────────────────────────────────
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U0001F1E6-\U0001F1FF"  # regional indicator flags
    "\U00002702-\U000027B0"
    "\U0000FE0F"   # variation selector
    "\U0000200D"   # zero width joiner
    "\U000020E3"   # combining enclosing keycap (4️⃣ 5️⃣ …)
    "\U00002640-\U00002642"
    "\U00002600-\U00002B55"
    "\u00a9\u00ae"  # © ®
    "]+",
    flags=re.UNICODE,
)

def strip_emoji(text) -> str:
    if not text:
        return ""
    return _EMOJI_RE.sub("", str(text)).strip()

NEW_DAYS        = 14      # عدد الأيام لاعتبار الملزمة "جديدة"
_YEAR_RE = re.compile(r"(?<!\d)20\d{2}(?!\d)")
_DIGIT_TRANSLATION = str.maketrans({
    **{chr(0x0660 + digit): str(digit) for digit in range(10)},
    **{chr(0x06F0 + digit): str(digit) for digit in range(10)},
})
_TYPE_PRIORITY = [
    (("ملزمة", "ملزمه", "ملزم"), 0),
    (("واجبات", "واجب"), 1),
    (("وزاريات", "وزارية", "وزاري"), 2),
    (("مراجعة", "مراجعات"), 3),
]


def _compound_sort_key(button: dict) -> tuple:
    """يطابق ترتيب البوت: أحدث سنة أولاً، ثم أولوية نوع المحتوى في اسم الزر."""
    label = str(button.get("label") or "")
    years = _year_candidates(label)
    year = max((int(value) for value in years), default=0)
    type_priority = 99
    for keywords, priority in _TYPE_PRIORITY:
        if any(keyword in label for keyword in keywords):
            type_priority = priority
            break
    return -year, type_priority

# ── الأنواع المسموح بها ──────────────────────────────────────────────
# حرف التطويل العربي (kashida) — يُزال قبل مقارنة التسميات
_TATWEEL = "\u0640"

def _clean(label: str) -> str:
    """يُزيل التطويل والإيموجي ليتبقى النص العربي الصريح."""
    return str(label or "").replace(_TATWEEL, "")

# كلمات تدل على قائمة ملازم/كتب/ملخصات (للقوائم الفرعية)
_MENU_WHITELIST = ["ملازم", "ملزمة", "ملزمه", "كتب", "كتاب", "ملخص", "ملخصات"]
# كلمات تدل على صف دراسي (للقوائم الجذرية)
_GRADE_KEYWORDS = ["أول", "ثاني", "ثالث", "رابع", "خامس", "سادس", "سابع", "ثامن",
                   "اول", "متوسط", "إعدادي", "اعدادي", "ابتدائي",
                   "العلمي", "الأدبي", "الادبي"]
# كلمات تدل على محتوى مسموح به (للأزرار من نوع content)
_CONTENT_WHITELIST = ["ملزمة", "ملزمه", "كتاب", "ملخص"]

def _is_grade_menu(btn: dict) -> bool:
    """يُعيد True إذا كانت القائمة تمثّل صفاً دراسياً."""
    label = _clean(btn.get("label", ""))
    return any(kw in label for kw in _GRADE_KEYWORDS)

def _is_allowed_menu(btn: dict) -> bool:
    """يُعيد True إذا كانت القائمة الفرعية تنتمي لملازم/كتب/ملخصات فقط."""
    label = _clean(btn.get("label", ""))
    return any(kw in label for kw in _MENU_WHITELIST)

def _is_allowed_content(btn: dict) -> bool:
    """يُعيد True فقط إذا كانت التسمية تنتمي لملزمة أو كتاب أو ملخص."""
    label = _clean(btn.get("label", ""))
    return any(kw in label for kw in _CONTENT_WHITELIST)
_PDF_THUMB_DIR  = "/tmp/pdf_thumbs"
_PDF_THUMB_TTL  = 86400   # يوم كامل

# ── Cache بسيط (file_id / bid -> (url|None, timestamp)) ──────────
_file_url_cache: dict = {}
_FILE_URL_TTL = 3600  # ساعة واحدة
_SEARCH_INDEX_TTL = 30
_search_index_cache = {"expires": 0.0, "records": None}
_search_index_lock = Lock()


def _get_mongo():
    from bot.data_access import get_mongo_db
    return get_mongo_db()


def _col(name: str):
    return _get_mongo()[name]


# ─────────────────────────────────────────────────────────────────────
# مساعدات Telegram API
# ─────────────────────────────────────────────────────────────────────

def _file_url(file_id: str) -> str | None:
    """يجلب رابط ملف من Telegram API ويخزنه مؤقتاً."""
    if not file_id or not BOT_TOKEN:
        return None
    now = time.time()
    cached = _file_url_cache.get(file_id)
    if cached:
        url, ts = cached
        if now - ts < _FILE_URL_TTL:
            return url

    try:
        resp = _req.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=6,
        )
        data = resp.json()
        if data.get("ok"):
            fp = data["result"]["file_path"]
            url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}"
            _file_url_cache[file_id] = (url, now)
            return url
    except Exception as e:
        logging.debug(f"[file_url] error={e}")

    _file_url_cache[file_id] = (None, now)
    return None


# ─────────────────────────────────────────────────────────────────────
# مساعدات البيانات
# ─────────────────────────────────────────────────────────────────────

def _btn(bid: int):
    return _col("buttons").find_one({"id": bid, "deleted": {"$ne": 1}, "hidden": {"$ne": 1}})


def _has_visible_ancestors(btn: dict, ancestors: dict) -> bool:
    """يتأكد أن زر المحتوى ينتمي إلى مسار ظاهر في الموقع، لا إلى سجل يتيم."""
    parent_id = btn.get("parent_id")
    visited = set()
    while parent_id is not None:
        if parent_id in visited:
            return False
        visited.add(parent_id)
        parent = ancestors.get(parent_id)
        if not parent:
            return False
        parent_id = parent.get("parent_id")
    return True


def _children(pid):
    q = {"deleted": {"$ne": 1}, "hidden": {"$ne": 1}}
    if pid is None:
        q["parent_id"] = None
    else:
        q["parent_id"] = pid
    return list(_col("buttons").find(q).sort([("ord", 1), ("id", 1)]))


def _items(bid: int):
    return list(_col("content_items").find({"button_id": bid}).sort([("ord", 1), ("id", 1)]))


def _rating(bid: int) -> dict:
    pipeline = [
        {"$match": {"button_id": bid}},
        {"$group": {"_id": None, "cnt": {"$sum": 1}, "avg": {"$avg": "$rating"}}}
    ]
    result = list(_col("button_ratings").aggregate(pipeline))
    if not result:
        return {"count": 0, "avg": 0.0, "stars": ""}
    r = result[0]
    avg = float(r.get("avg") or 0)
    full  = int(avg)
    half  = 1 if (avg - full) >= 0.5 else 0
    empty = 5 - full - half
    stars = "★" * full + ("½" if half else "") + "☆" * empty
    return {"count": r["cnt"], "avg": round(avg, 1), "stars": stars}


def _breadcrumb(bid: int) -> list:
    path = []
    current = bid
    while current is not None:
        doc = _col("buttons").find_one({"id": current})
        if not doc:
            break
        path.append({"id": doc["id"], "label": doc.get("label", "")})
        current = doc.get("parent_id")
    path.reverse()
    return path


def _is_new(btn: dict) -> bool:
    created_at = btn.get("created_at")
    if not created_at:
        return False
    return time.time() - created_at < NEW_DAYS * 86400


def _timestamp_value(value) -> float:
    if hasattr(value, "timestamp"):
        try:
            return float(value.timestamp())
        except (OSError, OverflowError, ValueError):
            return 0
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def _content_item_timestamp(item: dict) -> float:
    object_id = item.get("_id")
    if hasattr(object_id, "generation_time"):
        return _timestamp_value(object_id.generation_time)
    return _timestamp_value(item.get("created_at"))


def _item_sort_key(item: dict) -> tuple:
    return (
        _content_item_timestamp(item),
        str(item.get("_id") or item.get("id") or ""),
    )


def _has_content_media(bid: int) -> bool:
    """هل يوجد صورة أو PDF لهذا الزر؟"""
    return bool(_col("content_items").find_one(
        {"button_id": bid, "type": {"$in": ["photo", "document", "file"]}}
    ))


def _pdf_thumbnail(bid: int) -> bytes | None:
    """يولّد صورة JPEG من أول صفحة PDF ويخزّنها مؤقتاً."""
    os.makedirs(_PDF_THUMB_DIR, exist_ok=True)
    cache_path = f"{_PDF_THUMB_DIR}/{bid}.jpg"
    # استخدم الكاش إذا كان حديثاً
    try:
        if os.path.exists(cache_path) and time.time() - os.path.getmtime(cache_path) < _PDF_THUMB_TTL:
            with open(cache_path, "rb") as f:
                return f.read()
    except OSError:
        pass
    # ابحث عن ملف PDF (النوع قد يكون "document" أو "file")
    item = _col("content_items").find_one(
        {"button_id": bid, "type": {"$in": ["document", "file"]}},
        sort=[("ord", 1), ("id", 1)]
    )
    if not item or not item.get("file_id"):
        return None
    pdf_url = _file_url(item["file_id"])
    if not pdf_url:
        return None
    try:
        resp = _req.get(pdf_url, timeout=30)
        if resp.status_code != 200:
            return None
        import fitz  # PyMuPDF
        doc = fitz.open(stream=resp.content, filetype="pdf")
        if doc.page_count == 0:
            return None
        page = doc[0]
        mat = fitz.Matrix(1.5, 1.5)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("jpeg")
        with open(cache_path, "wb") as f:
            f.write(img_bytes)
        return img_bytes
    except Exception as e:
        logging.warning(f"[pdf_thumb bid={bid}] {e}")
        return None


def _parse_content_lines(bid: int, items: list | None = None) -> dict:
    """
    يجمع وصف ملفات الملزمة ويستخرج منه:
      - title  : السطر الأول (نوع + مادة + جزء …)
      - teacher: ما بعد "للاستاذ"
      - year   : سنة الإصدار من الوصف أو تسمية الزر
    """
    if items is None:
        items = _items(bid)
    lines = _content_metadata_lines(items)
    teacher = _teacher_from_lines(lines)
    year = _release_year(lines)
    return {
        "title": lines[0] if lines else "",
        "teacher": teacher,
        "year": year,
    }


def _content_metadata_lines(items: list) -> list:
    """يجمع أسطر الوصف والعنوان من كل عناصر الملزمة بترتيبها."""
    lines = []
    for item in items:
        for field in ("content", "caption", "file_name", "filename", "name"):
            raw = item.get(field)
            if not raw:
                continue
            for line in str(raw).splitlines():
                clean = strip_emoji(re.sub(r"[|★☆]+", "", line)).strip()
                clean = clean.strip(" |–-")
                if clean:
                    lines.append(clean)
    return lines


def _teacher_from_lines(lines: list) -> str:
    for line in lines:
        match = re.search(
            r"(?:للاستاذ|للأستاذ|الأستاذ|الاستاذ)\s*[:：]?\s*(.+)",
            line,
        )
        if match:
            return strip_emoji(match.group(1)).replace("|", "").strip()
    return ""


def _year_candidates(text) -> list:
    normalized = str(text or "").translate(_DIGIT_TRANSLATION)
    return _YEAR_RE.findall(normalized)


def _release_year(lines: list, fallback_text: str = "") -> str:
    """يفضّل سنة الإصدار الصريحة، ثم يستعمل أحدث سنة متاحة في وصف الملزمة."""
    explicit_years = []
    for line in lines:
        normalized = _normalize_search_text(line)
        if any(marker in normalized for marker in (
            "سنة الاصدار", "تاريخ الاصدار", "الاصدار", "اصدار",
            "لسنة", "للسنة", "للعام", "عام",
        )):
            explicit_years.extend(_year_candidates(line))
    years = explicit_years or _year_candidates(" ".join(lines))
    years.extend(_year_candidates(fallback_text))
    return max(years, default="")


def _append_year(title: str, year: str) -> str:
    title = str(title or "").strip()
    year = str(year or "")
    if year and year not in str(title).translate(_DIGIT_TRANSLATION):
        return f"{title} {year}".strip()
    return title


def _note_display_name(btn: dict, items: list | None = None) -> str:
    """يبني اسم الملزمة من وصفها، ويضمّن سنة الإصدار في العنوان."""
    if items is None:
        items = _items(btn["id"])
    info = _parse_content_lines(btn["id"], items)

    title   = info.get("title", "")
    teacher = info.get("teacher", "")
    year    = _release_year(
        _content_metadata_lines(items),
        btn.get("label", ""),
    )

    # إذا لم يوجد عنوان من المحتوى، نرجع للتسمية الاحتياطية
    if not title:
        title = strip_emoji(btn.get("label", ""))

    parts = [title]
    if teacher:
        parts.append(f"للاستاذ {teacher}")
    display_name = " ".join(parts)
    display_name = _append_year(display_name, year)

    return display_name


def _attachment_title(item: dict, index: int) -> str:
    """يستخرج عنوان الفصل أو الملف من الوصف المخزّن مع مرفق البوت."""
    title = ""
    for line in _content_metadata_lines([item]):
        title = re.sub(r"^[\s|:;،\-–—]+", "", line)
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            break
    year = _release_year(_content_metadata_lines([item]))
    return _append_year(title or f"الملف {index}", year)[:160]


def _file_items(items: list) -> list:
    return [
        (index, item)
        for index, item in enumerate(
            (item for item in items
             if item.get("type") in {"document", "file"} and item.get("file_id")),
            start=1,
        )
    ]


def _attachment_display_title(item: dict, index: int, group_label: str = "") -> str:
    title = _attachment_title(item, index)
    if title == f"الملف {index}":
        title = strip_emoji(group_label) or title
    # سنة الملف نفسه لها الأولوية؛ سنة زر البوت ليست سنة كل ملف تحته.
    year = _release_year(_content_metadata_lines([item])) or _release_year([], group_label)
    return _append_year(title, year)


def _independent_notes(btn: dict, items: list | None = None) -> list:
    """عرض ملفات زر البوت كبطاقات مستقلة في الموقع دون تغيير بيانات البوت."""
    if items is None:
        items = _items(btn["id"])
    files = _file_items(items)
    if not files:
        return [{**_enrich(btn), "is_attachment": False, "url": f"/note/{btn['id']}"}]

    cards = []
    seen = set()
    for index, item in files:
        file_id = item["file_id"]
        if file_id in seen:
            continue
        seen.add(file_id)
        added_at = _content_item_timestamp(item) or _timestamp_value(btn.get("created_at"))
        cards.append({
            "id": btn["id"],
            "file_id": file_id,
            "url": f"/attachment/{file_id}",
            "display_label": _attachment_display_title(item, index, btn.get("label", "")),
            "subtitle": _attachment_search_subtitle(item, btn.get("label", "")),
            "thumb_url": False,
            "is_new": bool(added_at and time.time() - added_at < NEW_DAYS * 86400),
            "click_count": 0,
            "is_attachment": True,
        })
    return cards


def _find_visible_attachment(file_id: str) -> dict | None:
    for record in _search_index_records():
        for index, item in _file_items(record["items"]):
            if item.get("file_id") == file_id:
                return {
                    "record": record,
                    "button": record["button"],
                    "item": item,
                    "index": index,
                }
    return None


def _storage_message_url(item: dict) -> str:
    message_id = item.get("channel_msg_id")
    if not message_id:
        return ""
    try:
        from bot.data_access import get_storage_channel_id
        channel_id = str(get_storage_channel_id() or "").strip()
    except Exception:
        return ""

    if channel_id.startswith("@"):
        return f"https://t.me/{channel_id[1:]}/{message_id}"
    if channel_id.startswith("-100") and channel_id[4:].isdigit():
        return f"https://t.me/c/{channel_id[4:]}/{message_id}"
    return ""


_SIMILAR_TITLE_STOPWORDS = {
    "ملزمة", "ملازم", "ملخص", "ملخصات", "واجب", "واجبات",
    "وزاريات", "وزاري", "الفصل", "فصل", "الجزء", "جزء",
    "سنة", "الاصدار", "اصدار",
}


def _attachment_topic_tokens(title: str) -> set:
    return {
        token for token in _search_tokens(title)
        if token not in _SIMILAR_TITLE_STOPWORDS and not token.isdigit()
    }


def _similar_attachments(selected: dict, limit: int = 6) -> list:
    selected_button = selected["button"]
    selected_item = selected["item"]
    selected_file_id = selected_item.get("file_id")
    selected_title = _attachment_display_title(
        selected_item, selected["index"], selected_button.get("label", "")
    )
    selected_topics = _attachment_topic_tokens(selected_title)
    selected_teacher = _normalize_search_text(
        _teacher_from_lines(_content_metadata_lines([selected_item]))
    )
    candidates = []

    for record in _search_index_records():
        button = record["button"]
        for index, item in _file_items(record["items"]):
            file_id = item.get("file_id")
            if not file_id or file_id == selected_file_id:
                continue

            title = _attachment_display_title(item, index, record["label"])
            teacher = _normalize_search_text(
                _teacher_from_lines(_content_metadata_lines([item]))
            )
            same_button = button.get("id") == selected_button.get("id")
            same_teacher = bool(selected_teacher and teacher and selected_teacher == teacher)
            same_parent = (
                selected_button.get("parent_id") is not None
                and selected_button.get("parent_id") == button.get("parent_id")
            )
            shared_topics = len(selected_topics & _attachment_topic_tokens(title))
            if not (same_button or same_teacher or same_parent or shared_topics >= 2):
                continue

            added_at = _content_item_timestamp(item)
            candidates.append({
                "file_id": file_id,
                "url": f"/attachment/{file_id}",
                "display_label": title,
                "subtitle": _attachment_search_subtitle(item, record["label"]),
                "is_new": bool(added_at and time.time() - added_at < NEW_DAYS * 86400),
                "_score": (same_button, same_teacher, same_parent, shared_topics),
                "_sort": _item_sort_key(item),
            })

    candidates.sort(
        key=lambda item: (item["_score"], item["_sort"]),
        reverse=True,
    )
    results = []
    seen_files = {selected_file_id}
    for candidate in candidates:
        if candidate["file_id"] in seen_files:
            continue
        seen_files.add(candidate["file_id"])
        candidate.pop("_score", None)
        candidate.pop("_sort", None)
        results.append(candidate)
        if len(results) >= limit:
            break
    return results


def _enrich(btn: dict) -> dict:
    bid = btn["id"]
    return {
        **btn,
        "rating":        _rating(bid),
        "is_new":        _is_new(btn),
        "thumb_url":     _has_content_media(bid),
        "click_count":   btn.get("click_count", 0),
        "display_label": _note_display_name(btn),
    }


def _normalize_search_text(value) -> str:
    """يوحّد اختلافات الكتابة العربية الشائعة قبل مطابقة كلمات البحث."""
    text = unicodedata.normalize("NFKC", strip_emoji(value or "")).replace(_TATWEEL, "")
    text = text.translate(_DIGIT_TRANSLATION)
    text = re.sub(r"[\u064b-\u065f\u0670]", "", text)
    text = text.translate(str.maketrans({
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
        "ى": "ي", "ک": "ك", "ی": "ي",
    }))
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _search_tokens(q: str) -> list:
    return list(dict.fromkeys(re.findall(r"[\w]+", _normalize_search_text(q))))


def _search_item_text(item: dict) -> str:
    fields = ("content", "caption", "file_name", "filename", "name")
    return " ".join(str(item.get(field) or "") for field in fields)


def _search_matches_normalized(normalized_text: str, tokens: list) -> bool:
    return all(token in normalized_text for token in tokens)


def _search_index_records() -> list:
    """يحمّل فهرس البحث مرة واحدة ويحدّثه دورياً لتبقى الطلبات الحية سريعة."""
    global _search_index_cache
    now = time.monotonic()
    if _search_index_cache["records"] is not None and now < _search_index_cache["expires"]:
        return _search_index_cache["records"]

    with _search_index_lock:
        now = time.monotonic()
        if _search_index_cache["records"] is not None and now < _search_index_cache["expires"]:
            return _search_index_cache["records"]

        visible_buttons = {
            doc["id"]: doc
            for doc in _col("buttons").find(
                {"deleted": {"$ne": 1}, "hidden": {"$ne": 1}},
                {"id": 1, "type": 1, "label": 1, "parent_id": 1,
                 "created_at": 1, "click_count": 1},
            )
        }
        items_by_button = {}
        for item in _col("content_items").find(
            {"button_id": {"$exists": True, "$ne": None}},
            {"button_id": 1, "id": 1, "type": 1, "file_id": 1, "content": 1,
             "caption": 1, "file_name": 1, "filename": 1, "name": 1, "ord": 1,
             "created_at": 1, "channel_msg_id": 1, "_id": 1},
        ).sort([("ord", 1), ("id", 1)]):
            items_by_button.setdefault(item.get("button_id"), []).append(item)

        allowed_terms = tuple(_normalize_search_text(word) for word in _CONTENT_WHITELIST)
        records = []
        for btn in sorted(visible_buttons.values(), key=lambda doc: str(doc.get("id", ""))):
            if btn.get("type") != "content" or not _has_visible_ancestors(btn, visible_buttons):
                continue

            items = items_by_button.get(btn["id"], [])
            label = str(btn.get("label") or "")
            normalized_label = _normalize_search_text(label)
            normalized_item_texts = [
                _normalize_search_text(_search_item_text(item))
                for item in items
            ]
            aggregate_normalized = " ".join(
                [normalized_label, *normalized_item_texts]
            )
            allowed = _is_allowed_content(btn) or any(
                term in aggregate_normalized for term in allowed_terms
            )
            if not allowed:
                continue

            records.append({
                "button": btn,
                "items": items,
                "label": label,
                "normalized_label": normalized_label,
                "normalized_item_texts": normalized_item_texts,
                "aggregate_normalized": aggregate_normalized,
            })

        _search_index_cache = {
            "expires": time.monotonic() + _SEARCH_INDEX_TTL,
            "records": records,
        }
        return records


def _attachment_search_subtitle(item: dict, group_label: str) -> str:
    raw = _search_item_text(item)
    clean_group = str(strip_emoji(group_label)).translate(_DIGIT_TRANSLATION)
    clean_group = re.sub(_YEAR_RE, "", clean_group)
    clean_group = re.sub(r"\s+", " ", clean_group).strip()
    parts = [clean_group] if clean_group else []
    teacher = ""
    for line in str(raw).splitlines():
        clean_line = strip_emoji(line)
        match = re.search(r"(?:للاستاذ|للأستاذ|الأستاذ|الاستاذ)\s*[:：]?\s*(.+)", clean_line)
        if match:
            teacher = match.group(1).translate(_DIGIT_TRANSLATION)
            teacher = re.sub(_YEAR_RE, "", teacher)
            teacher = re.sub(r"\s+", " ", teacher).strip(" |:،")
            if teacher:
                if _normalize_search_text(teacher) not in _normalize_search_text(group_label):
                    parts.append(f"الأستاذ {teacher}")
                break
    return " · ".join(dict.fromkeys(part for part in parts if part))


def _search_note_display_name(btn: dict, items: list) -> str:
    """يبني عنوان نتيجة البحث من أول وصف ملف محمّل مسبقاً دون استعلام إضافي."""
    return _note_display_name(btn, items)


def _search_content(q: str, limit: int = 50) -> list:
    tokens = _search_tokens(q)
    if not tokens:
        return []

    results = []
    seen_files = set()
    seen_notes = set()
    normalized_query = " ".join(tokens)

    for record in _search_index_records():
        btn = record["button"]
        bid = btn["id"]
        items = record["items"]
        label = record["label"]
        normalized_label = record["normalized_label"]
        if not _search_matches_normalized(record["aggregate_normalized"], tokens):
            continue

        label_match = _search_matches_normalized(normalized_label, tokens)
        exact_label_match = normalized_query in normalized_label
        file_items = [
            (file_index, item, record["normalized_item_texts"][item_index])
            for file_index, (item_index, item) in enumerate(
                (
                    (item_index, item)
                    for item_index, item in enumerate(items)
                    if item.get("type") in {"document", "file"} and item.get("file_id")
                ),
                start=1,
            )
        ]
        matching_files = [
            (index, item, item_normalized)
            for index, item, item_normalized in file_items
            if _search_matches_normalized(
                f"{normalized_label} {item_normalized}",
                tokens,
            )
        ]
        selected_files = file_items if label_match else matching_files

        if selected_files:
            for index, item, _item_normalized in selected_files:
                file_id = item["file_id"]
                if file_id in seen_files:
                    continue
                seen_files.add(file_id)
                display_label = _attachment_display_title(item, index, label)
                years = _year_candidates(display_label)
                results.append({
                    "id": bid,
                    "url": f"/attachment/{file_id}",
                    "display_label": display_label,
                    "subtitle": _attachment_search_subtitle(item, label),
                    "thumb_url": False,
                    "is_new": False,
                    "click_count": 0,
                    "is_attachment": True,
                    "_rank": 0 if exact_label_match else (1 if label_match else 2),
                    "_year": max((int(year) for year in years), default=0),
                })
            continue

        if bid not in seen_notes:
            note = {
                **btn,
                "rating": {"count": 0, "avg": 0.0, "stars": ""},
                "is_new": _is_new(btn),
                "thumb_url": False,
                "click_count": btn.get("click_count", 0),
                "display_label": _search_note_display_name(btn, items),
                "url": f"/note/{bid}",
                "subtitle": "",
                "is_attachment": False,
                "_rank": 0 if exact_label_match else (1 if label_match else 2),
                    "_year": max(
                    (int(year) for year in _year_candidates(
                        " ".join([label] + [_search_item_text(item) for item in items])
                    )),
                    default=0,
                ),
            }
            results.append(note)
            seen_notes.add(bid)

    results.sort(key=lambda result: (result["_rank"], -result["_year"]))
    for result in results:
        result.pop("_rank", None)
        result.pop("_year", None)
    return results[:max(1, limit)]


def _latest_notes(limit: int = 8) -> list:
    """يعرض كل مرفق كإضافة مستقلة، لا كحزمة واحدة مرتبطة بزر البوت."""
    candidates = []
    for record in _search_index_records():
        btn = record["button"]
        items = record["items"]
        file_items = _file_items(items)

        if file_items:
            for index, item in file_items:
                added_at = _content_item_timestamp(item) or _timestamp_value(
                    btn.get("created_at")
                )
                candidates.append({
                    "id": btn["id"],
                    "file_id": item["file_id"],
                    "url": f"/attachment/{item['file_id']}",
                    "display_label": _attachment_display_title(
                        item, index, record["label"]
                    ),
                    "subtitle": _attachment_search_subtitle(item, record["label"]),
                    "thumb_url": False,
                    "is_new": bool(
                        added_at and time.time() - added_at < NEW_DAYS * 86400
                    ),
                    "click_count": 0,
                    "is_attachment": True,
                    "_dedupe_key": ("file", item["file_id"]),
                    "_sort": _item_sort_key(item),
                })
            continue

        item_added_at = max(
            (_content_item_timestamp(item) for item in items),
            default=0,
        )
        added_at = max(item_added_at, _timestamp_value(btn.get("created_at")))
        note_btn = {**btn, "created_at": added_at} if added_at else btn
        note = {
            **_enrich(note_btn),
            "url": f"/note/{btn['id']}",
            "subtitle": "",
            "is_attachment": False,
            "_dedupe_key": ("note", btn["id"]),
            "_sort": (added_at, str(btn["id"])),
        }
        candidates.append(note)

    candidates.sort(key=lambda item: item["_sort"], reverse=True)
    latest = []
    seen = set()
    for candidate in candidates:
        dedupe_key = candidate.pop("_dedupe_key")
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidate.pop("_sort", None)
        latest.append(candidate)
        if len(latest) >= limit:
            break
    return latest


# ─────────────────────────────────────────────────────────────────────
# Flask app factory
# ─────────────────────────────────────────────────────────────────────

def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("SESSION_SECRET", "alameer-secret")
    app.jinja_env.filters["strip_emoji"] = strip_emoji

    @app.before_request
    def check_database_configuration():
        if request.endpoint != "static" and not os.environ.get("MONGODB_URI"):
            return Response(
                "Website setup is incomplete: add MONGODB_URI in Replit Secrets, "
                "then restart the Website workflow. No educational content can "
                "be loaded until the existing MongoDB database is connected.",
                status=503,
                mimetype="text/plain",
            )

    # ── الصفحة الرئيسية ──────────────────────────────────────────────
    @app.route("/")
    def index():
        # الصفحة الرئيسية: فقط القوائم التي تمثّل صفوفاً دراسية
        categories = [c for c in _children(None)
                      if c.get("type") != "content" and _is_grade_menu(c)]
        latest     = _latest_notes(8)
        return render_template("index.html",
            categories=categories,
            latest=latest,
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            title=SITE_NAME,
            og_title=SITE_NAME,
            og_description="مكتبة ملازم وكتب دراسية مجانية لجميع الصفوف",
            og_url="/",
            og_image="",
        )

    # ── صفحة فئة / قسم ───────────────────────────────────────────────
    @app.route("/cat/<int:bid>")
    def category(bid: int):
        btn = _btn(bid)
        if not btn:
            abort(404)
        # الروابط القديمة لحزمة ملفات تعرض بطاقات مستقلة، لا صفحة ملزمة تجمعها.
        children = [btn] if btn.get("type") == "content" else _children(bid)
        if btn.get("type") == "compound" and btn.get("sort_by_year", 0):
            children = sorted(children, key=_compound_sort_key)
        # نُطبّق فلتر القوائم فقط إذا كنا مباشرةً داخل صف دراسي (parent_id=None)
        # في المستويات الأعمق (داخل ملازم/كتب/ملخصات) نعرض كل شيء
        if btn.get("parent_id") is None:
            menus = [c for c in children if c.get("type") != "content"
                     and _is_allowed_menu(c)]
        else:
            menus = [c for c in children if c.get("type") != "content"]
        contents = []
        seen_files = set()
        for child in children:
            if child.get("type") != "content":
                continue
            for card in _independent_notes(child):
                file_id = card.get("file_id")
                if file_id and file_id in seen_files:
                    continue
                if file_id:
                    seen_files.add(file_id)
                contents.append(card)
        breadcrumb = _breadcrumb(bid)
        return render_template("category.html",
            btn=btn,
            menus=menus,
            contents=contents,
            breadcrumb=breadcrumb,
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            title=f"{strip_emoji(btn.get('label',''))} — {SITE_NAME}",
            og_title=f"{strip_emoji(btn.get('label',''))} — {SITE_NAME}",
            og_description=f"تصفح ملازم وكتب {strip_emoji(btn.get('label',''))}",
            og_url=f"/cat/{bid}",
            og_image="",
        )

    # ── صفحة الملزمة ─────────────────────────────────────────────────
    @app.route("/note/<int:bid>")
    def note(bid: int):
        btn = _btn(bid)
        if not btn or btn.get("type") != "content":
            abort(404)
        items         = _items(bid)
        if _file_items(items):
            notes = _independent_notes(btn, items)
            if len(notes) == 1:
                return redirect(notes[0]["url"])
            return redirect(url_for("category", bid=bid))
        rating        = _rating(bid)
        breadcrumb    = _breadcrumb(bid)
        thumb         = _has_content_media(bid)
        display_label = _note_display_name(btn, items)

        # نص المقدمة: أول عنصر نصي
        preview_text = next(
            (it.get("content", "") for it in items if it.get("type") == "text" and it.get("content")),
            ""
        )
        preview_text = strip_emoji(preview_text)
        # كل الصور — بـ file_id مستقل لكل صورة (إصلاح الغاليري)
        photos = [
            {"file_id": it.get("file_id")}
            for it in items
            if it.get("type") == "photo" and it.get("file_id")
        ]
        # رابط deep-link للبوت لفتح الملزمة مباشرة
        bot_deep_link = f"https://t.me/{BOT_USERNAME}?start=btn_{bid}"

        return render_template("note.html",
            btn=btn,
            items=items,
            rating=rating,
            breadcrumb=breadcrumb,
            thumb=thumb,
            display_label=display_label,
            preview_text=preview_text,
            photos=photos,
            bot_deep_link=bot_deep_link,
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            title=f"{display_label} | {SITE_NAME}",
            og_title=f"{display_label} — {SITE_NAME}",
            og_description=preview_text[:160] if preview_text else f"ملزمة {display_label}",
            og_url=f"/note/{bid}",
            og_image="",
        )

    # ── صفحة ملف مستقل مع اقتراحات مشابهة ────────────────────────────
    @app.route("/attachment/<path:file_id>")
    def attachment_detail(file_id: str):
        selected = _find_visible_attachment(file_id)
        if not selected:
            abort(404)

        record = selected["record"]
        item = selected["item"]
        display_label = _attachment_display_title(
            item, selected["index"], record["label"]
        )
        subtitle = _attachment_search_subtitle(item, record["label"])
        similar = _similar_attachments(selected)
        file_available = bool(_file_url(file_id))
        channel_message_url = _storage_message_url(item)

        return render_template(
            "attachment.html",
            file_id=file_id,
            display_label=display_label,
            subtitle=subtitle,
            similar=similar,
            file_available=file_available,
            channel_message_url=channel_message_url,
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            title=f"{display_label} | {SITE_NAME}",
            og_title=f"{display_label} — {SITE_NAME}",
            og_description=subtitle or display_label,
            og_url=f"/attachment/{file_id}",
            og_image="",
        )

    # ── صفحة البحث ───────────────────────────────────────────────────
    @app.route("/search")
    def search():
        q       = request.args.get("q", "").strip()
        results = _search_content(q) if q else []
        return render_template("search.html",
            query=q,
            results=results,
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            title=f"نتائج البحث: {q} | {SITE_NAME}" if q else f"بحث | {SITE_NAME}",
            og_title=f"نتائج البحث: {q} — {SITE_NAME}" if q else f"بحث — {SITE_NAME}",
            og_description=f"نتائج البحث عن '{q}'" if q else "ابحث في مكتبة الامير",
            og_url=f"/search?q={q}",
            og_image="",
        )

    # ── API بحث (JSON لـ live search) ────────────────────────────────
    @app.route("/api/search")
    def api_search():
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify([])
        return jsonify([
            {
                "id": result["id"],
                "label": result["display_label"],
                "url": result["url"],
                "subtitle": result.get("subtitle", ""),
            }
            for result in _search_content(q, limit=15)
        ])

    # ── Thumbnail: أول صفحة من PDF ────────────────────────────────────
    @app.route("/thumb/<int:bid>")
    def thumb(bid: int):
        data = _pdf_thumbnail(bid)
        if data:
            return Response(data, mimetype="image/jpeg",
                            headers={"Cache-Control": "max-age=3600"})
        return redirect(url_for("static", filename="img/no-thumb.svg"))

    # ── File proxy بـ file_id (للغاليري والـ PDF) ────────────────────
    @app.route("/file/<path:file_id>")
    def file_proxy(file_id: str):
        url = _file_url(file_id)
        if not url:
            abort(404)
        # للـ PDF: نعيد البيانات مباشرة حتى يعمل الـ iframe في المتصفح
        try:
            r = _req.get(url, timeout=30, stream=True)
            content_type = r.headers.get("Content-Type", "application/octet-stream")
            is_pdf = "pdf" in content_type or file_id.lower().endswith(".pdf")
            if is_pdf:
                def generate():
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
                resp_headers = {
                    "Content-Type": content_type,
                    "Content-Disposition": "inline",
                }
                if "Content-Length" in r.headers:
                    resp_headers["Content-Length"] = r.headers["Content-Length"]
                return Response(generate(), status=200, headers=resp_headers)
        except Exception as e:
            logging.debug(f"[file_proxy] fallback redirect: {e}")
        return redirect(url)

    # ── robots.txt ───────────────────────────────────────────────────
    @app.route("/robots.txt")
    def robots():
        base = SITE_URL or request.host_url.rstrip("/")
        content = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            "Disallow: /file/\n"
            "Disallow: /thumb/\n"
            f"Sitemap: {base}/sitemap.xml\n"
        )
        return Response(content, mimetype="text/plain")

    # ── sitemap.xml ──────────────────────────────────────────────────
    @app.route("/sitemap.xml")
    def sitemap():
        base = SITE_URL or request.host_url.rstrip("/")
        urls = [{"loc": base, "priority": "1.0", "changefreq": "daily"}]

        # كل الفئات
        cats = list(_col("buttons").find({
            "deleted": {"$ne": 1}, "hidden": {"$ne": 1},
            "type": {"$ne": "content"}
        }, {"id": 1, "updated_at": 1}))
        for c in cats:
            urls.append({
                "loc": f"{base}/cat/{c['id']}",
                "priority": "0.8",
                "changefreq": "weekly",
            })

        # كل الملازم
        notes = list(_col("buttons").find({
            "deleted": {"$ne": 1}, "hidden": {"$ne": 1},
            "type": "content"
        }, {"id": 1, "created_at": 1}))
        for n in notes:
            urls.append({
                "loc": f"{base}/note/{n['id']}",
                "priority": "0.9",
                "changefreq": "monthly",
            })

        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for u in urls:
            lines.append("  <url>")
            lines.append(f"    <loc>{u['loc']}</loc>")
            lines.append(f"    <changefreq>{u['changefreq']}</changefreq>")
            lines.append(f"    <priority>{u['priority']}</priority>")
            lines.append("  </url>")
        lines.append("</urlset>")

        return Response("\n".join(lines), mimetype="application/xml")

    # ── صفحة 404 ─────────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html",
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            title=f"404 — {SITE_NAME}",
            og_title=f"404 — {SITE_NAME}",
            og_description="الصفحة غير موجودة",
            og_url="/",
            og_image="",
        ), 404

    return app
