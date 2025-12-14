const CACHE_NAME = 'webssh-cache-v1';
const urlsToCache = [
    '/',
    '/static/css/bootstrap.min.css',
    '/static/css/xterm.min.css',
    '/static/css/fullscreen.min.css',
    '/static/js/jquery.min.js',
    '/static/js/popper.min.js',
    '/static/js/bootstrap.min.js',
    '/static/js/xterm.min.js',
    '/static/js/xterm-addon-fit.min.js',
    '/static/js/main.js',
    '/static/img/favicon-16.png',
    '/static/img/favicon-32.png',
    '/static/img/favicon-96.png'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(urlsToCache))
    );
});

self.addEventListener('fetch', event => {
    event.respondWith(
        caches.match(event.request)
            .then(response => response || fetch(event.request))
    );
});
