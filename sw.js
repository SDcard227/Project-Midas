/* Project Midas service worker — makes the app installable + a little offline-tolerant.
   Static pages are network-first with a cache fallback; API + uploads are never cached
   (so you never see stale market data or a stale feed). */
const CACHE = 'midas-v1';
const CORE = ['/', '/index.html', '/midas-theme.js', '/whispers.html', '/gossip.html',
              '/intelligence.html', '/pit.html', '/funnies.html', '/manifest.json'];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(CORE).catch(() => {})).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.pathname.startsWith('/api/') || u.pathname.startsWith('/uploads/')) {
    return;   // always hit the network for API calls + user uploads
  }
  e.respondWith(
    fetch(e.request)
      .then((r) => { const cp = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, cp)); return r; })
      .catch(() => caches.match(e.request))
  );
});
