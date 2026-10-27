// Observa as requisições de cada aba e guarda os manifestos de streaming (HLS .m3u8, DASH .mpd)
// e vídeos progressivos, junto com os cabeçalhos da sessão (cookie, referer, origin, authorization).
// O popup lê essa lista de chrome.storage.session.

const KEEP_HEADER = /^(cookie|referer|origin|user-agent|authorization|x-[\w-]+)$/i;
const SEGMENT_PATH = /\.(ts|m4s|m4a|m4v|aac|vtt|webvtt|key)$/i;
const SKIP_URL = /googlevideo\.com|\/videoplayback[?/]/i; // YouTube: endereços assinados, não reaproveitáveis
const MAX_PER_TAB = 40;

const HOST = 'videocassete_web';   // nome do processo local registrado pelo instalador
const t = (key, ...subs) => (chrome.i18n.getMessage(key) || key)
  .replace(/\{(\d)\}/g, (m, i) => (subs[i] != null ? String(subs[i]) : m));
const LANG = chrome.i18n.getUILanguage();

// Chrome precisa de 'extraHeaders' para ver Cookie/Referer; o Firefox mostra tudo e recusa essa opção.
const EXTRA = chrome.webRequest.OnBeforeSendHeadersOptions && chrome.webRequest.OnBeforeSendHeadersOptions.EXTRA_HEADERS
  ? ['extraHeaders'] : [];

const pendingHeaders = new Map(); // requestId -> cabeçalhos da requisição
let queue = Promise.resolve();     // serializa as escritas no storage

function keepHeaders(list) {
  const out = {};
  for (const h of list || []) if (KEEP_HEADER.test(h.name)) out[h.name] = h.value;
  return out;
}

function header(list, name) {
  const h = (list || []).find(x => x.name.toLowerCase() === name);
  return h ? h.value : '';
}

function classify(details) {
  const url = details.url;
  if (SKIP_URL.test(url) || url.startsWith('blob:') || url.startsWith('data:')) return null;
  const path = url.split(/[?#]/)[0].toLowerCase();
  const type = header(details.responseHeaders, 'content-type').toLowerCase();
  if (/mpegurl/.test(type) || path.endsWith('.m3u8')) return 'hls';
  if (/dash\+xml/.test(type) || path.endsWith('.mpd')) return 'dash';
  if (details.type === 'media' && /^video\/(mp4|webm|quicktime|x-matroska|x-flv)/.test(type) && !SEGMENT_PATH.test(path)) {
    const range = header(details.responseHeaders, 'content-range');
    const size = range.includes('/') ? Number(range.split('/')[1]) : Number(header(details.responseHeaders, 'content-length'));
    if (size > 2e6) return 'file';
  }
  return null;
}

function addStream(tabId, item) {
  queue = queue.then(async () => {
    const key = 'tab_' + tabId;
    const list = (await chrome.storage.session.get(key))[key] || [];
    const existing = list.find(s => s.url === item.url);
    if (existing) {
      existing.headers = item.headers; // cookies mais recentes
    } else {
      list.push(item);
      if (list.length > MAX_PER_TAB) list.shift();
    }
    await chrome.storage.session.set({ [key]: list });
    const count = new Set(list.map(s => s.url)).size;
    chrome.action.setBadgeText({ tabId, text: String(count) }).catch(() => {});
  }).catch(err => console.warn('addStream', err));
}

chrome.action.setBadgeBackgroundColor({ color: '#2f6fde' });

// Cabeçalhos que o player manda para cada servidor (ex.: token de autorização nos segmentos do
// SharePoint, diferente do que vai no manifesto). O worker repete os mesmos por servidor.
const hostHeaders = new Map();   // tabId -> { host: { headers, time } }
const hostHeadersSaved = new Map();
const AUTH_HEADER = /^(authorization|x-[\w-]+)$/i;

function rememberHostHeaders(tabId, url, headers) {
  if (!Object.keys(headers).some(k => AUTH_HEADER.test(k))) return;   // só interessa quem leva autenticação
  let host;
  try { host = new URL(url).hostname.toLowerCase(); } catch (e) { return; }
  const perTab = hostHeaders.get(tabId) || {};
  perTab[host] = { headers, time: Date.now() };
  hostHeaders.set(tabId, perTab);
  const last = hostHeadersSaved.get(tabId) || 0;
  if (Date.now() - last > 5000) {   // persiste no máximo a cada 5 s (o service worker pode reiniciar)
    hostHeadersSaved.set(tabId, Date.now());
    chrome.storage.session.set({ ['hh_' + tabId]: perTab }).catch(() => {});
  }
}

chrome.webRequest.onBeforeSendHeaders.addListener(
  details => {
    if (details.tabId < 0) return;
    const headers = keepHeaders(details.requestHeaders);
    pendingHeaders.set(details.requestId, headers);
    setTimeout(() => pendingHeaders.delete(details.requestId), 60000);
    rememberHostHeaders(details.tabId, details.url, headers);
  },
  { urls: ['<all_urls>'], types: ['xmlhttprequest', 'media', 'other'] },
  ['requestHeaders', ...EXTRA]
);

chrome.webRequest.onHeadersReceived.addListener(
  details => {
    if (details.tabId < 0 || details.statusCode >= 400) return;
    const kind = classify(details);
    if (!kind) return;
    const headers = pendingHeaders.get(details.requestId) || {};
    const initiator = details.initiator || originOf(details.originUrl);   // Chrome: initiator; Firefox: originUrl
    if (!headers.Referer && !headers.referer && initiator) headers.Referer = initiator + '/';
    addStream(details.tabId, { url: details.url, kind, headers, time: Date.now() });
  },
  { urls: ['<all_urls>'], types: ['xmlhttprequest', 'media', 'other'] },
  ['responseHeaders', ...EXTRA]
);

function originOf(url) {
  try { return url ? new URL(url).origin : ''; } catch (e) { return ''; }
}

// Página nova na aba: limpa a lista antiga.
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === 'loading' && info.url) {
    queue = queue.then(() => chrome.storage.session.remove('tab_' + tabId)).catch(() => {});
  }
});

