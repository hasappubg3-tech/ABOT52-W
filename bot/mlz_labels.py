"""Formatting and narrowly scoped identification of legacy material titles."""
import re


def build_mlz_label(material_type, year, label_emojis):
    if not label_emojis or len(label_emojis) != 1:
        raise ValueError("حدد إيموجياً مخصصاً واحداً لأسماء الملازم.")
    fallback, emoji_id = next(iter(label_emojis.items()))
    if not fallback or not emoji_id:
        raise ValueError("إعداد إيموجي الملازم غير صالح.")
    material_type = str(material_type or "ملزمة").replace("📌", "").strip()
    year = str(year or "").strip()
    match = re.fullmatch(r"\((\d{4})\)", year)
    if match:
        year = match.group(1)
    # Do not repeat a fallback already included by the AI or a manual input.
    title = " ".join(part for part in (material_type, year) if part)
    title = " ".join(title.replace(fallback, "").split())
    title = re.sub(r"\((\d{4})\)$", r"\1", title)
    return f"{title} {fallback}"


def legacy_mlz_label_plan(buttons, label_emojis):
    """Only pin-wrapped content inside a materials tree; never rename other UI."""
    by_id = {button["id"]: button for button in buttons}

    def is_in_materials(button):
        visited = set()
        parent_id = button.get("parent_id")
        while parent_id is not None and parent_id not in visited:
            visited.add(parent_id)
            parent = by_id.get(parent_id)
            if not parent:
                return False
            if "ملازم" in parent.get("label", "").replace("ـ", ""):
                return True
            parent_id = parent.get("parent_id")
        return False

    changes = []
    for button in buttons:
        if button.get("type") != "content" or button.get("deleted") == 1:
            continue
        match = re.fullmatch(r"\s*📌\s*([^📌]+?)\s*📌\s*", button.get("label", ""))
        if not match or not is_in_materials(button):
            continue
        body = match.group(1).strip()
        # The old formatter put a year at the end, sometimes in parentheses.
        year_match = re.fullmatch(r"(.+?)\s+\(?(\d{4})\)?", body)
        if year_match:
            material_type, year = year_match.groups()
        else:
            material_type, year = body, ""
        changes.append({
            "id": button["id"],
            "before": button["label"],
            "after": build_mlz_label(material_type, year, label_emojis),
            "label_emojis": dict(label_emojis),
        })
    return changes
