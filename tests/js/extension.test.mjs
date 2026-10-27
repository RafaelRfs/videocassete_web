// Testes unitários da extensão: node --test tests/js/
import assert from 'node:assert/strict';
import test from 'node:test';
import { FakeElement, chromeStub, fakeDocument, load, messages, tick } from './harness.mjs';

const BG_EXPORTS = '({ classify, keepHeaders, originOf, t, EXTRA, HOST })';

function loadBackground(opts) {
  const stub = chromeStub(opts);
  const { result } = load('background.js', { chrome: stub.chrome }, BG_EXPORTS);
  return { ...stub, bg: result };
}

const res = (url, type, headers = {}) => ({
  url, type,
  responseHeaders: Object.entries(headers).map(([name, value]) => ({ name, value })),
});

// ---------------------------------------------------------------- background.js

test('background: detecta HLS, DASH e vídeo progressivo; ignora segmentos e YouTube', () => {
  const { bg } = loadBackground();
  assert.equal(bg.classify(res('https://a.com/x/master.m3u8?t=1', 'xmlhttprequest')), 'hls');
  assert.equal(bg.classify(res('https://a.com/playlist', 'xmlhttprequest', { 'Content-Type': 'application/vnd.apple.mpegurl' })), 'hls');
  assert.equal(bg.classify(res('https://a.com/v/manifest.mpd', 'xmlhttprequest')), 'dash');
  assert.equal(bg.classify(res('https://a.com/v/m', 'other', { 'content-type': 'application/dash+xml' })), 'dash');
  assert.equal(bg.classify(res('https://a.com/movie.mp4', 'media', { 'content-type': 'video/mp4', 'content-range': 'bytes 0-1/50000000' })), 'file');
  assert.equal(bg.classify(res('https://a.com/small.mp4', 'media', { 'content-type': 'video/mp4', 'content-length': '1000' })), null);
  assert.equal(bg.classify(res('https://a.com/seg1.m4s', 'media', { 'content-type': 'video/mp4', 'content-length': '9000000' })), null);
  assert.equal(bg.classify(res('https://r1.googlevideo.com/videoplayback?x=1', 'media', { 'content-type': 'video/mp4' })), null);
  assert.equal(bg.classify(res('blob:https://a.com/1', 'media')), null);
});

test('background: guarda só cabeçalhos de sessão', () => {
  const { bg } = loadBackground();
  const kept = bg.keepHeaders([{ name: 'Cookie', value: 'a=1' }, { name: 'Accept', value: '*/*' },
    { name: 'Authorization', value: 'Bearer x' }, { name: 'X-Token', value: 't' }, { name: 'Referer', value: 'https://p/' }]);
  assert.deepEqual(Object.keys(kept).sort(), ['Authorization', 'Cookie', 'Referer', 'X-Token']);
});

test('background: usa extraHeaders no Chrome e não no Firefox', () => {
  const chrome = loadBackground();
  assert.deepEqual([...chrome.bg.EXTRA], ['extraHeaders']);
  assert.ok(chrome.listeners.beforeSendHeaders[0].spec.includes('extraHeaders'));
  const firefox = loadBackground({ firefox: true });
  assert.deepEqual([...firefox.bg.EXTRA], []);
  assert.deepEqual([...firefox.listeners.beforeSendHeaders[0].spec], ['requestHeaders']);
  assert.deepEqual([...firefox.listeners.headersReceived[0].spec], ['responseHeaders']);
});

for (const firefox of [false, true]) {
  test(`background: stream detectado vai para a lista da aba com os cabeçalhos (${firefox ? 'Firefox' : 'Chrome'})`, async () => {
    const { listeners, session } = loadBackground({ firefox });
    const url = 'https://cdn.example/v/master.m3u8';
    listeners.beforeSendHeaders[0].fn({ tabId: 5, requestId: 'r1', url, requestHeaders: [{ name: 'Cookie', value: 'sid=1' }] });
    listeners.headersReceived[0].fn({
      tabId: 5, requestId: 'r1', url, statusCode: 200, type: 'xmlhttprequest', responseHeaders: [],
      ...(firefox ? { originUrl: 'https://site.example/watch/1' } : { initiator: 'https://site.example' }),
    });
    await tick();
    const [item] = session.tab_5;
    assert.equal(item.url, url);
    assert.equal(item.kind, 'hls');
    assert.equal(item.headers.Cookie, 'sid=1');
    assert.equal(item.headers.Referer, 'https://site.example/');
  });
}

