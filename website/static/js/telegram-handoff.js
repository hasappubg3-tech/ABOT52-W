(() => {
  const POLL_INTERVAL_MS = 1000;
  const MAX_WAIT_MS = 2 * 60 * 1000;
  const FAILURE_STATES = new Set(["not_sent", "failed", "expired"]);

  const showMessage = (message) => {
    if (typeof window.showToastGlobal === "function") {
      window.showToastGlobal(message);
    }
  };

  const wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

  document.addEventListener("click", async (event) => {
    const link = event.target.closest("a[data-telegram-handoff-target]");
    if (!link) return;

    const webApp = window.Telegram?.WebApp;
    if (
      !webApp?.initData ||
      typeof webApp.openTelegramLink !== "function" ||
      typeof webApp.close !== "function"
    ) {
      return;
    }

    event.preventDefault();
    if (link.dataset.telegramHandoffBusy === "true") return;
    link.dataset.telegramHandoffBusy = "true";

    let openedBot = false;
    try {
      webApp.ready?.();
      const createResponse = await fetch(link.dataset.telegramHandoffUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ target: link.dataset.telegramHandoffTarget }),
      });
      if (!createResponse.ok) throw new Error("handoff creation failed");

      const handoff = await createResponse.json();
      if (!handoff.telegram_url || !handoff.status_url) {
        throw new Error("handoff response was incomplete");
      }

      webApp.openTelegramLink(handoff.telegram_url);
      openedBot = true;

      const deadline = Date.now() + MAX_WAIT_MS;
      while (Date.now() < deadline) {
        await wait(POLL_INTERVAL_MS);
        const statusResponse = await fetch(handoff.status_url, {
          credentials: "same-origin",
          cache: "no-store",
        });
        if (!statusResponse.ok) throw new Error("handoff status unavailable");

        const { status } = await statusResponse.json();
        if (status === "delivered") {
          window.setTimeout(() => webApp.close(), 350);
          return;
        }
        if (FAILURE_STATES.has(status)) {
          showMessage("لم يرسل البوت الملف. راجع رسالة البوت ثم حاول مرة أخرى.");
          link.dataset.telegramHandoffBusy = "false";
          return;
        }
      }

      showMessage("ما زال إرسال الملف غير مؤكد. افتح البوت للتحقق من رسالته.");
      link.dataset.telegramHandoffBusy = "false";
    } catch {
      if (!openedBot) {
        try {
          webApp.openTelegramLink(link.href);
        } catch {
          window.location.assign(link.href);
        }
      }
      showMessage("تعذّر تأكيد إرسال الملف. افتح البوت للتحقق من رسالته.");
      link.dataset.telegramHandoffBusy = "false";
    }
  });
})();
