const VERSION = "v3";
const SHELL = `shell-${VERSION}`, DATA = `data-${VERSION}`;
const ASSETS = ["./", "index.html", "assets/app.js", "assets/styles.css", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png", "icons/favicon-32.png"];

self.addEventListener("install", (e) => { e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => ![SHELL, DATA].includes(k)).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.endsWith("/data/signals.json")) { // network-first; fall back to last good copy offline
    e.respondWith(fetch(req).then((r) => { const copy = r.clone(); caches.open(DATA).then((c) => c.put("data/signals.json", copy)); return r; })
      .catch(() => caches.open(DATA).then((c) => c.match("data/signals.json")).then((r) => r || Response.error())));
    return;
  }
  // shell: stale-while-revalidate
  e.respondWith(caches.match(req, { ignoreSearch: true }).then((hit) => {
    const net = fetch(req).then((r) => { if (r.ok) caches.open(SHELL).then((c) => c.put(req, r.clone())); return r; }).catch(() => hit);
    return hit || net;
  }));
});
