// Carrega os scripts da extensão num contexto isolado (vm) com um "chrome" e um DOM mínimos de mentira.
// Sem dependências: só node:vm e node:fs.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
export const EXT = path.join(ROOT, 'extension');

export function messages(locale = 'en') {
  return JSON.parse(fs.readFileSync(path.join(EXT, '_locales', locale, 'messages.json'), 'utf8'));
}

function event(listeners, name) {
  return { addListener: (fn, filter, spec) => (listeners[name] ||= []).push({ fn, filter, spec }) };
}

// firefox: sem as constantes EXTRA_HEADERS (o Firefox recusa 'extraHeaders')
export function chromeStub({ firefox = false, locale = 'en', nativeReply = null } = {}) {
  const msgs = messages(locale);
  const listeners = {};
  const session = {};
  const local = {};
  const sentNative = [];
  const getter = store => async keys => {
    const list = keys == null ? Object.keys(store) : [].concat(keys);
    return Object.fromEntries(list.filter(k => k in store).map(k => [k, store[k]]));
  };
  const chrome = {
    i18n: { getMessage: k => (msgs[k] ? msgs[k].message : ''), getUILanguage: () => (locale === 'en' ? 'en-US' : 'pt-BR') },
    webRequest: {
      onBeforeSendHeaders: event(listeners, 'beforeSendHeaders'),
      onHeadersReceived: event(listeners, 'headersReceived'),
      ...(firefox ? {} : {
        OnBeforeSendHeadersOptions: { EXTRA_HEADERS: 'extraHeaders' },
        OnHeadersReceivedOptions: { EXTRA_HEADERS: 'extraHeaders' },
      }),
    },
    tabs: { onUpdated: event(listeners, 'tabUpdated'), onRemoved: event(listeners, 'tabRemoved'), query: async () => [] },
    runtime: {
      lastError: null,
      onConnect: event(listeners, 'connect'),
      onMessage: event(listeners, 'message'),
      sendNativeMessage(host, msg, cb) {
        sentNative.push({ host, msg });
        const reply = typeof nativeReply === 'function' ? nativeReply(msg) : nativeReply;
        if (!reply) chrome.runtime.lastError = { message: 'Specified native messaging host not found.' };
        setTimeout(() => { cb(reply || undefined); chrome.runtime.lastError = null; }, 0);
      },
      sendMessage: async () => undefined,
      connectNative: () => ({ onMessage: event(listeners, 'nativeMsg'), onDisconnect: event(listeners, 'nativeDisc'), postMessage() {}, disconnect() {} }),
      reload() {},
    },
    storage: {
      session: { get: getter(session), set: async o => Object.assign(session, o), remove: async k => [].concat(k).forEach(x => delete session[x]) },
      local: { get: getter(local), set: async o => Object.assign(local, o) },
    },
    action: { setBadgeText: () => Promise.resolve(), setBadgeBackgroundColor() {} },
    cookies: { getAll: async () => [] },
    permissions: { contains: async () => true, request: async () => true },
    scripting: { executeScript: async () => [] },
  };
  return { chrome, listeners, session, local, sentNative };
}

// ---------------------------------------------------------------- DOM mínimo

export class FakeElement {
  constructor(tag = 'div', props = {}) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.dataset = {};
    this.style = {};
    this.attrs = {};
    this.listeners = {};
    this.hidden = false;
    this.textContent = '';
    this.className = '';
    Object.assign(this, props);
  }

  append(...nodes) {
    for (const n of nodes) {
      if (n instanceof FakeElement) { n.parentElement = this; this.children.push(n); }
      else if (n != null) this.children.push(String(n));
    }
    return this;
  }

  replaceChildren(...nodes) { this.children = []; return this.append(...nodes); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatchEvent() { return true; }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  getBoundingClientRect() { return this.rect || { width: 0, height: 0, top: 0, bottom: 0 }; }
  scrollIntoView() {}

  *descendants() {
    for (const c of this.children) {
      if (c instanceof FakeElement) { yield c; yield* c.descendants(); }
    }
  }

  querySelectorAll(selector) {
    const m = selector.match(/^(\w+)(?:\[(\w+)\])?$/);
    if (!m) {   // [data-i18n] e parecidos: procura pelo atributo em dataset
      const data = selector.match(/^\[data-([\w-]+)\]$/);
      const key = data ? data[1].replace(/-(\w)/g, (_, c) => c.toUpperCase()) : null;
      return key ? [...this.descendants()].filter(e => key in e.dataset) : [];
    }
    const [tag, attr] = m.slice(1);
    return [...this.descendants()].filter(e => e.tagName === tag.toUpperCase() && (!attr || e.getAttribute(attr) != null));
  }

  // texto de todos os descendentes (aproximação de innerText)
  get innerText() {
    return this.children.map(c => (c instanceof FakeElement ? c.innerText : c)).concat(this.textContent || []).join('\n');
  }
}

export function fakeDocument(body = new FakeElement('body')) {
  const byId = new Map();
  return {
    body,
    documentElement: new FakeElement('html'),
    getElementById(id) {
      if (!byId.has(id)) byId.set(id, new FakeElement('div', { id }));
      return byId.get(id);
    },
    createElement: tag => new FakeElement(tag),
    querySelectorAll: selector => body.querySelectorAll(selector),
    querySelector: selector => body.querySelectorAll(selector)[0] || null,
  };
}

// Roda o arquivo no contexto e devolve o valor da expressão final (ex.: '({ clock, mb })').
export function load(file, globals, exportsExpr = '') {
  const context = vm.createContext({ console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {}, URL, Promise, ...globals });
  context.window = context;
  context.globalThis = context;
  const code = fs.readFileSync(path.join(EXT, file), 'utf8') + (exportsExpr ? '\n;' + exportsExpr : '');
  return { context, result: vm.runInContext(code, context, { filename: file }) };
}

export const tick = (ms = 10) => new Promise(r => setTimeout(r, ms));
