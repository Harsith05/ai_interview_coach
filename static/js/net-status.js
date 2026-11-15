// Connection watcher, loaded on every page (base.html).
// - Registers the service worker that shows the offline page when the server is down.
// - Watches fetch(): if a request can't reach the server (network error, 502/504 from a proxy,
//   or a 503 with Retry-After from our DB-down handler), shows a banner and polls /healthz
//   until the server answers again. Errors still reach the page's own handlers unchanged.
(() => {
  if ('serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
  }

  const origFetch = window.fetch.bind(window);
  let banner = null, timer = null, wait = 3000;

  function ensureBanner() {
    if (banner) return banner;
    const style = document.createElement('style');
    style.textContent = `
      .net-banner { position: fixed; left: 50%; bottom: 20px; transform: translateX(-50%); z-index: 1000;
        display: flex; align-items: center; gap: 12px; max-width: calc(100% - 32px); padding: 12px 16px;
        border-radius: 14px; font: 500 14px/1.4 Inter, system-ui, sans-serif; box-shadow: 0 12px 40px rgba(15,23,42,.18);
        background: #fff7ed; color: #92400e; border: 1px solid #fed7aa; }
      .net-banner.ok { background: #f0fdf4; color: #166534; border-color: #bbf7d0; }
      .net-banner button { font: inherit; font-weight: 700; background: none; border: 0; color: inherit;
        text-decoration: underline; cursor: pointer; padding: 0; white-space: nowrap; }
      .net-banner .x { text-decoration: none; opacity: .6; font-size: 18px; line-height: 1; }`;
    document.head.appendChild(style);
    banner = document.createElement('div');
    banner.className = 'net-banner';
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    return banner;
  }

  function show(ok, text, actionLabel, action) {
    const b = ensureBanner();
    b.classList.toggle('ok', ok);
    b.innerHTML = '';
    b.append(document.createTextNode(text));
    if (actionLabel) {
      const btn = document.createElement('button');
      btn.type = 'button'; btn.textContent = actionLabel; btn.onclick = action;
      b.append(btn);
    }
    const x = document.createElement('button');
    x.type = 'button'; x.className = 'x'; x.textContent = '×'; x.setAttribute('aria-label', 'Dismiss');
    x.onclick = () => b.remove();
    b.append(x);
    if (!b.isConnected) document.body.appendChild(b);
  }

  async function serverUp() {
    try { return (await origFetch('/healthz', { cache: 'no-store' })).ok; } catch { return false; }
  }

  async function poll() {
    if (await serverUp()) {
      timer = null; wait = 3000;
      show(true, 'Connection restored. If something stopped, retry it now.', 'Reload page', () => location.reload());
      setTimeout(() => { if (banner?.classList.contains('ok')) banner.remove(); }, 8000);
      return;
    }
    wait = Math.min(wait * 1.5, 30000);
    timer = setTimeout(poll, wait);
  }

  function serverDown() {
    show(false, "Can't reach the server. We'll keep trying, and your progress so far is saved.", 'Retry now', () => {
      clearTimeout(timer); timer = null; wait = 3000; poll();
    });
    if (!timer) timer = setTimeout(poll, wait);
  }

  window.fetch = async (...args) => {
    const url = String(args[0]?.url ?? args[0] ?? '');
    try {
      const r = await origFetch(...args);
      if (!url.includes('/healthz') &&
          (r.status === 502 || r.status === 504 || (r.status === 503 && r.headers.has('Retry-After')))) serverDown();
      return r;
    } catch (e) {
      if (e?.name !== 'AbortError') serverDown();
      throw e;
    }
  };

  window.addEventListener('offline', serverDown);
  window.addEventListener('online', () => { if (timer) { clearTimeout(timer); timer = null; } poll(); });
})();
