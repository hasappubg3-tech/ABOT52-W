"""Data-derived search metadata; never adds hidden text to the page body."""
import re
from html import unescape


def plain_text(value):
    text = re.sub(r"<[^>]*>", "", str(value or ""))
    return re.sub(r"\s+", " ", unescape(text)).strip(" |:،؛.-")


def description(value, limit=240):
    text = plain_text(value)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip("،؛.-") + "…"


def unique_labels(labels):
    result = []
    for label in labels:
        label = plain_text(label)
        if label and label not in result:
            result.append(label)
    return result


def category_metadata(labels, site_name):
    # Leaf first, then its subject/teacher/grade ancestors. Repeated menu
    # names such as "ملازم" now retain their distinct educational context.
    context = " — ".join(unique_labels(reversed(labels)))
    return (
        f"{context} | {site_name}",
        description(
            f"تصفح {context}. استعرض الملازم والكتب والملخصات الدراسية "
            f"المتاحة في {site_name}، والتحميل مجاناً عبر بوت التلگرام."
        ),
    )


def material_description(title, labels, site_name, summary=""):
    title = plain_text(title)
    context = " — ".join(
        label for label in unique_labels(labels) if label not in title
    )
    parts = [title + "."]
    if context:
        parts.append(f"ضمن {context}.")
    if summary:
        parts.append(plain_text(summary))
    parts.append(f"متاح للتحميل مجاناً عبر بوت {site_name} على التلگرام.")
    return description(" ".join(parts))


def breadcrumb_schema(entries, origin):
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "name": plain_text(name),
                "item": origin + path,
            }
            for index, (name, path) in enumerate(entries, start=1)
        ],
    }