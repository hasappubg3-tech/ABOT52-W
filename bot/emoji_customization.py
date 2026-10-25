"""Owner-only in-chat emoji replacement for bot messages and reusable text templates."""
import copy
import hashlib
import logging
import os
import secrets
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity

from .shared import get_mongo_db

_OWNER_LAST_MESSAGE = None
_OVERRIDE_CACHE = {"expires": 0.0, "values": {}}
_BOT_ORIGINAL_METHODS = {}
_OVERRIDE_TTL = 30
_FLOW_TTL = 600
_STATE_KEY = "bot_emoji_customization"
_CUSTOM_EMOJI = getattr(MessageEntity, "CUSTOM_EMOJI", "custom_emoji")
_UI_PREFIX = "🛠 تخصيص الإيموجي:"

_UI_PREFIXES = (
    "اختر الإيموجي الذي تريد تغييره في آخر رسالة أرسلها البوت:",
    "أرسل الإيموجي الجديد الذي تريد استخدامه:",
    "معاينة التغيير المقترح:",
    "✅ تم حفظ تغيير الإيموجي.",
    "تم إلغاء تغيير الإيموجي.",
)


def is_owner(uid):
    owner_id = os.environ.get("SUPER_ADMIN_ID", "").strip()
    return bool(owner_id.isdigit() and uid is not None and str(uid) == owner_id)


def is_private_owner_message(uid, chat_id, chat_type):
    return is_owner(uid) and chat_type == "private" and str(chat_id) == str(uid)


def is_emoji_change_command(text):
    return " ".join(str(text or "").strip().lower().split()) in {
        "تغيير الايموجي", "تغيير الإيموجي", "غير الايموجي",
        "غير الإيموجي", "تغير الايموجي", "تغير الإيموجي",
    }


def _template_key(text):
    if text.startswith("⭐ تقييم الملف:"):
        return "rating:item"
    if text.startswith("⭐ تقييم المحتوى:"):
        return "rating:button"
    return "exact:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


def _utf16_to_index(text, offset):
    used = 0
    for index, char in enumerate(text):
        if used >= offset:
            return index
        used += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def _index_to_utf16(text, index):
    return _utf16_length(text[:index])


def _copy_entity(entity, offset=None, length=None):
    values = entity.to_dict()
    if offset is not None:
        values["offset"] = offset
    if length is not None:
        values["length"] = length
    return MessageEntity(**values)


def _emoji_base(char):
    code = ord(char)
    return (
        0x1F000 <= code <= 0x1FAFF
        or 0x2600 <= code <= 0x27BF
        or 0x2190 <= code <= 0x21FF
        or 0x2300 <= code <= 0x23FF
        or 0x2B00 <= code <= 0x2BFF
        or char in "©®™‼⁉〰〽"
    )


def _emoji_cluster_end(text, start):
    char = text[start]
    if char in "#*0123456789":
        end = start + 1
        if end < len(text) and text[end] == "\ufe0f":
            end += 1
        if end < len(text) and text[end] == "\u20e3":
            return end + 1
        return None

    code = ord(char)
    if 0x1F1E6 <= code <= 0x1F1FF:
        end = start + 1
        if end < len(text) and 0x1F1E6 <= ord(text[end]) <= 0x1F1FF:
            end += 1
        return end
    if not _emoji_base(char):
        return None

    def consume_suffix(index):
        while index < len(text):
            suffix = ord(text[index])
            if (suffix in (0xFE0E, 0xFE0F)
                    or 0x1F3FB <= suffix <= 0x1F3FF
                    or 0xE0020 <= suffix <= 0xE007F
                    or 0x0300 <= suffix <= 0x036F):
                index += 1
            else:
                break
        return index

    end = consume_suffix(start + 1)
    while end + 1 < len(text) and text[end] == "\u200d" and _emoji_base(text[end + 1]):
        end = consume_suffix(end + 2)
    return end


