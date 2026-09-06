const CACHE_NAME='bmt-shell-v31-83';
const STATIC_ASSETS=[
  '/static/style.css','/static/student.js','/static/teacher.js','/static/admin.js',
  '/static/password-visibility.js','/static/auth-recovery.js','/static/smart_quiz_scanner.js',
  '/static/storage-service.js','/static/storage-config.js','/static/site.webmanifest',
  '/static/logo-favicon.svg','/static/logo-favicon.png','/static/favicon.ico'
];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const req=event.request;
  if(req.method !== 'GET') return;
  const url=new URL(req.url);
  if(url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  // Never serve stale HTML: deployments must become visible immediately.
  if(req.mode === 'navigate' || req.destination === 'document') {
    event.respondWith(fetch(req).catch(() => caches.match('/static/style.css').then(() => new Response('Offline: please reconnect and reload.', {status:503, headers:{'Content-Type':'text/plain; charset=utf-8'}}))));
    return;
  }
  // Static assets: network first, then versioned cache for offline use.
  event.respondWith(fetch(req).then(res => {
    if(res.ok && ['script','style','image','font'].includes(req.destination)) {
      const copy=res.clone(); caches.open(CACHE_NAME).then(c => c.put(req,copy));
    }
    return res;
  }).catch(() => caches.match(req)));
});
