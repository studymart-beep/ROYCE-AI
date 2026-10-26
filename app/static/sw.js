/* Royce AI service worker (served at /sw.js so its scope covers /m and /workspace).
   - Static app shell: cache-first from a versioned cache. Bump CACHE when shell files change.
   - /api/*: network-only. Never cached, never faked. Offline calls get a clear JSON 503 that the page shows.
   - Navigations that fail offline get the offline page, which says the AI needs a connection. */
const CACHE = "royce-shell-v5";
const SHELL = [
  "/m",
  "/workspace",
  "/offline",
  "/static/mobile.css",
  "/static/mobile.js",
  "/static/api-base.js",
  "/static/workspace.css",
  "/static/workspace.js",
  "/static/attach.js",
  "/static/icon.svg",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/icon-maskable-192.png",
  "/static/icon-maskable-512.png",
  "/static/manifest.json",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function offlineApiResponse() {
  return new Response(
    JSON.stringify({ error: "offline", message: "you're offline. AI needs a connection." }),
    { status: 503, headers: { "content-type": "application/json", "cache-control": "no-store" } }
  );
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  // owner data: always the network, never the cache
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(fetch(req).catch(() => offlineApiResponse()));
    return;
  }

  // static shell: cache-first
  if (SHELL.includes(url.pathname)) {
    e.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) caches.open(CACHE).then((c) => c.put(req, res.clone()));
        return res;
      }))
    );
    return;
  }

  // other page navigations: network, falling back to the offline page
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("/offline")));
  }
});
