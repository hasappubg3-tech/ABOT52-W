"""
الموقع الإلكتروني لشبكة الامير التعليمية
Flask app — يعمل في process منفصل بجانب البوت
"""
import os
import re
import time
import logging
import unicodedata
import secrets
import hashlib
import hmac
import json
from xml.etree import ElementTree as ET
from datetime import datetime, timedelta
from threading import Lock
from urllib.parse import parse_qsl, urlparse
import requests as _req
from flask import Flask, render_template, jsonify, request, redirect, abort, url_for, Response, session, flash, g, has_request_context
from pymongo.errors import PyMongoError
from . import feedback as feedback_store
from . import search as search_engine
from . import seo
from .attachment_urls import allocate_slug
from .model_answer_titles import answer_context, format_answer_title
from bot.download_targets import encode_delivery_target, parse_delivery_target

BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Mdry7bot")
SITE_NAME    = "شبكة الامير التعليمية"
# The public sitemap must not inherit HTTP or a preview/proxy hostname.
SITE_URL     = "https://alameer-iq.com"

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
    if _is_stage_menu(btn):
        return False
    label = _clean(btn.get("label", ""))
    return any(kw in label for kw in _GRADE_KEYWORDS)

def _is_stage_menu(btn: dict) -> bool:
    """Bot-only grouping folders; individual grades remain website categories."""
    label = " ".join(re.findall(r"\w+", _normalize_search_text(btn.get("label", ""))))
    return bool(re.fullmatch(
        r"(?:(?:المرحلة|المرحله|مرحلة|مرحله)\s+)?(?:ال)?"
        r"(?:ابتدائي[ةه]?|متوسط[ةه]?)", label
    ))

def _grade_display_label(label: str) -> str:
    """Display grade names without their numeric ordering markers."""
    clean = strip_emoji(label)
    clean = re.sub(r"[0-9٠-٩۰-۹]+", "", clean)
    return re.sub(r"[\s\-–—_]+", " ", clean).strip()

def _is_model_answers(btn: dict) -> bool:
    label = _normalize_search_text(btn.get("label", "")).replace("ة", "ه")
    return bool(re.search(r"(?:اجوبه|اجابات)\s+(?:ال)?نموذجيه", label))

def _is_allowed_menu(btn: dict) -> bool:
    """يُعيد True إذا كانت القائمة الفرعية تنتمي لملازم/كتب/ملخصات فقط."""
    label = _clean(btn.get("label", ""))
    return any(kw in label for kw in _MENU_WHITELIST) or _is_model_answers(btn)

def _is_allowed_content(btn: dict) -> bool:
    """يُعيد True فقط إذا كانت التسمية تنتمي لملزمة أو كتاب أو ملخص."""
    label = _clean(btn.get("label", ""))
    return any(kw in label for kw in _CONTENT_WHITELIST) or _is_model_answers(btn)
_PDF_THUMB_DIR  = "/tmp/pdf_thumbs"
_PDF_THUMB_TTL  = 86400   # يوم كامل

# ── Cache بسيط (file_id / bid -> (url|None, timestamp)) ──────────
_file_url_cache: dict = {}
_FILE_URL_TTL = 3600  # ساعة واحدة
_SEARCH_INDEX_TTL = 30
_search_index_cache = {
    "expires": 0.0,
    "records": None,
    "visibility_revision": None,
}
_search_index_lock = Lock()
_LATEST_NOTES_CACHE_TTL = 300
_latest_notes_cache = {
    "expires": 0.0,
    "revision": None,
    "source_stamp": None,
    "items": None,
}
_latest_notes_lock = Lock()
_DOWNLOAD_DISPLAY_CACHE = {"expires": 0.0, "counts": {}}
_DOWNLOAD_DISPLAY_LOCK = Lock()
_TRAFFIC_INDEX_LOCK = Lock()
_traffic_indexes_ready = False


def _get_mongo():
    from bot.data_access import get_mongo_db
    return get_mongo_db()


def _col(name: str):
    return _get_mongo()[name]


def _bot_download_display_counts() -> dict:
    """Read the bot's hourly snapshots once per minute for all website cards."""
    now = time.monotonic()
    if now < _DOWNLOAD_DISPLAY_CACHE["expires"]:
        return _DOWNLOAD_DISPLAY_CACHE["counts"]
    with _DOWNLOAD_DISPLAY_LOCK:
        now = time.monotonic()
        if now < _DOWNLOAD_DISPLAY_CACHE["expires"]:
            return _DOWNLOAD_DISPLAY_CACHE["counts"]
        counts = {
            row["item_id"]: max(0, int(row.get("display_count") or 0))
            for row in _col("item_download_stats").find(
                {}, {"item_id": 1, "display_count": 1}
            )
            if row.get("item_id") is not None
        }
        for twin in _col("item_twins").find({}, {"a": 1, "b": 1}):
            left, right = twin.get("a"), twin.get("b")
            if left is None or right is None:
                continue
            canonical = min(left, right)
            display_count = counts.get(canonical, 0)
            counts[left] = display_count
            counts[right] = display_count
        _DOWNLOAD_DISPLAY_CACHE.update(
            counts=counts, expires=time.monotonic() + 60
        )
        return counts


def _download_tracking_for_button(button_id: int) -> bool:
    _search_index_records()
    return any(
        record["button"].get("id") == button_id and record.get("download_tracking")
        for record in _search_index_cache.get("attachment_records", [])
    )


def _attachment_download_count(button_id: int, item_id: int) -> int | None:
    if not _download_tracking_for_button(button_id):
        return None
    return _bot_download_display_counts().get(item_id, 0)


def _attachment_download_count_from_record(record: dict, item_id: int) -> int | None:
    if not record.get("download_tracking"):
        return None
    return _bot_download_display_counts().get(item_id, 0)


def _safe_traffic_tag(value, allowed: set[str]) -> str:
    value = str(value or "").strip().lower()
    return value if value in allowed else ""


def _classify_traffic_source():
    """Return a privacy-safe source label, never a full referrer or query."""
    sources = {
        "telegram", "google", "bing", "duckduckgo", "yahoo",
        "facebook", "instagram", "whatsapp", "youtube", "tiktok",
    }
    mediums = {"bot", "organic", "social", "referral", "link", "email"}
    campaigns = {"search_button", "website_share", "bot_search", "other"}
    tagged_source = _safe_traffic_tag(request.args.get("utm_source"), sources)
    if tagged_source:
        return (
            tagged_source,
            _safe_traffic_tag(request.args.get("utm_medium"), mediums),
            _safe_traffic_tag(request.args.get("utm_campaign"), campaigns),
        )

    referrer = request.referrer
    if not referrer:
        return "direct", "", ""
    host = (urlparse(referrer).hostname or "").lower().removeprefix("www.")
    current_host = (request.host.split(":", 1)[0] or "").lower().removeprefix("www.")
    if host == current_host:
        return (
            session.get("_traffic_source", "direct"),
            session.get("_traffic_medium", ""),
            session.get("_traffic_campaign", ""),
        )
    if host == "t.me" or host.endswith(".telegram.org") or host == "telegram.org":
        return "telegram", "", ""
    search_hosts = (
        "google.", "bing.com", "duckduckgo.com", "search.yahoo.",
        "yandex.", "baidu.com", "ecosia.org",
    )
    if any(host.startswith(prefix) or host.endswith(prefix) for prefix in search_hosts):
        return "search", "", ""
    return "referral", "", ""


