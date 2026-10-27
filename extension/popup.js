const HOST = 'videocassete_web';
const LANG = chrome.i18n.getUILanguage();
const DEFAULTS = { quality: 'best', threads: 8, outDir: '' };
const ACTIVE = ['queued', 'starting', 'analyzing', 'downloading', 'merging', 'recording'];

let settings = { ...DEFAULTS };
let tab = null;
let lastJobs = '';

const $ = id => document.getElementById(id);

// Texto no idioma do navegador (_locales); {0}, {1}... são trocados pelos argumentos.
function t(key, ...subs) {
  const text = chrome.i18n.getMessage(key) || key;
  return text.replace(/\{(\d)\}/g, (m, i) => (subs[i] != null ? String(subs[i]) : m));
}

function translatePage() {
  for (const node of document.querySelectorAll('[data-i18n]')) node.textContent = t(node.dataset.i18n);
  for (const node of document.querySelectorAll('[data-i18n-title]')) node.title = t(node.dataset.i18nTitle);
  for (const node of document.querySelectorAll('[data-i18n-placeholder]')) node.placeholder = t(node.dataset.i18nPlaceholder);
  document.documentElement.lang = LANG;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node[k] = v;
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

function native(msg) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(HOST, { ...msg, lang: LANG }, resp => {
      const err = chrome.runtime.lastError;
      if (err) return reject(new Error(err.message));
      if (!resp) return reject(new Error(t('noResponse')));
      if (resp.ok === false) return reject(new Error(resp.error));
      resolve(resp);
    });
  });
}

