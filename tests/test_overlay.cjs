const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(process.env.OVERLAY_SOURCE ||
  path.join(__dirname, '..', 'ai-overlay.user.js'), 'utf8');

// Run the userscript with isolated GM storage, events, and deterministic timers.
function harness(preferences = {}, assistantLayout = true, stylesheetSupport = 'supported', activation = { agent: 'Agent', session: 'chat-a', hash: '' }) {
  class Element {
    constructor(tag = 'div', classes = []) {
      this.tag = tag;
      this.children = [];
      this._text = "";
      this.classes = new Set(classes);
      this.listeners = new Map();
      this.style = {};
      this.attrs = {};
      this.classList = {
        toggle: (name, force = !this.classes.has(name)) => {
          force ? this.classes.add(name) : this.classes.delete(name);
          return force;
        },
        remove: name => this.classes.delete(name),
      };
    }
    get className() { return [...this.classes].join(' '); }
    set className(value) { this.classes = new Set(value.split(' ')); }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    set textContent(value) { this._text = value; this.children = []; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this._text = ''; this.children = children; }
    addEventListener(name, fn) {
      const listeners = this.listeners.get(name) || [];
      listeners.push(fn);
      this.listeners.set(name, listeners);
    }
    fire(name, extra = {}) {
      for (const fn of this.listeners.get(name) || []) {
        fn({ target: this, composedPath: () => [this], ...extra });
      }
    }
    setAttribute(name, value) { this.attrs[name] = value; }
    setPointerCapture(id) { this.pointer = id; }
    hasPointerCapture(id) { return this.pointer === id; }
    releasePointerCapture() { this.pointer = null; }
    closest() { return null; }
  }
  const elements = [];
  const root = new Element();
  if (stylesheetSupport !== 'unsupported') {
    root.adoptedStyleSheets = [];
  }
  Object.defineProperty(root, 'innerHTML', {
    set(html) {
      root.children = [];
      // Only tag, class, and hidden state are needed for these regressions.
      for (const match of html.matchAll(/<(section|header|button|div|input|select|p|small|time)\b([^>]*)>/g)) {
        const classes = /class="([^"]*)"/.exec(match[2]);
        const element = new Element(match[1], classes ? classes[1].split(' ') : []);
        element.hidden = /\bhidden\b/.test(match[2]);
        element.checked = false;
        elements.push(element);
      }
    },
  });
  const descendants = element => [element, ...element.children.flatMap(descendants)];
  root.querySelector = selector => elements.flatMap(descendants).find(element => selector.startsWith('.')
    ? element.classes.has(selector.slice(1)) : element.tag === selector);
  const host = new Element();
  const window = new Element();
  window.innerWidth = 1000;
  window.innerHeight = 800;
  host.getBoundingClientRect = () => ({
    left: host.style.left !== 'auto' ? parseFloat(host.style.left) : window.innerWidth - 346,
    top: host.style.top !== 'auto' ? parseFloat(host.style.top) : window.innerHeight - 216,
    width: 330, height: 200,
  });
  host.attachShadow = () => root;
  const document = new Element();
  document.title = 'Overlay regression test';
  document.hidden = false;
  let hostCreated = false;
  document.createElement = tag => {
    if (!hostCreated) { hostCreated = true; return host; }
    return new Element(tag);
  };
  document.documentElement = { append: element => { element.isConnected = true; } };
  const storage = new Map([['preferences', preferences], ['assistantLayout', assistantLayout]]);
  const timers = new Map();
  const requests = [];
  let timerId = 0;
  let interval;
  const marker = value => '#ai-activity=' + encodeURIComponent(JSON.stringify(value));
  const location = { origin: 'https://example.org', pathname: '/', host: 'example.org', search: '', hash: activation ? marker(activation) : '' };
  const history = { state: null, replaceState(_state, _title, url) { location.hash = url.includes('#') ? url.slice(url.indexOf('#')) : ''; } };
  const menus = new Map();
  class CSSStyleSheet {
    replaceSync(css) {
      if (stylesheetSupport === 'throws') throw new Error('constructed sheets unavailable');
      this.cssText = css;
    }
  }
  vm.runInNewContext(source, {
    document, window, Element, Date,
    CSSStyleSheet: stylesheetSupport === 'unsupported' ? undefined : CSSStyleSheet,
    location, history, prompt: () => null,
    GM_getValue: (name, fallback) => storage.get(name) ?? fallback,
    GM_setValue: (name, value) => storage.set(name, structuredClone(value)),
    GM_registerMenuCommand: (name, fn) => menus.set(name, fn),
    GM_xmlhttpRequest: request => requests.push(request),
    setInterval: fn => { interval = fn; },
    setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; },
    clearTimeout: id => timers.delete(id),
  });
  return { document, window, root, host, timers, requests, storage, location, history, menus, marker,
    tick: () => interval(),
    respond: channels => requests.at(-1).onload({ status: 200,
      responseText: JSON.stringify({ version: 2, channels }) }),
    $: selector => root.querySelector(selector),
    open: () => root.querySelector('.settings-toggle').fire('click') };
}


