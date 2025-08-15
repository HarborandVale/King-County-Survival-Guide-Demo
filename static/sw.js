const CACHE = "kcsg-v1";
const ASSETS = [
  "/", "/guided", "/map", "/privacy", "/manifest.json",
  "/static/logo.png"
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  // Try cache first, then network; update cache in background.
  e.respondWith(
    caches.match(e.request).then(cached => {
      const fetcher = fetch(e.request).then(resp => {
        if (resp.ok && (resp.type === "basic" || resp.type === "opaque")) {
          const copy = resp.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
        }
        return resp;
      }).catch(_ => cached || new Response("Offline", {status: 503}));
      return cached || fetcher;
    })
  );
});
