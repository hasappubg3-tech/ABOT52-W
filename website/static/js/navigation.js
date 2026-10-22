(() => {
  const layer = document.getElementById('site-navigation');
  const opener = document.getElementById('navigation-toggle');
  const drawer = layer?.querySelector('.navigation-drawer');
  if (!layer || !opener || !drawer) return;

  const closeButtons = layer.querySelectorAll('[data-navigation-close]');
  const groupToggle = layer.querySelector('.navigation-group-toggle');
  const gradeList = document.getElementById('navigation-grades');
  const focusableSelector = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])'
  ].join(',');
  const backgroundElements = [
    document.querySelector('.navbar'),
    document.querySelector('.main-content'),
    document.querySelector('.footer')
  ].filter(Boolean);
  let previousFocus = null;
  let scrollPosition = 0;
  let savedBodyStyles = null;
  let savedBackgroundStates = [];

  function lockPageScroll() {
    scrollPosition = window.scrollY || window.pageYOffset;
    savedBodyStyles = {
      position: document.body.style.position,
      top: document.body.style.top,
      width: document.body.style.width,
      overflow: document.body.style.overflow
    };
    document.body.style.position = 'fixed';
    document.body.style.top = `-${scrollPosition}px`;
    document.body.style.width = '100%';
    document.body.style.overflow = 'hidden';
  }

  function unlockPageScroll() {
    if (!savedBodyStyles) return;
    Object.assign(document.body.style, savedBodyStyles);
    savedBodyStyles = null;
    // The site's smooth scrolling must not animate restoration from page top.
    window.scrollTo({ top: scrollPosition, left: 0, behavior: 'instant' });
  }

  function setBackgroundInert(inert) {
    if (inert) {
      savedBackgroundStates = backgroundElements.map(element => ({
        element,
        inert: element.inert,
        ariaHidden: element.getAttribute('aria-hidden')
      }));
      backgroundElements.forEach(element => {
        element.inert = true;
        element.setAttribute('aria-hidden', 'true');
      });
      return;
    }
    savedBackgroundStates.forEach(({ element, inert: wasInert, ariaHidden }) => {
      element.inert = wasInert;
      if (ariaHidden === null) element.removeAttribute('aria-hidden');
      else element.setAttribute('aria-hidden', ariaHidden);
    });
    savedBackgroundStates = [];
  }

  function openDrawer() {
    if (layer.classList.contains('is-open')) return;
    previousFocus = document.activeElement;
    lockPageScroll();
    layer.classList.add('is-open');
    layer.setAttribute('aria-hidden', 'false');
    opener.setAttribute('aria-expanded', 'true');
    drawer.querySelector('.navigation-close')?.focus({ preventScroll: true });
    setBackgroundInert(true);
    document.addEventListener('keydown', onKeyDown);
  }

  function closeDrawer({ restoreFocus = true } = {}) {
    if (!layer.classList.contains('is-open')) return;
    layer.classList.remove('is-open');
    layer.setAttribute('aria-hidden', 'true');
    opener.setAttribute('aria-expanded', 'false');
    document.removeEventListener('keydown', onKeyDown);
    setBackgroundInert(false);
    unlockPageScroll();
    if (restoreFocus) {
      requestAnimationFrame(() => {
        if (previousFocus instanceof HTMLElement) previousFocus.focus({ preventScroll: true });
      });
    }
  }

  function onKeyDown(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeDrawer();
      return;
    }
    if (event.key !== 'Tab') return;
    const controls = [...drawer.querySelectorAll(focusableSelector)]
      .filter(element => element.getClientRects().length && !element.closest('[hidden]'));
    if (!controls.length) {
      event.preventDefault();
      drawer.focus();
      return;
    }
    const first = controls[0];
    const last = controls[controls.length - 1];
    const activeIndex = controls.indexOf(document.activeElement);
    if (event.shiftKey && (activeIndex <= 0 || document.activeElement === drawer)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (activeIndex === controls.length - 1 || activeIndex === -1)) {
      event.preventDefault();
      first.focus();
    }
  }

  opener.addEventListener('click', openDrawer);
  closeButtons.forEach(button => button.addEventListener('click', () => closeDrawer()));
  groupToggle?.addEventListener('click', () => {
    const expanded = groupToggle.getAttribute('aria-expanded') === 'true';
    if (expanded && gradeList?.contains(document.activeElement)) groupToggle.focus();
    groupToggle.setAttribute('aria-expanded', String(!expanded));
    if (gradeList) gradeList.hidden = expanded;
  });
  layer.querySelector('.navigation-search')?.addEventListener('click', (event) => {
    // Keep the site's outside-click handler from immediately closing search.
    event.stopPropagation();
    closeDrawer({ restoreFocus: false });
    const searchBar = document.getElementById('search-bar');
    const searchInput = document.getElementById('search-input');
    if (!searchBar || !searchInput) return;
    if (searchBar.classList.contains('search-bar--persistent') || !searchBar.classList.contains('open')) {
      document.getElementById('search-toggle')?.click();
    }
    searchInput.focus({ preventScroll: true });
  });
})();