const mb = n => n >= 1e9 ? (n / 1e9).toFixed(2) + ' GB' : (n / 1e6).toFixed(1) + ' MB';
const clock = s => {
  if (!s) return '';
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, sec = s % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(sec).padStart(2, '0');
};
const noQuery = url => url.split(/[?#]/)[0];

function fileLabel(url) {
  try {
    const u = new URL(url);
    const name = decodeURIComponent(u.pathname.split('/').filter(Boolean).pop() || u.hostname);
    return { name, host: u.hostname };
  } catch (e) {
    return { name: url, host: '' };
  }
}

// ---------------------------------------------------------------- streams da aba

const YOUTUBE_PAGE = /^https?:\/\/([^/]+\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)\//i;
const YOUTUBE_VIDEO = /^https?:\/\/(([^/]+\.)?youtube\.com\/(watch\?|shorts\/|live\/|embed\/)|youtu\.be\/)/i;

const YTDLP_SITE = /^https?:\/\/([^/]+\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com|instagram\.com|facebook\.com|fb\.watch|tiktok\.com|x\.com|twitter\.com|vimeo\.com|threads\.net)\//i;

// Cookies do site (YouTube, Instagram, Facebook...) para o yt-dlp entrar com a sua sessão.
async function youtubeCookies() {
  return siteCookies('https://www.youtube.com/');
}

async function siteCookies(url) {
  let domain;
  try { domain = new URL(url).hostname.replace(/^(www|m|web|mobile)\./i, ''); } catch (e) { return []; }
  if (domain === 'fb.watch') domain = 'facebook.com';
  const all = await chrome.cookies.getAll({ domain });
  return all.map(c => ({
    domain: c.domain, hostOnly: c.hostOnly, path: c.path, secure: c.secure,
    expirationDate: c.expirationDate || 0, name: c.name, value: c.value,
  }));
}

// Cookies que o navegador mandaria para cada servidor (ex.: segmentos no SharePoint enquanto o
// manifesto vem de outro domínio). O worker usa cada um só no servidor dono dele.
async function hostCookies(hosts) {
  const jar = {};
  for (const host of new Set(hosts || [])) {
    try {
      const list = await chrome.cookies.getAll({ url: 'https://' + host + '/' });
      if (list.length) jar[host] = list.map(c => c.name + '=' + c.value).join('; ');
    } catch (e) { /* host inválido: ignora */ }
  }
  return jar;
}

const hostOf = url => { try { return new URL(url).hostname.toLowerCase(); } catch (e) { return ''; } };

// Cabeçalhos de autenticação que o player desta aba mandou para cada servidor (token mais recente).
async function playerHeaders(hosts) {
  let all = {};
  try { all = (await chrome.runtime.sendMessage({ type: 'host-headers', tabId: tab.id })) || {}; } catch (e) { /* background antigo */ }
  const out = {};
  for (const host of new Set(hosts || [])) if (all[host]) out[host] = all[host].headers;
  return out;
}

async function loadStreams() {
  const key = 'tab_' + tab.id;
  let list = (await chrome.storage.session.get(key))[key] || [];
  const box = $('streams');
  box.replaceChildren();
  if (YOUTUBE_PAGE.test(tab.url || '')) {
    // No YouTube não há manifesto: o vídeo da própria página vai pelo yt-dlp.
    list = YOUTUBE_VIDEO.test(tab.url) ? [{ url: tab.url, kind: 'youtube', headers: {}, youtube: true }] : [];
    $('noStreams').textContent = t('openYoutubeVideo');
  }
  $('noStreams').hidden = list.length > 0;
  const children = new Set();
  const cards = list.map(item => {
    const card = streamCard(item);
    box.append(card.node);
    return card;
  });
  // analisa em paralelo; esconde as playlists que já aparecem dentro de um master
  await Promise.all(cards.map(async card => {
    const info = await card.probe();
    for (const c of (info && info.children) || []) children.add(noQuery(c));
    for (const other of cards) if (children.has(noQuery(other.item.url)) && other !== card) other.node.hidden = true;
  }));
  $('noStreams').hidden = cards.some(c => !c.node.hidden);
}

function streamCard(item) {
  let { name, host } = fileLabel(item.url);
  if (item.youtube) name = (tab.title || t('youtubeVideo')).replace(/^\(\d+\)\s*/, '').replace(/ - YouTube$/, '');
  const cookies = item.youtube ? youtubeCookies() : Promise.resolve(undefined);
  const meta = el('div', { class: 'meta', textContent: host + ' · ' + t('analyzing'), title: item.url });
  const errBox = el('div', { class: 'err', hidden: true });
  const select = el('select', { disabled: true });
  const button = el('button', { class: 'primary', textContent: t('download'), disabled: true });
  const node = el('div', { class: 'card' },
    el('div', { class: 'top' }, el('span', { class: 'tag', textContent: item.kind.toUpperCase() }),
      el('span', { class: 'name', textContent: name, title: item.url })),
    meta, errBox, el('div', { class: 'actions' }, select, button));

  let recordInstead = false;
  let probeHosts = [hostOf(item.url)];
  // Download direto não serve (DRM, erro de manifesto...): oferece a gravação pela página.
  function offerRecording(message) {
    errBox.hidden = false;
    errBox.textContent = message + ' ' + t('recordAlternative');
    select.hidden = true;
    recordInstead = true;
    button.textContent = t('recordFromPage');
    button.disabled = false;
  }

  button.addEventListener('click', async () => {
    if (recordInstead) {
      button.disabled = true;
      const resp = await startRecording();
      if (!resp.ok) { errBox.textContent = resp.error; button.disabled = false; }
      else button.textContent = t('recordingOk');
      return;
    }
    button.disabled = true;
    button.textContent = t('sending');
    try {
      await native({
        cmd: 'download', url: item.url, headers: item.headers, title: tab.title, cookies: await cookies,
        host_cookies: await hostCookies(probeHosts), host_headers: await playerHeaders(probeHosts),
        quality: select.value || settings.quality, threads: settings.threads, out_dir: settings.outDir,
      });
      button.textContent = t('sent');
      refreshJobs();
    } catch (e) {
      offerRecording(e.message);
    }
  });

  async function probe() {
    try {
      const info = await native({ cmd: 'probe', url: item.url, headers: item.headers, cookies: await cookies });
      probeHosts = [...probeHosts, ...(info.hosts || [])];
      const bits = [host];
      if (info.duration) bits.push(clock(info.duration));
      if (info.variants.length) bits.push(info.variants.length > 1 ? t('qualityMany', info.variants.length) : t('qualityOne'));
      if (info.size) bits.push(mb(info.size));
      meta.textContent = bits.join(' · ');
      if (info.error) {
        offerRecording(info.error);
        return info;
      }
      const variants = [...info.variants].sort((a, b) => (b.height || 0) - (a.height || 0));
      if (variants.length) {
        select.append(el('option', { value: settings.quality, textContent: t('auto', qualityName(settings.quality)) }));
        for (const v of variants) select.append(el('option', { value: v.id, textContent: v.label }));
        if (item.youtube) select.append(el('option', { value: 'mp3', textContent: t('audioOnlyMp3') }));
      } else {
        select.append(el('option', { value: 'best', textContent: info.kind === 'file' ? t('singleFile') : t('singleQuality') }));
      }
      select.disabled = false;
      button.disabled = false;
      return info;
    } catch (e) {
      meta.textContent = host;
      offerRecording(e.message);
      return null;
    }
  }

  return { node, item, probe };
}

// ---------------------------------------------------------------- gravação pela página

async function startRecording(target) {
  const options = {
    playbackRate: Number($('recRate').value) || 1,
    fromStart: $('recFromStart').checked,
    outDir: settings.outDir,
    waitForAds: true,
    target,   // { frameId, index } quando escolhido na lista "Vídeos na página"
  };
  let resp;
  try {
    resp = await chrome.runtime.sendMessage({ type: 'record-start', tabId: tab.id, title: tab.title, pageUrl: tab.url, options });
  } catch (e) {
    if (/Receiving end does not exist/i.test(e.message)) {
      // o popup é lido do disco a cada abertura, mas o código de fundo só atualiza ao recarregar a extensão
      setTimeout(() => chrome.runtime.reload(), 1500);
      return { ok: false, error: t('extUpdated') };
    }
    resp = { ok: false, error: e.message };
  }
  resp = resp || { ok: false, error: t('noExtResponse') };
  if (resp.ok) setTimeout(() => { lastJobs = ''; refreshJobs(); }, 600);
  return resp;
}

// ---------------------------------------------------------------- vídeos na página (feeds)

async function loadPageVideos() {
  if (!tab || !/^https?:/i.test(tab.url || '')) return;
  let results = [];
  try {
    results = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ['scan.js'] });
  } catch (e) {
    return;   // página que não aceita injeção (loja do Chrome etc.)
  }
  const items = results.flatMap(r => (r.result || []).map(v => ({ ...v, frameId: r.frameId })));
  const show = items.length >= 2 || (items.length === 1 && !YOUTUBE_PAGE.test(tab.url));
  $('pageVideosSection').hidden = !show;
  if (!show) return;
  items.sort((a, b) => (b.playing - a.playing) || (b.visible - a.visible) || (a.frameId - b.frameId) || (a.index - b.index));
  $('pageVideosCount').textContent = '(' + items.length + ')';
  $('pageVideos').replaceChildren(...items.map(pageVideoCard));
}