def emoji_occurrences(text, entities=None):
    """Return emoji grapheme spans with Telegram UTF-16 offsets and line slots."""
    custom_by_start = {}
    for entity in entities or ():
        if getattr(entity, "type", None) != _CUSTOM_EMOJI:
            continue
        start = _utf16_to_index(text, entity.offset)
        end = _utf16_to_index(text, entity.offset + entity.length)
        if end > start:
            custom_by_start[start] = (end, getattr(entity, "custom_emoji_id", None))

    result = []
    line = 0
    line_position = 0
    index = 0
    while index < len(text):
        if text[index] == "\n":
            line += 1
            line_position = 0
            index += 1
            continue
        custom = custom_by_start.get(index)
        end = custom[0] if custom else _emoji_cluster_end(text, index)
        if end is None:
            index += 1
            continue
        result.append({
            "start": index,
            "end": end,
            "offset": _index_to_utf16(text, index),
            "length": _utf16_length(text[index:end]),
            "glyph": text[index:end],
            "custom_emoji_id": custom[1] if custom else None,
            "line": line,
            "position": line_position,
        })
        line_position += 1
        index = end
    return result


def replace_emoji(text, entities, occurrence_index, glyph, custom_emoji_id=None, bot=None):
    occurrences = emoji_occurrences(text, entities)
    if not 0 <= occurrence_index < len(occurrences):
        raise ValueError("That emoji occurrence no longer exists in the message.")
    selected = occurrences[occurrence_index]
    start, end = selected["start"], selected["end"]
    delta = len(glyph) - (end - start)
    updated_text = text[:start] + glyph + text[end:]
    updated_entities = []

    def move_offset(position, is_end=False):
        if position <= start:
            return position
        if position >= end:
            return position + delta
        return start + (len(glyph) if is_end else 0)

    for entity in entities or ():
        entity_start = _utf16_to_index(text, entity.offset)
        entity_end = _utf16_to_index(text, entity.offset + entity.length)
        if (getattr(entity, "type", None) == _CUSTOM_EMOJI
                and entity_start == start and entity_end == end):
            continue
        new_start = move_offset(entity_start)
        new_end = move_offset(entity_end, is_end=True)
        if new_end <= new_start:
            continue
        new_offset = _index_to_utf16(updated_text, new_start)
        shifted = _copy_entity(
            entity,
            offset=new_offset,
            length=_index_to_utf16(updated_text, new_end) - new_offset,
        )
        updated_entities.append(shifted)

    if custom_emoji_id:
        updated_entities.append(MessageEntity(
            type=_CUSTOM_EMOJI,
            offset=_index_to_utf16(updated_text, start),
            length=_utf16_length(glyph),
            custom_emoji_id=str(custom_emoji_id),
        ))
    updated_entities.sort(key=lambda entity: (entity.offset, -entity.length))
    return updated_text, updated_entities


def _collection():
    return get_mongo_db()["bot_emoji_overrides"]


def _load_overrides():
    now = time.monotonic()
    if now < _OVERRIDE_CACHE["expires"]:
        return _OVERRIDE_CACHE["values"]
    values = {}
    for row in _collection().find({}, {"_id": 1, "occurrences": 1}):
        values[row["_id"]] = row.get("occurrences") or {}
    _OVERRIDE_CACHE.update(values=values, expires=now + _OVERRIDE_TTL)
    return values


def _save_override(template_key, span, glyph, custom_emoji_id):
    field = f"occurrences.{span['line']}.{span['position']}"
    _collection().update_one(
        {"_id": template_key},
        {"$set": {field: {
            "glyph": glyph,
            "custom_emoji_id": str(custom_emoji_id) if custom_emoji_id else "",
        }}},
        upsert=True,
    )
    _OVERRIDE_CACHE["expires"] = 0.0


