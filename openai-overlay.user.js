// ==UserScript==
// @name         OpenAI browser activity overlay
// @namespace    local.openai.browser-activity
// @version      1.1.1
// @description  Shows local assistant updates and page activity without sending page content anywhere.
// @match        https://*/*
// @match        http://*/*
// @exclude      http://127.0.0.1:17391/*
// @exclude      http://localhost:17391/*
// @noframes
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';
  const feed = 'http://127.0.0.1:17391/status';
  const defaults = { corner: 'bottom-right', compact: false, activity: true, onlyUpdated: false };
  let prefs = { ...defaults, ...GM_getValue('preferences', {}) };
  let siteHidden = GM_getValue('hidden:' + location.origin, false);
  const host = document.createElement('div');
  host.style.cssText = 'all:initial;position:fixed;right:16px;bottom:16px;z-index:2147483647;display:block';
  const root = host.attachShadow({ mode: 'closed' });
  root.innerHTML = `<style>
    :host{color-scheme:light}*{box-sizing:border-box}
    section{width:min(330px,calc(100vw - 32px));max-height:calc(100vh - 32px);overflow:auto;background:#132b28;color:#edf6f2;border:1px solid #42655d;border-radius:14px;box-shadow:0 8px 32px #0004;font:13px/1.5 system-ui;text-align:left}
    header{position:sticky;top:0;z-index:1;background:#132b28;display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid #42655d}
    strong{flex:1;font-size:13px}button,select{color:inherit;background:#132b28;border:1px solid #66877f;border-radius:6px;padding:2px 8px;cursor:pointer;font:inherit}
    .body{padding:12px}.label{font-size:10px;text-transform:uppercase;letter-spacing:.09em;color:#b1cbc2;margin-top:10px}.label:first-child{margin-top:0}
    p{margin:3px 0;overflow-wrap:anywhere;white-space:pre-wrap}small{font-size:11px;color:#b1cbc2}
    section.folded .body,section.compact .details{display:none}section.folded header{border-bottom:0}
    .options{border-top:1px solid #42655d;margin-top:10px;padding-top:10px}.options[hidden]{display:none}
    label{display:block;margin:8px 0}select{max-width:100%}input{vertical-align:middle}.options small{display:block}
    a{color:#baf3dc}time{display:block;font-size:11px;color:#b1cbc2}
  </style><section aria-label="OpenAI activity overlay"><header><strong>OpenAI · browser activity</strong><button class="settings-toggle" type="button" aria-label="Overlay options" aria-expanded="false">Options</button><button class="fold-toggle" type="button" aria-label="Minimize overlay">−</button></header>
  <div class="body"><div class="label">Assistant update</div><p class="update">Connecting to local feed…</p><time></time>
  <div class="details"><div class="label">This page</div><p class="page"></p><small class="address"></small>
  <div class="activity-block"><div class="label">Page activity</div><p class="activity">Page opened</p></div>
  <small class="connection"></small></div><a href="http://127.0.0.1:17391/" target="_blank" rel="noopener noreferrer">Local update panel</a>
  <div class="options" hidden><small>Options apply to all sites and tabs.</small>
  <label>Position <select class="corner"><option value="bottom-right">Bottom right</option><option value="bottom-left">Bottom left</option><option value="top-right">Top right</option><option value="top-left">Top left</option></select></label>
  <label><input class="compact" type="checkbox"> Compact view</label>
  <label><input class="show-activity" type="checkbox"> Show page activity</label>
  <label><input class="only-updated" type="checkbox"> Only show pages with an assistant update</label>
  <button class="hide-site" type="button">Hide on this site</button>
  <small>Restore using Violentmonkey's “Show overlay on this site” menu command.</small>
  <small>v1.1.1 · local feed only</small></div></div></section>`;
  document.documentElement.append(host);
  const $ = selector => root.querySelector(selector);
  const section = $('section');
  const toggle = $('.fold-toggle');
  toggle.addEventListener('click', () => {
    const folded = section.classList.toggle('folded');
    toggle.textContent = folded ? '+' : '−';
    toggle.setAttribute('aria-label', folded ? 'Expand overlay' : 'Minimize overlay');
  });
  let key = '';
  let cache = null;
  let pending = false;
  let connected = false;
  let lastSuccess = 0;
  function applyPrefs() {
    const top = prefs.corner.startsWith('top');
    const left = prefs.corner.endsWith('left');
    host.style.top = top ? '16px' : 'auto';
    host.style.bottom = top ? 'auto' : '16px';
    host.style.left = left ? '16px' : 'auto';
    host.style.right = left ? 'auto' : '16px';
    section.classList.toggle('compact', prefs.compact);
    $('.activity-block').hidden = !prefs.activity;
    $('.corner').value = prefs.corner;
    $('.compact').checked = prefs.compact;
    $('.show-activity').checked = prefs.activity;
    $('.only-updated').checked = prefs.onlyUpdated;
    const entry = cache?.pages?.[key] || cache?.global;
    host.style.display = siteHidden || (prefs.onlyUpdated && !entry) ? 'none' : 'block';
  }
  $('.settings-toggle').addEventListener('click', () => {
    $('.options').hidden = !$('.options').hidden;
    $('.settings-toggle').setAttribute('aria-expanded', String(!$('.options').hidden));
    section.classList.remove('folded');
    toggle.textContent = '−';
    toggle.setAttribute('aria-label', 'Minimize overlay');
  });
  for (const [selector, property, checkbox] of [
    ['.corner', 'corner', false], ['.compact', 'compact', true],
    ['.show-activity', 'activity', true], ['.only-updated', 'onlyUpdated', true]
  ]) {
    $(selector).addEventListener('change', event => {
      prefs[property] = checkbox ? event.target.checked : event.target.value;
      GM_setValue('preferences', prefs); applyPrefs();
    });
  }
  $('.hide-site').addEventListener('click', () => {
    siteHidden = true; GM_setValue('hidden:' + location.origin, true); applyPrefs();
  });
  GM_registerMenuCommand('Show overlay on this site', () => {
    siteHidden = false; GM_setValue('hidden:' + location.origin, false);
    prefs.onlyUpdated = false; GM_setValue('preferences', prefs); applyPrefs();
  });
  GM_registerMenuCommand('Reset overlay options', () => {
    prefs = { ...defaults }; GM_setValue('preferences', prefs);
    siteHidden = false; GM_setValue('hidden:' + location.origin, false); applyPrefs();
  });
  function paintPage() {
    const nextKey = location.origin + location.pathname;
    $('.page').textContent = document.title || location.hostname;
    $('.address').textContent = location.host + location.pathname;
    if (nextKey !== key) {
      key = nextKey;
      $('.activity').textContent = 'Page opened';
      paintUpdate();
    }
  }
  function paintUpdate() {
    const entry = cache?.pages?.[key] || cache?.global;
    $('.update').textContent = entry?.message || (connected ? 'No assistant update for this page yet.' : 'Local feed offline. Start server.py.');
    if (entry?.updated) {
      const date = new Date(entry.updated);
      $('time').textContent = `${entry.scope ? 'This page' : 'Shared update'} · ${date.toLocaleTimeString()}`;
      $('time').dateTime = entry.updated;
    } else {
      $('time').textContent = '';
    }
    $('.connection').textContent = connected ? 'Local feed connected · updates every 3 seconds' : 'Feed offline · last update may be stale';
    applyPrefs();
  }
  function poll() {
    if (pending || document.hidden) return;
    pending = true;
    GM_xmlhttpRequest({
      method: 'GET', url: feed, timeout: 2500, anonymous: true,
      onload(response) {
        try {
          if (response.status !== 200) throw new Error('Feed unavailable');
          const parsed = JSON.parse(response.responseText);
          if (!parsed || typeof parsed.pages !== 'object' || parsed.pages === null) throw new Error('Invalid feed');
          cache = parsed; connected = true; lastSuccess = Date.now();
        } catch (_) { connected = false; }
        pending = false; paintUpdate();
      },
      onerror: failed, ontimeout: failed, onabort: failed
    });
  }
  function failed() { pending = false; connected = false; paintUpdate(); }
  document.addEventListener('click', event => {
    if (event.composedPath().includes(host)) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest('a')) $('.activity').textContent = 'Link clicked';
    else if (target?.closest('button,[role="button"]')) $('.activity').textContent = 'Button clicked';
  }, true);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { paintPage(); poll(); } });
  paintPage(); poll();
  setInterval(() => {
    if (!host.isConnected) document.documentElement.append(host);
    prefs = { ...defaults, ...GM_getValue('preferences', {}) };
    siteHidden = GM_getValue('hidden:' + location.origin, false);
    paintPage();
    applyPrefs();
    if (connected && Date.now() - lastSuccess > 15000) { connected = false; paintUpdate(); }
    poll();
  }, 3000);
})();
