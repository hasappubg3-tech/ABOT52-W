"""Owner-only forwarding shortcut; independent of simulated student permissions."""

from collections.abc import Mapping

from telegram.ext import filters

from .emoji_customization import (
    _template_key, begin_emoji_customization, find_recent_owner_message,
    is_private_owner_message,
)


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def forward_origin(message):
    # PTB 20.7 preserves newer Bot API fields here instead of creating attributes.
    return (getattr(message, "forward_origin", None)
            or (getattr(message, "api_kwargs", None) or {}).get("forward_origin"))


def is_forwarded(message):
    return bool(
        forward_origin(message)
        or getattr(message, "forward_from", None)
        or getattr(message, "forward_date", None)
    )


class ForwardOriginFilter(filters.MessageFilter):
    """Also accept forwards whose modern metadata is unknown to older PTB."""

    def filter(self, message):
        return is_forwarded(message)


async def handle_forwarded_emoji_message(message, bot, uid, chat_id, chat_type, user_data):
    origin = forward_origin(message)
    legacy_sender = getattr(message, "forward_from", None)
    if not is_forwarded(message):
        return False
    if chat_type != "private" or str(chat_id) != str(uid):
        return False

    sender = _field(origin, "sender_user") if origin else legacy_sender
    try:
        current_bot_id = getattr(bot, "id", None)
    except RuntimeError:
        current_bot_id = None
    if not current_bot_id or str(_field(sender, "id")) != str(current_bot_id):
        # Other forwarded files must retain their normal upload/content workflow.
        return False
    if not is_private_owner_message(uid, chat_id, chat_type):
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "🛠 تخصيص الإيموجي: تعرّفت على رسالة هذا البوت، "
                "لكن الحساب الحالي غير معرّف كمالك في إعدادات هذه النسخة.\n"
                "هذه الميزة للمالك فقط، ولا تكفي صلاحية المشرف.\n"
                "إذا كنت صاحب البوت، أرسل /myid لعرض نتيجة فحص حسابك."
            ),
        )
        return True

    is_caption = not bool(getattr(message, "text", None))
    text = getattr(message, "text", None) or getattr(message, "caption", None)
    if not text:
        await bot.send_message(
            chat_id=chat_id,
            text="🛠 تخصيص الإيموجي: هذه الرسالة لا تحتوي نصاً أو وصفاً فيه إيموجيات قابلة للتغيير.",
        )
        return True
    entities = list(getattr(message, "caption_entities" if is_caption else "entities", None) or ())
    original = find_recent_owner_message(text, uid)
    if original:
        markup = original.get("reply_markup")
        has_keyboard = bool(getattr(markup, "inline_keyboard", None))
        target = {
            **original,
            "text": text,
            "entities": entities,
            "reply_markup": markup,
            "editable": True,
            "is_caption": is_caption,
            "forwarded": True,
            "original_message_found": True,
            "original_markup_found": has_keyboard,
        }
    else:
        target = {
            "chat_id": chat_id, "message_id": message.message_id,
            "text": text, "entities": entities, "reply_markup": None,
            "template_key": _template_key(text, entities),
            "editable": False, "is_caption": is_caption,
            "original_message_found": False,
            "forwarded": True, "original_markup_found": False,
        }
    return await begin_emoji_customization(
        bot, uid, chat_id, chat_type, user_data, explicit_target=target,
    )
