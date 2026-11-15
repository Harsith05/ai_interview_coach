// Service worker: when a page can't be loaded because the server is down (or the network is
// gone), show the cached "Can't reach the server" page instead of the browser's own error.
// It only touches page navigations; API calls, streams and static files go straight to the network.
// Bump CACHE when templates/error.html changes so clients pick up the new offline page.
const CACHE = 'aic-offline-v1';
const OFFLINE_URL = '/offline';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.add(new Request(OFFLINE_URL, { cache: 'reload' }))));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.startsWith('aic-') && k !== CACHE).map((k) => caches.delete(k)));
    if (self.registration.navigationPreload) await self.registration.navigationPreload.enable();
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.mode !== 'navigate' || req.method !== 'GET') return;
  event.respondWith((async () => {
    try {
      return (await event.preloadResponse) || (await fetch(req));
    } catch {
      return (await caches.match(OFFLINE_URL)) || Response.error();
    }
  })());
});
