/* Adds the headers that make the page cross-origin isolated (needed for multi-threaded ffmpeg.wasm); GitHub Pages can't set headers itself. */
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{
  if(e.request.cache==='only-if-cached'&&e.request.mode!=='same-origin')return;
  e.respondWith(fetch(e.request).then(r=>{
    if(!r.status)return r;
    const h=new Headers(r.headers);h.set('Cross-Origin-Embedder-Policy','require-corp');h.set('Cross-Origin-Opener-Policy','same-origin');
    return new Response(r.body,{status:r.status,statusText:r.statusText,headers:h});
  }).catch(()=>fetch(e.request)));
});
