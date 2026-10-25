"""Owner-only in-chat emoji replacement for bot messages and reusable text templates."""
import hashlib
import logging
import os
import re
import secrets
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.ext import ExtBot
from bot.emoji_buttons import button_occurrences, preview_keyboard, replace_button_icon

from .shared import get_mongo_db

_OWNER_LAST_MESSAGE = None
_OVERRIDE_CACHE = {"expires": 0.0, "values": {}}
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
        "تغيير الايموجيات", "تغيير الإيموجيات",
        "تغير الايموجيات", "تغير الإيموجيات",
    }


def _template_key(text, entities=None):
    lines = text.split("\n")
    if any("عدد التقييمات:" in line for line in lines):
        for index, line in enumerate(lines[:4]):
            if "تقييم الملف:" in line:
                return f"rating:item:{index}"
            if "تقييم المحتوى:" in line:
                return f"rating:button:{index}"
    normalized = text
    for span in reversed(emoji_occurrences(text, entities)):
        normalized = normalized[:span["start"]] + "<emoji>" + normalized[span["end"]:]
    normalized = re.sub(r"\d+", "#", normalized)
    return "message:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
    return MessageEntity.de_json(values, None)


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
    for row in _collection().find({}, {"_id": 1, "occurrences": 1, "buttons": 1}):
        values[row["_id"]] = row
    _OVERRIDE_CACHE.update(values=values, expires=now + _OVERRIDE_TTL)
    return values


def _save_override(template_key, span, glyph, custom_emoji_id):
    is_button = span.get("scope") == "button"
    field = (f"buttons.{span['row']}.{span['column']}" if is_button
             else f"occurrences.{span['line']}.{span['position']}")
    replacement = {
        "glyph": glyph,
        "custom_emoji_id": str(custom_emoji_id) if custom_emoji_id else "",
    }
    if is_button:
        replacement["signature"] = span["signature"]
    _collection().update_one(
        {"_id": template_key},
        {"$set": {field: replacement}},
        upsert=True,
    )
    _OVERRIDE_CACHE["expires"] = 0.0


def _apply_saved_overrides(message):
    text = getattr(message, "text", None)
    markup = getattr(message, "reply_markup", None)
    if not text:
        return text, list(getattr(message, "entities", None) or ()), markup
    key = _template_key(text, getattr(message, "entities", None))
    saved = _load_overrides().get(key, {})
    entities = list(getattr(message, "entities", None) or ())
    spans = emoji_occurrences(text, entities)
    slots = {(span["line"], span["position"]): index
             for index, span in enumerate(spans)}
    replacements = []
    for line_key, row in saved.get("occurrences", {}).items():
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
    for span in button_occurrences(markup, emoji_occurrences):
        replacement = saved.get("buttons", {}).get(str(span["row"]), {}).get(str(span["column"]))
        if replacement and replacement.get("signature") == span["signature"]:
            markup = replace_button_icon(
                markup, span, replacement["glyph"],
                replacement.get("custom_emoji_id"), emoji_occurrences,
            )
    return text, entities, markup


def prepare_external_message(text, entities=None, reply_markup=None):
    """Render saved text substitutions for senders that do not use ExtBot."""
    message = type("OutgoingText", (), {})()
    message.text = text
    message.entities = list(entities or ())
    message.reply_markup = reply_markup
    key = _template_key(text, entities)
    try:
        new_text, new_entities, new_markup = _apply_saved_overrides(message)
        return new_text, new_entities, new_markup, key
    except Exception:
        logging.warning("Saved emoji override could not be applied to an external message.")
        return text, list(entities or ()), reply_markup, key


def capture_external_message(chat_id, message_id, text, entities=None, reply_markup=None,
                             template_key=None, chat_type="private"):
    message = type("ExternalBotMessage", (), {})()
    message.chat = type("Chat", (), {"id": chat_id, "type": chat_type})()
    message.chat_id = chat_id
    message.message_id = message_id
    message.text = text
    message.entities = list(entities or ())
    message.reply_markup = reply_markup
    _capture_owner_message(message, template_key=template_key)


def _message_is_customization_ui(text):
    return bool(text and (
        str(text).startswith(_UI_PREFIX)
        or any(str(text).startswith(prefix) for prefix in _UI_PREFIXES)
    ))


def _is_customization_message(message):
    if _message_is_customization_ui(getattr(message, "text", None)):
        return True
    return any(
        str(button.callback_data or "").startswith("emoji_custom_")
        for row in getattr(getattr(message, "reply_markup", None), "inline_keyboard", ()) or ()
        for button in row
    )


def _capture_owner_message(message, template_key=None):
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
        "reply_markup": getattr(message, "reply_markup", None),
        "template_key": template_key or _template_key(message.text, message.entities),
        "created_at": time.monotonic(),
    }


