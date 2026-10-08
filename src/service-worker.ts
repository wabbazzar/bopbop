/// <reference types="@sveltejs/kit" />
/// <reference no-default-lib="true" />
/// <reference lib="esnext" />
/// <reference lib="webworker" />
import { build, files, version } from '$service-worker';
const sw = self as unknown as ServiceWorkerGlobalScope;
const PREFIX = 'bopbop-shell-';
const CACHE = `${PREFIX}${version}`;
const ASSETS = new Set([...build, ...files]);

sw.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await cache.addAll([...ASSETS, '/index.html']);
  })());
  // Activate after old tabs close, so their lazy imports keep working.
});
sw.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith(PREFIX) && key !== CACHE) await caches.delete(key);
    }
    await sw.clients.claim();
  })());
});
sw.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== sw.location.origin ||
      url.pathname.startsWith('/api/')) return;
  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try { return await fetch(request); }
      catch { return (await (await caches.open(CACHE)).match('/index.html')) ?? new Response('Offline', { status: 503 }); }
    })());
  } else if (ASSETS.has(url.pathname)) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE);
      const cached = await cache.match(url.pathname);
      if (cached) return cached;
      const fresh = await fetch(request);
      if (fresh.ok) await cache.put(url.pathname, fresh.clone());
      return fresh;
    })());
  }
});
