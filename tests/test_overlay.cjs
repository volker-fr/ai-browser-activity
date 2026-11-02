const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(process.env.OVERLAY_SOURCE ||
  path.join(__dirname, '..', 'ai-overlay.user.js'), 'utf8');

// Run the userscript with isolated GM storage, events, and deterministic timers.
function harness(preferences = {}, assistantLayout = true) {
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
    closest() { return null; }
  }
  const elements = [];
  const root = new Element();
  Object.defineProperty(root, 'innerHTML', {
    set(html) {
      // Only tag, class, and hidden state are needed for these regressions.
      for (const match of html.matchAll(/<(section|button|div|input|select|p|small|time)\b([^>]*)>/g)) {
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
  const location = { origin: 'https://example.org', pathname: '/', host: 'example.org' };
  vm.runInNewContext(source, {
    document, Element, Date,
    location,
    GM_getValue: (name, fallback) => storage.get(name) ?? fallback,
    GM_setValue: (name, value) => storage.set(name, structuredClone(value)),
    GM_registerMenuCommand() {},
    GM_xmlhttpRequest: request => requests.push(request),
    setInterval: fn => { interval = fn; },
    setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; },
    clearTimeout: id => timers.delete(id),
  });
  return { document, root, host, timers, requests, storage, location,
    tick: () => interval(),
    respond: channels => requests.at(-1).onload({ status: 200,
      responseText: JSON.stringify({ version: 2, channels }) }),
    $: selector => root.querySelector(selector),
    open: () => root.querySelector('.settings-toggle').fire('click') };
}

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

test('Every agent/session renders its own labelled latest update on the same page', () => {
  const h = harness();
  const channels = { a: channel('Agent A', 'chat-a', 'First action'),
    b: channel('Agent A', 'chat-b', 'Second action'),
    c: channel('Agent B', 'chat-a', 'Third action') };
  h.respond(channels);
  let rows = h.$('.update').children;
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map(row => row.children[0].textContent),
    ['Agent A · chat-a', 'Agent A · chat-b', 'Agent B · chat-a']);
  assert.deepEqual(rows.map(row => row.children[1].textContent),
    ['First action', 'Second action', 'Third action']);
  channels.a.pages['https://example.org/'] = entry('New action');
  h.tick();
  h.respond(channels);
  rows = h.$('.update').children;
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map(row => row.children[1].textContent),
    ['New action', 'Second action', 'Third action']);
});

test('Page precedence and fallback selection apply independently to each session', () => {
  const h = harness();
  const a = channel('Agent', 'a', 'Specific A');
  a.global = entry('Fallback A');
  const b = { agent: 'Agent', session: 'b', global: entry('Fallback B'), pages: {} };
  const unrelated = { agent: 'Other', session: 'c', global: null,
    pages: { 'https://example.org/elsewhere': entry('Unrelated') } };
  h.respond({ a, b, unrelated });
  assert.deepEqual(h.$('.update').children.map(row => row.children[1].textContent),
    ['Specific A', 'Fallback B']);
  h.location.pathname = '/elsewhere';
  h.tick();
  assert.deepEqual(h.$('.update').children.map(row => row.children[1].textContent),
    ['Fallback A', 'Fallback B', 'Unrelated']);
});

test('Update-only visibility includes any matching agent/session', () => {
  const h = harness({ onlyUpdated: true });
  assert.equal(h.host.style.display, 'none');
  h.respond({ a: channel('Agent', 'a', 'Visited page') });
  assert.equal(h.host.style.display, 'block');
  h.tick();
  h.respond({});
  assert.equal(h.host.style.display, 'none');
  assert.equal(h.$('.update').children.length, 0);
});

test('Agent/session labels and messages render as literal text', () => {
  const h = harness();
  h.respond({ a: channel('<img src=x>', '<b>session</b>', '<script>unsafe()</script>') });
  const row = h.$('.update').children[0];
  assert.deepEqual(row.children.map(node => node.tag), ['small', 'p', 'time']);
  assert.equal(row.children[0].textContent, '<img src=x> · <b>session</b>');
  assert.equal(row.children[1].textContent, '<script>unsafe()</script>');
});
