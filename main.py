from bot.shared import *
from bot.loader import load_bot_symbols

globals().update(load_bot_symbols())

async def _error_handler(update, context):
    """معالج مركزي للأخطاء."""
    from telegram.error import Conflict, NetworkError, TimedOut
    import asyncio
    err = context.error
    if isinstance(err, Conflict):
        logging.warning(
            "⚠️ تعارض: نسخة أخرى من البوت تعمل في مكان آخر. "
            "أوقف أي نسخة أخرى حتى يعمل البوت بشكل صحيح."
        )
        await asyncio.sleep(5)
    elif isinstance(err, (NetworkError, TimedOut)):
        logging.warning(f"خطأ شبكة مؤقت: {err}")
    else:
        logging.error(f"خطأ غير متوقع: {err}", exc_info=err)

def _start_flask():
    """تشغيل سيرفر Flask دائماً في خيط منفصل."""
    import threading, os as _os
    from website.app import create_app
    flask_app = create_app()
    port = int(_os.environ.get("PORT", 5000))
    web_thread = threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=port, use_reloader=False),
        daemon=True,
    )
    web_thread.start()
    logging.info(f"✅ سيرفر الموقع يعمل على المنفذ {port}")

def main():
    if not BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN غير موجود!"); return
    if not MONGODB_URI:
        logging.error("MONGODB_URI غير موجود!"); return
    init_db()
    if os.environ.get("RUN_EMBEDDED_WEBSITE", "1") != "0":
        _start_flask()
    from telegram.ext import JobQueue
    import httpx, asyncio

    # حذف أي webhook قديم قبل بدء الـ polling
    try:
        asyncio.get_event_loop().run_until_complete(
            httpx.AsyncClient().get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook",
                params={"drop_pending_updates": "true"},
                timeout=10,
            )
        )
        logging.info("تم حذف الـ webhook بنجاح.")
    except Exception as e:
        logging.warning(f"تعذّر حذف الـ webhook: {e}")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).job_queue(JobQueue()).build()

    media_filter = (filters.TEXT | filters.PHOTO | filters.Document.ALL |
                    filters.VIDEO | filters.AUDIO | filters.VOICE) & ~filters.COMMAND

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("storage_status", cmd_storage_status))
    app.add_handler(CommandHandler("repair_storage", cmd_repair_storage))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(PollAnswerHandler(on_poll_answer))
    app.add_handler(CallbackQueryHandler(cb_manage))
    app.add_handler(MessageHandler(media_filter, on_message))
    app.add_error_handler(_error_handler)

    logging.info("البوت يعمل بنظام Long Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