chrome.tabs.onRemoved.addListener(tabId => {
  queue = queue.then(() => chrome.storage.session.remove(['tab_' + tabId, 'hh_' + tabId])).catch(() => {});
  hostHeaders.delete(tabId);
  hostHeadersSaved.delete(tabId);
});

// ---------------------------------------------------------------- gravação pela página
// popup -> record-start -> recorder.js na aba -> pedaços por runtime.connect -> host (connectNative) -> disco.
// O port nativo aberto mantém este service worker vivo enquanto houver gravação.

const sessions = new Map(); // jobId -> { tabId, frameId, contentPort, done, discard }
const pending = new Map();  // reqId -> resolve
let nativePortRef = null;
let reqSeq = 0;

function nativePort() {
  if (nativePortRef) return nativePortRef;
  nativePortRef = chrome.runtime.connectNative(HOST);
  nativePortRef.onMessage.addListener(msg => {
    const resolve = pending.get(msg.reqId);
    if (resolve) { pending.delete(msg.reqId); resolve(msg); }
  });
  nativePortRef.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError;
    nativePortRef = null;
    for (const resolve of pending.values()) resolve({ ok: false, error: (err && err.message) || t('hostClosed') });
    pending.clear();
    for (const s of sessions.values()) {
      try { s.contentPort && s.contentPort.postMessage({ type: 'stop', reason: t('hostClosed') }); } catch (e) { /* noop */ }
    }
    sessions.clear();
  });
  return nativePortRef;
}

function nativeRequest(msg) {
  return new Promise(resolve => {
    const reqId = ++reqSeq;
    pending.set(reqId, resolve);
    try { nativePort().postMessage({ ...msg, reqId, lang: LANG }); } catch (e) { pending.delete(reqId); resolve({ ok: false, error: e.message }); }
  });
}

function nativeSend(msg) {
  try { nativePort().postMessage(msg); } catch (e) { /* o onDisconnect cuida */ }
}

function releaseNative() {
  if (nativePortRef && sessions.size === 0) {
    nativePortRef.disconnect();
    nativePortRef = null;
  }
}

