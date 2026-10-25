(() => {
  const status = document.getElementById('stats-status');
  const content = document.getElementById('stats-content');
  const number = new Intl.NumberFormat('ar-IQ');
  const sourceNames = {
    direct: 'مباشرة',
    search: 'محركات البحث',
    google: 'Google',
    bing: 'Bing',
    duckduckgo: 'DuckDuckGo',
    yahoo: 'Yahoo',
    telegram: 'تيليگرام',
    facebook: 'Facebook',
    instagram: 'Instagram',
    whatsapp: 'WhatsApp',
    youtube: 'YouTube',
    tiktok: 'TikTok',
    referral: 'مواقع أخرى',
  };
  const sourceName = value => sourceNames[value] || 'رابط خارجي';

  function addCell(row, text) {
    const cell = document.createElement('td');
    cell.textContent = text || '—';
    cell.style.padding = '.7rem .4rem';
    row.appendChild(cell);
  }

  function renderRows(id, rows, columns, emptyText) {
    const body = document.getElementById(id);
    body.replaceChildren();
    if (!rows.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = columns;
      cell.textContent = emptyText;
      cell.style.padding = '.8rem .4rem';
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    rows.forEach(values => {
      const row = document.createElement('tr');
      values.forEach(value => addCell(row, value));
      body.appendChild(row);
    });
  }

  function render(data) {
    document.getElementById('total-visits').textContent = number.format(data.total_visits);
    document.getElementById('total-downloads').textContent = number.format(data.total_bot_downloads);

    const sources = document.getElementById('source-list');
    sources.replaceChildren();
    if (!data.visits_by_source.length) {
      sources.textContent = 'لا توجد زيارات مسجلة بعد.';
    } else {
      data.visits_by_source.forEach(entry => {
        const row = document.createElement('p');
        row.textContent = `${sourceName(entry.source)}: ${number.format(entry.visits)}`;
        sources.appendChild(row);
      });
    }

    renderRows(
      'source-details',
      data.source_details.map(row => [
        sourceName(row.source), row.medium, row.campaign, number.format(row.visits),
      ]),
      4,
      'لا توجد تفاصيل مسجلة بعد.',
    );
    renderRows(
      'daily-visits',
      data.daily_visits.map(row => [row.date, number.format(row.visits)]),
      2,
      'لا توجد زيارات مسجلة بعد.',
    );

    const downloads = document.getElementById('top-downloads');
    downloads.replaceChildren();
    if (!data.top_downloads.length) {
      downloads.textContent = 'لا توجد تحميلات مسجلة بعد.';
    } else {
      data.top_downloads.forEach(item => {
        const row = document.createElement('li');
        row.textContent = `${item.label} — ${number.format(item.downloads)} تحميل`;
        downloads.appendChild(row);
      });
    }
  }

  async function load() {
    const telegram = window.Telegram?.WebApp;
    const initData = telegram?.initData;
    if (!initData) {
      status.textContent = 'افتح هذه اللوحة من زر /webstats داخل محادثة البوت بحساب المالك.';
      return;
    }
    telegram.ready();
    telegram.expand();
    try {
      const authResponse = await fetch('/api/admin/stats/auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ initData }),
      });
      if (!authResponse.ok) {
        status.textContent = 'لم يتم التحقق من الحساب. تأكد أنك فتحت اللوحة من البوت بحساب المالك.';
        return;
      }
      const response = await fetch('/api/admin/stats/summary', {
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (!response.ok) {
        status.textContent = 'انتهت صلاحية التحقق. أغلق اللوحة وافتحها مجدداً من البوت.';
        return;
      }
      const data = await response.json();
      render(data);
      content.hidden = false;
      status.textContent = 'الزيارات تُحسب مرة لكل جلسة بعد 30 دقيقة من عدم النشاط. أرقام التحميل في الموقع تتحدّث كل ساعة.';
    } catch {
      status.textContent = 'تعذر تحميل الإحصائيات حالياً. حاول فتح اللوحة مرة أخرى.';
    }
  }

  load();
})();