def _ensure_traffic_index():
    global _traffic_indexes_ready
    if _traffic_indexes_ready:
        return
    with _TRAFFIC_INDEX_LOCK:
        if _traffic_indexes_ready:
            return
        _col("website_traffic_daily").create_index(
            [("date", 1), ("source", 1), ("medium", 1), ("campaign", 1)]
        )
        _traffic_indexes_ready = True


def _record_traffic_visit():
    now = time.time()
    previous = float(session.get("_traffic_last_seen", 0) or 0)
    if now - previous >= 1800:
        source, medium, campaign = _classify_traffic_source()
        day = datetime.utcnow().strftime("%Y-%m-%d")
        key = f"{day}|{source}|{medium}|{campaign}"
        _ensure_traffic_index()
        _col("website_traffic_daily").update_one(
            {"_id": key},
            {
                "$setOnInsert": {
                    "date": day, "source": source,
                    "medium": medium, "campaign": campaign,
                },
                "$inc": {"visits": 1},
            },
            upsert=True,
        )
        session["_traffic_source"] = source
        session["_traffic_medium"] = medium
        session["_traffic_campaign"] = campaign
    session["_traffic_last_seen"] = now


def _verify_owner_webapp_init_data(init_data: str) -> bool:
    owner_id = os.environ.get("SUPER_ADMIN_ID", "").strip()
    if not BOT_TOKEN or not owner_id.isdigit() or not init_data or len(init_data) > 4096:
        return False
    pairs = parse_qsl(init_data, keep_blank_values=True)
    if len({key for key, _ in pairs}) != len(pairs):
        return False
    values = dict(pairs)
    received_hash = values.pop("hash", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", received_hash):
        return False
    try:
        auth_date = int(values.get("auth_date", ""))
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    if auth_date > now + 60 or now - auth_date > 600:
        return False
    data_check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key, data_check.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash.lower()):
        return False
    try:
        user = json.loads(values.get("user", "{}"))
    except (TypeError, ValueError):
        return False
    return str(user.get("id", "")) == owner_id


def _website_study_material_path(button: dict, buttons: dict) -> bool:
    """Only expose counters under the bot's ملازم, books, and summaries folders."""
    parent_id = button.get("parent_id")
    visited = set()
    allowed = {"ملازم", "ملزم", "ملزمة", "ملخص", "ملخصات", "كتاب", "كتب"}
    while parent_id is not None and parent_id not in visited:
        visited.add(parent_id)
        parent = buttons.get(parent_id)
        if not parent:
            break
        if parent.get("type") in {"menu", "compound"}:
            words = set(_normalize_search_text(parent.get("label") or "").split())
            stems = {word[2:] if word.startswith("ال") else word for word in words}
            if stems & allowed:
                return True
        parent_id = parent.get("parent_id")
    return False


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
    button = _col("buttons").find_one(
        {"id": bid, "deleted": {"$ne": 1}, "hidden": {"$ne": 1}}
    )
    if not button or _is_bot_only_entry(button):
        return None

    parent_id = button.get("parent_id")
    visited = {bid}
    while parent_id is not None:
        if parent_id in visited:
            return None
        visited.add(parent_id)
        parent = _col("buttons").find_one(
            {"id": parent_id}, {"id": 1, "parent_id": 1, "label": 1}
        )
        if not parent:
            break
        if _is_bot_only_entry(parent):
            return None
        parent_id = parent.get("parent_id")
    return button


def _is_bot_only_entry(button: dict) -> bool:
    """Student/community group entries belong to the bot, not the materials site."""
    label = _normalize_search_text(button.get("label") or "")
    return any(word in label for word in ("كروب", "کروب", "گروب", "جروب", "group"))


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
        if _is_bot_only_entry(parent):
            return False
        parent_id = parent.get("parent_id")
    return True


def _children(pid):
    q = {"deleted": {"$ne": 1}, "hidden": {"$ne": 1}}
    if pid is None:
        q["parent_id"] = None
    else:
        q["parent_id"] = pid
    buttons = _col("buttons").find(q).sort([("ord", 1), ("id", 1)])
    return [button for button in buttons if not _is_bot_only_entry(button)]

def _website_grades():
    """Flatten visible stage folders without changing the underlying bot tree."""
    if has_request_context() and hasattr(g, "website_grades"):
        return g.website_grades
    grades, visited = [], set()

    def collect(parent_id):
        for button in _children(parent_id):
            if button["id"] in visited or button.get("type") == "content":
                continue
            visited.add(button["id"])
            if _is_stage_menu(button):
                collect(button["id"])
            elif _is_grade_menu(button):
                grades.append(button)

    collect(None)
    if has_request_context():
        g.website_grades = grades
    return grades

def _model_answer_sections(grade_id):
    """Expose real answer sections even when the bot groups them under Other."""
    sections = []
    candidates = _col("buttons").find({
        "deleted": {"$ne": 1}, "hidden": {"$ne": 1},
        "type": {"$ne": "content"},
        "label": {"$regex": "ج.*(?:و.*ب|ا.*ب)"},
    }).sort([("ord", 1), ("id", 1)])
    for button in candidates:
        if not _is_model_answers(button):
            continue
        parent_id = button.get("parent_id")
        visited = {button["id"]}
        while parent_id is not None and parent_id not in visited:
            if parent_id == grade_id:
                sections.append(button)
                break
            visited.add(parent_id)
            parent = _col("buttons").find_one({
                "id": parent_id, "deleted": {"$ne": 1}, "hidden": {"$ne": 1},
            })
            if not parent or _is_bot_only_entry(parent) or _is_grade_menu(parent):
                break
            parent_id = parent.get("parent_id")
    return sections


def _items(bid: int):
    items = [
        item for item in _col("content_items").find({"button_id": bid}).sort(
            [("ord", 1), ("id", 1)]
        )
        if not item.get("website_hidden")
    ]
    has_visible_file = any(
        item.get("type") in {"document", "file"} and item.get("file_id")
        for item in items
    )
    if not has_visible_file and _col("content_items").find_one({
        "button_id": bid,
        "type": {"$in": ["document", "file"]},
        "file_id": {"$exists": True, "$ne": None},
        "website_hidden": True,
    }):
        return []
    return items


def _rating(bid: int) -> dict:
    return feedback_store.rating_summary(_col, "btn", bid)


def _feedback_context(button, item=None):
    return feedback_store.context(_col, button, item)


def _breadcrumb(bid: int) -> list:
    path = []
    current = bid
    visited = set()
    while current is not None and current not in visited:
        visited.add(current)
        doc = _col("buttons").find_one({"id": current})
        if not doc:
            break
        if not _is_stage_menu(doc):
            path.append({"id": doc["id"], "label": doc.get("label", "")})
        current = doc.get("parent_id")
    path.reverse()
    return path


def _seo_labels(breadcrumb):
    return [strip_emoji(crumb.get("label", "")) for crumb in breadcrumb]


def _seo_breadcrumb(breadcrumb, current_path):
    entries = [("الرئيسية", "/")]
    for index, crumb in enumerate(breadcrumb):
        path = current_path if index == len(breadcrumb) - 1 else url_for(
            "category", bid=crumb["id"]
        )
        entries.append((strip_emoji(crumb.get("label", "")), path))
    return seo.breadcrumb_schema(entries, SITE_URL)


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


