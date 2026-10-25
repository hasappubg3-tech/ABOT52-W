"""Owner-only forwarding shortcut; independent of simulated student permissions."""

from .emoji_customization import (
    _template_key, begin_emoji_customization, is_private_owner_message,
)


async def handle_forwarded_emoji_message(message, bot, uid, chat_id, chat_type, user_data):
    origin = getattr(message, "forward_origin", None)
    legacy_sender = getattr(message, "forward_from", None)
    if not origin and not legacy_sender and not getattr(message, "forward_date", None):
        return False
    if not is_private_owner_message(uid, chat_id, chat_type):
        return False

    sender = getattr(origin, "sender_user", None) if origin else legacy_sender
    try:
        current_bot_id = getattr(bot, "id", None)
    except RuntimeError:
        current_bot_id = None
    if not current_bot_id or getattr(sender, "id", None) != current_bot_id:
        # Other forwarded files must retain their normal upload/content workflow.
        return False

    is_caption = not bool(getattr(message, "text", None))
    text = getattr(message, "text", None) or getattr(message, "caption", None)
    if not text:
        await bot.send_message(
            chat_id=chat_id,
            text="🛠 تخصيص الإيموجي: هذه الرسالة لا تحتوي نصاً أو وصفاً فيه إيموجيات قابلة للتغيير.",
        )
        return True
    entities = list(getattr(message, "caption_entities" if is_caption else "entities", None) or ())
    target = {
        "chat_id": chat_id, "message_id": message.message_id,
        "text": text, "entities": entities, "reply_markup": None,
        "template_key": _template_key(text, entities),
        "editable": False, "is_caption": is_caption,
    }
    # Never use the last bot message as a fallback for a forwarded selection.
    return await begin_emoji_customization(
        bot, uid, chat_id, chat_type, user_data, explicit_target=target,
    )