def _apply_saved_overrides(message):
    text = getattr(message, "text", None)
    if not text:
        return text, list(getattr(message, "entities", None) or ())
    key = _template_key(text)
    saved = _load_overrides().get(key, {})
    entities = list(getattr(message, "entities", None) or ())
    spans = emoji_occurrences(text, entities)
    slots = {(span["line"], span["position"]): index
             for index, span in enumerate(spans)}
    replacements = []
    for line_key, row in saved.items():
        try:
            line = int(line_key)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        for position_key, replacement in row.items():
            try:
                position = int(position_key)
            except (TypeError, ValueError):
                continue
            occurrence_index = slots.get((line, position))
            if occurrence_index is None:
                continue
            old = spans[occurrence_index]
            new_glyph = replacement.get("glyph", "")
            custom_id = replacement.get("custom_emoji_id") or None
            if not new_glyph or (old["glyph"] == new_glyph
                                 and old["custom_emoji_id"] == custom_id):
                continue
            replacements.append((occurrence_index, new_glyph, custom_id))
    for occurrence_index, glyph, custom_id in sorted(replacements, reverse=True):
        text, entities = replace_emoji(text, entities, occurrence_index, glyph, custom_id)
    return text, entities


def _message_is_customization_ui(text):
    return bool(text and (
        str(text).startswith(_UI_PREFIX)
        or any(str(text).startswith(prefix) for prefix in _UI_PREFIXES)
    ))


def _capture_owner_message(message):
    global _OWNER_LAST_MESSAGE
    if not message or not getattr(message, "text", None) or not getattr(message, "chat", None):
        return
    owner_id = os.environ.get("SUPER_ADMIN_ID", "").strip()
    chat = message.chat
    if (not owner_id.isdigit() or str(chat.id) != owner_id
            or getattr(chat, "type", None) != "private"
            or _message_is_customization_ui(message.text)):
        return
    _OWNER_LAST_MESSAGE = {
        "chat_id": chat.id,
        "message_id": message.message_id,
        "text": message.text,
        "entities": list(getattr(message, "entities", None) or ()),
        "template_key": _template_key(message.text),
        "created_at": time.monotonic(),
    }


def _owner_target(message, owner_id):
    if (message and getattr(message, "text", None)
            and not _message_is_customization_ui(message.text)
            and getattr(message, "chat", None)
            and str(message.chat.id) == str(owner_id)
            and getattr(message.chat, "type", None) == "private"
            and getattr(getattr(message, "from_user", None), "is_bot", False)):
        return {
            "chat_id": message.chat.id,
            "message_id": message.message_id,
            "text": message.text,
            "entities": list(getattr(message, "entities", None) or ()),
            "template_key": _template_key(message.text),
            "created_at": time.monotonic(),
        }
    if (_OWNER_LAST_MESSAGE
            and str(_OWNER_LAST_MESSAGE["chat_id"]) == str(owner_id)
            and time.monotonic() - _OWNER_LAST_MESSAGE["created_at"] <= _FLOW_TTL):
        return dict(_OWNER_LAST_MESSAGE)
    return None


def _context_label(text, occurrence):
    line_start = text.rfind("\n", 0, occurrence["start"]) + 1
    line_end = text.find("\n", occurrence["end"])
    if line_end < 0:
        line_end = len(text)
    line_text = text[line_start:line_end].strip().replace("\t", " ")
    return line_text[:40] or f"السطر {occurrence['line'] + 1}"