_PART_TITLE_RE = re.compile(
    r"\s+((?:ال)?(?:جزء|فصل|باب|وحدة|درس|محاضرة|مبحث)\b.*)$",
    flags=re.IGNORECASE,
)
_TEACHER_MARKER_RE = r"(?:للاستاذ|للأستاذ|الأستاذ|الاستاذ)"


def _teacher_name(text: str) -> str:
    """يقتصر اسم المدرس على الاسم، من دون السنة أو رقم الجزء الذي يليه."""
    teacher = re.sub(r"\s+", " ", str(text or "")).strip(" |:،؛.-")
    teacher = re.split(
        r"\s+(?=(?:20[0-9٠-٩۰-۹]{2}\b|(?:ال)?(?:جزء|فصل|باب|وحدة|درس|محاضرة|مبحث)\b))",
        teacher,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" |:،؛.-")
    return teacher


def _title_with_teacher(title: str, teacher: str, year: str) -> str:
    """يضع المدرس في العنوان ثم السنة ثم تسمية الجزء أو الفصل."""
    title = str(title or "")
    teacher = _teacher_name(teacher)
    if not teacher:
        return _append_year(title, year)

    title = re.sub(
        rf"{_TEACHER_MARKER_RE}\s*[:：]?\s*{re.escape(teacher)}",
        " ",
        title,
        count=1,
        flags=re.IGNORECASE,
    )
    if year:
        title = re.sub(
            r"(?<!\d)[0-9٠-٩۰-۹]{4}(?!\d)",
            lambda match: " " if match.group(0).translate(_DIGIT_TRANSLATION) == year
            else match.group(0),
            title,
        )
    title = re.sub(r"\s+", " ", title).strip(" |:،؛.-()")
    part_match = _PART_TITLE_RE.search(title)
    part = part_match.group(1).strip(" |:،؛.-()") if part_match else ""
    subject = title[:part_match.start()].strip(" |:،؛.-()") if part_match else title
    pieces = [subject, f"للأستاذ {teacher}", str(year or ""), part]
    return " ".join(piece for piece in pieces if piece).strip() or title


_STAGE_GRADE_WORDS = (
    "الأول|الاول|اول|الاولى|الأولى|الثاني|الثانى|ثاني|الثالث|ثالث|"
    "الرابع|رابع|الخامس|خامس|السادس|سادس|السابع|سابع|الثامن|ثامن|"
    "التاسع|تاسع|العاشر|عاشر|الحادي عشر|الثاني عشر|"
    "(?:1[0-2]|[1-9]|[٠-٩])"
)
_STAGE_TRACK_WORDS = (
    "العلمي|علمي|الأدبي|الادبي|أدبي|ادبي|الابتدائي|ابتدائي|"
    "الابتدائية|ابتدائية|المتوسط|متوسط|المتوسطة|متوسطة|"
    "الإعدادي|الاعدادي|اعدادي|الإعدادية|الاعدادية|اعدادية"
)
_STAGE_GRADE_RE = re.compile(
    rf"(?:^|\s)(?:(?:ال)?صف\s+)?"
    rf"((?:{_STAGE_GRADE_WORDS})(?:\s+(?:{_STAGE_TRACK_WORDS}))?)"
    rf"(?=$|\s)",
    flags=re.IGNORECASE,
)


def _study_stage_from_label(label: str) -> str:
    """Turns a grade-menu label into a consistent title suffix."""
    clean = re.sub(r"\s+", " ", strip_emoji(label)).strip(" |:،؛.-")
    if not clean:
        return ""

    match = _STAGE_GRADE_RE.search(clean)
    if match:
        return "الصف " + match.group(1).strip()

    stage_match = re.search(
        r"(?:المرحلة\s+)?((?:ال)?(?:ابتدائية|ابتدائي|متوسطة|متوسط|"
        r"إعدادية|اعدادية|الإعدادية|الاعدادية))",
        clean,
    )
    if stage_match:
        stage = stage_match.group(1)
        if not stage.startswith("ال"):
            stage = "ال" + stage
        return "المرحلة " + stage
    return ""


def _study_stage_for_button(button: dict, buttons: dict | None = None) -> str:
    """Finds the closest school-grade ancestor without changing the material slug."""
    if buttons is None:
        cached = _search_index_cache.get("study_stage_by_button")
        if cached is None:
            _search_index_records()
            cached = _search_index_cache.get("study_stage_by_button", {})
        return cached.get(button.get("id"), "")

    current_id = button.get("parent_id")
    visited = {button.get("id")}
    while current_id is not None and current_id not in visited:
        visited.add(current_id)
        ancestor = buttons.get(current_id)
        if not ancestor:
            break
        stage = _study_stage_from_label(ancestor.get("label", ""))
        if stage:
            return stage
        current_id = ancestor.get("parent_id")
    # Some older data has the grade embedded in the content button itself.
    return _study_stage_from_label(button.get("label", ""))


def _append_study_stage(title: str, study_stage: str) -> str:
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    study_stage = re.sub(r"\s+", " ", str(study_stage or "")).strip()
    if not study_stage:
        return title
    grade_name = re.sub(r"^الصف\s+", "", study_stage)
    title = re.sub(
        rf"(?<!\S)(?:الصف\s+)?{re.escape(grade_name)}(?=\s|$|[،؛,.])",
        " ",
        title,
    )
    title = re.sub(r"\s+", " ", title).strip(" |:،؛.-")
    return f"{title} {study_stage}".strip()

def _answer_context_for_button(button, buttons=None):
    if buttons is None:
        return _search_index_cache.get("answer_context_by_button", {}).get(button.get("id"))
    labels, visited = [], set()
    current = button
    while current and current.get("id") not in visited:
        visited.add(current.get("id"))
        labels.append(strip_emoji(current.get("label", "")).replace(_TATWEEL, ""))
        current = buttons.get(current.get("parent_id"))
    return answer_context(list(reversed(labels)))


def _note_display_name(
    btn: dict, items: list | None = None, study_stage: str | None = None,
    model_context: dict | None = None,
) -> str:
    """يبني اسم الملزمة من وصفها، ويضمّن سنة الإصدار في العنوان."""
    if items is None:
        items = _items(btn["id"])
    info = _parse_content_lines(btn["id"], items)

    title   = info.get("title", "")
    teacher = info.get("teacher", "") or _teacher_from_lines([btn.get("label", "")])
    year    = _release_year(
        _content_metadata_lines(items),
        btn.get("label", ""),
    )

    # إذا لم يوجد عنوان من المحتوى، نرجع للتسمية الاحتياطية
    if not title:
        title = strip_emoji(btn.get("label", ""))

    if study_stage is None:
        study_stage = _study_stage_for_button(btn)
        if model_context is None:
            model_context = _answer_context_for_button(btn)
    answer_title = format_answer_title(
        model_context, _content_metadata_lines(items), study_stage
    )
    if answer_title:
        return answer_title
    return _append_study_stage(
        _title_with_teacher(title, teacher, year), study_stage
    )


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


