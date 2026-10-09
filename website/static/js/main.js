/* ═══════════════════════════════════════════════════════════════
   شبكة الامير التعليمية — الجافاسكريبت الرئيسي
   ═══════════════════════════════════════════════════════════════ */

// ── البحث الحي ───────────────────────────────────────────────────
(function () {
  const toggleBtn    = document.getElementById('search-toggle');
  const searchBar    = document.getElementById('search-bar');
  const searchInput  = document.getElementById('search-input');
  const clearBtn     = document.getElementById('clear-search');
  const resultsBox   = document.getElementById('search-results');
  const isPersistent = searchBar?.classList.contains('search-bar--persistent');

  if (!searchBar || !searchInput) return;

  const navbar = searchBar.closest('.navbar');
  let lastScrollY = Math.max(0, window.scrollY);
  let settlingSearchLayout = false;

  function setSearchCollapsed(collapsed) {
    if (!isPersistent || !navbar) return;
    if (navbar.classList.contains('home-search-collapsed') === collapsed) return;
    navbar.classList.toggle('home-search-collapsed', collapsed);
    toggleBtn?.setAttribute('aria-expanded', String(!collapsed));
    if (collapsed) clearResults();
    // تجاهل تغير موضع الصفحة الناتج عن تغير ارتفاع الشريط نفسه.
    settlingSearchLayout = true;
    requestAnimationFrame(() => {
      lastScrollY = Math.max(0, window.scrollY);
      settlingSearchLayout = false;
    });
  }

  if (isPersistent) {
    window.addEventListener('scroll', () => {
      if (settlingSearchLayout) return;
      const currentY = Math.max(0, window.scrollY);
      if (searchBar.contains(document.activeElement)) {
        lastScrollY = currentY;
        return;
      }
      const delta = currentY - lastScrollY;
      if (currentY <= 40) {
        setSearchCollapsed(false);
      } else if (Math.abs(delta) < 12) {
        return;
      } else if (delta > 0 && currentY > 120) {
        setSearchCollapsed(true);
      } else if (delta < 0) {
        setSearchCollapsed(false);
      }
      lastScrollY = currentY;
    }, { passive: true });
  }

  // الأيقونة تعيد فتح بحث الرئيسية، وتفتح/تغلق البحث في الصفحات الداخلية.
  toggleBtn?.addEventListener('click', () => {
    if (isPersistent) {
      setSearchCollapsed(false);
      searchInput.focus();
      return;
    }
    const isOpen = searchBar.classList.toggle('open');
    toggleBtn.setAttribute('aria-expanded', String(isOpen));
    if (isOpen) {
      searchInput.focus();
    } else {
      searchInput.value = '';
      clearResults();
    }
  });

  if (new URLSearchParams(window.location.search).get('open_search') === '1') {
    if (!isPersistent) searchBar.classList.add('open');
    window.Telegram?.WebApp?.ready();
    window.Telegram?.WebApp?.expand();
    const focusSearch = () => searchInput.focus({ preventScroll: true });
    requestAnimationFrame(() => {
      focusSearch();
      setTimeout(focusSearch, 200);
    });
  }

  // زر مسح
  clearBtn?.addEventListener('click', () => {
    searchInput.value = '';
    searchInput.focus();
    clearResults();
    clearBtn.classList.add('hidden');
  });

  function clearResults() {
    if (resultsBox) {
      resultsBox.innerHTML = '';
      resultsBox.style.display = 'none';
    }
  }

  // Debounce
  let debounceTimer;
  let requestSequence = 0;
  searchInput.addEventListener('input', (e) => {
    const q = e.target.value.trim();
    clearBtn?.classList.toggle('hidden', !q);

    clearTimeout(debounceTimer);
    if (Array.from(q.replace(/\s/g, '')).length < 2) { clearResults(); return; }

    debounceTimer = setTimeout(() => doSearch(q), 280);
  });

  // Enter → صفحة نتائج كاملة
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const q = searchInput.value.trim();
      if (q) window.location.href = `/search?q=${encodeURIComponent(q)}`;
    }
    if (e.key === 'Escape') {
      if (!isPersistent) searchBar.classList.remove('open');
      if (!isPersistent) toggleBtn?.setAttribute('aria-expanded', 'false');
      clearResults();
    }
  });

  async function doSearch(q) {
    const sequence = ++requestSequence;
    try {
      const res  = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      if (sequence !== requestSequence || searchInput.value.trim() !== q) return;
      showResults(data, q);
    } catch { /* صامت */ }
  }

  function showResults(items, q) {
    if (!resultsBox) return;
    if (!items || items.length === 0) {
      resultsBox.innerHTML = `<div class="search-result-item" style="color:#888;text-align:center">لا توجد نتائج لـ "${escHtml(q)}"</div>`;
    } else {
      resultsBox.innerHTML = items.map(item => {
        const subtitle = item.subtitle
          ? `<span class="search-result-subtitle">${escHtml(item.subtitle)}</span>`
          : '';
        return `<a class="search-result-item" href="${escHtml(item.url)}"><span>${escHtml(item.label)}</span>${subtitle}</a>`;
      }).join('') +
      (items.length >= 15
        ? `<a class="search-result-item" href="/search?q=${encodeURIComponent(q)}" style="color:var(--gold);text-align:center">عرض كل النتائج ←</a>`
        : '');
    }
    resultsBox.style.display = 'block';
  }

  // إغلاق عند الضغط خارج شريط البحث
  document.addEventListener('click', (e) => {
    if (searchBar.contains(e.target) || toggleBtn?.contains(e.target)) return;
    if (!isPersistent) searchBar.classList.remove('open');
    if (!isPersistent) toggleBtn?.setAttribute('aria-expanded', 'false');
    clearResults();
  });
})();

// ── مشاركة الملزمة (من صفحة القائمة) ───────────────────────────
function shareNote(event, id, label) {
  event.preventDefault();
  event.stopPropagation();
  const url = `${window.location.origin}/note/${id}`;
  if (navigator.share) {
    navigator.share({ title: label, url });
  } else {
    navigator.clipboard.writeText(url).then(() => {
      showToastGlobal('تم نسخ الرابط ✓');
    });
  }
}

// Toast عالمي
function showToastGlobal(msg) {
  let t = document.getElementById('global-toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'global-toast';
    t.className = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2500);
}

// ── انتعاش الصور عند الخطأ ───────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('img').forEach(img => {
    img.addEventListener('error', function () {
      const parent = this.closest('.note-thumb, .gallery-item');
      if (parent && !this.dataset.errHandled) {
        this.dataset.errHandled = '1';
        this.style.display = 'none';
        // عرض placeholder نصي
        const letter = (this.alt || 'م')[0];
        const ph = document.createElement('div');
        ph.className = 'note-thumb-placeholder';
        ph.innerHTML = `<span>${letter}</span>`;
        parent.appendChild(ph);
      }
    });
  });
});

// ── مساعد HTML escape ─────────────────────────────────────────────
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