test('background: ignora abas internas e respostas de erro', async () => {
  const { listeners, session } = loadBackground();
  listeners.headersReceived[0].fn({ tabId: -1, requestId: 'a', url: 'https://x/a.m3u8', statusCode: 200, responseHeaders: [] });
  listeners.headersReceived[0].fn({ tabId: 3, requestId: 'b', url: 'https://x/b.m3u8', statusCode: 403, responseHeaders: [] });
  await tick();
  assert.deepEqual(Object.keys(session), []);
});

test('background: cabeçalhos de autenticação do player por servidor', async () => {
  const { listeners } = loadBackground();
  listeners.beforeSendHeaders[0].fn({ tabId: 7, requestId: 'q', url: 'https://media.example/seg1.ts',
    requestHeaders: [{ name: 'Authorization', value: 'Bearer abc' }] });
  const reply = await new Promise(resolve => listeners.message[0].fn({ type: 'host-headers', tabId: 7 }, {}, resolve));
  assert.equal(reply['media.example'].headers.Authorization, 'Bearer abc');
});

test('background: nome do host nativo e textos traduzidos', () => {
  const { bg } = loadBackground();
  assert.equal(bg.HOST, 'videocassete_web');
  assert.equal(bg.t('playBlocked', 'NotAllowedError'), 'the browser did not allow playing the video: NotAllowedError');
  assert.equal(bg.originOf('https://a.example:8443/x?y'), 'https://a.example:8443');
  assert.equal(bg.originOf(''), '');
});

// ---------------------------------------------------------------- popup.js

const POPUP_EXPORTS = '({ clock, mb, fileLabel, qualityName, t, noQuery, jobCard, YTDLP_SITE, YOUTUBE_VIDEO })';

async function loadPopup(opts = {}) {
  const stub = chromeStub(opts);
  const document = fakeDocument();
  const { result } = load('popup.js', { chrome: stub.chrome, document }, POPUP_EXPORTS);
  await tick(20);
  return { ...stub, document, popup: result };
}

test('popup: formatação de tempo, tamanho e nomes', async () => {
  const { popup } = await loadPopup();
  assert.equal(popup.clock(0), '');
  assert.equal(popup.clock(65), '1:05');
  assert.equal(popup.clock(3725), '1:02:05');
  assert.equal(popup.mb(1.5e6), '1.5 MB');
  assert.equal(popup.mb(2.25e9), '2.25 GB');
  assert.deepEqual({ ...popup.fileLabel('https://cdn.x.com/a/b/master%20v.m3u8?tok=1') }, { name: 'master v.m3u8', host: 'cdn.x.com' });
  assert.equal(popup.noQuery('https://a/b.m3u8?x#y'), 'https://a/b.m3u8');
  assert.equal(popup.qualityName('best'), 'best');
  assert.equal(popup.qualityName('720'), 'up to 720p');
});

test('popup: reconhece links de YouTube e redes sociais', async () => {
  const { popup } = await loadPopup();
  assert.ok(popup.YOUTUBE_VIDEO.test('https://www.youtube.com/watch?v=abc'));
  assert.ok(popup.YOUTUBE_VIDEO.test('https://youtu.be/abc'));
  assert.ok(!popup.YOUTUBE_VIDEO.test('https://www.youtube.com/feed/subscriptions'));
  for (const url of ['https://www.instagram.com/reel/x/', 'https://fb.watch/x/', 'https://x.com/a/status/1', 'https://vimeo.com/1']) {
    assert.ok(popup.YTDLP_SITE.test(url), url);
  }
  assert.ok(!popup.YTDLP_SITE.test('https://example.com/video.mp4'));
});

test('popup: traduz a página e mostra "processo local ausente" sem o host', async () => {
  const { document, sentNative } = await loadPopup({ locale: 'pt_BR', nativeReply: null });
  assert.equal(document.getElementById('hostStatus').textContent, 'processo local ausente');
  assert.equal(document.getElementById('hostMissing').hidden, false);
  assert.match(document.getElementById('hostError').textContent, /native messaging host not found/);
  assert.equal(sentNative[0].host, 'videocassete_web');
  assert.equal(sentNative[0].msg.cmd, 'ping');
  assert.equal(sentNative[0].msg.lang, 'pt-BR');
});