def _attachment_display_title(
    item: dict, index: int, group_label: str = "", study_stage: str = "",
    model_context: dict | None = None,
) -> str:
    answer_title = format_answer_title(
        model_context,
        [*_content_metadata_lines([item]),
         item.get("file_name", ""), item.get("filename", ""), item.get("name", "")],
        study_stage,
    )
    if answer_title:
        return answer_title
    title = _attachment_title(item, index)
    if title == f"الملف {index}":
        title = strip_emoji(group_label) or title
    # سنة الملف نفسه لها الأولوية؛ سنة زر البوت ليست سنة كل ملف تحته.
    year = _release_year(_content_metadata_lines([item])) or _release_year([], group_label)
    teacher = _teacher_from_lines(_content_metadata_lines([item]))
    if not teacher:
        teacher = _teacher_from_lines([group_label])
    return _append_study_stage(
        _title_with_teacher(title, teacher, year), study_stage
    )


def _independent_notes(btn: dict, items: list | None = None) -> list:
    """عرض ملفات زر البوت كبطاقات مستقلة في الموقع دون تغيير بيانات البوت."""
    if items is None:
        items = _items(btn["id"])
    if not items:
        return []
    files = _file_items(items)
    if not files:
        return [{**_enrich(btn), "is_attachment": False, "url": f"/note/{btn['id']}"}]

    cards = []
    study_stage = _study_stage_for_button(btn)
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
            "url": _attachment_url(file_id),
            "display_label": _attachment_display_title(
                item, index, btn.get("label", ""), study_stage,
                _answer_context_for_button(btn),
            ),
            "subtitle": _attachment_search_subtitle(item, btn.get("label", "")),
            "thumb_url": False,
            "is_new": bool(added_at and time.time() - added_at < NEW_DAYS * 86400),
            "click_count": 0,
            "is_attachment": True,
            "download_count": _attachment_download_count(btn["id"], item["id"]),
        })
    return cards


def _find_visible_attachment(file_id: str) -> dict | None:
    records = _search_index_records()
    for record in _search_index_cache.get("attachment_records", records):
        for index, item in _file_items(record["items"]):
            if item.get("file_id") == file_id:
                return {
                    "record": record,
                    "button": record["button"],
                    "item": item,
                    "index": index,
                }

    # Category pages can list visible study files that are intentionally absent
    # from the search index (for example, a button titled only "واجبات").
    # Resolve those links from the source records, but keep hidden/deleted paths
    # and student-group content inaccessible.
    candidates = _col("content_items").find({
        "file_id": file_id,
        "type": {"$in": ["document", "file"]},
    })
    for item in candidates:
        if item.get("website_hidden"):
            continue
        bid = item.get("button_id")
        button = _col("buttons").find_one({
            "id": bid, "deleted": {"$ne": 1}, "hidden": {"$ne": 1},
        })
        if (not button or button.get("type") != "content"
                or _is_bot_only_entry(button)):
            continue

        grade_found = _is_grade_menu(button)
        ancestors = {bid: button}
        parent_id = button.get("parent_id")
        visited = {bid}
        visible_path = True
        while parent_id is not None:
            if parent_id in visited:
                visible_path = False
                break
            visited.add(parent_id)
            parent = _col("buttons").find_one({
                "id": parent_id, "deleted": {"$ne": 1}, "hidden": {"$ne": 1},
            })
            if not parent or _is_bot_only_entry(parent):
                visible_path = False
                break
            ancestors[parent["id"]] = parent
            grade_found = grade_found or _is_grade_menu(parent)
            parent_id = parent.get("parent_id")

        if not visible_path or not grade_found:
            continue

        items = _items(bid)
        for index, visible_item in _file_items(items):
            if visible_item.get("file_id") == file_id:
                record = {
                    "button": button,
                    "items": items,
                    "label": button.get("label", ""),
                    "study_stage": _study_stage_for_button(button, ancestors),
                    "answer_context": _answer_context_for_button(button, ancestors),
                }
                return {
                    "record": record,
                    "button": button,
                    "item": visible_item,
                    "index": index,
                }
    return None


def _bot_download_url(bid: int, item_id: int | None = None) -> str:
    """الملف المستقل يرسل معرّف العنصر، لا رابط الزر الذي يجمع ملفات متعددة."""
    if item_id is not None:
        payload = f"file_{encode_delivery_target(bid, item_id)}"
    else:
        payload = f"btn_{bid}"
    return f"https://t.me/{BOT_USERNAME}?start={payload}"


_telegram_handoff_index_lock = Lock()
_telegram_handoff_index_ready = False


def _telegram_handoff_collection():
    global _telegram_handoff_index_ready
    collection = _col("telegram_download_handoffs")
    if not _telegram_handoff_index_ready:
        with _telegram_handoff_index_lock:
            if not _telegram_handoff_index_ready:
                collection.create_index("expires_at", expireAfterSeconds=0)
                _telegram_handoff_index_ready = True
    return collection


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
        selected_item, selected["index"], selected_button.get("label", ""),
        selected.get("record", {}).get("study_stage", ""),
        selected.get("record", {}).get("answer_context"),
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

            title = _attachment_display_title(
                item, index, record["label"], record.get("study_stage", ""),
                record.get("answer_context"),
            )
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
                "url": _attachment_url(file_id),
                "display_label": title,
                "subtitle": _attachment_search_subtitle(item, record["label"]),
                "is_new": bool(added_at and time.time() - added_at < NEW_DAYS * 86400),
                "download_count": _attachment_download_count_from_record(
                    record, item["id"]
                ),
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


def _website_visibility_revision() -> int:
    state = _col("website_index_state").find_one({"_id": "content_visibility"})
    return int(state.get("revision", 0)) if state else 0


