"""Small, stable Telegram payloads; never embed long Telegram file IDs."""
import re


def encode_delivery_target(button_id: int, item_id: int | None = None) -> str:
    target = str(button_id)
    if item_id is not None:
        target += f"_{item_id}"
    if parse_delivery_target(target) is None:
        raise ValueError("Invalid content download target")
    return target


def parse_delivery_target(target) -> tuple[int, int | None] | None:
    target = str(target)
    # Leave room for both /start and the longest notification callback prefix.
    if len(target) > 48:
        return None
    match = re.fullmatch(r"([1-9][0-9]*)(?:_([1-9][0-9]*))?", target)
    if not match:
        return None
    return int(match[1]), int(match[2]) if match[2] else None