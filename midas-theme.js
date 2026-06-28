/* MIDAS — shared theme + nav helper.
   One line per page: <script src="midas-theme.js"></script> in <head>.
   Gives every page: dark mode (no flash), a Forward button mirroring Back,
   and a midasToggleTheme() API the Settings page calls. */
(function () {
  // 1) Dark-mode variable overrides + the Forward button styling (injected once).
  var css = ''
    + 'html[data-theme="dark"]{--bg:#14120e;--surface:#1c1915;--card:#211d17;--border:#332e25;'
    + '--border2:#4a4338;--text:#ece6da;--mid:#b6ac98;--dim:#8c8273;'
    + '--shadow:0 1px 3px rgba(0,0,0,.45),0 4px 18px rgba(0,0,0,.35);}'
    + 'html[data-theme="dark"] body{background:#14120e;color:#ece6da;}'
    + 'html[data-theme="dark"] nav{background:rgba(20,18,14,.96)!important;border-bottom-color:#332e25!important;}'
    + 'html[data-theme="dark"] .nav-burger{color:#ece6da!important;}'
    + 'html[data-theme="dark"] #midas-back,html[data-theme="dark"] #midas-fwd{background:rgba(20,18,14,.92)!important;border-color:#4a4338!important;color:#b6ac98!important;}'
    + 'html[data-theme="dark"] .news-mast,html[data-theme="dark"] .mh-name{border-color:#4a4338!important;}'
    + '#midas-fwd{position:fixed;top:72px;right:16px;z-index:150;font-family:"Share Tech Mono",monospace;'
    + 'font-size:.64rem;letter-spacing:.08em;text-transform:uppercase;background:rgba(245,240,232,.92);'
    + 'border:1px solid #c4bdb0;color:#5c5045;padding:5px 11px;border-radius:3px;text-decoration:none;'
    + 'backdrop-filter:blur(4px);cursor:pointer}'
    + '#midas-fwd:hover{color:#1e1a14;border-color:#1e1a14}'
    + '#midas-back.nav-off,#midas-fwd.nav-off{opacity:.32;pointer-events:none;cursor:default}'
    + '@media(max-width:760px){#midas-fwd{top:64px;right:10px;font-size:.56rem;padding:4px 8px}}'
    + '.midas-toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%) translateY(12px);z-index:9999;background:#1e1a14;color:#f5f0e8;font-family:"Share Tech Mono",monospace;font-size:.78rem;letter-spacing:.03em;padding:11px 18px;border-radius:4px;box-shadow:0 8px 28px rgba(0,0,0,.25);opacity:0;transition:opacity .25s,transform .25s;max-width:90vw;text-align:center}'
    + '.midas-toast.show{opacity:1;transform:translateX(-50%) translateY(0)}'
    + '.midas-toast.err{background:#8a3030;color:#fff}'
    + 'html[data-theme="dark"] .midas-toast{background:#ece6da;color:#14120e}';
  var st = document.createElement('style');
  st.textContent = css;
  document.head.appendChild(st);

  // 2) Apply saved theme immediately (this runs in <head>, before paint = no flash).
  try {
    if (localStorage.getItem('midas-theme') === 'dark')
      document.documentElement.setAttribute('data-theme', 'dark');
  } catch (e) {}

  // 3) Theme API for the Settings page.
  window.midasToggleTheme = function () {
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    if (dark) { document.documentElement.removeAttribute('data-theme'); _save('light'); }
    else { document.documentElement.setAttribute('data-theme', 'dark'); _save('dark'); }
    return !dark;
  };
  window.midasIsDark = function () {
    return document.documentElement.getAttribute('data-theme') === 'dark';
  };
  function _save(v) { try { localStorage.setItem('midas-theme', v); } catch (e) {} }

  // Toast — on-site replacement for browser alert(). midasToast("msg") or ("msg","error").
  window.midasToast = function (msg, kind) {
    var t = document.createElement('div');
    t.className = 'midas-toast' + (kind === 'error' ? ' err' : '');
    t.textContent = msg;
    document.body.appendChild(t);
    requestAnimationFrame(function () { t.classList.add('show'); });
    setTimeout(function () {
      t.classList.remove('show');
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 320);
    }, 3200);
  };

  // 5) PWA — make Midas installable (manifest + apple meta + service worker).
  (function () {
    function meta(n, c) { var m = document.createElement('meta'); m.name = n; m.content = c; document.head.appendChild(m); }
    if (!document.querySelector('link[rel="manifest"]')) {
      var lm = document.createElement('link'); lm.rel = 'manifest'; lm.href = '/manifest.json';
      document.head.appendChild(lm);
    }
    meta('theme-color', '#f5f0e8');
    meta('apple-mobile-web-app-capable', 'yes');
    meta('apple-mobile-web-app-status-bar-style', 'default');
    meta('apple-mobile-web-app-title', 'Midas');
    var la = document.createElement('link'); la.rel = 'apple-touch-icon'; la.href = '/apple-touch-icon.png';
    document.head.appendChild(la);
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', function () {
        navigator.serviceWorker.register('/sw.js').catch(function () {});
      });
    }
  })();

  // Grey Back/Forward when there's nowhere to go. The Navigation API (canGoBack/
  // canGoForward) is exact where supported; we fall back to history.length for Back,
  // and hide Forward when it can't be detected (a dead Forward button is worse than none).
  window.midasNavState = function () {
    var back = document.getElementById('midas-back');
    var fwd  = document.getElementById('midas-fwd');
    var nv   = window.navigation;
    var canBack = (nv && typeof nv.canGoBack === 'boolean') ? nv.canGoBack : (history.length > 1);
    if (back) back.classList.toggle('nav-off', !canBack);
    if (fwd) {
      if (nv && typeof nv.canGoForward === 'boolean') { fwd.style.display = ''; fwd.classList.toggle('nav-off', !nv.canGoForward); }
      else { fwd.style.display = 'none'; }
    }
  };
  window.addEventListener('pageshow', function () { if (window.midasNavState) window.midasNavState(); });

  // 4) Forward button (mirrors Back) + a Settings gear in the nav (reachable everywhere).
  document.addEventListener('DOMContentLoaded', function () {
    if (!document.getElementById('midas-fwd')) {
      var a = document.createElement('a');
      a.id = 'midas-fwd';
      a.href = '#';
      a.innerHTML = 'Forward &rarr;';
      a.title = 'Forward';
      a.onclick = function (e) { e.preventDefault(); history.forward(); };
      document.body.appendChild(a);
    }
    var nav = document.querySelector('nav .nav-links');
    if (nav && !nav.querySelector('.midas-gear')) {
      var li = document.createElement('li');
      var g = document.createElement('a');
      g.href = 'settings.html';
      g.className = 'midas-gear';
      g.title = 'Settings';
      g.innerHTML = '&#9881;';
      g.style.fontSize = '1.1rem';
      li.appendChild(g);
      var cta = nav.querySelector('.nav-cta');
      if (cta && cta.parentElement && cta.parentElement.tagName === 'LI')
        nav.insertBefore(li, cta.parentElement);
      else nav.appendChild(li);
    }
    window.midasNavState();
  });
})();