function pageVideoCard(v) {
  const bits = [];
  if (v.playing) bits.push(t('playing'));
  else if (v.visible) bits.push(t('visible'));
  if (v.duration) bits.push(clock(v.duration));
  if (v.height) bits.push(v.width + 'x' + v.height);
  bits.push(v.link ? hostOf(v.link).replace(/^www\./, '') : v.direct ? t('directFile') : t('noPostLink'));
  const errBox = el('div', { class: 'err', hidden: true });
  const say = (msg, ok) => { errBox.hidden = false; errBox.className = ok ? 'meta' : 'err'; errBox.textContent = msg; };

  const format = el('select', { title: t('format') },
    el('option', { value: 'video', textContent: t('video') }),
    el('option', { value: 'mp3', textContent: t('mp3') }));
  const show = el('button', {
    textContent: '👁', title: t('showOnPage'),
    onclick: () => chrome.scripting.executeScript({
      target: { tabId: tab.id, frameIds: [v.frameId] },
      func: i => {
        const el = document.querySelectorAll('video')[i];
        if (!el) return;
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
        const old = el.style.outline;
        el.style.outline = '4px solid #2f6fde';
        setTimeout(() => { el.style.outline = old; }, 2500);
      },
      args: [v.index],
    }),
  });
  const download = el('button', {
    class: 'primary', textContent: t('download'), disabled: !v.link && !v.direct,
    title: v.link || v.direct || t('noPostLinkUseRecord'),
    onclick: async ev => {
      ev.target.disabled = true;
      try {
        if (v.link) {   // post/reel/vídeo: yt-dlp com os cookies do site
          // rede social: título genérico (a legenda vira nome quebrado); o worker troca pelo nome final do arquivo
          const social = !YOUTUBE_PAGE.test(v.link);
          const title = social ? hostOf(v.link).replace(/^(www|m|web)\./, '').split('.')[0] + ' ' + new Date().toLocaleTimeString()
            : (v.label || tab.title);
          await native({
            cmd: 'download', url: v.link, title, cookies: await siteCookies(v.link),
            quality: format.value === 'mp3' ? 'mp3' : settings.quality, threads: settings.threads, out_dir: settings.outDir,
          });
        } else {        // <video src="https://..."> comum
          await native({
            cmd: 'download', url: v.direct, title: v.label || tab.title, headers: { Referer: tab.url },
            host_cookies: await hostCookies([hostOf(v.direct)]), quality: 'best',
            threads: settings.threads, out_dir: settings.outDir,
          });
        }
        say(t('sentToDownloads'), true);
        lastJobs = ''; refreshJobs();
      } catch (e) {
        say(e.message);
        ev.target.disabled = false;
      }
    },
  });
  const record = el('button', {
    textContent: t('recordBtn'), title: t('recordBtnTitle'),
    onclick: async ev => {
      ev.target.disabled = true;
      const resp = await startRecording({ frameId: v.frameId, index: v.index });
      if (resp.ok) say(t('recordingFollow'), true);
      else { say(resp.error); ev.target.disabled = false; }
    },
  });

  const thumb = v.poster ? el('img', { class: 'thumb', src: v.poster, alt: '' }) : el('div', { class: 'thumb' });
  return el('div', { class: 'card pv' + (v.playing ? ' playing' : '') },
    thumb,
    el('div', { class: 'body' },
      el('div', { class: 'name', textContent: v.label || t('videoN', v.index + 1), title: v.label || '' }),
      el('div', { class: 'meta', textContent: bits.join(' · '), title: v.link || v.direct || '' }),
      el('div', { class: 'actions' }, show, v.link ? format : null, download, record),
      errBox));
}