test('popup: com o host, mostra pronto e manda o idioma em cada comando', async () => {
  const nativeReply = msg => (msg.cmd === 'ping'
    ? { ok: true, version: '2.0.0', python: '3.13', ffmpeg: '/x/ffmpeg', ytdlp: null, hints: { 'yt-dlp': 'brew install yt-dlp' } }
    : { ok: true, jobs: [] });
  const { document, sentNative } = await loadPopup({ nativeReply });
  assert.equal(document.getElementById('hostStatus').textContent, 'ready · missing yt-dlp');
  assert.match(document.getElementById('hostStatus').title, /brew install yt-dlp/);
  assert.ok(sentNative.some(s => s.msg.cmd === 'list'));
  assert.ok(sentNative.every(s => s.msg.lang === 'en-US'));
});

test('popup: card de download mostra progresso e os botões certos', async () => {
  const { popup } = await loadPopup();
  const texts = node => [node.textContent, ...node.children.flatMap(c => (c instanceof FakeElement ? texts(c) : [c]))];
  const running = popup.jobCard({ id: 'j1', title: 'Video', status: 'downloading', done: 3, total: 10, unit: 'segments',
    bytes: 5e6, speed: 1e6, percent: 30, kind: 'download' });
  const all = texts(running).join(' | ');
  assert.match(all, /Downloading · 3\/10 segments · 5\.0 MB · 1\.0 MB\/s/);
  assert.match(all, /Cancel/);
  const failed = texts(popup.jobCard({ id: 'j2', title: 'V', status: 'error', error: 'HTTP 403', kind: 'download', percent: 0 })).join(' | ');
  assert.match(failed, /Resume/);
  assert.match(failed, /Remove/);
  assert.match(failed, /HTTP 403/);
  const done = texts(popup.jobCard({ id: 'j3', title: 'V', status: 'done', kind: 'download', percent: 100 })).join(' | ');
  assert.match(done, /Open folder/);
});

// ---------------------------------------------------------------- recorder.js e scan.js

test('recorder: escolhe o maior vídeo e um formato suportado', () => {
  const body = new FakeElement('body');
  const small = new FakeElement('video', { rect: { width: 100, height: 50 }, readyState: 4, duration: 30, currentTime: 2 });
  const big = new FakeElement('video', { rect: { width: 1280, height: 720 }, readyState: 4, duration: 600, currentTime: 0 });
  body.append(small, big);
  const MediaRecorder = { isTypeSupported: mime => mime.startsWith('video/webm') };
  const { context } = load('recorder.js', { chrome: chromeStub().chrome, document: fakeDocument(body), MediaRecorder });
  const info = context.__sdRecorder.survey(null);
  assert.equal(info.area, 1280 * 720);
  assert.equal(info.duration, 600);
  assert.equal(info.mime, 'video/webm;codecs=vp9,opus');
  assert.equal(context.__sdRecorder.survey(0).duration, 30);
});

test('scan: acha o link do post de cada vídeo do feed', async () => {
  const body = new FakeElement('body');
  const post = (id, caption) => {
    const article = new FakeElement('article');
    const video = new FakeElement('video', { rect: { width: 400, height: 700, top: 0, bottom: 700 }, duration: 15, paused: false,
      currentSrc: 'blob:https://www.instagram.com/1' });
    const link = new FakeElement('a', { attrs: { href: `/reel/${id}/` }, href: `https://www.instagram.com/reel/${id}/?__x=1` });
    article.append(new FakeElement('span', { textContent: 'perfil_' + id }), new FakeElement('span', { textContent: caption }), video, link);
    return article;
  };
  body.append(post('AAA', 'primeira legenda'), post('BBB', 'segunda legenda'));
  const { result } = load('scan.js', {
    document: fakeDocument(body), location: new URL('https://www.instagram.com/'), innerHeight: 900,
    MouseEvent: class {}, getComputedStyle: () => ({}),
  });
  const list = await result;
  assert.equal(list.length, 2);
  assert.equal(list[0].link, 'https://www.instagram.com/reel/AAA/');
  assert.equal(list[1].link, 'https://www.instagram.com/reel/BBB/');
  assert.equal(list[0].direct, '');
  assert.equal(list[0].playing, true);
  assert.match(list[0].label, /perfil_AAA/);
});

test('locales: en e pt_BR têm as mesmas chaves', () => {
  assert.deepEqual(Object.keys(messages('en')).sort(), Object.keys(messages('pt_BR')).sort());
});
