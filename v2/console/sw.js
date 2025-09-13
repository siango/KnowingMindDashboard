self.addEventListener('install', e=> self.skipWaiting());
self.addEventListener('activate', e=> self.clients.claim());
// no fetch handler -> let network handle normally (prevents 'Loading...' hang)
