// Minimal service worker. Cache the app shell so the PWA opens instantly
// even on a flaky connection. API and audio responses are network-only.

const CACHE = 'feynman-shell-v1';
const SHELL = ['/', '/app.js', '/users.js', '/debug.js', '/styles.css', '/manifest.json'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // API + audio + SSE: always go to the network. No caching.
  if (
    url.pathname.startsWith('/session/') ||
    url.pathname.startsWith('/audio/') ||
    url.pathname.startsWith('/profile/') ||
    url.pathname.startsWith('/diagnosis/') ||
    url.pathname.startsWith('/users') ||
    url.pathname.startsWith('/leaderboard') ||
    url.pathname === '/events'
  ) {
    return;
  }

  // Shell: stale-while-revalidate.
  event.respondWith(
    caches.match(req).then((cached) => {
      const fetched = fetch(req)
        .then((resp) => {
          if (resp && resp.ok) {
            const copy = resp.clone();
            caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
          }
          return resp;
        })
        .catch(() => cached);
      return cached || fetched;
    })
  );
});