def _owner_target(message, owner_id):
    if (message and getattr(message, "text", None)
            and not _is_customization_message(message)
            and getattr(message, "chat", None)
            and str(message.chat.id) == str(owner_id)
            and getattr(message.chat, "type", None) == "private"
            and getattr(getattr(message, "from_user", None), "is_bot", False)):
        return {
            "chat_id": message.chat.id,
            "message_id": message.message_id,
            "text": message.text,
            "entities": list(getattr(message, "entities", None) or ()),
            "reply_markup": getattr(message, "reply_markup", None),
            "template_key": (
                _OWNER_LAST_MESSAGE["template_key"]
                if (_OWNER_LAST_MESSAGE
                    and _OWNER_LAST_MESSAGE["message_id"] == message.message_id)
                else _template_key(message.text, getattr(message, "entities", None))
            ),
            "created_at": time.monotonic(),
        }
    if (_OWNER_LAST_MESSAGE
            and str(_OWNER_LAST_MESSAGE["chat_id"]) == str(owner_id)
            and time.monotonic() - _OWNER_LAST_MESSAGE["created_at"] <= _FLOW_TTL):
        return dict(_OWNER_LAST_MESSAGE)
    return None


def _context_label(text, occurrence):
    if occurrence.get("scope") == "button":
        return "زر: " + occurrence["label"][:45]
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
    occurrences += button_occurrences(target.get("reply_markup"), emoji_occurrences)
    if not occurrences:
        await bot.send_message(
            chat_id=chat_id,
            text=f"{_UI_PREFIX} آخر رسالة من البوت ما بيها إيموجيات قابلة للتغيير.",
        )
        return True
    nonce = secrets.token_hex(5)
    user_data[_STATE_KEY] = {
        "nonce": nonce, "phase": "select", "target": target,
        "occurrences": occurrences, "created_at": time.monotonic(),
    }
    await bot.send_message(
        chat_id=chat_id,
        text=_UI_PREFIXES[0],
        reply_markup=_selection_keyboard(user_data[_STATE_KEY], 0),
    )
    return True


def _selection_keyboard(state, page):
    start = page * 20
    nonce = state["nonce"]
    keyboard = [
        [InlineKeyboardButton(
            f"{index + 1}. {span['glyph']} — {_context_label(state['target']['text'], span)}",
            callback_data=f"emoji_custom_select:{nonce}:{index}",
        )]
        for index, span in enumerate(state["occurrences"][start:start + 20], start)
    ]
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton(
            "السابق", callback_data=f"emoji_custom_page:{nonce}:{page - 1}",
        ))
    if start + 20 < len(state["occurrences"]):
        navigation.append(InlineKeyboardButton(
            "التالي", callback_data=f"emoji_custom_page:{nonce}:{page + 1}",
        ))
    if navigation:
        keyboard.append(navigation)
    keyboard.append([InlineKeyboardButton(
        "إلغاء", callback_data=f"emoji_custom_cancel:{nonce}",
    )])
    return InlineKeyboardMarkup(keyboard)


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
        selected = state["occurrences"][state["occurrence_index"]]
        new_markup = target.get("reply_markup")
        try:
            if selected.get("scope") == "button":
                new_text, new_entities = target["text"], target["entities"]
                new_markup = replace_button_icon(
                    new_markup, selected, span["glyph"],
                    span.get("custom_emoji_id"), emoji_occurrences,
                )
            else:
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
        if len(prefix + new_text) > 4096:
            prefix = ""
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
            "new_reply_markup": new_markup,
            "new_glyph": span["glyph"],
            "new_custom_emoji_id": span.get("custom_emoji_id"),
        }
        await bot.send_message(
            chat_id=chat_id,
            text=prefix + new_text,
            entities=preview_entities,
            reply_markup=preview_keyboard(new_markup, state["nonce"]),
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
    if action == "emoji_custom_preview":
        await query.answer("هذه معاينة فقط.")
        return True
    if action == "emoji_custom_page" and state["phase"] == "select" and len(parts) == 3:
        try:
            page = int(parts[2])
            if page < 0 or page * 20 >= len(state["occurrences"]):
                raise ValueError
        except ValueError:
            await query.answer("الصفحة غير صالحة.", show_alert=True)
            return True
        await query.answer()
        await query.edit_message_text(
            _UI_PREFIXES[0], reply_markup=_selection_keyboard(state, page),
        )
        return True
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
            if index < 0:
                raise ValueError
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
                reply_markup=state.get("new_reply_markup"),
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


async def customize_outgoing_message(message, edit_message):
    """Apply overrides to a Telegram-parsed message, preserving its entities."""
    if not message:
        return message
    text = getattr(message, "text", None)
    if not text or _is_customization_message(message):
        return message
    key = _template_key(text, getattr(message, "entities", None))
    try:
        new_text, entities, markup = _apply_saved_overrides(message)
        if (new_text != text or entities != list(getattr(message, "entities", None) or ())
                or markup != getattr(message, "reply_markup", None)):
            result = await edit_message(
                chat_id=message.chat_id,
                message_id=message.message_id,
                text=new_text,
                entities=entities,
                reply_markup=markup,
            )
            if result and getattr(result, "text", None):
                message = result
    except Exception:
        logging.warning("Saved emoji override could not be applied to a bot message.")
    _capture_owner_message(message, template_key=key)
    return message


class EmojiCustomizationBot(ExtBot):
    """Use supported ExtBot overrides rather than mutating immutable bot methods."""

    async def send_message(self, *args, **kwargs):
        message = await super().send_message(*args, **kwargs)
        return await customize_outgoing_message(message, super().edit_message_text)

    async def edit_message_text(self, *args, **kwargs):
        message = await super().edit_message_text(*args, **kwargs)
        return await customize_outgoing_message(message, super().edit_message_text)
