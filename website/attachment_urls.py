"""Arabic attachment permalinks, independent of Telegram API identifiers."""
import hashlib
import re
import unicodedata


def arabic_slug(title: str) -> str:
    text = unicodedata.normalize("NFKC", str(title or ""))
    text = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", text)
    text = re.sub(r"(?:للأستاذ|للاستاذ|الاستاذ)", "الأستاذ", text)
    text = re.sub(r"[\W_]+", "-", text, flags=re.UNICODE).strip("-")
    return text[:180].rstrip("-") or "ملزمة"


def allocate_slug(file_id: str, title: str, by_file: dict, by_slug: dict) -> str:
    """Keep identical files together and disambiguate different same-title files."""
    if file_id in by_file:
        return by_file[file_id]
    base = arabic_slug(title)
    slug = base
    if slug in by_slug and by_slug[slug] != file_id:
        digest = hashlib.sha256(file_id.encode("utf-8")).hexdigest()
        length = 10
        slug = f"{base}-{digest[:length]}"
        while slug in by_slug and by_slug[slug] != file_id:
            length += 2
            slug = f"{base}-{digest[:length]}"
    by_file[file_id] = slug
    by_slug[slug] = file_id
    return slug