async def begin_emoji_customization(bot, uid, chat_id, chat_type, user_data, replied_message=None):
    global _OWNER_LAST_MESSAGE
    if not is_private_owner_message(uid, chat_id, chat_type):
        return False
    target = _owner_target(replied_message, uid) or _OWNER_LAST_MESSAGE
    if not target or time.monotonic() - target["created_at"] > _FLOW_TTL:
        await bot.send_message(
            chat_id=chat_id,
            text=f"{_UI_PREFIX} ما عندي رسالة حديثة أغيّر إيموجياتها. اطلب الرسالة مرة ثانية، أو ردّ على رسالة البوت بهذه العبارة.",
        )
        return True

    occurrences = emoji_occurrences(target["text"], target["entities"])
    if not occurrences:
        await bot.send_message(
            chat_id=chat_id,
            text=f"{_UI_PREFIX} آخر رسالة من البوت ما بيها إيموجيات قابلة للتغيير.",
        )
        return True
    nonce = secrets.token_hex(5)
    keyboard = [
        [InlineKeyboardButton(
            f"{index + 1}. {span['glyph']} — {_context_label(target['text'], span)}",
            callback_data=f"emoji_custom_select:{nonce}:{index}",
        )]
        for index, span in enumerate(occurrences[:80])
    ]
    keyboard.append([InlineKeyboardButton(
        "إلغاء", callback_data=f"emoji_custom_cancel:{nonce}",
    )])
    user_data[_STATE_KEY] = {
        "nonce": nonce, "phase": "select", "target": target,
        "occurrences": occurrences[:80], "created_at": time.monotonic(),
    }
    await bot.send_message(
        chat_id=chat_id,
        text=_UI_PREFIXES[0],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return True


def _is_single_emoji_message(message):
    text = getattr(message, "text", None) or ""
    occurrences = emoji_occurrences(text, getattr(message, "entities", None))
    if len(occurrences) != 1:
        return None
    span = occurrences[0]
    remainder = text[:span["start"]] + text[span["end"]:]
    if remainder.strip():
        return None
    return span


async def handle_owner_emoji_input(message, bot, uid, chat_id, chat_type, user_data):
    state = user_data.get(_STATE_KEY)
    if not state:
        return False
    if not is_private_owner_message(uid, chat_id, chat_type):
        user_data.pop(_STATE_KEY, None)
        user_data.pop("state", None)
        return False
    if time.monotonic() - state.get("created_at", 0) > _FLOW_TTL:
        user_data.pop(_STATE_KEY, None)
        await bot.send_message(
            chat_id=chat_id, text=f"{_UI_PREFIX} انتهت المهلة. ابدأ من جديد.",
        )
        return True
    if state["phase"] == "await_emoji":
        span = _is_single_emoji_message(message)
        if span is None:
            await bot.send_message(
                chat_id=chat_id,
                text=f"{_UI_PREFIX} أرسل إيموجياً واحداً فقط، عادياً أو مخصصاً.",
            )
            return True
        target = state["target"]
        try:
            new_text, new_entities = replace_emoji(
                target["text"], target["entities"], state["occurrence_index"],
                span["glyph"], span.get("custom_emoji_id"),
            )
        except ValueError:
            user_data.pop(_STATE_KEY, None)
            await bot.send_message(
                chat_id=chat_id,
                text=f"{_UI_PREFIX} لم أعد أجد هذا الإيموجي في الرسالة. ابدأ من جديد.",
            )
            return True

        prefix = _UI_PREFIXES[2] + "\n\n"
        shift = _utf16_length(prefix)
        preview_entities = [
            _copy_entity(entity, offset=entity.offset + shift)
            for entity in new_entities
        ]
        user_data[_STATE_KEY] = {
            **state,
            "phase": "confirm",
            "new_text": new_text,
            "new_entities": new_entities,
            "new_glyph": span["glyph"],
            "new_custom_emoji_id": span.get("custom_emoji_id"),
        }
        await bot.send_message(
            chat_id=chat_id,
            text=prefix + new_text,
            entities=preview_entities,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("حفظ التغيير", callback_data=f"emoji_custom_save:{state['nonce']}"),
                InlineKeyboardButton("إلغاء", callback_data=f"emoji_custom_cancel:{state['nonce']}"),
            ]]),
        )
        return True
    if state["phase"] == "confirm":
        await bot.send_message(
            chat_id=chat_id,
            text=f"{_UI_PREFIX} عاينت التغيير. اضغط «حفظ التغيير» أو «إلغاء» من الأزرار.",
        )
        return True
    return False


