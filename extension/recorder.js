// Gravação pela página: injetado pela extensão no frame que tem o vídeo.
// É a técnica do download_videos.js (captureStream + MediaRecorder), mas cada pedaço vai
// para o disco na hora (background -> processo local), então fechar a aba não perde o que já foi gravado.
// Vídeo com DRM ativo (EME) sai preto: o recorder detecta isso e para com o motivo, em vez de gravar preto.

(() => {
  if (window.__sdRecorder && window.__sdRecorder.active) return;   // gravação em andamento neste frame

  const TAG = '[Videocassete Web]';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const t = (key, ...subs) => (chrome.i18n.getMessage(key) || key)
    .replace(/\{(\d)\}/g, (m, i) => (subs[i] != null ? String(subs[i]) : m));

  function pickVideo(index) {
    if (Number.isInteger(index)) return document.querySelectorAll('video')[index] || null;   // escolhido na lista
    let best = null, bestArea = -1;
    for (const v of document.querySelectorAll('video')) {
      const r = v.getBoundingClientRect();
      const area = r.width * r.height + (v.readyState > 0 ? 1 : 0);
      if (area > bestArea) { best = v; bestArea = area; }
    }
    return best;
  }

  function pickMime() {
    return ['video/mp4;codecs=avc1.640028,mp4a.40.2', 'video/mp4;codecs=avc1.42E01E,mp4a.40.2', 'video/mp4',
      'video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm']
      .find(t => MediaRecorder.isTypeSupported(t));
  }

  // Quadro todo preto enquanto o vídeo avança = o navegador está protegendo a imagem (DRM).
  function frameIsBlack(video) {
    try {
      const c = document.createElement('canvas');
      c.width = 32; c.height = 18;
      const g = c.getContext('2d');
      g.drawImage(video, 0, 0, 32, 18);
      const px = g.getImageData(0, 0, 32, 18).data;
      let sum = 0;
      for (let i = 0; i < px.length; i += 4) sum += px[i] + px[i + 1] + px[i + 2];
      return sum / (32 * 18 * 3) < 3;
    } catch (e) {
      return false; // canvas "tainted" (vídeo de outro domínio sem CORS): não dá para medir, segue gravando
    }
  }

  function toBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        // o mime pode ter vírgula ("codecs=avc1...,mp4a..."): o base64 começa depois de ";base64,"
        const url = String(reader.result);
        const at = url.indexOf(';base64,');
        resolve(at >= 0 ? url.slice(at + 8) : '');
      };
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  }

  // Levantamento usado pelo background para escolher o frame com o maior vídeo.
  function survey(index) {
    const v = pickVideo(index);
    if (!v) return null;
    const r = v.getBoundingClientRect();
    return {
      area: r.width * r.height, duration: isFinite(v.duration) ? v.duration : 0,
      currentTime: v.currentTime, mediaKeys: !!v.mediaKeys, readyState: v.readyState,
      mime: pickMime() || '',
    };
  }

  async function start(opts) {
    const port = chrome.runtime.connect({ name: 'rec:' + opts.session });
    let recorder = null, timer = null, stopping = false, sendQueue = Promise.resolve();
    const post = msg => { try { port.postMessage(msg); } catch (e) { /* aba fechando */ } };
    const fail = reason => { post({ type: 'error', reason }); cleanup(); };

    const video = pickVideo(opts.videoIndex);
    if (!video) return fail(t('recNoVideoHere'));
    if (Number.isInteger(opts.videoIndex)) video.scrollIntoView({ block: 'center' });   // feeds pausam vídeo fora da tela
    const rate = Number(opts.playbackRate) || 1;
    const adSelector = opts.adSelector || '.ad-showing';
    const isAd = () => !!(opts.waitForAds !== false && document.querySelector(adSelector));
    const prevRate = video.playbackRate;

    function cleanup() {
      clearInterval(timer);
      try { video.playbackRate = prevRate; } catch (e) { /* noop */ }
      window.__sdRecorder.active = null;
    }

    function stop(reason) {
      if (stopping) return;
      stopping = true;
      clearInterval(timer);
      if (recorder && recorder.state !== 'inactive') {
        recorder.onstop = () => { sendQueue.then(() => { post({ type: 'end', reason }); cleanup(); }); };
        recorder.stop();
      } else {
        post({ type: 'end', reason });
        cleanup();
      }
    }

    port.onMessage.addListener(msg => {
      if (msg.type === 'stop') stop(msg.reason || t('reasonUserStop'));
    });
    port.onDisconnect.addListener(() => { if (!stopping) { stopping = true; try { recorder && recorder.stop(); } catch (e) { /* noop */ } cleanup(); } });
    window.__sdRecorder.active = { stop };

    while (isAd()) {
      post({ type: 'status', note: t('waitAd') });
      await sleep(1000);
      if (stopping) return;
    }
    if (opts.fromStart && isFinite(video.duration) && video.currentTime > 1) {
      const seeked = new Promise(r => video.addEventListener('seeked', r, { once: true }));
      video.currentTime = 0;
      await Promise.race([seeked, sleep(10000)]);
    }
    video.playbackRate = rate;
    try {
      await video.play();
    } catch (e) {
      video.muted = true; // autoplay com som bloqueado: o áudio continua indo para a gravação
      try { await video.play(); } catch (e2) { return fail(t('playBlocked', e2.message)); }
    }

    const stream = video.captureStream ? video.captureStream() : video.mozCaptureStream();
    for (let i = 0; stream.getTracks().length === 0; i++) {
      if (i > 50) return fail(t('captureEmpty'));
      await sleep(100);
    }
    const mime = pickMime();
    recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: Number(opts.videoBitsPerSecond) || 8e6 });
    recorder.ondataavailable = e => {
      if (!e.data.size) return;
      const pos = video.currentTime, dur = isFinite(video.duration) ? video.duration : 0;
      sendQueue = sendQueue.then(async () => post({ type: 'chunk', data: await toBase64(e.data), pos, dur }));
    };
    recorder.onerror = e => fail(t('recorderError', (e.error && e.error.message) || t('unknownErr')));
    post({ type: 'started', mime, rate, duration: isFinite(video.duration) ? video.duration : 0 });
    recorder.start(1000);
    console.log(TAG, 'gravando', mime, 'a', rate + 'x');

    const endAt = opts.duration ? video.currentTime + Number(opts.duration) : null;
    const t0 = Date.now();
    let endedTicks = 0, blackChecks = 0, lastBeat = 0;
    timer = setInterval(() => {
      if (stopping) return;
      const ad = isAd();
      if (ad || video.paused) {
        if (recorder.state === 'recording') recorder.pause();
        endedTicks = video.ended && !ad ? endedTicks + 1 : 0;
        if (endedTicks > 4) return stop(t('endOfVideo'));
        if (Date.now() - lastBeat > 2000) {
          lastBeat = Date.now();
          post({ type: 'status', note: ad ? t('adPaused') : t('videoPaused') });
        }
        return;
      }
      endedTicks = 0;
      if (recorder.state === 'paused') recorder.resume();
      if (video.playbackRate !== rate) video.playbackRate = rate;
      if (endAt != null && video.currentTime >= endAt) return stop(t('chosenDuration'));
      // DRM ativo: confere a imagem nos primeiros segundos; só para se ficar preta o tempo todo
      if (video.mediaKeys && blackChecks < 6 && Date.now() - t0 > 1500 * (blackChecks + 1)) {
        blackChecks = frameIsBlack(video) ? blackChecks + 1 : 99;
        if (blackChecks === 6) {
          stopping = true;
          clearInterval(timer);
          try { recorder.stop(); } catch (e) { /* noop */ }
          return fail(t('drmBlack'));
        }
      }
      if (Date.now() - lastBeat > 2000) {
        lastBeat = Date.now();
        post({ type: 'status', pos: video.currentTime, dur: isFinite(video.duration) ? video.duration : 0 });
      }
    }, 250);
  }

  window.__sdRecorder = { survey, start, active: null };
})();
