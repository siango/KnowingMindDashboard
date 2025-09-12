const VER = '20250913-062804';
const CACHE_NAME = 'km-cache-' + VER;
self.addEventListener('install', e=>{ self.skipWaiting(); });
self.addEventListener('activate', e=>{
  e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE_NAME).map(k=>caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', e=>{
  const req = e.request;
  e.respondWith(fetch(req).catch(()=>caches.match(req)));
});