async def handle_owner_emoji_callback(update, context):
    query = update.callback_query
    data = query.data or ""
    if not data.startswith("emoji_custom_"):
        return False
    uid = query.from_user.id
    message = query.message
    if not is_private_owner_message(
        uid,
        getattr(getattr(message, "chat", None), "id", None),
        getattr(getattr(message, "chat", None), "type", None),
    ):
        await query.answer("هذه الميزة مخصصة لمالك البوت.", show_alert=True)
        return True

    state = context.user_data.get(_STATE_KEY)
    parts = data.split(":")
    if (not state or time.monotonic() - state.get("created_at", 0) > _FLOW_TTL
            or len(parts) < 2 or parts[1] != state.get("nonce")):
        if state and time.monotonic() - state.get("created_at", 0) > _FLOW_TTL:
            context.user_data.pop(_STATE_KEY, None)
        await query.answer("انتهت صلاحية هذه القائمة. ابدأ من جديد.", show_alert=True)
        return True

    action = parts[0]
    if action == "emoji_custom_cancel":
        context.user_data.pop(_STATE_KEY, None)
        await query.answer("تم الإلغاء.")
        try:
            await query.edit_message_text("تم إلغاء تغيير الإيموجي.")
        except Exception:
            pass
        return True

    if action == "emoji_custom_select" and state["phase"] == "select" and len(parts) == 3:
        try:
            index = int(parts[2])
            span = state["occurrences"][index]
        except (ValueError, IndexError):
            await query.answer("الإيموجي المحدد غير صالح.", show_alert=True)
            return True
        context.user_data[_STATE_KEY] = {
            **state, "phase": "await_emoji", "occurrence_index": index,
            "selected_glyph": span["glyph"], "created_at": time.monotonic(),
        }
        await query.answer()
        await context.bot.send_message(
            chat_id=uid,
            text=f"{_UI_PREFIXES[1]}\nالمحدد: {span['glyph']}",
        )
        return True

    if action == "emoji_custom_save" and state["phase"] == "confirm":
        target = state["target"]
        span = state["occurrences"][state["occurrence_index"]]
        try:
            _save_override(
                target["template_key"], span,
                state["new_glyph"], state.get("new_custom_emoji_id"),
            )
        except Exception:
            logging.exception("Owner emoji customization could not be persisted.")
            await query.answer("تعذّر حفظ الإعداد. الرسالة لم تتغير.", show_alert=True)
            return True
        try:
            await context.bot.edit_message_text(
                chat_id=target["chat_id"],
                message_id=target["message_id"],
                text=state["new_text"],
                entities=state["new_entities"],
            )
        except Exception:
            logging.exception("Owner emoji customization could not be applied.")
            await query.answer(
                "حُفظ الإعداد للرسائل القادمة، لكن تعذّر تعديل الرسالة الحالية.",
                show_alert=True,
            )
            return True
        context.user_data.pop(_STATE_KEY, None)
        await query.answer("تم الحفظ.")
        try:
            await query.edit_message_text("✅ تم حفظ تغيير الإيموجي.")
        except Exception:
            pass
        return True

    await query.answer("هذه الخطوة لم تعد صالحة.", show_alert=True)
    return True


def install_bot_emoji_customization(bot):
    """Track the owner's latest text message and apply saved template overrides."""
    if getattr(bot, "_emoji_customization_installed", False):
        return
    original_send = bot.send_message
    original_edit = bot.edit_message_text
    _BOT_ORIGINAL_METHODS[id(bot)] = (original_send, original_edit)

    async def apply_and_capture(message):
        if not message:
            return message
        text = getattr(message, "text", None)
        if not text or _message_is_customization_ui(text):
            _capture_owner_message(message)
            return message
        try:
            new_text, entities = _apply_saved_overrides(message)
            if new_text != text or entities != list(getattr(message, "entities", None) or ()):
                result = await original_edit(
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                    text=new_text,
                    entities=entities,
                )
                if result and getattr(result, "text", None):
                    message = result
        except Exception:
            logging.warning("Saved emoji override could not be applied to a bot message.")
        _capture_owner_message(message)
        return message

    async def tracked_send_message(*args, **kwargs):
        message = await original_send(*args, **kwargs)
        return await apply_and_capture(message)

    async def tracked_edit_message_text(*args, **kwargs):
        message = await original_edit(*args, **kwargs)
        return await apply_and_capture(message)

    try:
        bot.send_message = tracked_send_message
        bot.edit_message_text = tracked_edit_message_text
        bot._emoji_customization_installed = True
    except Exception:
        _BOT_ORIGINAL_METHODS.pop(id(bot), None)
        logging.exception("Could not initialize owner emoji customization hooks.")
