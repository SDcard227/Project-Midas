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
    + '#midas-bell{position:fixed;bottom:20px;right:20px;z-index:300;font-family:"Share Tech Mono",monospace;font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;background:#1e1a14;color:#f5f0e8;border:none;padding:10px 16px;border-radius:22px;cursor:pointer;box-shadow:0 4px 16px rgba(30,26,20,.28);display:none}'
    + '#midas-bell.show{display:block}'
    + '#midas-bell-n{background:#8a3030;color:#fff;border-radius:9px;padding:0 5px;margin-left:6px;display:none}'
    + '#midas-bell-n.on{display:inline-block}'
    + '#midas-notif{position:fixed;bottom:64px;right:20px;z-index:300;width:320px;max-width:90vw;max-height:60vh;overflow-y:auto;background:#fff;border:1px solid #c4bdb0;border-radius:6px;box-shadow:0 12px 40px rgba(30,26,20,.25);display:none}'
    + '#midas-notif.open{display:block}'
    + '.mn-h{padding:11px 15px;border-bottom:1px solid #ddd8ce;font-family:"Share Tech Mono",monospace;font-size:.62rem;letter-spacing:.12em;text-transform:uppercase;color:#9a9088}'
    + '.mn-i{display:block;padding:11px 15px;border-bottom:1px solid #ede8dd;font-size:.84rem;color:#1e1a14;text-decoration:none}'
    + '.mn-i:hover{background:#f5f0e8}.mn-i.unread{background:#fbf6ee}'
    + '.mn-i .mt{font-family:"Share Tech Mono",monospace;font-size:.52rem;color:#9a9088;margin-top:3px;text-transform:uppercase;letter-spacing:.08em}'
    + '.mn-e{padding:24px;text-align:center;color:#9a9088;font-family:"Share Tech Mono",monospace;font-size:.72rem}'
    + 'html[data-theme="dark"] #midas-notif{background:#211d17;border-color:#4a4338}html[data-theme="dark"] .mn-i{color:#ece6da;border-color:#332e25}html[data-theme="dark"] .mn-i.unread{background:#2a2620}'
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

  // Unread DM/notification count -> a red badge on the Messages tab. Polled.
  function _mnEsc(s){ return (s||'').replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];}); }
  window.midasPollNotif = function () {
    fetch('/api/notifications').then(function (r) { return r.json(); }).then(function (d) {
      window._midasNotif = (d && d.items) || [];
      var n = document.getElementById('midas-bell-n'); if (!n) return;
      if (d && d.unread > 0) { n.textContent = d.unread > 9 ? '9+' : d.unread; n.classList.add('on'); }
      else { n.classList.remove('on'); }
    }).catch(function () {});
  };
  window.midasToggleNotif = function () {
    var dd = document.getElementById('midas-notif'); if (!dd) return;
    if (dd.classList.contains('open')) { dd.classList.remove('open'); return; }
    var items = window._midasNotif || [];
    dd.innerHTML = '<div class="mn-h">Alerts</div>' + (items.length
      ? items.map(function (it) { return '<a class="mn-i' + (it.read ? '' : ' unread') + '" href="' + (it.link || 'javascript:void(0)') + '">' + _mnEsc(it.text) + '<div class="mt">' + _mnEsc(it.kind) + '</div></a>'; }).join('')
      : '<div class="mn-e">No alerts yet.</div>');
    dd.classList.add('open');
    fetch('/api/notifications/read', { method: 'POST' }).then(function () {
      var n = document.getElementById('midas-bell-n'); if (n) n.classList.remove('on');
    }).catch(function () {});
  };
  function _ensureBell() {
    if (document.getElementById('midas-bell')) return;
    var b = document.createElement('button'); b.id = 'midas-bell'; b.innerHTML = 'Alerts<span id="midas-bell-n"></span>';
    b.onclick = function (e) { e.stopPropagation(); window.midasToggleNotif(); };
    var d = document.createElement('div'); d.id = 'midas-notif';
    document.body.appendChild(b); document.body.appendChild(d);
    document.addEventListener('click', function () { var x = document.getElementById('midas-notif'); if (x) x.classList.remove('open'); });
    b.classList.add('show');
  }

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
    // Canonical nav — rendered identically on every page (active link auto-detected by
    // URL). Public sections always show; the personal tabs (Profile, Messages, Settings)
    // only appear once you're logged in.
    var links = document.querySelector('nav .nav-links');
    if (links) {
      var path = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
      var PUBLIC = [
        ['intelligence.html', 'Signals'], ['whispers.html', 'The Wire'],
        ['gossip.html', 'The Floor'], ['parlor.html', 'The Parlor'],
        ['exchange.html', 'The Exchange'], ['pit.html', 'The Pit'],
        ['practice.html', 'Replay'], ['dashboard.html', 'Command']
      ];
      var _item = function (n) {
        return '<li><a href="' + n[0] + '"' + (n[0] === path ? ' class="active"' : '') + '>' + n[1] + '</a></li>';
      };
      var _polling = false;
      var _buildNav = function (user) {
        var html = PUBLIC.map(_item).join('');
        if (user) {
          html += _item(['profile.html', 'Profile']);
          html += _item(['messages.html', 'Messages']);
          html += '<li><a href="account.html" class="nav-cta' + (path === 'account.html' ? ' active' : '') + '">Account</a></li>';
          html += '<li><a href="settings.html" class="midas-gear" title="Settings" style="font-size:1.1rem">&#9881;</a></li>';
        } else {
          html += '<li><a href="account.html" class="nav-cta' + (path === 'account.html' ? ' active' : '') + '">Get Access</a></li>';
        }
        links.innerHTML = html;
        window.midasNavState();
        if (user) { _ensureBell(); if (!_polling) { _polling = true; window.midasPollNotif(); setInterval(window.midasPollNotif, 45000); } }
      };
      _buildNav(null);   // public bar first — logged-out users never see personal tabs
      fetch('/api/me').then(function (r) { return r.json(); })
        .then(function (d) { if (d && d.user) _buildNav(d.user); }).catch(function () {});
    }
    window.midasNavState();
  });
})();
