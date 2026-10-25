"""Preserve inline-button actions while customizing their leading emoji icon."""

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _signature(button):
    data = button.to_dict()
    action = str(data.get("callback_data") or data.get("url") or data.get("web_app") or "")
    return re.sub(r"\d+", "#", action)


def button_occurrences(markup, scan):
    found = []
    for row_index, row in enumerate(getattr(markup, "inline_keyboard", ()) or ()):
        for column, button in enumerate(row):
            data = button.to_dict()
            custom_id = data.get("icon_custom_emoji_id")
            spans = scan(button.text)
            first = spans[0] if spans and not button.text[:spans[0]["start"]].strip() else None
            if not custom_id and not first:
                continue
            found.append({
                "scope": "button", "row": row_index, "column": column,
                "glyph": first["glyph"] if first else "🔹",
                "custom_emoji_id": custom_id, "label": button.text,
                "signature": _signature(button),
            })
    return found


def replace_button_icon(markup, span, glyph, custom_id, scan):
    rows = [list(row) for row in markup.inline_keyboard]
    button = rows[span["row"]][span["column"]]
    if _signature(button) != span["signature"]:
        raise ValueError("The button action changed.")
    data = button.to_dict()
    text = button.text
    spans = scan(text)
    first = spans[0] if spans and not text[:spans[0]["start"]].strip() else None
    label = text[first["end"]:].lstrip() if first else text.lstrip()
    if custom_id:
        data["text"] = label or glyph
        data["icon_custom_emoji_id"] = str(custom_id)
    else:
        data["text"] = f"{glyph} {label}".rstrip()
        data.pop("icon_custom_emoji_id", None)
    rows[span["row"]][span["column"]] = InlineKeyboardButton.de_json(data, None)
    return InlineKeyboardMarkup(rows)


def preview_keyboard(markup, nonce):
    rows = []
    for row in getattr(markup, "inline_keyboard", ()) or ():
        preview_row = []
        for button in row:
            data = button.to_dict()
            icon = data.get("icon_custom_emoji_id")
            preview_row.append(InlineKeyboardButton(
                button.text,
                callback_data=f"emoji_custom_preview:{nonce}",
                api_kwargs={"icon_custom_emoji_id": icon} if icon else None,
            ))
        rows.append(preview_row)
    rows.append([
        InlineKeyboardButton("حفظ التغيير", callback_data=f"emoji_custom_save:{nonce}"),
        InlineKeyboardButton("إلغاء", callback_data=f"emoji_custom_cancel:{nonce}"),
    ])
    return InlineKeyboardMarkup(rows)
