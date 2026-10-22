"""Pure title formatting for model answers, using source facts only."""
import re
import unicodedata


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_YEAR = re.compile(r"(?<!\d)20\d{2}(?!\d)")
_ROUND = re.compile(
    r"(?:(?:ال)?دور\s+)+(?:ال)?(اول|ثاني|ثالث|رابع|1|2|3|4)(?!\w)"
    r"|(?:ال)?تمهيدي"
)
_ORDINALS = {
    "اول": "الأول", "1": "الأول", "ثاني": "الثاني", "2": "الثاني",
    "ثالث": "الثالث", "3": "الثالث", "رابع": "الرابع", "4": "الرابع",
}
_DETAIL = re.compile(
    r"(?<!\w)(?:ال)?(?:احيائي|تطبيقي|ادبي|علمي|نازحين|موهوبين|محاولات|تكميلي|موصل)(?!\w)"
    r"|خارج\s+القطر"
)
_DETAIL_NAMES = {"احيائي": "أحيائي", "ادبي": "أدبي"}
_BRANCHES = {"احيائي", "تطبيقي", "ادبي", "علمي"}
_SUBJECT_ALIASES = (
    ("كيمياء", "الكيمياء"), ("فيزياء", "الفيزياء"),
    ("رياضيات", "الرياضيات"), ("احياء", "الأحياء"),
    ("انكليزي", "اللغة الإنكليزية"), ("انجليزي", "اللغة الإنكليزية"),
    ("انگليزي", "اللغة الإنكليزية"), ("عربي", "اللغة العربية"),
    ("عربية", "اللغة العربية"), ("اسلامي", "التربية الإسلامية"),
)


def clean_text(text):
    text = unicodedata.normalize("NFKC", str(text or "")).translate(_DIGITS)
    text = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", text)
    return re.sub(r"\s+", " ", text).strip(" |:،؛_-.()")


def normalized(text):
    return clean_text(text).translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي"}))


def answer_context(labels):
    """labels are clean root-to-file labels, never an unrelated sibling's data."""
    for index, label in enumerate(labels):
        value = normalized(label).replace("ة", "ه")
        if re.search(r"(?:اجوبه|اجابات)\s+(?:ال)?نموذجيه", value):
            tail = labels[index + 1:]
            return {
                "subject": clean_text(tail[0]) if len(tail) > 1 else "",
                "details": [clean_text(part) for part in tail[1:]] if len(tail) > 1 else [],
            }
    return None


def _detail_name(text):
    value = normalized(text)
    if value.startswith("ال") and value[2:] in _BRANCHES:
        value = value[2:]
    return _DETAIL_NAMES.get(value, clean_text(text))


def format_answer_title(context, metadata, study_stage=""):
    if context is None:
        return None
    path = [clean_text(value) for value in context.get("details", []) if value]
    # A leaf's placement is authoritative; descriptions/filenames only fill gaps.
    file_texts = [
        clean_text(re.sub(r"(?:لل?صف|الصف)\s+(?:ال)?سادس\s+(?:ال)?(?:علمي|ادبي)", "", normalized(value)))
        for value in metadata if value
    ]
    sources = [*reversed(path), *file_texts]
    subject = context.get("subject", "")
    if not subject:
        for text in file_texts:
            subject = next((display for keyword, display in _SUBJECT_ALIASES if keyword in text), "")
            if subject:
                break
    year = next((match.group() for text in sources if (match := _YEAR.search(text))), "")
    round_name = ""
    for text in sources:
        match = _ROUND.search(normalized(text))
        if match:
            round_name = "الدور " + _ORDINALS.get(match.group(1), "التمهيدي")
            break

    details, seen = [], set()
    has_branch = False
    for text in reversed(path):
        value = normalized(text)
        if not value or "نموذجي" in value:
            continue
        residual = _YEAR.sub("", value)
        residual = _ROUND.sub("", residual)
        residual = clean_text(residual)
        if not residual:
            continue
        # Avoid treating a subject-only content label as a special exam variant.
        if any(keyword in normalized(subject) and keyword in residual
               for keyword in ("اسلامي", "كيمياء", "فيزياء", "رياضيات", "احياء", "انكليزي", "انگليزي", "عربي")):
            continue
        for match in _DETAIL.finditer(residual):
            key = normalized(match.group())
            bare = key[2:] if key.startswith("ال") else key
            if bare in _BRANCHES:
                has_branch = True
        # Preserve unknown special path labels too, not only today's known list.
        residual = re.sub(r"(?<!\w)(?:ال)?احيائي(?!\w)", "أحيائي", residual)
        residual = re.sub(r"(?<!\w)(?:ال)?ادبي(?!\w)", "أدبي", residual)
        if normalized(residual) not in seen:
            details.append(residual)
            seen.add(normalized(residual))
    for text in file_texts:
        for match in _DETAIL.finditer(normalized(text)):
            key = match.group()
            bare = key[2:] if key.startswith("ال") else key
            if bare in _BRANCHES and has_branch:
                continue
            if bare in _BRANCHES:
                has_branch = True
            # Do not repeat a detail already carried by a full path label.
            if any(re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", value) for value in seen):
                continue
            details.append(_detail_name(key))
            seen.add(key)
    parts = ["الجواب النموذجي"]
    if subject:
        parts.append("لأسئلة مادة " + subject)
    parts.extend(value for value in (year, round_name, *details) if value)
    if study_stage:
        parts.append("للصف " + re.sub(r"^(?:ل?ل?صف|الصف)\s+", "", study_stage).strip())
    return " ".join(parts)
