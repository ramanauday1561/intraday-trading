const VERSION = "v17";
const SHELL = `shell-${VERSION}`, DATA = `data-${VERSION}`;
const ASSETS = ["./", "index.html", "kite.html", "assets/kite.js", "assets/app.js", "assets/styles.css", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png", "icons/favicon-32.png"];

self.addEventListener("install", (e) => { e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => ![SHELL, DATA].includes(k)).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (/\/data\/[\w\/-]+\.json$/.test(url.pathname)) { // all data: network-first, last good copy offline
    const key = url.pathname.slice(url.pathname.indexOf("data/"));
    e.respondWith(fetch(req).then((r) => { if (r.ok) { const copy = r.clone(); caches.open(DATA).then((c) => c.put(key, copy)); } return r; })
      .catch(() => caches.open(DATA).then((c) => c.match(key)).then((r) => r || Response.error())));
    return;
  }
  // shell: stale-while-revalidate
  e.respondWith(caches.match(req, { ignoreSearch: true }).then((hit) => {
    const net = fetch(req).then((r) => { if (r.ok) caches.open(SHELL).then((c) => c.put(req, r.clone())); return r; }).catch(() => hit);
    return hit || net;
  }));
});

self.addEventListener("push", (e) => {
  let m = {};
  try { m = e.data ? e.data.json() : {}; } catch { m = { body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(m.title || "Intraday Quant Desk", {
    body: m.body || "", icon: "icons/icon-192.png", badge: "icons/icon-192.png", tag: m.tag, data: { url: m.url || "./" } }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = new URL(e.notification.data && e.notification.data.url || "./", self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cs) => {
    for (const c of cs) if ("navigate" in c) return c.focus().then(() => c.navigate(url));
    return self.clients.openWindow(url);
  }));
});
