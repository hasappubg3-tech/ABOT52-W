"""Permission catalog and routing rules shared by the Telegram admin UI."""

ADMIN_PERMISSIONS = {
    "ai_upload": "إضافة الملازم بالذكاء الاصطناعي",
    "buttons": "إدارة الأزرار والمحتوى والاختبارات",
    "bot_settings": "إعدادات البوت والذكاء الاصطناعي",
    "broadcast": "الإذاعة وإرسال الرسائل الجماعية",
    "backups": "تنزيل النسخ الاحتياطية",
    "stats": "الإحصائيات والملفات الترند",
    "file_requests": "استلام طلبات الملفات والرد عليها",
    "admins": "إدارة المشرفين وصلاحياتهم",
}


def admin_callback_permission(data, *, admin_section=False):
    if data.startswith("ci_website_toggle_"):
        return "buttons"
    if data.startswith("mlz_ed_"):
        return "buttons"
    if data.startswith("mlz_"):
        return "ai_upload"
    if (data in ("aa", "st_admins") or data.startswith(
            ("da_", "ap_", "apt_", "fr_admins_", "fr_admin_add_", "fr_admin_del_"))):
        return "admins"
    if data == "st_restore":
        return "owner"
    if data.startswith("st_backup"):
        return "backups"
    if data.startswith(("st_stats", "st_trending")):
        return "stats"
    if data.startswith("st_broadcast"):
        return "broadcast"
    if data == "st_back":
        return "settings_menu"
    if data.startswith("st_"):
        return "bot_settings"
    if data == "don_thanks_set":
        return "bot_settings"
    if data.startswith(("exg_manage_", "exg_add_topic_")):
        return "buttons"
    if data.startswith("fu_thanks_set_"):
        return "file_requests"
    if admin_section and data not in ("noop", "cancel"):
        return "buttons"
    return None


_PUBLIC_INPUT_STATES = {
    "ai_chat_mode", "wait_file_upload", "wait_file_request", "wait_qab_grade",
    "wait_comment", "wait_donate_stars", "wait_grade_calc",
    "wait_cd_label", "wait_cd_datetime",
    "wait_pom_study_min", "wait_pom_break_min",
    "wait_ses_study_time", "wait_ses_edit_study", "wait_ses_edit_break",
    "wait_ses_break_time", "wait_ses_room_name_create", "wait_ses_password",
    "wait_ses_rename", "wait_ses_chat", "wait_ses_join_pw",
    "wait_ses_study_time_pre",
}
_BUTTON_INPUT_STATES = {
    "wait_clone_id", "wait_label", "wait_item_content", "wait_compound_text",
    "wait_item_desc", "wait_quiz_question", "wait_quiz_option",
    "wait_quiz_ai_count", "wait_quiz_ai_source", "wait_exam_q", "wait_exam_a",
    "wait_exam_edit_q", "wait_exam_edit_a", "wait_edit_label",
    "wait_mlz_new_desc",
}


def admin_state_permission(state):
    """Recheck authorization on every input, including after a role is revoked."""
    if not state or state in _PUBLIC_INPUT_STATES:
        return None
    if state.startswith("wait_freply_"):
        return "file_supervisor"
    if state in ("wait_admin_id", "wait_file_admin_id"):
        return "admins"
    if state == "wait_restore_zip":
        return "owner"
    if state == "wait_broadcast_msg":
        return "broadcast"
    if state == "wait_fu_thanks":
        return "file_requests"
    if state in _BUTTON_INPUT_STATES or state.startswith("wait_maintenance_msg_"):
        return "buttons"
    if state.startswith("wait_mlz_"):
        return "ai_upload"
    # Unknown input states are not allowed to bypass the admin boundary.
    return "bot_settings"
