"""
ميزة حاسبة القبول الجامعي
يقرأ البيانات من data/qaboolat.csv ويُرجع قائمة الكليات المتاحة بحسب المعدل والفرع.
"""
import csv
import os
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

# ── رقم الزر الذي يُشغّل الميزة ────────────────────────────────
QABOOLAT_BTN_ID = 9670

_CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "qaboolat.csv")
_data_cache = None


# ── تحميل البيانات ───────────────────────────────────────────────
def _load_data() -> list:
    global _data_cache
    if _data_cache is not None:
        return _data_cache
    rows = []
    try:
        with open(_CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    rows.append({
                        "university":  row["university"].strip(),
                        "college":     row["college"].strip(),
                        "department":  row["department"].strip(),
                        "branch":      row["branch"].strip(),
                        "min_grade":   float(row["min_grade"]),
                    })
                except (ValueError, KeyError):
                    continue
    except FileNotFoundError:
        pass
    _data_cache = rows
    return rows


def reload_data():
    """إعادة تحميل البيانات من الملف (بعد تحديث CSV)."""
    global _data_cache
    _data_cache = None
    return _load_data()


# ── بحث ─────────────────────────────────────────────────────────
def search_results(branch: str, grade: float) -> list:
    """يُرجع الكليات التي يُقبل فيها الطالب مرتبةً من أعلى معدل مطلوب للأدنى."""
    data = _load_data()
    results = [r for r in data if r["branch"] == branch and r["min_grade"] <= grade]
    results.sort(key=lambda r: r["min_grade"], reverse=True)
    return results


# ── تنسيق الرسالة ────────────────────────────────────────────────
def format_results(branch: str, grade: float, results: list) -> list[str]:
    """
    يُرجع قائمة برسائل جاهزة للإرسال (مقسّمة لتتجنب حد تيليغرام 4096 حرف).
    """
    if not results:
        return [
            f"😔 *لا توجد كليات متاحة لمعدل {grade:.2f} ({branch})*\n\n"
            "تأكد من إدخال المعدل بشكل صحيح، أو أن المعدل مرتفع بما يكفي."
        ]

    # تجميع حسب الجامعة
    by_uni: dict[str, list[str]] = {}
    for r in results:
        uni = r["university"]
        if uni not in by_uni:
            by_uni[uni] = []
        entry = r["college"]
        if r["department"]:
            entry += f" — {r['department']}"
        entry += f" *({r['min_grade']:.2f})*"
        by_uni[uni].append(entry)

    # بناء الرسائل مع ترك هامش تحت حد Telegram (4096 UTF-16 units).
    header = (
        f"🎓 *نتائج القبول — معدل {grade:.2f} ({branch})*\n"
        f"✅ عدد الكليات التي تُقبل فيها: *{len(results)}*\n"
        "——————————————"
    )
    continuation_header = (
        f"🎓 *تكملة النتائج — معدل {grade:.2f} ({branch})*\n"
        f"✅ عدد الكليات التي تُقبل فيها: *{len(results)}*"
    )
    chunks = []
    current_lines = [header]
    current_len = len(header.encode("utf-16-le")) // 2
    LIMIT = 3500

    def _line_cost(line: str) -> int:
        # سطر جديد قبل كل سطر جديد عند ضمه إلى الرسالة الحالية.
        return 1 + len(line.encode("utf-16-le")) // 2

    for uni, colleges in by_uni.items():
        heading = f"🏛 *{uni}*"
        college_lines = [f"  • {college}" for college in colleges]

        # إذا لم تتسع بداية الجامعة وأول كلية، ابدأ رسالة متابعة قبل الجامعة.
        if current_len + _line_cost(heading) + _line_cost(college_lines[0]) > LIMIT:
            chunks.append("\n".join(current_lines))
            current_lines = [continuation_header]
            current_len = len(continuation_header.encode("utf-16-le")) // 2

        current_lines.append(heading)
        current_len += _line_cost(heading)

        for college_line in college_lines:
            if current_len + _line_cost(college_line) > LIMIT:
                chunks.append("\n".join(current_lines))
                continued_heading = f"🏛 *{uni} — تكملة*"
                current_lines = [continuation_header, continued_heading]
                current_len = (
                    len(continuation_header.encode("utf-16-le")) // 2
                    + _line_cost(continued_heading)
                )

            if current_len + _line_cost(college_line) > LIMIT:
                raise ValueError("A college entry exceeds the safe Telegram message size.")
            current_lines.append(college_line)
            current_len += _line_cost(college_line)

    if current_lines:
        current_lines.append("\n——————————————")
        current_lines.append("_البيانات للسنة الدراسية 2025/2026_")
        chunks.append("\n".join(current_lines))

    return chunks


# ── لوحة اختيار الفرع ────────────────────────────────────────────
def branch_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⚗️ علمي", callback_data="qab_branch_علمي"),
        InlineKeyboardButton("📚 أدبي", callback_data="qab_branch_أدبي"),
    ]])


# ── المُشغّل الرئيسي ─────────────────────────────────────────────
async def handle_qaboolat_trigger(m, ctx, uid):
    """يُرسل رسالة اختيار الفرع عند ضغط زر القبولات."""
    ctx.user_data.pop("qab_branch", None)
    ctx.user_data.pop("state", None)
    await m.reply_text(
        "🎓 *حاسبة القبول الجامعي*\n\n"
        "اختر فرعك الدراسي:",
        parse_mode="Markdown",
        reply_markup=branch_keyboard()
    )