function setupRecorderBox() {
  if (!tab || !/^https?:/i.test(tab.url || '')) return;
  $('recBox').hidden = false;
  $('recGo').addEventListener('click', async () => {
    $('recGo').disabled = true;
    $('recError').hidden = true;
    const resp = await startRecording();
    if (!resp.ok) {
      $('recError').textContent = resp.error;
      $('recError').hidden = false;
      $('recGo').disabled = false;
    } else {
      $('recGo').textContent = t('recordingOk');
    }
  });
}

function qualityName(q) {
  return { best: t('qName_best'), worst: t('qName_worst') }[q] || t('qName_upTo', q);
}

// ---------------------------------------------------------------- downloads

async function refreshJobs() {
  let jobs;
  try {
    jobs = (await native({ cmd: 'list' })).jobs;
  } catch (e) {
    return;
  }
  const snapshot = JSON.stringify(jobs);
  if (snapshot === lastJobs) return;
  lastJobs = snapshot;
  const box = $('jobs');
  box.replaceChildren(...jobs.map(jobCard));
  $('noJobs').hidden = jobs.length > 0;
}

function jobCard(job) {
  const active = ACTIVE.includes(job.status);
  const recording = job.kind === 'recording';
  let detail = chrome.i18n.getMessage('st_' + job.status) || job.status;
  if (recording && job.status === 'recording') {
    detail += ' · ' + (clock(job.position) || '0:00') + (job.duration ? ' / ' + clock(job.duration) : '');
    if (job.note) detail += ' · ' + job.note;
  } else if (job.unit === 'percent' && active && !recording) {
    if (job.quality === 'mp3' && job.status === 'merging') detail = t('convertingMp3');
    else detail += ` · ${job.part > 1 || job.quality === 'mp3' ? t('audioLower') : t('videoLower')} ${job.done}%`;
  } else if (job.total && job.unit !== 'percent') {
    detail += ' · ' + t('segments', job.done, job.total);
  }
  if (job.bytes) detail += ' · ' + mb(job.bytes);
  if (job.status === 'downloading' && job.speed) detail += ' · ' + mb(job.speed) + '/s';

  const refresh = () => { lastJobs = ''; refreshJobs(); };
  const act = (label, cmd, primary) => el('button', {
    class: primary ? 'primary' : '', textContent: label,
    onclick: async ev => {
      ev.target.disabled = true;
      try { await native({ cmd, id: job.id }); } catch (e) { alertInline(card, e.message); }
      refresh();
    },
  });
  // Gravação ativa: o background avisa o recorder da aba; se a sessão sumiu, o host resolve sozinho.
  const recAct = (label, discard, primary) => el('button', {
    class: primary ? 'primary' : '', textContent: label,
    onclick: async ev => {
      ev.target.disabled = true;
      const resp = await chrome.runtime.sendMessage({ type: 'record-stop', jobId: job.id, discard });
      if (!resp || !resp.ok) {
        try { await native({ cmd: discard ? 'cancel' : 'resume', id: job.id }); } catch (e) { alertInline(card, e.message); }
      }
      setTimeout(refresh, 800);
    },
  });

  const buttons = [];
  if (recording && job.status === 'recording') {
    buttons.push(recAct(t('stopSave'), false, true), recAct(t('cancel'), true));
  } else if (active) {
    buttons.push(act(t('cancel'), 'cancel'));
  }
  if (recording && job.status === 'interrupted') {
    buttons.push(act(t('saveRecorded'), 'resume', true));
  } else if (!recording && (job.status === 'interrupted' || job.status === 'error')) {
    // Retomar com cookies novos dos servidores do job e, se a aba ainda tem o stream, cabeçalhos novos
    buttons.push(el('button', {
      class: 'primary', textContent: t('resume'),
      onclick: async ev => {
        ev.target.disabled = true;
        const key = 'tab_' + (tab && tab.id);
        const list = (tab && (await chrome.storage.session.get(key))[key]) || [];
        const fresh = list.find(s => s.url === job.url);
        try {
          await native({
            cmd: 'resume', id: job.id, host_cookies: await hostCookies([hostOf(job.url), ...(job.hosts || [])]),
            host_headers: await playerHeaders([hostOf(job.url), ...(job.hosts || [])]),
            headers: fresh ? fresh.headers : undefined,
          });
        } catch (e) { alertInline(card, e.message); }
        refresh();
      },
    }));
  }
  if (job.status === 'done') buttons.push(act(t('openFolder'), 'open', true));
  if (!active) buttons.push(act(t('remove'), 'remove'));

  const percent = job.status === 'done' ? 100 : job.percent;
  const card = el('div', { class: 'card status-' + job.status },
    el('div', { class: 'top' }, el('span', { class: 'name', textContent: job.title, title: job.output || job.title })),
    el('div', { class: 'meta', textContent: detail }),
    job.error ? el('div', { class: 'err', textContent: job.error }) : null,
    el('div', { class: 'bar' }, el('div', { style: `width:${percent}%` })),
    el('div', { class: 'actions' }, ...buttons));
  return card;
}