test('Feed panel detects version per document and never creates overlay or polls', () => {
  for (const hostname of ['localhost', '127.0.0.1']) {
    for (const [installed, expected] of [['1.5.4', 'Up to date'], ['1.5.3', 'Update available'],
      ['1.6.0', 'Newer than source'], ['1.5.4.0', 'Up to date'], ['bad', 'not detected']]) {
      const status = { dataset: { sourceVersion: '1.5.4' }, textContent: 'not detected' };
      let requested = 0;
      vm.runInNewContext(source, {
        location: { protocol: 'http:', port: '17391', hostname, pathname: '/' },
        document: { getElementById: () => status }, GM_info: { script: { version: installed } },
        GM_xmlhttpRequest: () => { requested++; },
      });
      assert.match(status.textContent, new RegExp(expected));
      assert.equal(requested, 0);
    }
    vm.runInNewContext(source, {
      location: { protocol: 'http:', port: '17391', hostname, pathname: '/status' },
      document: { getElementById: () => { throw new Error('must not touch non-panel'); } },
    });
  }
});

test('Options starts hidden and closes on outside pointer or Escape', () => {
  const h = harness();
  assert.equal(h.$('.options').hidden, true);
  h.open();
  assert.equal(h.$('.settings-toggle').attrs['aria-expanded'], 'true');
  h.document.fire('pointerdown', { composedPath: () => [h.host] });
  assert.equal(h.$('.options').hidden, false);
  h.document.fire('pointerdown');
  assert.equal(h.$('.options').hidden, true);
  assert.equal(h.$('.settings-toggle').attrs['aria-expanded'], 'false');
  h.open();
  h.document.fire('keydown', { key: 'Escape' });
  assert.equal(h.$('.options').hidden, true);
});

test('Options timeout resets during interaction and closes after ten idle seconds', () => {
  const h = harness();
  h.open();
  const original = [...h.timers.keys()][0];
  h.root.fire('input');
  assert.equal(h.timers.has(original), false);
  assert.equal(h.timers.size, 1);
  const timer = [...h.timers.values()][0];
  assert.equal(timer.delay, 10000);
  timer.fn();
  assert.equal(h.$('.options').hidden, true);
  assert.equal(h.timers.size, 0);
});

test('Minimizing or hiding a tab closes Options', () => {
  const h = harness();
  h.open();
  h.$('.fold-toggle').fire('click');
  assert.equal(h.$('.options').hidden, true);
  assert.equal(h.$('section').classes.has('folded'), true);
  h.open();
  assert.equal(h.$('section').classes.has('folded'), false);
  h.document.hidden = true;
  h.document.fire('visibilitychange');
  assert.equal(h.$('.options').hidden, true);
});

test('Stored compact preference targets the checkbox, even when section has compact class', () => {
  const h = harness({ compact: true });
  assert.equal(h.$('section').classes.has('compact'), true);
  assert.equal(h.$('.compact-option').checked, true);
  h.$('.compact-option').checked = false;
  h.$('.compact-option').fire('change');
  assert.equal(h.$('section').classes.has('compact'), false);
  assert.equal(h.storage.get('preferences').compact, false);
});

test('Default visibility includes pages without updates; offline guidance names the server', () => {
  const h = harness();
  assert.equal(h.host.style.display, 'block');
  h.requests[0].onerror();
  assert.match(h.$('.update').textContent, /Start overlay_server\.py\./);
});