def _search_index_records() -> list:
    """يحمّل فهرس البحث مرة واحدة ويحدّثه دورياً لتبقى الطلبات الحية سريعة."""
    global _search_index_cache
    now = time.monotonic()
    revision = _website_visibility_revision()
    if (_search_index_cache["records"] is not None
            and now < _search_index_cache["expires"]
            and _search_index_cache.get("visibility_revision") == revision):
        return _search_index_cache["records"]

    with _search_index_lock:
        now = time.monotonic()
        revision = _website_visibility_revision()
        if (_search_index_cache["records"] is not None
                and now < _search_index_cache["expires"]
                and _search_index_cache.get("visibility_revision") == revision):
            return _search_index_cache["records"]

        all_buttons = {
            doc["id"]: doc
            for doc in _col("buttons").find(
                {},
                {"id": 1, "type": 1, "label": 1, "parent_id": 1,
                 "created_at": 1, "click_count": 1, "unified_rating": 1,
                 "hidden": 1, "deleted": 1},
            )
        }
        visible_buttons = {
            bid: button for bid, button in all_buttons.items()
            if not button.get("deleted") and not button.get("hidden")
        }
        items_by_button = {}
        all_items_by_button = {}
        hidden_item_buttons = set()
        hidden_attachment_buttons = set()
        for item in _col("content_items").find(
            {"button_id": {"$exists": True, "$ne": None}},
            {"button_id": 1, "id": 1, "type": 1, "file_id": 1, "content": 1,
             "caption": 1, "file_name": 1, "filename": 1, "name": 1, "ord": 1,
             "created_at": 1, "channel_msg_id": 1, "website_hidden": 1, "_id": 1},
        ).sort([("ord", 1), ("id", 1)]):
            all_items_by_button.setdefault(item.get("button_id"), []).append(item)
            if item.get("website_hidden"):
                hidden_item_buttons.add(item.get("button_id"))
                if item.get("type") in {"document", "file"} and item.get("file_id"):
                    hidden_attachment_buttons.add(item.get("button_id"))
                continue
            items_by_button.setdefault(item.get("button_id"), []).append(item)

        allowed_terms = tuple(_normalize_search_text(word) for word in _CONTENT_WHITELIST)
        records = []
        attachment_records = []
        study_stage_by_button = {}
        answer_context_by_button = {}
        for btn in sorted(visible_buttons.values(), key=lambda doc: str(doc.get("id", ""))):
            if (btn.get("type") != "content"
                    or _is_bot_only_entry(btn)
                    or not _has_visible_ancestors(btn, visible_buttons)):
                continue

            study_stage = _study_stage_for_button(btn, visible_buttons)
            study_stage_by_button[btn["id"]] = study_stage
            model_context = _answer_context_for_button(btn, visible_buttons)
            answer_context_by_button[btn["id"]] = model_context
            items = items_by_button.get(btn["id"], [])
            if not items and btn["id"] in hidden_item_buttons:
                continue
            if (btn["id"] in hidden_attachment_buttons
                    and not _file_items(items)):
                continue
            label = str(btn.get("label") or "")
            download_tracking = _website_study_material_path(btn, visible_buttons)
            if _file_items(items):
                attachment_records.append({
                    "button": btn, "items": items, "label": label,
                    "study_stage": study_stage,
                    "answer_context": model_context,
                    "download_tracking": download_tracking,
                })
            normalized_label = _normalize_search_text(label)
            normalized_item_texts = [
                _normalize_search_text(_search_item_text(item))
                for item in items
            ]
            aggregate_normalized = " ".join(
                [normalized_label, *normalized_item_texts]
            )
            context = []
            parent_id = btn.get("parent_id")
            while parent_id is not None:
                parent = visible_buttons[parent_id]
                context.append(str(parent.get("label") or ""))
                parent_id = parent.get("parent_id")
            allowed = _is_allowed_content(btn) or any(
                term in aggregate_normalized for term in allowed_terms
            ) or any(_is_model_answers({"label": label}) for label in context)
            if not allowed:
                continue

            records.append({
                "button": btn,
                "items": items,
                "label": label,
                "study_stage": study_stage,
                "answer_context": model_context,
                "normalized_label": normalized_label,
                "normalized_item_texts": normalized_item_texts,
                "aggregate_normalized": aggregate_normalized,
                "download_tracking": download_tracking,
            })
            records[-1]["search_context"] = " ".join(context)

        # Reserve names from hidden/deleted source files without exposing their pages.
        representatives = {}
        for record in attachment_records:
            for index, item in _file_items(record["items"]):
                representatives.setdefault(item["file_id"], (record["button"], index, item))
        for btn in sorted(all_buttons.values(), key=lambda doc: str(doc.get("id", ""))):
            for index, item in _file_items(all_items_by_button.get(btn["id"], [])):
                representatives.setdefault(item["file_id"], (btn, index, item))
        by_file, by_slug = {}, {}
        for btn, index, item in sorted(
            representatives.values(),
            key=lambda entry: (int(entry[2].get("id") or 0), entry[2]["file_id"]),
        ):
            title = _attachment_display_title(item, index, btn.get("label", ""))
            # Older files may store the teacher only in the parent compound menu.
            if not _teacher_from_lines(_content_metadata_lines([item]) + [btn.get("label", "")]):
                parent_id, visited = btn.get("parent_id"), set()
                while parent_id is not None and parent_id not in visited:
                    visited.add(parent_id)
                    parent = all_buttons.get(parent_id)
                    if not parent:
                        break
                    if parent.get("type") == "compound":
                        title = _title_with_teacher(
                            title, strip_emoji(parent.get("label", "")),
                            _release_year(_content_metadata_lines([item]), btn.get("label", "")),
                        )
                        break
                    parent_id = parent.get("parent_id")
            allocate_slug(item["file_id"], title, by_file, by_slug)
        attachment_paths = {fid: "/attachment/" + slug for fid, slug in by_file.items()}
        for record in records:
            record["search_documents"] = _record_search_documents(record, attachment_paths)
        _search_index_cache = {
            "expires": time.monotonic() + _SEARCH_INDEX_TTL,
            "records": records,
            "visibility_revision": revision,
            "attachment_records": attachment_records,
            "attachment_slugs": by_file,
            "slug_files": by_slug,
            "study_stage_by_button": study_stage_by_button,
            "answer_context_by_button": answer_context_by_button,
        }
        return records


def _attachment_url(file_id: str, selected: dict | None = None) -> str:
    """One URL builder for cards, metadata, legacy redirects, and sitemap."""
    if (_search_index_cache.get("attachment_slugs") is None
            or time.monotonic() >= _search_index_cache.get("expires", 0)):
        _search_index_records()
    by_file = _search_index_cache.setdefault("attachment_slugs", {})
    by_slug = _search_index_cache.setdefault("slug_files", {})
    if file_id not in by_file:
        # A new file can appear while the short-lived index is still cached.
        selected = selected or _find_visible_attachment(file_id)
        if not selected:
            abort(404)
        title = _attachment_display_title(
            selected["item"], selected["index"], selected["button"].get("label", "")
        )
        with _search_index_lock:
            allocate_slug(file_id, title, by_file, by_slug)
    return "/attachment/" + by_file[file_id]


def _attachment_search_subtitle(item: dict, group_label: str) -> str:
    clean_group = str(strip_emoji(group_label)).translate(_DIGIT_TRANSLATION)
    clean_group = re.sub(_YEAR_RE, "", clean_group)
    clean_group = re.sub(r"[()]+", " ", clean_group)
    return re.sub(r"\s+", " ", clean_group).strip(" |:،؛.-")


def _search_note_display_name(
    btn: dict, items: list, study_stage: str = "", model_context: dict | None = None
) -> str:
    """يبني عنوان نتيجة البحث من أول وصف ملف محمّل مسبقاً دون استعلام إضافي."""
    return _note_display_name(btn, items, study_stage, model_context)


def _record_search_documents(record: dict, attachment_paths: dict | None = None) -> list:
    """Keep captions file-specific; shared text and ancestor labels add context."""
    btn, items, label = record["button"], record["items"], record["label"]
    common_text = " ".join(
        _search_item_text(item) for item in items
        if item.get("type") not in {"document", "file"}
    )
    context = record.get("search_context", "")
    documents = []
    files = _file_items(items)
    if files:
        for index, item in files:
            display_label = _attachment_display_title(
                item, index, label, record.get("study_stage", ""),
                record.get("answer_context"),
            )
            # An old file must not inherit the year of its newer siblings.
            file_label = re.sub(_YEAR_RE, "", label.translate(_DIGIT_TRANSLATION))
            text = f"{file_label} {context} {common_text} {_search_item_text(item)} {display_label}"
            documents.append({
                "search": search_engine.prepare(text, display_label),
                "year": max((int(y) for y in _year_candidates(display_label)), default=0),
                "result": {
                    "id": btn["id"],
                    "url": (attachment_paths[item["file_id"]] if attachment_paths is not None
                            else _attachment_url(item["file_id"])),
                    "display_label": display_label,
                    "subtitle": _attachment_search_subtitle(item, label),
                    "thumb_url": False,
                    "is_new": False,
                    "click_count": 0,
                    "is_attachment": True,
                    "download_count": _attachment_download_count_from_record(
                        record, item["id"]
                    ),
                },
            })
    else:
        title = _search_note_display_name(
            btn, items, record.get("study_stage", ""), record.get("answer_context")
        )
        text = f"{label} {context} {' '.join(_search_item_text(item) for item in items)}"
        documents.append({
            "search": search_engine.prepare(text, title),
            "year": max((int(y) for y in _year_candidates(text)), default=0),
            "result": {
                **btn,
                "rating": {"count": 0, "avg": 0.0, "stars": ""},
                "is_new": _is_new(btn),
                "thumb_url": False,
                "click_count": btn.get("click_count", 0),
                "display_label": title,
                "url": f"/note/{btn['id']}",
                "subtitle": "",
                "is_attachment": False,
            },
        })
    return documents


