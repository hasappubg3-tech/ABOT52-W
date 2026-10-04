"""Arabic-aware, typo-tolerant ranking without external services or dependencies."""
import math
import re
import unicodedata
from collections import Counter
from functools import lru_cache


_TRANSLATION = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي",
    "ة": "ه", "ؤ": "و", "ئ": "ي", "ک": "ك", "ی": "ي",
    **{chr(0x0660 + n): str(n) for n in range(10)},
    **{chr(0x06F0 + n): str(n) for n in range(10)},
})
_ALIASES = {
    "ملازم": "ملزمه", "ملزم": "ملزمه", "كتب": "كتاب",
    "ملخصات": "ملخص", "مراجعات": "مراجعه",
    "وزاريات": "وزاري", "وزاريه": "وزاري",
}
_FILLER = {
    "اريد", "اريدها", "اريده", "ابي", "ابغي", "ابحث", "بحث", "عن",
    "وين", "اين", "ممكن", "رجاء", "رجاءا", "لو", "سمحت", "من", "فضلك",
    "لي", "الي", "في", "هذا", "هذه", "هاي", "هذي", "مال", "كل",
    "جدا", "شلون", "مع", "افضل", "تحميل", "تنزيل", "رابط", "pdf", "بي", "دي", "اف",
    "استاذ", "استاذه", "شرح", "لل", "ل", "و",
}
_GENERAL = {
    "ملزمه", "كتاب", "ملخص", "مراجعه", "وزاري", "جزء", "فصل",
    "سنه", "عام", "اصدار", "دراسي", "دراسيه",
}


def _canonical(word):
    # Articles/prepositions should not distinguish «الفيزياء» from «فيزياء».
    if word.startswith("وال") and len(word) > 5:
        word = word[3:]
    elif word.startswith("لل") and len(word) > 4:
        word = word[2:]
    elif word.startswith("ال") and len(word) > 4:
        word = word[2:]
    return _ALIASES.get(word, word)


def tokens(text):
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    text = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", text).translate(_TRANSLATION)
    return [
        _canonical(word) for word in re.findall(r"[^\W_]+", text)
        if len(word) <= 64
    ]


def _vocabulary(words):
    vocabulary = set(words)
    # Support missing spaces in names, such as عبدالله / عبد الله.
    vocabulary.update(
        left + right for left, right in zip(words, words[1:])
        if left not in _FILLER | _GENERAL and right not in _FILLER | _GENERAL
        and not left.isdigit() and not right.isdigit()
        and len(left + right) <= 64
    )
    return frozenset(vocabulary)


def prepare(text, title=""):
    """Precompute each independent file's vocabulary when the index refreshes."""
    words = tokens(text)
    return {
        "words": _vocabulary(words),
        "title_words": _vocabulary(tokens(title)),
        "phrase": " ".join(words),
    }


@lru_cache(maxsize=32768)
def _similarity(query, word):
    if query == word:
        return 1.0
    # Never guess a year or fuzzy-match very short words.
    if query.isdigit() or word.isdigit():
        return 0.0
    if len(query) >= 2 and word.startswith(query):
        return 0.85
    if len(query) < 4 or len(word) < 3:
        return 0.0
    allowance = 2 if min(len(query), len(word)) >= 7 else 1
    if abs(len(query) - len(word)) > allowance:
        return 0.0
    if len(query) == len(word):
        changed = [i for i, (left, right) in enumerate(zip(query, word)) if left != right]
        if (len(changed) == 2 and changed[1] == changed[0] + 1
                and query[changed[0]] == word[changed[1]]
                and query[changed[1]] == word[changed[0]]):
            return 0.9
    # Bounded Damerau-Levenshtein: insert/delete/substitute/transposed letters.
    previous_previous = None
    previous = list(range(len(word) + 1))
    for i, char in enumerate(query, 1):
        current = [i]
        for j, other in enumerate(word, 1):
            distance = min(
                current[j - 1] + 1, previous[j] + 1,
                previous[j - 1] + (char != other),
            )
            if (previous_previous is not None and j > 1
                    and char == word[j - 2] and query[i - 2] == other):
                distance = min(distance, previous_previous[j - 2] + 1)
            current.append(distance)
        if min(current) > allowance:
            return 0.0
        previous_previous, previous = previous, current
    distance = previous[-1]
    return (0.78 if distance == 1 else 0.62) if distance <= allowance else 0.0


def rank(documents, query):
    """Ignore unmatched extra words, but do not match generic terms alone
    when a student also supplied a specific name/subject.
    """
    query_words = list(dict.fromkeys(
        word for word in tokens(str(query)[:400]) if word not in _FILLER
    ))[:24]
    if not query_words:
        return []
    specific = set(query_words) - _GENERAL
    vocabulary = set()
    frequencies = Counter()
    for document in documents:
        words = document["search"]["words"]
        vocabulary.update(words)
        frequencies.update(words)
    # Compare each query word with the vocabulary once, not once per file.
    matches = {
        query_word: {
            word: quality for word in vocabulary
            if (quality := _similarity(query_word, word))
        }
        for query_word in query_words
    }
    ranked = []
    phrase = " ".join(query_words)
    specific_pairs = [
        f"{left} {right}" for left, right in zip(query_words, query_words[1:])
        if left in specific and right in specific
        and not left.isdigit() and not right.isdigit()
    ]
    for document in documents:
        index = document["search"]
        score = 0.0
        matched_specific = False
        matched_count = 0
        for query_word, alternatives in matches.items():
            matching_words = index["words"].intersection(alternatives)
            if not matching_words:
                continue
            word = max(matching_words, key=lambda word: (
                alternatives[word], word in index["title_words"], word,
            ))
            quality = alternatives[word]
            # Rare names/subjects matter more than common "ملزمة" boilerplate.
            weight = 0.3 if query_word in _GENERAL else 1.0
            rarity = 1 + min(1, math.log1p(len(documents) / (1 + frequencies[word])))
            # Match quality outweighs rarity/title placement: a transposed
            # «حسين» must not rank below a different teacher named «حسن».
            score += weight * quality * (3 + rarity)
            if word in index["title_words"]:
                score += weight * quality * 0.25
            matched_specific |= query_word in specific
            matched_count += quality
        if not score or (specific and not matched_specific):
            continue
        score += 3 * matched_count / len(query_words)
        # A matching name/subject phrase must outweigh an incidental extra word.
        score += 2.5 * sum(pair in index["phrase"] for pair in specific_pairs)
        if phrase in index["phrase"]:
            score += 1.0
        ranked.append((score, document))
    return sorted(ranked, key=lambda row: (-row[0], -row[1]["year"]))