test('Overlay CSS uses a constructed stylesheet before page CSP can block it', () => {
  const h = harness();
  assert.equal(h.root.adoptedStyleSheets.length, 1);
  assert.match(h.root.adoptedStyleSheets[0].cssText, /section\{width:min\(330px/);
  assert.equal(h.root.children.length, 0);
});

test('Overlay CSS falls back to a shadow style element when constructed sheets are unavailable', () => {
  for (const support of ['unsupported', 'throws']) {
    const h = harness({}, true, support);
    assert.equal(h.root.children[0].tag, 'style');
    assert.match(h.root.children[0].textContent, /section\{width:min\(330px/);
  }
});


test('Default layout uses bottom-right and hides page details and generic activity', () => {
  const h = harness();
  assert.equal(h.host.style.right, '16px');
  assert.equal(h.host.style.bottom, '16px');
  assert.equal(h.host.style.left, 'auto');
  assert.equal(h.$('section').classes.has('compact'), true);
  assert.equal(h.$('.activity-block').hidden, true);
  assert.equal(h.$('.options').hidden, true);
});

test('Existing layout migrates once, then keeps subsequent user choices', () => {
  const h = harness({ corner: 'top-right', compact: false, activity: true, onlyUpdated: true }, false);
  assert.equal(h.storage.get('assistantLayout'), true);
  assert.equal(h.storage.get('preferences').corner, 'bottom-right');
  assert.equal(h.storage.get('preferences').compact, true);
  assert.equal(h.storage.get('preferences').activity, false);
  assert.equal(h.storage.get('preferences').onlyUpdated, true);
  const chosen = harness({ corner: 'top-right', compact: false, activity: true });
  assert.equal(chosen.host.style.right, '16px');
  assert.equal(chosen.$('section').classes.has('compact'), false);
});

test('Assistant message and time stay concise; cached updates show offline warning', () => {
  const h = harness();
  const entry = { message: 'Visited documentation and checked the release notes.',
    scope: 'https://example.org/', updated: '2026-11-02T16:00:00Z' };
  h.respond({ test: { agent: 'Agent', session: 'chat-a', global: null,
    pages: { 'https://example.org/': entry } } });
  assert.equal(h.$('.update').children[0].children[1].textContent, entry.message);
  assert.match(h.$('time').textContent, /^Updated /);
  assert.equal(h.$('time').textContent.includes('This page'), false);
  assert.equal(h.$('.connection').hidden, true);
  h.requests[0].onerror();
  assert.equal(h.$('.update').children[0].children[1].textContent, entry.message);
  assert.equal(h.$('.connection').hidden, false);
  assert.match(h.$('.connection').textContent, /stale/);
});


const entry = message => ({ message, scope: 'https://example.org/', updated: '2026-11-02T16:00:00Z' });
const channel = (agent, session, message) => ({ agent, session, global: null,
  pages: { 'https://example.org/': entry(message) } });

test('Only the explicitly activated document and session display updates', () => {
  const used = harness();
  const untouched = harness({}, true, 'supported', null);
  const channels = { a: channel('Agent', 'chat-a', 'Selected'), b: channel('Agent', 'chat-b', 'Other') };
  used.respond(channels);
  assert.equal(untouched.requests.length, 0);
  assert.equal(used.host.style.display, 'block');
  assert.equal(untouched.host.style.display, 'none');
  assert.deepEqual(used.$('.update').children.map(row => row.children[1].textContent), ['Selected']);
  untouched.location.hash = untouched.marker({ agent: 'Agent', session: 'chat-b', hash: '#original' });
  untouched.window.fire('hashchange');
  assert.equal(untouched.location.hash, '#original');
  assert.equal(untouched.host.style.display, 'block');
  assert.equal(untouched.requests.length, 1);
  untouched.respond(channels);
  assert.equal(untouched.$('.update').children[0].children[1].textContent, 'Other');
  untouched.menus.get('Stop activity on this page')();
  assert.equal(untouched.host.style.display, 'none');
  untouched.tick();
  assert.equal(untouched.requests.length, 1);
  assert.equal(harness({}, true, 'supported', null).host.style.display, 'none');
});

test('All five same-URL tabs activated for one session display its published update', () => {
  const tabs = Array.from({ length: 5 }, () => harness());
  const otherSession = harness({}, true, 'supported', { agent: 'Agent', session: 'chat-b', hash: '' });
  const unactivated = harness({}, true, 'supported', null);
  const channels = { a: channel('Agent', 'chat-a', 'Published from one tab'),
    b: channel('Agent', 'chat-b', 'Different session') };

  for (const tab of tabs) tab.respond(channels);
  otherSession.respond(channels);

  for (const tab of tabs) {
    assert.equal(tab.location.origin + tab.location.pathname, 'https://example.org/');
    assert.equal(tab.host.style.display, 'block');
    assert.equal(tab.$('.update').children[0].children[1].textContent, 'Published from one tab');
  }
  assert.equal(otherSession.host.style.display, 'block');
  assert.equal(otherSession.$('.update').children[0].children[1].textContent, 'Different session');
  assert.equal(unactivated.requests.length, 0);
  assert.equal(unactivated.host.style.display, 'none');
});

test('Invalid markers are ignored; activated overlay stays visible without updates and offline', () => {
  const h = harness({}, true, 'supported', null);
  h.location.hash = '#ai-activity=bad'; h.window.fire('hashchange');
  assert.equal(h.host.style.display, 'none');
  h.location.hash = h.marker({ agent: 'Agent', session: 'chat-a', hash: '' });
  h.window.fire('hashchange'); h.respond({});
  assert.equal(h.host.style.display, 'block');
  h.tick(); h.requests.at(-1).onerror();
  assert.equal(h.host.style.display, 'block');
});

test('Restoration preserves same origin for double-slash paths and only activates on success', () => {
  const h = harness({}, true, 'supported', null);
  h.location.pathname = '//other/path';
  h.location.search = '?q=1';
  let restored;
  h.history.replaceState = (_state, _title, url) => { restored = url; };
  h.location.hash = h.marker({ agent: 'Agent', session: 'chat-a', hash: '#original' });
  h.window.fire('hashchange');
  assert.equal(restored, 'https://example.org//other/path?q=1#original');
  assert.equal(h.host.style.display, 'block');

  const failed = harness({}, true, 'supported', null);
  failed.history.replaceState = () => { throw new Error('Cannot restore'); };
  failed.location.hash = failed.marker({ agent: 'Agent', session: 'chat-a', hash: '' });
  failed.window.fire('hashchange');
  assert.equal(failed.host.style.display, 'none');
});

test('Agent/session labels and messages render as literal text', () => {
  const h = harness({}, true, 'supported', { agent: '<img src=x>', session: '<b>session</b>', hash: '' });
  h.respond({ a: channel('<img src=x>', '<b>session</b>', '<script>unsafe()</script>') });
  const row = h.$('.update').children[0];
  assert.deepEqual(row.children.map(node => node.tag), ['small', 'p', 'time']);
  assert.equal(row.children[0].textContent, '<img src=x> · <b>session</b>');
  assert.equal(row.children[1].textContent, '<script>unsafe()</script>');
});

function pointer(h, name, x, y, extra = {}) {
  h.$('header').fire(name, { button: 0, isPrimary: true, pointerId: 1,
    clientX: x, clientY: y, preventDefault() {}, ...extra });
}

test('Dragging header persists placement across polling and reloads', () => {
  const h = harness();
  pointer(h, 'pointerdown', 674, 594);
  pointer(h, 'pointermove', 220, 160);
  assert.equal(h.host.style.left, '200px');
  assert.equal(h.host.style.top, '150px');
  assert.equal(h.host.style.right, 'auto');
  h.tick();
  assert.equal(h.host.style.left, '200px');
  pointer(h, 'pointerup', 220, 160);
  assert.deepEqual(h.storage.get('preferences').position, { x: 200, y: 150 });
  assert.equal(h.$('header').hasPointerCapture(1), false);
  const reload = harness(h.storage.get('preferences'));
  assert.equal(reload.host.style.left, '200px');
  assert.equal(reload.host.style.top, '150px');
});

test('Dragging clamps to viewport, handles cancellation, and reclamps on resize', () => {
  const h = harness();
  pointer(h, 'pointerdown', 674, 594);
  pointer(h, 'pointermove', -100, -100);
  assert.equal(h.host.style.left, '0px');
  assert.equal(h.host.style.top, '0px');
  pointer(h, 'pointermove', 2000, 2000);
  assert.equal(h.host.style.left, '670px');
  assert.equal(h.host.style.top, '600px');
  pointer(h, 'pointercancel', 2000, 2000);
  assert.deepEqual(h.storage.get('preferences').position, { x: 670, y: 600 });
  h.window.innerWidth = 600;
  h.window.innerHeight = 400;
  h.window.fire('resize');
  assert.equal(h.host.style.left, '270px');
  assert.equal(h.host.style.top, '200px');
  pointer(h, 'pointermove', 50, 50);
  assert.equal(h.host.style.left, '270px');
});

test('Header controls, secondary buttons, and unrelated pointers do not drag', () => {
  const h = harness();
  pointer(h, 'pointerdown', 674, 594, { target: { closest: () => h.$('.settings-toggle') } });
  pointer(h, 'pointermove', 200, 100);
  assert.equal(h.host.style.left, 'auto');
  pointer(h, 'pointerdown', 674, 594, { button: 2 });
  pointer(h, 'pointermove', 200, 100);
  assert.equal(h.host.style.left, 'auto');
  pointer(h, 'pointerdown', 674, 594);
  pointer(h, 'pointermove', 200, 100, { pointerId: 2 });
  assert.equal(h.host.style.left, 'auto');
  pointer(h, 'lostpointercapture', 674, 594);
  pointer(h, 'pointermove', 200, 100);
  assert.equal(h.host.style.left, 'auto');
});

test('Choosing a corner clears saved dragged placement', () => {
  const h = harness({ position: { x: 200, y: 150 } });
  h.$('.corner').value = 'top-left';
  h.$('.corner').fire('change');
  assert.equal(h.storage.get('preferences').position, undefined);
  assert.equal(h.host.style.left, '16px');
  assert.equal(h.host.style.top, '16px');
  assert.equal(h.host.style.right, 'auto');
  const invalid = harness({ position: { x: 'invalid', y: null } });
  assert.equal(invalid.host.style.right, '16px');
  assert.equal(invalid.host.style.bottom, '16px');
});