def _search_content(q: str, limit: int = 50) -> list:
    documents = []
    for record in _search_index_records():
        documents.extend(
            record["search_documents"] if "search_documents" in record
            else _record_search_documents(record)
        )
    results, seen = [], set()
    for _score, document in search_engine.rank(documents, q):
        result = document["result"]
        if result["url"] in seen:
            continue
        seen.add(result["url"])
        results.append(dict(result))
        if len(results) >= max(1, limit):
            break
    return results


def _latest_source_stamp():
    """Fast _id high-water marks keep paged results fresh without rebuilding the full index."""
    newest_ids = []
    for collection_name in ("content_items", "buttons"):
        newest = next(iter(_col(collection_name).find(
            {}, {"_id": 1}
        ).sort([("_id", -1)])), None)
        newest_ids.append(str(newest.get("_id")) if newest else None)
    return tuple(newest_ids)


def _latest_notes(limit: int = 8, offset: int = 0) -> list:
    """يعرض كل مرفق كإضافة مستقلة، لا كحزمة واحدة مرتبطة بزر البوت."""
    global _latest_notes_cache
    limit = max(0, int(limit))
    offset = max(0, int(offset))
    revision = _website_visibility_revision()
    source_stamp = _latest_source_stamp()
    now = time.monotonic()

    cached = _latest_notes_cache
    if (cached["items"] is not None and now < cached["expires"]
            and cached["revision"] == revision
            and cached["source_stamp"] == source_stamp):
        return [dict(item) for item in cached["items"][offset:offset + limit]]

    with _latest_notes_lock:
        revision = _website_visibility_revision()
        source_stamp = _latest_source_stamp()
        now = time.monotonic()
        cached = _latest_notes_cache
        if (cached["items"] is not None and now < cached["expires"]
                and cached["revision"] == revision
                and cached["source_stamp"] == source_stamp):
            return [dict(item) for item in cached["items"][offset:offset + limit]]

        # New uploads or visibility changes invalidate the list. Force a matching
        # index refresh so a recent addition is not hidden by the short search TTL.
        if cached["items"] is not None:
            _search_index_cache["expires"] = 0.0
        latest = _build_latest_notes()
        _latest_notes_cache = {
            "expires": time.monotonic() + _LATEST_NOTES_CACHE_TTL,
            "revision": _website_visibility_revision(),
            "source_stamp": _latest_source_stamp(),
            "items": latest,
        }
        return [dict(item) for item in latest[offset:offset + limit]]


def _build_latest_notes() -> list:
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
                    "url": _attachment_url(item["file_id"]),
                    "display_label": _attachment_display_title(
                        item, index, record["label"],
                        record.get("study_stage", ""),
                        record.get("answer_context"),
                    ),
                    "subtitle": _attachment_search_subtitle(item, record["label"]),
                    "thumb_url": False,
                    "is_new": bool(
                        added_at and time.time() - added_at < NEW_DAYS * 86400
                    ),
                    "click_count": 0,
                    "is_attachment": True,
                    "download_count": _attachment_download_count_from_record(
                        record, item["id"]
                    ),
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
    return latest


# ─────────────────────────────────────────────────────────────────────
# Flask app factory
# ─────────────────────────────────────────────────────────────────────

