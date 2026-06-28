/**
 * MIDAS Mini Player — persistent across all pages
 * Injects its own CSS + HTML, saves state to localStorage.
 * Default: compact bar (no video). Expand to watch.
 */
(function(){
'use strict';

const MP_STREAMS = [
  {label:'Bloomberg',  channelId:'UCIALMKvObZNtJ6AmdCLP7Lg'},
  {label:'CNBC TV',    channelId:'UCvJJ_dzjViJCoLf5uKUTwoA'},
  {label:'Al Jazeera', channelId:'UCfiwzLy-8yKzIbsmZTzxDgw'},
  {label:'Sky News',   channelId:'UCkFclpi8U9VJjfxLYoms7Aw'},
  {label:'France 24',  channelId:'UCCCPCZNChQdGa9EkATeye4g'},
  {label:'TRT World',  channelId:'UCnyCrv8b7bu0oWFXGyHaPzg'},
];

// ── State ────────────────────────────────────────────────────────────────────
let _idx      = parseInt(localStorage.getItem('mp_idx') || '0');
let _active   = localStorage.getItem('mp_active') === '1';
let _expanded = localStorage.getItem('mp_expanded') === '1';
let _muted    = false;
let _paused   = false;
let _vol      = 80;

function _save(){
  localStorage.setItem('mp_idx',     _idx);
  localStorage.setItem('mp_active',  _active  ? '1' : '0');
  localStorage.setItem('mp_expanded',_expanded ? '1' : '0');
}

// ── CSS ──────────────────────────────────────────────────────────────────────
const CSS = `
#mp-wrap{
  position:fixed;bottom:0;right:0;z-index:9000;
  width:300px;background:#0e0d0b;border:1px solid #3a3530;
  border-bottom:none;border-right:none;
  box-shadow:0 6px 32px rgba(0,0,0,.55);
  transition:transform .25s ease,opacity .25s ease;
  font-family:'Share Tech Mono','Courier New',monospace;
}
@media(max-width:480px){
  #mp-wrap{width:100%;right:0;border-left:none;}
}
#mp-wrap.mp-off{transform:translateY(130%);opacity:0;pointer-events:none;}
#mp-bar{
  display:flex;align-items:center;gap:6px;
  padding:0 8px;height:40px;background:#151310;
  border-bottom:1px solid #2a2520;cursor:default;
}
.mp-dot{width:5px;height:5px;border-radius:50%;background:#e05050;flex-shrink:0;
  animation:mp-pulse 1.5s ease-in-out infinite;}
@keyframes mp-pulse{0%,100%{opacity:1}50%{opacity:.35}}
.mp-lbl{flex:1;font-size:.6rem;letter-spacing:.18em;color:#c8b88a;
  overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}
.mp-b{background:none;border:none;color:#8a8078;font-size:.56rem;letter-spacing:.08em;
  cursor:pointer;padding:3px 5px;line-height:1;transition:color .15s;flex-shrink:0;}
.mp-b:hover{color:#fff;}
.mp-b.mp-ba{color:#c8b88a;}
#mp-ctrls{
  display:flex;align-items:center;gap:5px;
  padding:6px 8px;background:#111009;
}
.mp-cb{background:none;border:none;color:#9a9088;font-size:.8rem;
  cursor:pointer;padding:3px 6px;line-height:1;transition:color .15s;flex-shrink:0;}
.mp-cb:hover{color:#fff;}
.mp-cb.mp-ca{color:#c8b88a;}
.mp-sidx{font-size:.56rem;color:#4a4540;padding:0 3px;white-space:nowrap;}
.mp-vwrap{display:flex;align-items:center;gap:5px;flex:1;margin-left:4px;}
.mp-vi{font-size:.54rem;letter-spacing:.08em;color:#9a9088;cursor:pointer;flex-shrink:0;min-width:40px;}
.mp-vi:hover{color:#fff;}
.mp-vs{-webkit-appearance:none;appearance:none;height:3px;flex:1;
  background:#3a3530;outline:none;cursor:pointer;border-radius:2px;}
.mp-vs::-webkit-slider-thumb{-webkit-appearance:none;width:10px;height:10px;
  border-radius:50%;background:#c8b88a;cursor:pointer;}
#mp-video-box{display:none;position:relative;width:100%;aspect-ratio:16/9;background:#000;}
#mp-video-box.mp-show{display:block;}
#mp-iframe{width:100%;height:100%;border:none;display:block;}
`;

// ── HTML ─────────────────────────────────────────────────────────────────────
const HTML = `
<div id="mp-video-box">
  <iframe id="mp-iframe" src="" allowfullscreen allow="autoplay;encrypted-media"></iframe>
</div>
<div id="mp-bar">
  <span class="mp-dot"></span>
  <span class="mp-lbl" id="mp-lbl">LIVE</span>
  <button class="mp-b" id="mp-expand-btn" onclick="mpToggleExpand()" title="Show video">VIDEO</button>
  <button class="mp-b" onclick="mpPopout()" title="Full screen">FULL</button>
  <button class="mp-b" onclick="mpClose()" title="Close">✕</button>
</div>
<div id="mp-ctrls">
  <button class="mp-cb" onclick="mpPrev()" title="Prev">◀</button>
  <button class="mp-cb" id="mp-play" onclick="mpTogglePlay()" title="Pause">⏸</button>
  <button class="mp-cb" onclick="mpNext()" title="Next">▶</button>
  <span class="mp-sidx" id="mp-sidx">1/6</span>
  <div class="mp-vwrap">
    <span class="mp-vi" id="mp-vi" onclick="mpToggleMute()">MUTED</span>
    <input type="range" class="mp-vs" id="mp-vs" min="0" max="100" value="80" oninput="mpSetVol(this.value)">
  </div>
</div>
`;

// ── Inject ───────────────────────────────────────────────────────────────────
const styleEl = document.createElement('style');
styleEl.textContent = CSS;
document.head.appendChild(styleEl);

const wrap = document.createElement('div');
wrap.id = 'mp-wrap';
wrap.className = 'mp-off';
wrap.innerHTML = HTML;
document.body.appendChild(wrap);

// ── Helpers ──────────────────────────────────────────────────────────────────
function _ytUrl(cid){
  return `https://www.youtube.com/embed/live_stream?channel=${cid}&autoplay=1&mute=1&enablejsapi=1&controls=0&modestbranding=1&rel=0`;
}
function _cmd(fn, args){
  const f = document.getElementById('mp-iframe');
  if(!f || !f.contentWindow) return;
  f.contentWindow.postMessage(JSON.stringify({event:'command',func:fn,args:args||''}),'*');
}
function _updateLabel(){
  const s = MP_STREAMS[_idx];
  document.getElementById('mp-lbl').textContent = '● LIVE — ' + s.label;
  document.getElementById('mp-sidx').textContent = (_idx+1) + '/' + MP_STREAMS.length;
}

// ── Public API ───────────────────────────────────────────────────────────────
window.mpOpen = function(idx){
  _idx    = (idx == null ? _idx : idx) % MP_STREAMS.length;
  _active = true;
  _paused = false;
  _muted  = true;   // browsers only autoplay when muted; user taps the speaker to unmute
  _save();
  const frame = document.getElementById('mp-iframe');
  frame.src = _ytUrl(MP_STREAMS[_idx].channelId);
  _updateLabel();
  document.getElementById('mp-play').textContent = '⏸';
  var _vi = document.getElementById('mp-vi'); if(_vi) _vi.textContent = 'MUTED';
  wrap.classList.remove('mp-off');
  // Restore expanded state
  const vbox = document.getElementById('mp-video-box');
  const ebtn = document.getElementById('mp-expand-btn');
  if(_expanded){ vbox.classList.add('mp-show'); ebtn.textContent='HIDE'; }
  else { vbox.classList.remove('mp-show'); ebtn.textContent='VIDEO'; }
  setTimeout(()=>_cmd('setVolume',[_vol]),1500);
  if(_muted) setTimeout(()=>_cmd('mute'),1600);
};

window.mpClose = function(){
  _active   = false;
  _save();
  wrap.classList.add('mp-off');
  setTimeout(()=>{ document.getElementById('mp-iframe').src=''; }, 300);
};

window.mpToggleExpand = function(){
  _expanded = !_expanded;
  _save();
  const vbox = document.getElementById('mp-video-box');
  const ebtn = document.getElementById('mp-expand-btn');
  vbox.classList.toggle('mp-show', _expanded);
  ebtn.textContent = _expanded ? 'HIDE' : 'VIDEO';
};

window.mpPopout = function(){
  const cid = MP_STREAMS[_idx].channelId;
  // Use dashboard overlay if available, otherwise new tab
  if(typeof openYtPlayerOverlay === 'function'){
    openYtPlayerOverlay(`https://www.youtube.com/embed/live_stream?channel=${cid}&autoplay=1&mute=1`);
  } else {
    window.open(`https://www.youtube.com/watch?v=live_stream&channel=${cid}`, '_blank');
  }
};

window.mpTogglePlay = function(){
  _paused = !_paused;
  const btn = document.getElementById('mp-play');
  if(_paused){ _cmd('pauseVideo'); btn.textContent='▶'; }
  else { _cmd('playVideo'); btn.textContent='⏸'; }
};

window.mpToggleMute = function(){
  _muted = !_muted;
  document.getElementById('mp-vi').textContent = _muted ? 'MUTED' : 'SOUND';
  _cmd(_muted ? 'mute' : 'unMute');
};

window.mpSetVol = function(v){
  _vol = parseInt(v);
  _cmd('setVolume',[_vol]);
  document.getElementById('mp-vi').textContent = _vol===0 ? 'MUTED' : 'SOUND';
};

window.mpNext = function(){ window.mpOpen((_idx+1) % MP_STREAMS.length); };
window.mpPrev = function(){ window.mpOpen((_idx-1+MP_STREAMS.length) % MP_STREAMS.length); };

// ── Auto-restore on page load ────────────────────────────────────────────────
if(_active){
  // Small delay so page finishes rendering first
  setTimeout(()=>window.mpOpen(_idx), 400);
}

})();
