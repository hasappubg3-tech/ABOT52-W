"""
pyro_sender.py — إرسال رسائل مع إيموجي متحركة عبر Pyrogram (MTProto).
يعمل بجانب python-telegram-bot دون تعارض.
"""
import os, logging, asyncio
from typing import Optional

logger = logging.getLogger(__name__)

_client   = None   # pyrogram.Client
_started  = False  # هل تم الاتصال؟


# ── تهيئة العميل ─────────────────────────────────────────────────────
async def _get_client():
    global _client, _started
    if _client is None:
        try:
            from pyrogram import Client
            api_id    = int(os.environ.get("TELEGRAM_API_ID",   0))
            api_hash  =     os.environ.get("TELEGRAM_API_HASH", "")
            bot_token =     os.environ.get("TELEGRAM_BOT_TOKEN","")
            if not all([api_id, api_hash, bot_token]):
                return None
            _client = Client(
                "pyro_session",
                api_id    = api_id,
                api_hash  = api_hash,
                bot_token = bot_token,
                in_memory = True,     # بدون ملف جلسة
                no_updates= True,     # PTB يتولى الاستقبال
            )
        except Exception as e:
            logger.warning(f"Pyrogram init error: {e}")
            return None

    if not _started:
        try:
            await _client.start()
            _started = True
            logger.info("✅ Pyrogram: متصل — الإيموجي المتحرك مفعّل")
        except Exception as e:
            logger.warning(f"Pyrogram start error: {e}")
            return None

    return _client


async def start_pyro():
    """استدعِ هذه الدالة عند بدء البوت."""
    await _get_client()


async def stop_pyro():
    """أوقف Pyrogram عند إغلاق البوت."""
    global _client, _started
    if _client and _started:
        try:
            await _client.stop()
        except Exception:
            pass
        _started = False


# ── حساب الـ offset بوحدات UTF-16 (معيار Telegram) ──────────────────
def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


# ── بناء entities للإيموجي والخط العريض ──────────────────────────────
def _build_entities(text: str, alias_map: dict, bold: bool = False) -> list:
    """
    alias_map: {fallback_char: {"emoji_id": str, ...}}
    يُرجع قائمة pyrogram MessageEntity.
    """
    from pyrogram.types import MessageEntity

    entities = []

    if bold:
        entities.append(MessageEntity(type="bold", offset=0, length=_utf16_len(text)))

    for fb_char, doc in alias_map.items():
        if not fb_char or fb_char not in text:  # حارس ضد الحرف الفارغ (يسبب حلقة لا نهائية)
            continue
        pos = 0
        while True:
            idx = text.find(fb_char, pos)
            if idx == -1:
                break
            offset = _utf16_len(text[:idx])
            length = _utf16_len(fb_char)
            entities.append(MessageEntity(
                type           = "custom_emoji",
                offset         = offset,
                length         = length,
                custom_emoji_id= doc["emoji_id"],
            ))
            pos = idx + len(fb_char)

    return entities


# ── الدالة الرئيسية: إرسال نص مع إيموجي متحرك ───────────────────────
async def send_animated(
    chat_id        : int,
    text           : str,
    bold           : bool  = False,
    reply_markup           = None,   # PTB ReplyKeyboardMarkup أو None
    disable_web_preview: bool = True,
    emoji_map      : dict  = None,   # {fallback_char: emoji_id} خاص بالزر، None=عام
) -> bool:
    """
    يُرسل النص عبر Pyrogram مع entities للإيموجي المتحرك.
    emoji_map=None  → يستخدم القاموس العام (سلوك قديم للأزرار بدون label_emojis)
    emoji_map={}    → لا إيموجي مخصص (الاسم كُتب بإيموجي عادي)
    emoji_map={c:id}→ يستخدم فقط الإيموجيات المحددة للزر
    يُرجع True عند النجاح، False للرجوع إلى PTB.
    """
    if not text:
        return False

    try:
        original_text = text
        saved_entities = []
        template_key = None
        from .emoji_customization import (
            capture_external_message, emoji_occurrences, prepare_external_message,
        )
        text, saved_entities, reply_markup, template_key = prepare_external_message(
            text, reply_markup=reply_markup,
        )
        has_saved_change = text != original_text or bool(saved_entities)

        if emoji_map is None:
            # سلوك قديم: استخدم القاموس العام
            from .data_access import get_all_emoji_aliases
            all_aliases = get_all_emoji_aliases()
            alias_map = {
                a["alias"]: a
                for a in all_aliases
                if a["alias"]
                and a["alias"] == a.get("fallback", "")
                and a["alias"] in text
            }
        else:
            # استخدم فقط إيموجيات هذا الزر تحديداً
            alias_map = {
                char: {"emoji_id": eid, "alias": char, "fallback": char}
                for char, eid in emoji_map.items()
                if char and char in text
            }
        for span in emoji_occurrences(text, saved_entities):
            if span.get("custom_emoji_id"):
                alias_map[span["glyph"]] = {
                    "emoji_id": span["custom_emoji_id"],
                    "alias": span["glyph"],
                    "fallback": span["glyph"],
                }
        if not alias_map and not has_saved_change:
            return False   # لا إيموجي متحرك → PTB يكفي

        client = await _get_client()
        if client is None:
            return False

        entities = _build_entities(text, alias_map, bold=bold)

        # تحويل PTB ReplyKeyboardMarkup → Pyrogram إن وُجد
        pyro_markup = None
        if reply_markup is not None:
            pyro_markup = _ptb_kb_to_pyro(reply_markup)

        sent = await client.send_message(
            chat_id                  = chat_id,
            text                     = text,
            entities                 = entities or None,
            reply_markup             = pyro_markup,
            disable_web_page_preview = disable_web_preview,
            parse_mode               = None,   # نستخدم entities يدوياً
        )
        if sent:
            chat = getattr(sent, "chat", None)
            sent_chat_id = getattr(chat, "id", chat_id)
            kind = getattr(chat, "type", "private")
            kind = getattr(kind, "value", kind)
            capture_external_message(
                sent_chat_id,
                getattr(sent, "id", getattr(sent, "message_id", None)),
                getattr(sent, "text", text),
                getattr(sent, "entities", None),
                getattr(sent, "reply_markup", pyro_markup),
                template_key=template_key,
                chat_type=str(kind).lower(),
            )
        return True

    except Exception as e:
        logger.warning(f"Pyrogram send_animated failed: {e}")
        return False


# ── تحويل كيبورد PTB إلى Pyrogram ────────────────────────────────────
def _ptb_kb_to_pyro(ptb_kb):
    """يحوّل ReplyKeyboardMarkup من PTB إلى Pyrogram."""
    try:
        from pyrogram.types import (
            ReplyKeyboardMarkup as PyroKB,
            KeyboardButton      as PyroBtn,
        )
        rows = [
            [PyroBtn(btn.text) for btn in row]
            for row in ptb_kb.keyboard
        ]
        return PyroKB(
            rows,
            resize_keyboard  = getattr(ptb_kb, "resize_keyboard", True),
            one_time_keyboard= getattr(ptb_kb, "one_time_keyboard", False),
        )
    except Exception as e:
        logger.warning(f"_ptb_kb_to_pyro failed: {e}")
        return None