async function startRecording({ tabId, title, pageUrl, options }) {
  if ([...sessions.values()].some(s => s.tabId === tabId)) return { ok: false, error: t('recAlready') };
  // options.target = { frameId, index }: vídeo escolhido na lista "Vídeos na página"
  const target = options.target;
  const where = target ? { tabId, frameIds: [target.frameId] } : { tabId, allFrames: true };
  await chrome.scripting.executeScript({ target: where, files: ['recorder.js'] });
  const results = await chrome.scripting.executeScript({
    target: where,
    func: index => (window.__sdRecorder ? window.__sdRecorder.survey(index) : null),
    args: [target ? target.index : null],
  });
  const best = results.filter(r => r.result).sort((a, b) => b.result.area - a.result.area)[0];
  if (!best) return { ok: false, error: t('recNoVideo') };
  if (!best.result.mime) return { ok: false, error: t('recNoRecorder') };

  const resp = await nativeRequest({
    cmd: 'rec_start', title, url: pageUrl, ext: best.result.mime.startsWith('video/mp4') ? 'mp4' : 'webm',
    rate: Number(options.playbackRate) || 1, out_dir: options.outDir || '', duration: best.result.duration,
  });
  if (!resp.ok) { releaseNative(); return resp; }
  sessions.set(resp.id, { tabId, frameId: best.frameId, contentPort: null, done: false, discard: false, cancelled: false });
  await chrome.scripting.executeScript({
    target: { tabId, frameIds: [best.frameId] },
    func: opts => { window.__sdRecorder.start(opts); return true; },
    args: [{ ...options, session: resp.id, videoIndex: target ? target.index : undefined }],
  });
  return { ok: true, id: resp.id, drmSuspect: best.result.mediaKeys };
}

async function finishRecording(jobId, discard, reason) {
  const s = sessions.get(jobId);
  if (!s || s.done) return;
  s.done = true;
  await nativeRequest({ cmd: 'rec_stop', id: jobId, discard: !!discard, cancelled: !!(discard && s.cancelled), reason: reason || '' });
  sessions.delete(jobId);
  releaseNative();
}

async function stopRecording(jobId, discard) {
  const s = sessions.get(jobId);
  if (!s) return { ok: false, error: t('recNotActive') };
  s.discard = !!discard;
  s.cancelled = !!discard;
  if (s.contentPort) s.contentPort.postMessage({ type: 'stop', reason: discard ? t('reasonCancelled') : t('reasonUserStop') });
  else await finishRecording(jobId, discard, t('reasonUserStop'));
  return { ok: true };
}

chrome.runtime.onConnect.addListener(port => {
  if (!port.name.startsWith('rec:')) return;
  const jobId = port.name.slice(4);
  const s = sessions.get(jobId);
  if (!s) { port.disconnect(); return; }
  s.contentPort = port;
  port.onMessage.addListener(msg => {
    if (msg.type === 'chunk') nativeSend({ cmd: 'rec_chunk', id: jobId, data: msg.data, pos: msg.pos, dur: msg.dur });
    else if (msg.type === 'status' || msg.type === 'started') nativeSend({ cmd: 'rec_status', id: jobId, note: msg.note || '', pos: msg.pos, dur: msg.dur || msg.duration });
    else if (msg.type === 'end') finishRecording(jobId, s.discard, msg.reason);
    else if (msg.type === 'error') finishRecording(jobId, true, msg.reason);
  });
  port.onDisconnect.addListener(() => finishRecording(jobId, s.discard, t('reasonTabClosed')));
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'record-start') {
    startRecording(msg).then(sendResponse, e => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  if (msg.type === 'record-stop') {
    stopRecording(msg.jobId, msg.discard).then(sendResponse, e => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  if (msg.type === 'record-active') {
    sendResponse([...sessions.keys()]);
  }
  if (msg.type === 'host-headers') {
    const mem = hostHeaders.get(msg.tabId);
    if (mem) sendResponse(mem);
    else chrome.storage.session.get('hh_' + msg.tabId).then(r => sendResponse(r['hh_' + msg.tabId] || {}));
    return true;
  }
  return false;
});
