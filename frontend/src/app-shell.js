/** Local navigation only. No fetch, model call, or research submission. */
export function searchNavigation(entries, query) {
  const needle = String(query ?? '').slice(0, 80).trim().normalize('NFKC').toLowerCase();
  const seen = new Set();
  return entries.filter((entry) => {
    if (!entry || !/^\/[a-z0-9/_-]*$/i.test(entry.href) || typeof entry.label !== 'string' || !entry.label.trim()) return false;
    if (seen.has(entry.href) || !entry.label.normalize('NFKC').toLowerCase().includes(needle)) return false;
    seen.add(entry.href);
    return true;
  }).slice(0, 12).map(({ href, label }) => ({ href, label }));
}

export function mountAppShell(doc) {
  const root = doc.querySelector('[data-finathink-shell]');
  if (!root || root._finathinkShellCleanup) return root?._finathinkShellCleanup;
  const view = doc.defaultView;
  const nav = root.querySelector('#shell-navigation');
  const frame = root.querySelector('[data-shell-frame]');
  const menu = root.querySelector('[data-shell-menu]');
  const close = root.querySelector('[data-shell-close]');
  const shade = root.querySelector('[data-shell-shade]');
  const search = doc.querySelector('#shell-search');
  const searchOpen = root.querySelector('[data-shell-search-open]');
  const searchClose = search?.querySelector('[data-shell-search-close]');
  const input = search?.querySelector('[data-shell-search-input]');
  const results = search?.querySelector('[data-shell-search-results]');
  const status = search?.querySelector('[data-shell-search-status]');
  if (!view || !nav || !frame || !menu || !close || !shade || !search || !input || !results) return;
  const mobile = view.matchMedia('(max-width: 800px)');
  const entries = [...nav.querySelectorAll('a.nav-link')].map((link) => ({ href: link.getAttribute('href'), label: link.textContent.trim() }));
  let opened = false;
  let searchPrevious = null;

  const updateNavigation = () => {
    nav.inert = mobile.matches && !opened;
    if (nav.inert) nav.setAttribute('aria-hidden', 'true');
    else nav.removeAttribute('aria-hidden');
    frame.inert = mobile.matches && opened;
    menu.setAttribute('aria-expanded', String(opened));
    root.dataset.navigationOpen = String(opened);
    shade.hidden = !opened;
    doc.body.style.overflow = opened ? 'hidden' : '';
  };
  const closeNavigation = (restore = true) => {
    const wasOpen = opened;
    opened = false;
    updateNavigation();
    if (wasOpen && restore && mobile.matches) menu.focus();
  };
  const openNavigation = () => {
    if (!mobile.matches) return;
    opened = true;
    updateNavigation();
    close.focus();
  };
  const resize = () => closeNavigation(false);
  const onNavClick = (event) => {
    if (event.target.closest('a[href]')) closeNavigation(false);
  };
  const updateResults = () => {
    const matches = searchNavigation(entries, input.value);
    results.replaceChildren(...matches.map((item) => {
      const link = doc.createElement('a');
      link.className = 'shell-search-result';
      link.href = item.href;
      const name = doc.createElement('span');
      name.textContent = item.label;
      const path = doc.createElement('small');
      path.textContent = item.href;
      link.append(name, path);
      return link;
    }));
    if (status) status.textContent = matches.length ? `找到 ${matches.length} 个功能入口` : '没有匹配的入口，请换一个关键词。';
  };
  const openSearch = () => {
    if (search.open) return;
    searchPrevious = doc.activeElement;
    closeNavigation(false);
    input.value = '';
    updateResults();
    search.showModal();
    input.focus();
  };
  const closeSearch = () => search.close();
  const restoreSearchFocus = () => {
    const target = searchPrevious?.isConnected && !searchPrevious.closest('[inert]') ? searchPrevious : searchOpen;
    target?.focus();
  };
  const onKey = (event) => {
    if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      openSearch();
    }
    if (event.key === 'Escape' && opened) {
      event.preventDefault();
      closeNavigation();
    }
    if (event.key === 'Tab' && opened) {
      const items = [...nav.querySelectorAll('a[href], button:not(:disabled), summary')].filter((item) => item.getClientRects().length && !item.hidden);
      const first = items[0];
      const last = items.at(-1);
      if (event.shiftKey && doc.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && doc.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  };
  root.dataset.shellEnhanced = 'true';
  menu.hidden = false;
  close.hidden = false;
  searchOpen.hidden = false;
  updateNavigation();
  menu.addEventListener('click', openNavigation);
  close.addEventListener('click', closeNavigation);
  shade.addEventListener('click', closeNavigation);
  nav.addEventListener('click', onNavClick);
  mobile.addEventListener('change', resize);
  searchOpen.addEventListener('click', openSearch);
  searchClose?.addEventListener('click', closeSearch);
  search.addEventListener('close', restoreSearchFocus);
  input.addEventListener('input', updateResults);
  doc.addEventListener('keydown', onKey);
  const cleanup = () => {
    closeNavigation(false);
    nav.inert = false;
    nav.removeAttribute('aria-hidden');
    menu.removeEventListener('click', openNavigation);
    close.removeEventListener('click', closeNavigation);
    shade.removeEventListener('click', closeNavigation);
    nav.removeEventListener('click', onNavClick);
    mobile.removeEventListener('change', resize);
    searchOpen.removeEventListener('click', openSearch);
    searchClose?.removeEventListener('click', closeSearch);
    search.removeEventListener('close', restoreSearchFocus);
    input.removeEventListener('input', updateResults);
    doc.removeEventListener('keydown', onKey);
    view.removeEventListener('pagehide', onPageHide);
    delete root.dataset.shellEnhanced;
    delete root._finathinkShellCleanup;
  };
  const onPageHide = (event) => { if (!event.persisted) cleanup(); };
  view.addEventListener('pagehide', onPageHide);
  root._finathinkShellCleanup = cleanup;
  return cleanup;
}