function alertInline(card, message) {
  card.append(el('div', { class: 'err', textContent: message }));
}

// ---------------------------------------------------------------- configurações e link manual

async function loadSettings() {
  settings = { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
  $('defQuality').value = settings.quality;
  $('threads').value = settings.threads;
  $('outDir').value = settings.outDir;
  const save = () => {
    settings = {
      quality: $('defQuality').value,
      threads: Math.min(32, Math.max(1, Number($('threads').value) || DEFAULTS.threads)),
      outDir: $('outDir').value.trim(),
    };
    chrome.storage.local.set(settings);
  };
  for (const id of ['defQuality', 'threads', 'outDir']) $(id).addEventListener('change', save);
}

async function manualDownload() {
  const url = $('manualUrl').value.trim();
  if (!/^https?:\/\//i.test(url)) return;
  const headers = {};
  try {
    const cookies = await chrome.cookies.getAll({ url });
    if (cookies.length) headers.Cookie = cookies.map(c => c.name + '=' + c.value).join('; ');
  } catch (e) { /* sem cookies */ }
  if (tab && /^https?:/.test(tab.url)) headers.Referer = tab.url;
  const cookies = YTDLP_SITE.test(url) ? await siteCookies(url) : undefined;
  $('manualGo').disabled = true;
  $('manualGo').textContent = t('analyzingBtn');
  try {
    const info = await native({ cmd: 'probe', url, headers, cookies });  // recusa página HTML, DRM e live
    if (info.error) throw new Error(info.error);
    await native({ cmd: 'download', url, headers, cookies, title: info.title || (tab ? tab.title : ''), quality: settings.quality,
      threads: settings.threads, out_dir: settings.outDir });
    $('manualUrl').value = '';
    refreshJobs();
  } catch (e) {
    $('manualError').textContent = e.message;
    $('manualError').hidden = false;
  } finally {
    $('manualGo').disabled = false;
    $('manualGo').textContent = t('download');
  }
}

// ---------------------------------------------------------------- início

// Firefox (MV3) não concede o acesso aos sites na instalação: sem ele, a extensão não vê os streams.
async function checkSitePermission() {
  const origins = { origins: ['<all_urls>'] };
  let granted = true;
  try { granted = await chrome.permissions.contains(origins); } catch (e) { /* navegador sem a API */ }
  $('permMissing').hidden = granted;
  $('permGrant').onclick = async () => {
    try { if (await chrome.permissions.request(origins)) $('permMissing').hidden = true; } catch (e) { /* recusado */ }
  };
}

async function init() {
  translatePage();
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  checkSitePermission();
  await loadSettings();
  $('manualGo').addEventListener('click', manualDownload);
  $('manualUrl').addEventListener('input', () => { $('manualError').hidden = true; });
  try {
    const info = await native({ cmd: 'ping' });
    const hints = info.hints || {};
    const missing = [!info.ffmpeg && 'ffmpeg', !info.ytdlp && 'yt-dlp'].filter(Boolean);
    $('hostStatus').textContent = missing.length ? t('readyMissing', missing.join(', ')) : t('ready');
    $('hostStatus').className = 'pill ' + (missing.length ? 'bad' : 'ok');
    $('hostStatus').title = ['Videocassete Web ' + info.version + ' · Python ' + info.python,
      info.ffmpeg || t('noFfmpeg', hints.ffmpeg || ''),
      info.ytdlp || t('noYtdlp', hints['yt-dlp'] || ''),
      info.ytdlp && !info.deno ? t('noDeno', hints.deno || '') : (info.deno || '')].filter(Boolean).join('\n');
    if (!settings.outDir && info.downloads) $('outDir').placeholder = info.downloads;
  } catch (e) {
    $('hostStatus').textContent = t('hostAbsent');
    $('hostStatus').className = 'pill bad';
    $('hostMissing').hidden = false;
    $('hostError').textContent = e.message;
    return;
  }
  refreshJobs();
  setInterval(refreshJobs, 1000);
  setupRecorderBox();
  $('pageVideosRefresh').addEventListener('click', loadPageVideos);
  loadPageVideos();
  if (tab) loadStreams();
}

init();
