/* Service Worker: App-Shell aus dem Cache, API immer über das Netz, Web Push. */
"use strict";

// Eine Versionsnummer für Cache UND Assets — bei Frontend-Änderungen hochzählen
// (muss zu den ?v=-Parametern in index.html passen).
const VERSION = "32";
const CACHE_NAME = `wm26-v${VERSION}`;
const SHELL = [
  "/",
  `/styles.css?v=${VERSION}`,
  `/app.js?v=${VERSION}`,
  `/boot.js?v=${VERSION}`,
  "/fonts/space-grotesk-var.woff2",
  "/fonts/space-grotesk-var-ext.woff2",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/icon-180.png",
  // Illustrationen (Empty States müssen auch offline da sein)
  "/illustrationen/hero-login.webp",
  "/illustrationen/hero-push.webp",
  "/illustrationen/hero-team-pick.webp",
  "/illustrationen/empty-no-matches.webp",
  "/illustrationen/empty-no-live.webp",
  "/illustrationen/empty-no-news.webp",
  "/illustrationen/empty-no-tipps.webp",
  "/illustrationen/error-offline.webp",
  "/illustrationen/success-tipp.webp",
  "/illustrationen/volltreffer.webp",
  "/illustrationen/podium.webp",
  "/illustrationen/bonus-question.webp",
  "/illustrationen/champion-placeholder.webp",
  "/illustrationen/ki-chip.webp",
  "/illustrationen/stadium-hero.webp",
  "/illustrationen/news-fallback.webp",
];

self.addEventListener("install", (ereignis) => {
  ereignis.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (ereignis) => {
  ereignis.waitUntil(
    caches
      .keys()
      .then((namen) =>
        Promise.all(namen.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (ereignis) => {
  const url = new URL(ereignis.request.url);
  // Nur eigene GET-Anfragen behandeln; /api/ geht immer ans Netz (Live-Daten).
  if (
    ereignis.request.method !== "GET" ||
    url.origin !== self.location.origin ||
    url.pathname.startsWith("/api/")
  ) {
    return;
  }
  ereignis.respondWith(
    caches.match(ereignis.request).then(
      (treffer) =>
        treffer ||
        fetch(ereignis.request).then((antwort) => {
          if (antwort.ok) {
            const kopie = antwort.clone();
            caches
              .open(CACHE_NAME)
              .then((cache) => cache.put(ereignis.request, kopie))
              .catch(() => {
                /* Cache-Fehler (z. B. volles Kontingent) dürfen die Antwort nicht stören */
              });
          }
          return antwort;
        })
    )
  );
});

/* ----- Web Push (SPEC 5.5) ----- */

self.addEventListener("push", (ereignis) => {
  let daten = { titel: "WM26", text: "", url: "/" };
  try {
    daten = { ...daten, ...ereignis.data.json() };
  } catch {
    /* Payload fehlt oder ist kein JSON — Standardtext anzeigen */
  }
  ereignis.waitUntil(
    self.registration.showNotification(daten.titel, {
      body: daten.text,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url: daten.url },
      tag: daten.url, // gleiche URL ersetzt die alte Meldung statt zu stapeln
    })
  );
});

self.addEventListener("notificationclick", (ereignis) => {
  ereignis.notification.close();
  const ziel = ereignis.notification.data?.url || "/";
  ereignis.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((fenster) => {
      // Fenster, das bereits exakt das Ziel zeigt, nur fokussieren
      const zielUrl = new URL(ziel, self.location.origin);
      for (const client of fenster) {
        const clientUrl = new URL(client.url);
        if (
          clientUrl.pathname === zielUrl.pathname &&
          clientUrl.hash === zielUrl.hash &&
          "focus" in client
        ) {
          return client.focus();
        }
      }
      // Sonst ein offenes App-Fenster zum Ziel navigieren (Nutzerintention:
      // die Meldung wurde angeklickt), erst danach ein neues öffnen.
      for (const client of fenster) {
        if ("focus" in client && "navigate" in client) {
          return client.navigate(ziel).then(() => client.focus());
        }
      }
      return self.clients.openWindow(ziel);
    })
  );
});