def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        # The preview is cross-site inside Replit's iframe. CHIPS keeps its
        # signed guest session isolated and usable with third-party blocking.
        SESSION_COOKIE_SAMESITE="None",
        SESSION_COOKIE_PARTITIONED=True,
        PERMANENT_SESSION_LIFETIME=timedelta(days=365),
        MAX_CONTENT_LENGTH=32 * 1024,
    )
    app.jinja_env.filters["strip_emoji"] = strip_emoji
    app.jinja_env.filters["grade_display_label"] = _grade_display_label

    @app.context_processor
    def website_navigation():
        return {
            # API/admin pages should not query the public catalogue just to render navigation.
            "navigation_grades": [] if (
                request.path.startswith("/api/") or request.path.startswith("/admin/")
            ) else _website_grades(),
            "navigation_bot_username": BOT_USERNAME,
        }

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

    @app.after_request
    def record_anonymous_website_visit(response):
        if (
            request.method == "GET"
            and response.status_code < 400
            and response.mimetype == "text/html"
            and not request.path.startswith(("/api/", "/admin/", "/static/"))
        ):
            try:
                _record_traffic_visit()
            except Exception:
                app.logger.warning("Could not update anonymous traffic totals")
        return response

    @app.get("/admin/stats")
    def admin_stats():
        response = Response(
            render_template("admin_stats.html", site_name=SITE_NAME),
            mimetype="text/html",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/admin/stats/auth")
    def admin_stats_auth():
        data = request.get_json(silent=True) or {}
        if not _verify_owner_webapp_init_data(data.get("initData", "")):
            session.pop("_stats_owner_authenticated_at", None)
            return jsonify(error="owner_authentication_required"), 403
        session["_stats_owner_authenticated_at"] = int(time.time())
        response = jsonify(authenticated=True)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/admin/stats/summary")
    def admin_stats_summary():
        auth_at = int(session.get("_stats_owner_authenticated_at") or 0)
        now = int(time.time())
        if not auth_at or now - auth_at > 900 or auth_at > now + 60:
            session.pop("_stats_owner_authenticated_at", None)
            return jsonify(error="owner_authentication_required"), 401

        start_day = (datetime.utcnow().date() - timedelta(days=29)).isoformat()
        traffic_rows = list(_col("website_traffic_daily").find(
            {"date": {"$gte": start_day}}
        ))
        source_totals = {}
        daily_totals = {}
        source_details = {}
        for row in traffic_rows:
            source = row.get("source", "direct")
            visits = max(0, int(row.get("visits") or 0))
            source_totals[source] = source_totals.get(source, 0) + visits
            day = row.get("date", "")
            daily_totals[day] = daily_totals.get(day, 0) + visits
            detail_key = (
                source, row.get("medium", ""), row.get("campaign", "")
            )
            source_details[detail_key] = source_details.get(detail_key, 0) + visits

        download_rows = list(_col("item_download_stats").find({}))
        download_rows.sort(
            key=lambda row: max(0, int(row.get("count") or 0)), reverse=True
        )
        download_total = sum(max(0, int(row.get("count") or 0)) for row in download_rows)
        top_downloads = []
        for row in download_rows[:10]:
            item = _col("content_items").find_one({"id": row.get("item_id")}) or {}
            button = _col("buttons").find_one({"id": item.get("button_id")}) or {}
            label = (
                item.get("file_name") or item.get("filename") or item.get("name")
                or item.get("content") or button.get("label") or "ملف"
            )
            label = re.sub(r"\s+", " ", str(label)).strip()[:100]
            parent_label = str(button.get("label") or "").strip()
            if parent_label and parent_label not in label:
                label = f"{label} — {parent_label}"[:140]
            top_downloads.append({
                "label": label,
                "downloads": max(0, int(row.get("count") or 0)),
            })

        response = jsonify(
            period_days=30,
            total_visits=sum(source_totals.values()),
            visits_by_source=[
                {"source": source, "visits": visits}
                for source, visits in sorted(
                    source_totals.items(), key=lambda pair: pair[1], reverse=True
                )
            ],
            daily_visits=[
                {"date": day, "visits": visits}
                for day, visits in sorted(daily_totals.items(), reverse=True)
            ],
            source_details=[
                {
                    "source": key[0], "medium": key[1], "campaign": key[2],
                    "visits": visits,
                }
                for key, visits in sorted(
                    source_details.items(), key=lambda pair: pair[1], reverse=True
                )
            ],
            total_bot_downloads=download_total,
            top_downloads=top_downloads,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/telegram-download-handoffs")
    def create_telegram_download_handoff():
        data = request.get_json(silent=True) or {}
        target = parse_delivery_target(data.get("target", ""))
        if target is None:
            abort(400)

        bid, item_id = target
        button = _btn(bid)
        if not button or button.get("type") != "content" or button.get("hidden"):
            abort(404)
        items = _items(bid)
        if item_id is None:
            if not items:
                abort(404)
        elif not any(
            item.get("id") == item_id
            and item.get("button_id") == bid
            for item in items
        ):
            abort(404)

        now = datetime.utcnow()
        token = secrets.token_urlsafe(12)
        try:
            _telegram_handoff_collection().insert_one({
                "_id": token,
                "button_id": bid,
                "item_id": item_id,
                "status": "pending",
                "created_at": now,
                "expires_at": now + timedelta(hours=1),
            })
        except PyMongoError:
            app.logger.exception("Could not create Telegram download handoff")
            return jsonify(error="handoff_unavailable"), 503

        response = jsonify(
            telegram_url=f"https://t.me/{BOT_USERNAME}?start=handoff_{token}",
            status_url=url_for("telegram_download_handoff_status", token=token),
        )
        response.headers["Cache-Control"] = "no-store"
        return response, 201

    @app.get("/api/telegram-download-handoffs/<token>")
    def telegram_download_handoff_status(token: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{16}", token):
            abort(404)
        try:
            handoff = _telegram_handoff_collection().find_one(
                {"_id": token},
                {"status": 1, "expires_at": 1},
            )
        except PyMongoError:
            return jsonify(error="handoff_unavailable"), 503
        if not handoff or handoff.get("expires_at", datetime.min) <= datetime.utcnow():
            response = jsonify(status="expired")
        else:
            response = jsonify(status=handoff.get("status", "expired"))
        response.headers["Cache-Control"] = "no-store"
        return response

    # ── الصفحة الرئيسية ──────────────────────────────────────────────
    @app.route("/")
    def index():
        # الصفحة الرئيسية: فقط القوائم التي تمثّل صفوفاً دراسية
        categories = _website_grades()
        latest_page = _latest_notes(9)
        latest = latest_page[:8]
        return render_template("index.html",
            categories=categories,
            latest=latest,
            latest_has_more=len(latest_page) > 8,
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            title=SITE_NAME,
            og_title=SITE_NAME,
            og_description="مكتبة شبكة الامير التعليمية للملازم والكتب والملخصات الدراسية، مرتبة حسب المرحلة والمادة، مع تحميل الملفات مجاناً عبر بوت التلگرام.",
            og_url="/",
            og_image="",
            canonical_url=SITE_URL + url_for("index"),
            structured_data={
                "@context": "https://schema.org",
                "@type": "WebSite",
                "name": SITE_NAME,
                "url": SITE_URL + "/",
                "inLanguage": "ar",
            },
        )

    @app.route("/api/latest-additions")
    def api_latest_additions():
        try:
            offset = int(request.args.get("offset", "0"))
        except (TypeError, ValueError):
            return jsonify(error="invalid_offset"), 400
        if offset < 0 or offset > 10000:
            return jsonify(error="invalid_offset"), 400

        page_size = 8
        page = _latest_notes(page_size + 1, offset)
        notes = page[:page_size]
        return jsonify(
            html=render_template("_latest_note_cards.html", latest=notes),
            has_more=len(page) > page_size,
            next_offset=offset + len(notes),
        )

    # ── صفحة فئة / قسم ───────────────────────────────────────────────
    @app.route("/cat/<int:bid>")
    def category(bid: int):
        btn = _btn(bid)
        if not btn:
            abort(404)
        # الروابط القديمة لحزمة ملفات تعرض بطاقات مستقلة، لا صفحة ملزمة تجمعها.
        direct_items = None
        if btn.get("type") == "content":
            direct_items = _items(bid)
            if not direct_items and _col("content_items").find_one({"button_id": bid}):
                abort(404)
            children = [btn]
        else:
            children = _children(bid)
        if btn.get("type") == "compound" and btn.get("sort_by_year", 0):
            children = sorted(children, key=_compound_sort_key)
        # A grade can live inside a bot stage folder, not only at the root.
        if _is_grade_menu(btn):
            menus = [c for c in children if c.get("type") != "content"
                     and _is_allowed_menu(c)]
            existing = {menu["id"] for menu in menus}
            menus.extend(section for section in _model_answer_sections(bid)
                         if section["id"] not in existing)
        else:
            menus = [c for c in children if c.get("type") != "content"]
        contents = []
        seen_files = set()
        for child in children:
            if child.get("type") != "content":
                continue
            child_items = direct_items if child["id"] == bid else None
            for card in _independent_notes(child, child_items):
                file_id = card.get("file_id")
                if file_id and file_id in seen_files:
                    continue
                if file_id:
                    seen_files.add(file_id)
                contents.append(card)
        breadcrumb = _breadcrumb(bid)
        seo_title, seo_description = seo.category_metadata(
            _seo_labels(breadcrumb), SITE_NAME
        )
        canonical_path = url_for("category", bid=bid)
        return render_template("category.html",
            btn=btn,
            menus=menus,
            contents=contents,
            breadcrumb=breadcrumb,
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            title=seo_title,
            og_title=seo_title,
            og_description=seo_description,
            og_url=f"/cat/{bid}",
            og_image="",
            canonical_url=SITE_URL + canonical_path,
            structured_data=_seo_breadcrumb(breadcrumb, canonical_path)
                if len(breadcrumb) > 1 else None,
        )

    # ── صفحة الملزمة ─────────────────────────────────────────────────
    @app.route("/note/<int:bid>")
    def note(bid: int):
        btn = _btn(bid)
        if not btn or btn.get("type") != "content":
            abort(404)
        items         = _items(bid)
        if not items and _col("content_items").find_one({"button_id": bid}):
            abort(404)
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
        bot_deep_link = _bot_download_url(bid)
        feedback = _feedback_context(btn)
        canonical_path = url_for("note", bid=bid)

        return render_template("note.html",
            btn=btn,
            items=items,
            rating=rating,
            feedback=feedback,
            feedback_url=url_for("note_feedback", bid=bid, action="rate").rsplit("/", 1)[0],
            csrf_token=session.get("feedback_csrf", ""),
            breadcrumb=breadcrumb,
            thumb=thumb,
            display_label=display_label,
            preview_text=preview_text,
            photos=photos,
            bot_deep_link=bot_deep_link,
            bot_download_target=encode_delivery_target(bid),
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            title=f"{display_label} | {SITE_NAME}",
            og_title=f"{display_label} — {SITE_NAME}",
            og_description=seo.material_description(
                display_label, _seo_labels(breadcrumb[:-1]), SITE_NAME,
                summary=strip_emoji(preview_text),
            ),
            og_url=f"/note/{bid}",
            og_image="",
            canonical_url=SITE_URL + canonical_path,
            structured_data=_seo_breadcrumb(breadcrumb, canonical_path),
        )

    # ── صفحة ملف مستقل مع اقتراحات مشابهة ────────────────────────────
    @app.route("/attachment/<path:file_id>")
    def attachment_detail(file_id: str):
        _search_index_records()
        identifier = file_id
        file_id = _search_index_cache.get("slug_files", {}).get(identifier, identifier)
        selected = _find_visible_attachment(file_id)
        if not selected:
            abort(404)
        canonical_path = _attachment_url(file_id, selected)
        if identifier != canonical_path.removeprefix("/attachment/"):
            return redirect(canonical_path, code=301)

        record = selected["record"]
        item = selected["item"]
        display_label = _attachment_display_title(
            item, selected["index"], record["label"],
            record.get("study_stage", ""),
            record.get("answer_context"),
        )
        subtitle = _attachment_search_subtitle(item, record["label"])
        similar = _similar_attachments(selected)
        feedback = _feedback_context(selected["button"], item)
        breadcrumb = _breadcrumb(selected["button"]["id"])

        return render_template(
            "attachment.html",
            feedback=feedback,
            feedback_url=url_for("attachment_feedback", file_id=file_id, action="rate").rsplit("/", 1)[0],
            csrf_token=session.get("feedback_csrf", ""),
            display_label=display_label,
            subtitle=subtitle,
            similar=similar,
            bot_deep_link=_bot_download_url(selected["button"]["id"], item["id"]),
            bot_download_target=encode_delivery_target(
                selected["button"]["id"], item["id"]
            ),
            download_count=_attachment_download_count_from_record(
                record, item["id"]
            ),
            bot_username=BOT_USERNAME,
            site_name=SITE_NAME,
            title=f"{display_label} | {SITE_NAME}",
            og_title=f"{display_label} — {SITE_NAME}",
            og_description=seo.material_description(
                display_label, _seo_labels(breadcrumb[:-1]), SITE_NAME
            ),
            og_url=canonical_path,
            og_image="",
            canonical_url=SITE_URL + canonical_path,
            structured_data=seo.breadcrumb_schema(
                [("الرئيسية", "/"), (strip_emoji(display_label), canonical_path)],
                SITE_URL,
            ),
        )

    def save_feedback(button, item, action, destination):
        if action not in {"rate", "comment", "delete"}:
            abort(404)
        try:
            message = feedback_store.submit(_col, button, item, action, request.form)
            flash(message, "success")
        except ValueError as error:
            # Rendering directly also shows failures if cookies were blocked.
            app.logger.info("Website feedback rejected: %s", error)
            return render_template(
                "feedback_error.html", message=str(error), retry_url=destination,
                site_name=SITE_NAME, bot_username=BOT_USERNAME,
                title=f"لم تُحفظ المشاركة — {SITE_NAME}",
            ), 400
        except PyMongoError:
            # Do not log connection strings or database exception details.
            app.logger.error("Website feedback database operation failed")
            return render_template(
                "feedback_error.html",
                message="تعذّر حفظ المشاركة حالياً. حاول مرة أخرى لاحقاً.",
                retry_url=destination, site_name=SITE_NAME,
                bot_username=BOT_USERNAME, title=f"لم تُحفظ المشاركة — {SITE_NAME}",
            ), 503
        return redirect(destination + "#feedback", code=303)

    @app.post("/feedback/attachment/<path:file_id>/<action>")
    def attachment_feedback(file_id, action):
        selected = _find_visible_attachment(file_id)
        if not selected:
            abort(404)
        return save_feedback(selected["button"], selected["item"], action,
                             _attachment_url(file_id, selected))

    @app.post("/feedback/note/<int:bid>/<action>")
    def note_feedback(bid, action):
        button = _btn(bid)
        if not button or button.get("type") != "content" or _file_items(_items(bid)):
            abort(404)
        return save_feedback(button, None, action, url_for("note", bid=bid))

    @app.after_request
    def feedback_cache_policy(response):
        if request.endpoint in {"note", "attachment_detail", "note_feedback", "attachment_feedback"}:
            response.headers["Cache-Control"] = "private, no-store"
        return response

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
            meta_robots="noindex,follow",
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
                "download_count": result.get("download_count"),
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

    # ── معاينة الصور فقط؛ الملازم تُحمّل من خلال البوت حصراً ──────
    @app.route("/file/<path:file_id>")
    def file_proxy(file_id: str):
        selected = _find_visible_attachment(file_id)
        if selected:
            return redirect(_bot_download_url(selected["button"]["id"], selected["item"]["id"]))

        visible_photo = any(
            item.get("type") == "photo" and item.get("file_id") == file_id
            for record in _search_index_records()
            for item in record["items"]
        )
        if not visible_photo:
            abort(404)
        url = _file_url(file_id)
        if not url:
            abort(404)
        try:
            r = _req.get(url, timeout=30, stream=True)
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "application/octet-stream")
        except _req.RequestException:
            logging.warning("تعذر تحميل صورة المعاينة من Telegram.")
            abort(502)
        if content_type.split(";", 1)[0].strip().lower() not in {
            "image/jpeg", "image/png", "image/webp", "image/gif", "image/avif",
        }:
            r.close()
            abort(404)

        def generate():
            try:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            finally:
                r.close()

        return Response(
            generate(), mimetype=content_type.split(";", 1)[0],
            headers={"X-Content-Type-Options": "nosniff"},
        )

    # ── robots.txt ───────────────────────────────────────────────────
    @app.route("/robots.txt")
    def robots():
        base = SITE_URL
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
        root = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
        seen = set()

        def add_url(path, changefreq, priority):
            location = SITE_URL + path
            if location in seen:
                return
            seen.add(location)
            node = ET.SubElement(root, "url")
            ET.SubElement(node, "loc").text = location
            ET.SubElement(node, "changefreq").text = changefreq
            ET.SubElement(node, "priority").text = priority

        add_url(url_for("index"), "daily", "1.0")

        buttons = {
            button["id"]: button
            for button in _col("buttons").find(
                {"deleted": {"$ne": 1}, "hidden": {"$ne": 1}},
                {"id": 1, "type": 1, "label": 1, "parent_id": 1},
            )
        }
        for button in sorted(buttons.values(), key=lambda item: item["id"]):
            if (button.get("type") != "content"
                    and not _is_stage_menu(button)
                    and not _is_bot_only_entry(button)
                    and _has_visible_ancestors(button, buttons)):
                add_url(url_for("category", bid=button["id"]), "weekly", "0.8")

        # Publish the independent file pages, not their legacy redirect links.
        records = _search_index_records()
        for record in _search_index_cache.get("attachment_records", records):
            for _, item in _file_items(record["items"]):
                add_url(_attachment_url(item["file_id"]), "monthly", "0.9")
        for record in records:
            if not _file_items(record["items"]):
                add_url(url_for("note", bid=record["button"]["id"]), "monthly", "0.9")

        ET.indent(root, space="  ")
        xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        return Response(xml, content_type="application/xml; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})

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
            meta_robots="noindex,follow",
        ), 404

    return app
