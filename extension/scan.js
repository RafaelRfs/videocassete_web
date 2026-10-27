// Lista os <video> do frame para o popup escolher (feeds do Instagram/Facebook têm vários).
// Para cada vídeo procura o link do post subindo no DOM só enquanto o bloco tem esse único vídeo,
// para não pegar o link do post vizinho. Injetado com allFrames; o valor final (Promise) é o resultado.

(async () => {
  const POST_LINK = new RegExp([
    '/(p|reel|reels|tv)/[\\w-]+',                                   // Instagram
    '/videos?/', '/watch/?\\?v=', '/watch/live', '/reel/\\d+', '/share/(v|r|p)/', 'fb\\.watch/',
    '/posts/', 'permalink\\.php', 'story\\.php', 'story_fbid=', '/groups/[^/]+/(posts|permalink)/',   // Facebook
    '/video/\\d+',                                                   // TikTok
    '/status/\\d+',                                                  // X / Twitter
    'youtube\\.com/(watch|shorts)', 'youtu\\.be/', 'vimeo\\.com/\\d+',
  ].join('|'), 'i');
  const SOCIAL = /(^|\.)(facebook\.com|instagram\.com|tiktok\.com|x\.com|twitter\.com|threads\.net|youtube\.com|vimeo\.com)$/i;

  const site = host => {
    const h = String(host || '').toLowerCase().replace(/^(www|m|web|mobile)\./, '');
    return h === 'fb.watch' ? 'facebook.com' : h;
  };
  const pageSite = site(location.hostname);
  const videos = [...document.querySelectorAll('video')];

  function blockOf(video) {
    let node = video, best = video;
    for (let depth = 0; node && depth < 30; depth++, node = node.parentElement) {
      if (node.querySelectorAll && node.querySelectorAll('video').length > 1) break;   // já tem outro vídeo
      best = node;
    }
    return best;
  }

  // Facebook só preenche o href do link do post (o "2 h" do horário) quando o mouse passa por cima.
  async function revealLinks() {
    const lazy = [];
    for (const v of videos) {
      for (const a of blockOf(v).querySelectorAll('a')) {
        const href = a.getAttribute('href') || '';
        if (!href || href === '#' || href.startsWith('#')) lazy.push(a);
      }
    }
    if (!lazy.length) return;
    for (const a of lazy.slice(0, 200)) {
      for (const type of ['mouseover', 'mouseenter', 'focusin']) {
        a.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
      }
    }
    await new Promise(r => setTimeout(r, 400));
    for (const a of lazy.slice(0, 200)) a.dispatchEvent(new MouseEvent('mouseout', { bubbles: true, view: window }));
  }

  function cleanLink(href) {
    try {
      const u = new URL(href, location.href);
      for (const k of [...u.searchParams.keys()]) if (k.startsWith('__') || k === 'ref' || k === 'rdid') u.searchParams.delete(k);
      u.hash = '';
      return u.href;
    } catch (e) {
      return href;
    }
  }

  function postLink(video, block) {
    const found = [];
    let node = video.parentElement;
    for (let depth = 0; node && depth < 30; depth++, node = node.parentElement) {
      if (node.querySelectorAll('video').length > 1) break;
      for (const a of node.querySelectorAll('a[href]')) if (POST_LINK.test(a.href) && !found.includes(a.href)) found.push(a.href);
      if (found.some(h => site(new URL(h).hostname) === pageSite)) break;
    }
    // O <video> de uma rede social é da própria rede: link de outro site (YouTube na legenda) não serve
    const own = found.find(h => site(new URL(h).hostname) === pageSite);
    if (own) return cleanLink(own);
    if (!SOCIAL.test(pageSite) && found.length) return cleanLink(found[0]);
    if (videos.length === 1 && POST_LINK.test(location.href)) return cleanLink(location.href);
    return '';
  }

  function label(video, block) {
    const aria = video.getAttribute('aria-label') || video.title;
    if (aria) return aria.slice(0, 90);
    const text = (block.innerText || '').split('\n').map(s => s.trim()).filter(s => s.length > 2);
    return text.slice(0, 2).join(' · ').slice(0, 90);   // perfil + começo da legenda
  }

  function poster(video, block) {
    if (video.poster) return video.poster;
    const img = [...block.querySelectorAll('img')].find(i => i.naturalWidth >= 120 && /^https?:/.test(i.currentSrc || i.src));
    return img ? (img.currentSrc || img.src) : '';
  }

  await revealLinks();

  return videos.map((v, index) => {
    const r = v.getBoundingClientRect();
    const block = blockOf(v);
    const src = v.currentSrc || v.src || '';
    return {
      index,
      link: postLink(v, block),
      direct: /^https?:/.test(src) ? src : '',     // arquivo direto (não blob:/MSE)
      label: label(v, block),
      poster: poster(v, block),
      width: v.videoWidth, height: v.videoHeight,
      duration: isFinite(v.duration) ? v.duration : 0,
      visible: r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight,
      playing: !v.paused && !v.ended,
      area: r.width * r.height,
    };
  }).filter(x => x.area > 0 || x.duration > 0 || x.link);
})();
