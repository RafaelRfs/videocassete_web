#!/usr/bin/env python3
"""Baixa um stream HLS, DASH ou arquivo de vídeo em paralelo e junta em MP4.

Roda separado do navegador: a extensão Videocassete Web cria a pasta do job e
dispara este script, que segue mesmo com a aba ou o Chrome fechados. Se cair
no meio, rodar de novo na mesma pasta retoma do segmento onde parou.

Só usa a biblioteca padrão. O ffmpeg, se encontrado, junta as faixas em
MP4 e decripta HLS AES-128; sem ele, os segmentos são concatenados direto.
Streams com DRM (Widevine, FairPlay, PlayReady) e transmissões ao vivo não
são suportados.

Uso direto, sem a extensão:
    python stream_worker.py --url URL [--quality best|worst|720] [--out PASTA] [--header "Cookie: ..."]
    python stream_worker.py PASTA_DO_JOB      # retoma um job
"""
import argparse
import json
import math
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import platform_paths as plat  # noqa: E402
from i18n import set_lang, tr  # noqa: E402
import i18n  # noqa: E402

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/130.0 Safari/537.36')
JOBS_DIR = plat.JOBS_DIR
CHUNK_SIZE = 4 * 1024 * 1024    # pedaço de cada requisição paralela ao baixar um arquivo único
RETRIES = 5
DROP_HEADERS = {'range', 'accept-encoding', 'host', 'content-length', 'connection',
                'if-none-match', 'if-modified-since', 'if-range'}
NO_WINDOW = plat.NO_WINDOW


class Cancelled(Exception):
    pass


class StreamError(Exception):
    """Erro com mensagem pronta para o usuário."""


class DrmError(StreamError):
    """Mídia realmente criptografada com DRM (não só sinalizada no manifesto)."""

    def __init__(self, detail=''):
        super().__init__(tr('drm', detail=f' [{detail}]' if detail else ''))


ENCRYPTION_BOXES = (b'encv', b'enca', b'tenc', b'sinf')


# ---------------------------------------------------------------- HTTP

def request_headers(url, headers):
    """Cabeçalhos para esta URL. Cookie só vai para o servidor dono dele: o cookie capturado no
    manifesto fica no servidor do manifesto (__origin_host__), e os demais servidores recebem os
    cookies que a extensão leu do navegador para eles (__host_cookies__: {host: "a=1; b=2"})."""
    headers = headers or {}
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    origin = headers.get('__origin_host__')
    out = {k: v for k, v in headers.items() if not k.startswith('__') and k.lower() not in DROP_HEADERS}
    jar = headers.get('__host_cookies__') or {}
    player = (headers.get('__host_headers__') or {}).get(host) or {}
    if (origin and host != origin) or jar.get(host):
        out = {k: v for k, v in out.items() if k.lower() != 'cookie'}
    if origin and host != origin:   # token do manifesto (Authorization, X-...) não sai do servidor dele
        out = {k: v for k, v in out.items() if not re.match(r'(authorization|x-)', k, re.I)}
    # o que o player mandou para este servidor (ex.: Authorization) vale mais que o do manifesto
    for k, v in player.items():
        if k.lower() in DROP_HEADERS or (k.lower() == 'cookie' and jar.get(host)):
            continue
        out = {kk: vv for kk, vv in out.items() if kk.lower() != k.lower()}
        out[k] = v
    if jar.get(host):
        out['Cookie'] = jar[host]
    return out


_roots_lock = threading.Lock()
_roots_refreshed = set()


def refresh_windows_roots(url):
    """O Windows só baixa um certificado raiz quando um programa dele precisa, e o Python não dispara isso.
    Uma conexão pelo PowerShell (SChannel) faz o Windows buscar a raiz; a próxima tentativa do Python passa.
    Retorna True se vale tentar de novo."""
    netloc = urllib.parse.urlsplit(url).netloc.rsplit('@', 1)[-1]
    if os.name != 'nt' or not re.fullmatch(r'[A-Za-z0-9.-]+(:\d+)?', netloc):
        return False
    with _roots_lock:
        if netloc in _roots_refreshed:
            return False
        _roots_refreshed.add(netloc)
        log(f'certificate of {netloc} not recognized by Python; asking Windows to refresh its root store')
        command = (f"try {{ Invoke-WebRequest -Uri 'https://{netloc}/' -Method Head -UseBasicParsing "
                   f"-TimeoutSec 20 | Out-Null }} catch {{}}")
        try:
            subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
                           capture_output=True, timeout=40, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return True


def is_cert_error(err):
    return isinstance(getattr(err, 'reason', err), ssl.SSLCertVerificationError)


def open_url(url, headers, rng=None, method='GET', timeout=30):
    h = {'User-Agent': USER_AGENT}
    h.update(request_headers(url, headers))
    if rng:
        h['Range'] = f'bytes={rng[0]}-{rng[1]}'
    req = urllib.request.Request(url, headers=h, method=method)
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=plat.SSL_CONTEXT)
    except urllib.error.URLError as e:
        if is_cert_error(e) and refresh_windows_roots(url):
            return urllib.request.urlopen(req, timeout=timeout, context=plat.SSL_CONTEXT)
        raise


def retry_wait(err, attempt):
    """Segundos a esperar antes de tentar de novo; None se o erro não vale nova tentativa."""
    if is_cert_error(err):
        return None
    if isinstance(err, urllib.error.HTTPError):
        if err.code not in (408, 429, 500, 502, 503, 504):
            return None
        after = err.headers.get('Retry-After', '') if err.headers else ''
        if after.isdigit():
            return min(int(after), 60)
    return min(2 ** attempt, 15)


def open_url_retry(url, headers, rng=None, attempts=4):
    for attempt in range(attempts):
        try:
            return open_url(url, headers, rng)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            wait_s = retry_wait(e, attempt)
            if wait_s is None or attempt == attempts - 1:
                if is_cert_error(e):
                    raise StreamError(tr('cert_untrusted', host=urllib.parse.urlsplit(url).hostname))
                if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                    raise StreamError(tr('http_429'))
                raise
            time.sleep(wait_s)


YOUTUBE_PAGE = re.compile(r'(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)$', re.I)
# Redes que entregam o vídeo em pedaços assinados sem manifesto reaproveitável: vão pelo yt-dlp
YTDLP_SITES = re.compile(r'(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com|instagram\.com|facebook\.com|'
                         r'fb\.watch|tiktok\.com|x\.com|twitter\.com|vimeo\.com|threads\.net)$', re.I)
YOUTUBE_MEDIA = re.compile(r'(^|\.)googlevideo\.com$', re.I)


def uses_ytdlp(url):
    """Página de vídeo de YouTube, Instagram, Facebook etc.: vai pelo yt-dlp em vez do downloader HLS/DASH."""
    return bool(YTDLP_SITES.search(urllib.parse.urlsplit(url).hostname or ''))


def check_supported_host(url):
    if YOUTUBE_MEDIA.search(urllib.parse.urlsplit(url).hostname or ''):
        raise StreamError(tr('youtube_internal'))


def fetch_text(url, headers):
    with open_url_retry(url, headers) as r:
        return r.read().decode('utf-8', 'replace'), r.geturl()


def short(url):
    p = urllib.parse.urlsplit(url)
    return p.netloc + (p.path if len(p.path) < 60 else '...' + p.path[-57:])


def sniff(url, headers):
    """Descobre se a URL é HLS, DASH ou um arquivo de vídeo lendo só o começo."""
    check_supported_host(url)
    with open_url_retry(url, headers, rng=(0, 4095)) as r:
        head = r.read(4096)
        final = r.geturl()
        ctype = (r.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    check_supported_host(final)
    text = head.decode('utf-8', 'replace').lstrip('﻿ \r\n\t')
    if text.startswith('#EXTM3U'):
        return 'hls', final
    if '<MPD' in text[:3000]:
        return 'dash', final
    if ctype.startswith('text/') or text[:15].lower().startswith(('<!doctype', '<html', '<?xml', '{', '[')):
        raise StreamError(tr('not_a_stream'))
    is_media = (ctype.startswith(('video/', 'audio/')) or ctype in ('application/octet-stream', 'binary/octet-stream')
                or head[4:8] in (b'ftyp', b'moov', b'styp') or head[:4] == b'\x1aE\xdf\xa3' or head[:1] == b'G')
    if not is_media:
        raise StreamError(tr('not_video', ctype=ctype or tr('unknown')))
    return 'file', final


def file_size(url, headers):
    """(tamanho, aceita Range) de um arquivo, sem baixá-lo."""
    try:
        with open_url(url, headers, rng=(0, 0)) as r:
            cr = r.headers.get('Content-Range', '')
            if r.status == 206 and '/' in cr and not cr.endswith('/*'):
                return int(cr.rsplit('/', 1)[1]), True
            return int(r.headers.get('Content-Length') or 0), False
    except urllib.error.HTTPError:
        return 0, False


# ---------------------------------------------------------------- HLS

ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def hls_attrs(text):
    return {k: v.strip('"') for k, v in ATTR_RE.findall(text)}


def parse_hls_master(text, base):
    variants, audios = [], {}
    lines = [line.strip() for line in text.splitlines()]
    for i, line in enumerate(lines):
        if line.startswith('#EXT-X-STREAM-INF:'):
            a = hls_attrs(line.split(':', 1)[1])
            uri = next((x for x in lines[i + 1:] if x and not x.startswith('#')), None)
            if not uri:
                continue
            width, height = (int(n) for n in a['RESOLUTION'].split('x')) if 'RESOLUTION' in a else (0, 0)
            variants.append({
                'id': f'v{len(variants)}',
                'url': urllib.parse.urljoin(base, uri),
                'bandwidth': int(a.get('BANDWIDTH') or a.get('AVERAGE-BANDWIDTH') or 0),
                'width': width, 'height': height,
                'codecs': a.get('CODECS', ''),
                'audio': a.get('AUDIO'),
            })
        elif line.startswith('#EXT-X-MEDIA:'):
            a = hls_attrs(line.split(':', 1)[1])
            if a.get('TYPE') == 'AUDIO' and a.get('URI'):
                audios.setdefault(a.get('GROUP-ID'), []).append({
                    'url': urllib.parse.urljoin(base, a['URI']),
                    'default': a.get('DEFAULT') == 'YES',
                    'name': a.get('NAME', ''),
                })
    return variants, audios


def parse_hls_media(text, base):
    if '#EXT-X-ENDLIST' not in text:
        raise StreamError(tr('live'))
    segments, init, key = [], None, None
    seq, target, dur, pending_range = 0, 10, 0.0, None
    last_end = {}

    def byterange(value, uri):
        length, _, offset = value.partition('@')
        start = int(offset) if offset else last_end.get(uri, 0)
        last_end[uri] = start + int(length)
        return [start, start + int(length) - 1]

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXT-X-MEDIA-SEQUENCE:'):
            seq = int(line.split(':', 1)[1])
        elif line.startswith('#EXT-X-TARGETDURATION:'):
            target = math.ceil(float(line.split(':', 1)[1]))
        elif line.startswith('#EXT-X-KEY:'):
            a = hls_attrs(line.split(':', 1)[1])
            method = a.get('METHOD', 'NONE')
            if method == 'NONE':
                key = None
            elif method == 'AES-128' and a.get('KEYFORMAT', 'identity') == 'identity':
                key = {'uri': urllib.parse.urljoin(base, a['URI']), 'iv': a.get('IV')}
            else:
                raise DrmError(f'{method}, {a.get("KEYFORMAT", "identity")}')
        elif line.startswith('#EXT-X-MAP:'):
            a = hls_attrs(line.split(':', 1)[1])
            uri = urllib.parse.urljoin(base, a['URI'])
            init = {'url': uri, 'range': byterange(a['BYTERANGE'], uri) if 'BYTERANGE' in a else None}
        elif line.startswith('#EXTINF:'):
            dur = float(line[8:].split(',')[0] or 0)
        elif line.startswith('#EXT-X-BYTERANGE:'):
            pending_range = line.split(':', 1)[1]
        elif not line.startswith('#'):
            uri = urllib.parse.urljoin(base, line)
            segments.append({'url': uri, 'dur': dur, 'key': key,
                             'range': byterange(pending_range, uri) if pending_range else None})
            pending_range = None
    if not segments:
        raise StreamError(tr('no_segments'))
    return {
        'container': 'mp4' if init else 'ts',
        'init': init, 'segments': segments,
        'seq': seq, 'target': target,
        'keys': sorted({s['key']['uri'] for s in segments if s['key']}),
        'duration': sum(s['dur'] for s in segments),
    }


# ---------------------------------------------------------------- DASH

TPL_RE = re.compile(r'\$(RepresentationID|Number|Bandwidth|Time)(?:%0(\d+)d)?\$')


def xml_tag(el):
    return el.tag.rsplit('}', 1)[-1]


def xml_child(el, name):
    return next((c for c in el if xml_tag(c) == name), None) if el is not None else None


def xml_children(el, name):
    return [c for c in el if xml_tag(c) == name] if el is not None else []


def iso_duration(text):
    m = re.fullmatch(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?', (text or '').strip())
    if not m:
        return 0.0
    d, h, mi, s = (float(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def with_base(el, base):
    b = xml_child(el, 'BaseURL')
    return urllib.parse.urljoin(base, b.text.strip()) if b is not None and b.text else base


def fill_template(tpl, rep_id, bandwidth, number=None, time_=None):
    values = {'RepresentationID': rep_id, 'Number': number, 'Bandwidth': bandwidth, 'Time': time_}

    def sub(m):
        value = str(values[m.group(1)])
        return value.zfill(int(m.group(2))) if m.group(2) else value
    return TPL_RE.sub(sub, tpl).replace('$$', '$')


def dash_range(text):
    if not text:
        return None
    a, b = text.split('-')
    return [int(a), int(b)]


def template_segments(tpl, timeline, rbase, rep_id, bw, period_dur):
    timescale = int(tpl.get('timescale', 1))
    start = int(tpl.get('startNumber', 1))
    pto = int(tpl.get('presentationTimeOffset', 0))
    times = []
    if timeline is not None:
        entries = xml_children(timeline, 'S')
        t, n = 0, start
        for i, s in enumerate(entries):
            if s.get('t') is not None:
                t = int(s.get('t'))
            d, r = int(s.get('d')), int(s.get('r', 0))
            if r < 0:
                nxt = entries[i + 1].get('t') if i + 1 < len(entries) else None
                end = int(nxt) if nxt is not None else pto + period_dur * timescale
                r = math.ceil((end - t) / d) - 1
            for _ in range(r + 1):
                times.append((n, t))
                n, t = n + 1, t + d
    elif tpl.get('duration'):
        d = int(tpl['duration'])
        count = math.ceil(period_dur * timescale / d)
        times = [(start + i, pto + i * d) for i in range(count)]
    else:
        raise StreamError(tr('tpl_no_duration'))
    media = tpl['media']
    return [{'url': urllib.parse.urljoin(rbase, fill_template(media, rep_id, bw, n, t)), 'range': None}
            for n, t in times]


def parse_dash(text, base):
    root = ET.fromstring(text)
    if root.get('type') == 'dynamic':
        raise StreamError(tr('live'))
    total = iso_duration(root.get('mediaPresentationDuration'))
    base = with_base(root, base)
    periods = xml_children(root, 'Period')
    if not periods:
        raise StreamError(tr('mpd_no_period'))
    period = periods[0]
    period_dur = iso_duration(period.get('duration')) or total
    pbase = with_base(period, base)
    reps = []
    for aset in xml_children(period, 'AdaptationSet'):
        abase = with_base(aset, pbase)
        a_drm = xml_child(aset, 'ContentProtection') is not None
        a_type = aset.get('contentType') or (aset.get('mimeType') or '').split('/')[0]
        for rep in xml_children(aset, 'Representation'):
            mime = rep.get('mimeType') or aset.get('mimeType') or ''
            kind = a_type or mime.split('/')[0]
            if kind not in ('video', 'audio'):
                continue
            rid, bw = rep.get('id', ''), rep.get('bandwidth', '0')
            rbase = with_base(rep, abase)
            info = {
                'id': rid, 'kind': kind,
                'bandwidth': int(bw or 0),
                'width': int(rep.get('width') or aset.get('width') or 0),
                'height': int(rep.get('height') or aset.get('height') or 0),
                'codecs': rep.get('codecs') or aset.get('codecs') or '',
                'drm': a_drm or xml_child(rep, 'ContentProtection') is not None,
                'init': None, 'segments': [], 'single': None,
            }
            tpl_nodes = [n for n in (xml_child(period, 'SegmentTemplate'), xml_child(aset, 'SegmentTemplate'),
                                     xml_child(rep, 'SegmentTemplate')) if n is not None]
            seg_list = xml_child(rep, 'SegmentList')
            if seg_list is None:
                seg_list = xml_child(aset, 'SegmentList')
            if tpl_nodes:
                tpl = {}
                for n in tpl_nodes:
                    tpl.update(n.attrib)
                timeline = next((xml_child(n, 'SegmentTimeline') for n in reversed(tpl_nodes)
                                 if xml_child(n, 'SegmentTimeline') is not None), None)
                if tpl.get('initialization'):
                    info['init'] = {'url': urllib.parse.urljoin(rbase, fill_template(tpl['initialization'], rid, bw)),
                                    'range': None}
                info['segments'] = template_segments(tpl, timeline, rbase, rid, bw, period_dur)
            elif seg_list is not None:
                ini = xml_child(seg_list, 'Initialization')
                if ini is not None:
                    info['init'] = {'url': urllib.parse.urljoin(rbase, ini.get('sourceURL') or ''),
                                    'range': dash_range(ini.get('range'))}
                info['segments'] = [{'url': urllib.parse.urljoin(rbase, s.get('media') or ''),
                                     'range': dash_range(s.get('mediaRange'))}
                                    for s in xml_children(seg_list, 'SegmentURL')]
            else:
                info['single'] = rbase   # SegmentBase ou só BaseURL: um arquivo inteiro
            reps.append(info)
    if not reps:
        raise StreamError(tr('mpd_no_tracks'))
    return reps, total, len(periods)


# ---------------------------------------------------------------- escolha de qualidade

def pick(items, quality):
    if not items:
        return None
    q = str(quality or 'best')
    by_id = [x for x in items if x.get('id') == q]
    if by_id:
        return by_id[0]
    if any(x.get('height') for x in items):   # descarta variantes só de áudio
        items = [x for x in items if x.get('height')]
    ordered = sorted(items, key=lambda x: (x.get('height', 0), x.get('bandwidth', 0)))
    if q == 'worst':
        return ordered[0]
    if q.isdigit():
        fit = [x for x in ordered if x.get('height', 0) <= int(q)]
        return fit[-1] if fit else ordered[0]
    return ordered[-1]


def label(item):
    parts = []
    if item.get('height'):
        parts.append(f"{item['height']}p")
    if item.get('bandwidth'):
        parts.append(f"{item['bandwidth'] / 1e6:.1f} Mbps")
    codecs = [c.strip().split('.')[0] for c in item.get('codecs', '').split(',') if c.strip()]
    video = [c for c in codecs if c not in ('mp4a', 'ac-3', 'ec-3', 'opus', 'flac')]
    if video or codecs:
        parts.append((video or codecs)[0])
    if not item.get('height') and codecs and not video:
        parts.append(tr('audio_only'))
    return ' · '.join(parts) or item.get('id', '?')


def probe(url, headers, cookies=None):
    """Resumo do stream para a extensão: tipo, duração e qualidades disponíveis."""
    if uses_ytdlp(url):
        return probe_ytdlp(url, cookies)
    kind, final = sniff(url, headers)
    out = {'kind': kind, 'url': final, 'variants': [], 'children': [], 'duration': 0, 'error': None}
    urls = [final]   # servidores envolvidos: a extensão manda os cookies de cada um
    try:
        if kind == 'hls':
            text, final = fetch_text(final, headers)
            if '#EXT-X-STREAM-INF' in text:
                variants, audios = parse_hls_master(text, final)
                out['variants'] = [{'id': v['id'], 'label': label(v), 'height': v['height']} for v in variants]
                out['children'] = [v['url'] for v in variants] + [a['url'] for g in audios.values() for a in g]
                media = parse_hls_media(*fetch_text(pick(variants, 'worst')['url'], headers))
                urls += out['children']
            else:
                media = parse_hls_media(text, final)
            out['duration'] = media['duration']
            urls += [s['url'] for s in media['segments'][:1]] + media['keys'] + ([media['init']['url']] if media['init'] else [])
        elif kind == 'dash':
            reps, total, _ = parse_dash(*fetch_text(final, headers))
            for r in reps:
                urls += [x for x in ((r['init'] or {}).get('url'), r['single'],
                                     r['segments'][0]['url'] if r['segments'] else None) if x]
            for kind_ in ('video', 'audio'):
                check_drm(pick([r for r in reps if r['kind'] == kind_], 'worst'), headers)
            videos = [r for r in reps if r['kind'] == 'video'] or reps
            out['variants'] = [{'id': r['id'], 'label': label(r), 'height': r['height']} for r in videos]
            out['duration'] = total
        else:
            size, _ = file_size(final, headers)
            out['size'] = size
    except DrmError as e:
        out['drm'] = True
        out['error'] = str(e)
    except StreamError as e:
        out['error'] = str(e)
    out['hosts'] = url_hosts(urls)
    return out


def url_hosts(urls):
    return sorted({(urllib.parse.urlsplit(u).hostname or '').lower() for u in urls if u} - {''})


def media_is_encrypted(rep, headers):
    """Procura as caixas de criptografia CENC no começo da mídia. O manifesto pode ter
    <ContentProtection> e mesmo assim entregar a mídia aberta (ex.: Microsoft Stream)."""
    if rep.get('init'):
        src, rng = rep['init']['url'], rep['init'].get('range')
    elif rep.get('single'):
        src, rng = rep['single'], None
    elif rep.get('segments'):
        src, rng = rep['segments'][0]['url'], rep['segments'][0].get('range')
    else:
        return False
    with open_url_retry(src, headers, rng=rng or (0, 262143)) as r:
        head = r.read(262144)
    if head[:4] == b'\x1a\x45\xdf\xa3':
        return webm_is_encrypted(head)
    if not looks_like_media(head):   # nem cabeçalho MP4/WebM/TS: o serviço cifrou o arquivo inteiro
        return True
    return any(box in head for box in ENCRYPTION_BOXES)


MP4_BOXES = (b'ftyp', b'styp', b'moov', b'moof', b'sidx', b'free', b'emsg', b'mdat', b'prft')


def looks_like_media(head):
    """O começo do arquivo tem cara de MP4, WebM/Matroska ou MPEG-TS?"""
    if len(head) >= 8 and head[4:8] in MP4_BOXES:
        return True
    if head[:4] == b'\x1a\x45\xdf\xa3':
        return True
    return len(head) > 188 and head[0] == 0x47 and head[188] == 0x47


def webm_is_encrypted(head):
    """WebM/Matroska: procura ContentEncryption (0x5035) dentro do elemento Tracks."""
    tracks_id, seek_ref = b'\x16\x54\xae\x6b', b'\x53\xab\x84'   # Tracks; SeekID de 4 bytes no índice
    pos = head.find(tracks_id)
    while pos > 0 and head[pos - 3:pos] == seek_ref:   # pula as referências do SeekHead
        pos = head.find(tracks_id, pos + 4)
    if pos < 0 or pos + 5 > len(head):
        return False
    first = head[pos + 4]
    width = next((i + 1 for i in range(8) if first & (0x80 >> i)), 8)   # tamanho EBML (vint)
    size = first & (0xFF >> width)
    for b in head[pos + 5:pos + 4 + width]:
        size = (size << 8) | b
    body = head[pos + 4 + width:pos + 4 + width + size]
    return b'\x50\x35' in body


def check_drm(rep, headers):
    if not rep or not rep.get('drm'):
        return
    try:
        encrypted = media_is_encrypted(rep, headers)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        log(f'could not check encryption of {rep.get("id")} ({e}); continuing')
        return
    if encrypted:
        raise DrmError('DASH')
    log(f'ContentProtection signaled on {rep.get("id")}, but the media is not encrypted: continuing')
    rep['drm'] = False


# ---------------------------------------------------------------- plano de download

def build_plan(job):
    url, headers, quality = job['url'], job.get('headers') or {}, job.get('quality') or 'best'
    kind, final = sniff(url, headers)
    tracks = []
    if kind == 'hls':
        text, final = fetch_text(final, headers)
        if '#EXT-X-STREAM-INF' in text:
            variants, audios = parse_hls_master(text, final)
            v = pick(variants, quality)
            tracks.append({'name': 'video', **parse_hls_media(*fetch_text(v['url'], headers))})
            group = audios.get(v['audio']) or []
            if group:
                a = next((x for x in group if x['default']), group[0])
                tracks.append({'name': 'audio', **parse_hls_media(*fetch_text(a['url'], headers))})
        else:
            tracks.append({'name': 'main', **parse_hls_media(text, final)})
    elif kind == 'dash':
        reps, _, periods = parse_dash(*fetch_text(final, headers))
        video = pick([r for r in reps if r['kind'] == 'video'], quality)
        audio = pick([r for r in reps if r['kind'] == 'audio'], 'best')
        for name, rep in (('video', video), ('audio', audio)):
            if not rep:
                continue
            check_drm(rep, headers)
            if rep['single']:
                tracks.append({'name': name, 'container': 'file', 'init': None, 'keys': [],
                               'segments': split_file(rep['single'], headers)})
            else:
                tracks.append({'name': name, 'container': 'mp4', 'init': rep['init'], 'keys': [],
                               'segments': rep['segments']})
        if periods > 1:
            log(f'warning: the MPD has {periods} periods; only the first one was downloaded')
    else:
        tracks.append({'name': 'main', 'container': 'file', 'init': None, 'keys': [],
                       'segments': split_file(final, headers)})
    return {'kind': kind, 'tracks': tracks}


def split_file(url, headers):
    size, ranges = file_size(url, headers)
    if not ranges or size <= CHUNK_SIZE:
        return [{'url': url, 'range': None}]
    return [{'url': url, 'range': [a, min(a + CHUNK_SIZE, size) - 1]} for a in range(0, size, CHUNK_SIZE)]


# ---------------------------------------------------------------- estado e cancelamento

LOG_FILE = None


def log(msg):
    line = time.strftime('%H:%M:%S ') + msg
    if LOG_FILE:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    if sys.stdout and sys.stdout.isatty():
        print(line, flush=True)


class State:
    """Progresso do job, gravado em state.json a cada meio segundo."""

    def __init__(self, job_dir, title):
        self.path = job_dir / 'state.json'
        self.lock = threading.Lock()
        self.data = {'status': 'starting', 'title': title, 'done': 0, 'total': 0, 'bytes': 0,
                     'speed': 0, 'output': None, 'error': None, 'pid': os.getpid(), 'updated': time.time()}
        self._last = (time.time(), 0)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def add_bytes(self, n):
        with self.lock:
            self.data['bytes'] += n

    def segment_done(self):
        with self.lock:
            self.data['done'] += 1

    def set(self, **kw):
        with self.lock:
            self.data.update(kw)
        self.flush()

    def flush(self):
        with self.lock:
            now = time.time()
            t0, b0 = self._last
            if now - t0 >= 1:
                self.data['speed'] = int((self.data['bytes'] - b0) / (now - t0))
                self._last = (now, self.data['bytes'])
            self.data['updated'] = now
            payload = json.dumps(self.data, ensure_ascii=False)
        tmp = self.path.with_suffix('.tmp')
        for _ in range(5):
            try:
                tmp.write_text(payload, encoding='utf-8')
                os.replace(tmp, self.path)
                return
            except PermissionError:
                time.sleep(0.05)   # o host pode estar lendo o arquivo no Windows

    def _loop(self):
        while not self._stop.wait(0.5):
            self.flush()

    def close(self):
        self._stop.set()
        self.flush()


class CancelFlag:
    """Cancelamento pedido pela extensão (arquivo 'cancel' na pasta) ou por erro em outra thread."""

    def __init__(self, job_dir):
        self.file = job_dir / 'cancel'
        self.abort = threading.Event()
        self._checked = 0.0

    def check(self):
        if self.abort.is_set():
            raise Cancelled()
        now = time.monotonic()
        if now - self._checked > 1:
            self._checked = now
            if self.file.exists():
                self.abort.set()
                raise Cancelled()


# ---------------------------------------------------------------- download

class Throttle:
    """Limite de conexões simultâneas que cai pela metade quando o servidor começa a recusar
    (401/403/429 depois de já ter entregado segmentos): alguns servidores, como o SharePoint,
    respondem assim a pedidos demais à frente do player, em vez de mandar 429."""

    def __init__(self, limit):
        self.limit = max(1, int(limit))
        self.active = 0
        self.ok = 0
        self.cv = threading.Condition()

    def __enter__(self):
        with self.cv:
            while self.active >= self.limit:
                self.cv.wait(0.5)
            self.active += 1
        return self

    def __exit__(self, *exc):
        with self.cv:
            self.active -= 1
            self.cv.notify_all()

    def success(self):
        with self.cv:
            self.ok += 1

    def slow_down(self, code):
        with self.cv:
            now = time.monotonic()
            # várias recusas ao mesmo tempo contam como uma só: reduz no máximo a cada 2 s
            if self.limit > 1 and now - getattr(self, '_last_cut', 0) > 2:
                self._last_cut = now
                self.limit = max(1, self.limit // 2)
                log(f'server refused (HTTP {code}) after {self.ok} segments: reducing to {self.limit} connections')


SECRET_RE = re.compile(r'[A-Za-z0-9_\-.%+/=]{40,}')


def error_reason(err):
    """Trecho da resposta de erro do servidor, com tokens ocultados, para o log e a mensagem."""
    try:
        body = err.read(600).decode('utf-8', 'replace')
    except Exception:  # noqa: BLE001
        return ''
    try:
        data = json.loads(body)
        msg = data.get('error', {}).get('message') if isinstance(data.get('error'), dict) else data.get('message')
        body = msg or body
    except ValueError:
        pass
    return SECRET_RE.sub('***', ' '.join(str(body).split()))[:200]


AUTH_CODES = (401, 403, 429)


def download_to(seg, headers, path, state, cancel, throttle=None):
    if path.exists():
        return
    throttle = throttle or Throttle(1)
    tmp = path.with_name(path.name + '.part')
    rng = seg.get('range')
    wait_s, last_code, reason = 0, None, ''
    attempts = RETRIES + 3   # dá tempo do controle de ritmo agir antes de desistir
    for attempt in range(attempts):
        if wait_s:
            time.sleep(wait_s)
        cancel.check()
        got = 0
        try:
            with throttle, open_url(seg['url'], headers, rng) as r, open(tmp, 'wb') as f:
                skip, left = 0, None
                if rng and r.status != 206:   # servidor ignorou o Range: recorta na mão
                    skip, left = rng[0], rng[1] - rng[0] + 1
                while True:
                    buf = r.read(256 * 1024)
                    if not buf:
                        break
                    if skip:
                        cut = min(skip, len(buf))
                        buf, skip = buf[cut:], skip - cut
                    if left is not None:
                        buf, left = buf[:left], left - len(buf[:left])
                    f.write(buf)
                    got += len(buf)
                    state.add_bytes(len(buf))
                    cancel.check()
                    if left == 0:
                        break
            os.replace(tmp, path)
            state.segment_done()
            throttle.success()
            return
        except Cancelled:
            raise
        except urllib.error.HTTPError as e:
            state.add_bytes(-got)
            last_code, reason = e.code, error_reason(e) or reason
            log(f'attempt {attempt + 1}: HTTP {e.code} on {short(seg["url"])}' + (f' | server: {reason}' if reason else ''))
            recusa_com_progresso = e.code in AUTH_CODES and throttle.ok > 0
            if recusa_com_progresso:
                throttle.slow_down(e.code)   # já baixou antes: é ritmo, não falta de login
            elif e.code in (401, 403, 404, 410) and attempt >= 1:
                break
            wait_s = retry_wait(e, attempt) or min(2 ** attempt, 15)
            if recusa_com_progresso:
                wait_s = max(wait_s, min(3 * (attempt + 1), 20))
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            state.add_bytes(-got)
            log(f'attempt {attempt + 1} failed on {short(seg["url"])}: {e}')
            wait_s = min(2 ** attempt, 15)
    detail = tr('server_said', reason=reason) if reason else ''
    if last_code == 429:
        raise StreamError(tr('http_429_one', detail=detail))
    if last_code in (401, 403, 404, 410):
        raise StreamError(tr('http_auth', code=last_code, url=short(seg['url']), detail=detail))
    raise StreamError(tr('failed_after', n=attempts, url=short(seg['url']), detail=detail))


def track_ext(track):
    return {'ts': '.ts', 'mp4': '.m4s', 'file': '.bin'}[track['container']]


def segment_tasks(plan, seg_dir):
    tasks = []
    for tr in plan['tracks']:
        d = seg_dir / tr['name']
        d.mkdir(parents=True, exist_ok=True)
        if tr.get('init'):
            tasks.append((tr['init'], d / 'init.mp4'))
        for i, uri in enumerate(tr.get('keys') or []):
            tasks.append(({'url': uri, 'range': None}, d / f'key_{i}.key'))
        ext = track_ext(tr)
        for i, s in enumerate(tr['segments']):
            tasks.append((s, d / f'seg_{i:05d}{ext}'))
    return tasks


# ---------------------------------------------------------------- junção

def find_ffmpeg():
    return plat.find_tool('ffmpeg')


def write_local_playlist(track, d):
    """Playlist HLS apontando para os segmentos baixados, para o ffmpeg decriptar e juntar."""
    lines = ['#EXTM3U', '#EXT-X-VERSION:7', f'#EXT-X-TARGETDURATION:{track["target"]}',
             f'#EXT-X-MEDIA-SEQUENCE:{track["seq"]}', '#EXT-X-PLAYLIST-TYPE:VOD']
    if track.get('init'):
        lines.append('#EXT-X-MAP:URI="init.mp4"')
    keys = track.get('keys') or []
    current = None
    ext = track_ext(track)
    for i, s in enumerate(track['segments']):
        k = s.get('key')
        marker = (k['uri'], k.get('iv')) if k else None
        if marker != current:
            if k:
                iv = f',IV={k["iv"]}' if k.get('iv') else ''
                lines.append(f'#EXT-X-KEY:METHOD=AES-128,URI="key_{keys.index(k["uri"])}.key"{iv}')
            else:
                lines.append('#EXT-X-KEY:METHOD=NONE')
            current = marker
        lines.append(f'#EXTINF:{s.get("dur", 0):.3f},')
        lines.append(f'seg_{i:05d}{ext}')
    lines.append('#EXT-X-ENDLIST')
    path = d / 'local.m3u8'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def concat_track(track, d):
    ext = {'ts': '.ts', 'mp4': '.mp4', 'file': '.mp4'}[track['container']]
    out = d.parent / f'{track["name"]}{ext}'
    with open(out, 'wb') as f:
        parts = ([d / 'init.mp4'] if track.get('init') else []) + \
                [d / f'seg_{i:05d}{track_ext(track)}' for i in range(len(track['segments']))]
        for p in parts:
            with open(p, 'rb') as src:
                shutil.copyfileobj(src, f, 1024 * 1024)
    return out


def run_ffmpeg(ff, inputs, out, hls):
    cmd = [ff, '-hide_banner', '-loglevel', 'error', '-y']
    for p in inputs:
        if hls:
            cmd += ['-allowed_extensions', 'ALL', '-extension_picky', '0', '-protocol_whitelist', 'file,crypto,data']
        cmd += ['-i', str(p)]
    if len(inputs) > 1:
        cmd += ['-map', '0:v:0', '-map', '1:a:0']
    cmd += ['-c', 'copy', '-movflags', '+faststart', str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                       creationflags=NO_WINDOW)
    if r.returncode != 0 and hls and 'extension_picky' in r.stderr:
        return run_ffmpeg(ff, inputs, out, hls=False)   # ffmpeg antigo, sem essa opção
    if r.returncode != 0:
        raise StreamError(tr('ffmpeg_merge_failed', err=r.stderr.strip()[-600:]))


def unique_path(path):
    if not path.exists():
        return path
    for i in range(1, 1000):
        p = path.with_name(f'{path.stem} ({i}){path.suffix}')
        if not p.exists():
            return p
    return path


def merge(plan, seg_dir, out_dir, title):
    ff = find_ffmpeg()
    base = out_dir / safe_name(title)
    tracks = plan['tracks']
    if plan['kind'] == 'hls' and ff:
        inputs = [write_local_playlist(tr, seg_dir / tr['name']) for tr in tracks]
        out = unique_path(base.with_suffix('.mp4'))
        run_ffmpeg(ff, inputs, out, hls=True)
        return out
    if any(tr.get('keys') for tr in tracks):
        raise StreamError(tr('aes_needs_ffmpeg', hint=plat.install_hint('ffmpeg')))
    for tr in tracks:
        d = seg_dir / tr['name']
        first = d / 'init.mp4' if tr.get('init') else d / f'seg_00000{track_ext(tr)}'
        if first.exists() and not looks_like_media(first.read_bytes()[:4096]):
            raise DrmError(tr('service_ciphered'))
    files = [concat_track(tr, seg_dir / tr['name']) for tr in tracks]
    if ff:
        out = unique_path(base.with_suffix('.mp4'))
        run_ffmpeg(ff, files, out, hls=False)
        return out
    if len(files) == 1:
        out = unique_path(base.with_suffix(files[0].suffix))
        shutil.move(files[0], out)
        return out
    log('no ffmpeg: video and audio were left in separate files')
    outs = [unique_path(base.with_name(f'{base.name}.{f.stem}{f.suffix}')) for f in files]
    for f, o in zip(files, outs):
        shutil.move(f, o)
    return outs[0]


def safe_name(title):
    name = re.sub(r'^\(\d+\)\s*', '', title or 'video')
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', name).strip(' .')
    return name[:120] or 'video'


# ---------------------------------------------------------------- YouTube, Instagram, Facebook... (yt-dlp)

def find_ytdlp():
    return plat.find_tool('yt-dlp')


def find_deno():
    return plat.find_tool('deno')


def tool_env():
    """PATH com o Deno (o yt-dlp usa para o JavaScript do YouTube) e o ffmpeg."""
    extra = [os.path.dirname(p) for p in (find_deno(), find_ffmpeg()) if p]
    return dict(os.environ, PATH=os.pathsep.join(extra + [os.environ.get('PATH', '')]))


def write_cookie_file(cookies, path):
    """Cookies da extensão (chrome.cookies) no formato Netscape que o yt-dlp lê."""
    lines = ['# Netscape HTTP Cookie File']
    for c in cookies or []:
        domain = c.get('domain', '')
        if not c.get('hostOnly') and not domain.startswith('.'):
            domain = '.' + domain
        lines.append('\t'.join([
            domain, 'TRUE' if domain.startswith('.') else 'FALSE', c.get('path') or '/',
            'TRUE' if c.get('secure') else 'FALSE', str(int(c.get('expirationDate') or 0)),
            c.get('name', ''), c.get('value', ''),
        ]))
    Path(path).write_text('\n'.join(lines) + '\n', encoding='utf-8')


def ytdlp_command(cookie_file=None):
    exe = find_ytdlp()
    if not exe:
        raise StreamError(tr('need_ytdlp', hint=plat.install_hint('yt-dlp')))
    cmd = [exe, '--encoding', 'utf-8', '--ignore-config', '--no-playlist', '--no-warnings']
    ff = find_ffmpeg()
    if ff:
        cmd += ['--ffmpeg-location', ff]
    if cookie_file and Path(cookie_file).exists():
        cmd += ['--cookies', str(cookie_file)]
    return cmd


def ytdlp_error(text):
    errors = [line for line in text.splitlines() if line.startswith('ERROR:')]
    msg = errors[-1][6:].strip() if errors else (text.strip().splitlines() or [tr('yt_unknown')])[-1]
    low = msg.lower()
    if ('confirm you' in low and 'bot' in low) or 'login required' in low or 'log in' in low or 'cookies' in low:
        return tr('yt_login')
    if 'private' in low or 'members' in low:
        return tr('yt_private')
    if 'unsupported url' in low:
        return tr('yt_unsupported')
    if 'there is no video' in low or 'no video formats' in low:
        return tr('yt_no_video')
    if 'drm' in low:
        return str(DrmError('site'))
    return 'yt-dlp: ' + msg[:400]


def probe_ytdlp(url, cookies):
    tmp = JOBS_DIR.parent / f'probe-{os.getpid()}-{time.time_ns()}.txt'
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        if cookies:
            write_cookie_file(cookies, tmp)
        r = subprocess.run(ytdlp_command(tmp if cookies else None) + ['-J', url], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', env=tool_env(), timeout=120, creationflags=NO_WINDOW)
    finally:
        tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        raise StreamError(ytdlp_error(r.stderr))
    info = json.loads(r.stdout)
    if info.get('is_live'):
        raise StreamError(tr('live'))
    entries = [e for e in (info.get('entries') or []) if e] or [info]   # carrossel: um item por mídia
    formats = [f for e in entries for f in e.get('formats') or []]
    heights = sorted({f['height'] for f in formats
                      if f.get('vcodec') not in (None, 'none') and f.get('height')}, reverse=True)
    out = {'kind': 'ytdlp', 'url': url, 'title': info.get('title') or entries[0].get('title'),
           'duration': sum(e.get('duration') or 0 for e in entries),
           'variants': [{'id': str(h), 'label': f'{h}p', 'height': h} for h in heights],
           'children': [], 'error': None, 'items': len(entries)}
    if not heights and not any(f.get('acodec') not in (None, 'none') for f in formats):
        out['error'] = tr('yt_no_video')
    return out


def ytdlp_sort(quality):
    q = str(quality or 'best')
    if q == 'mp3':   # só áudio: melhor faixa de áudio, convertida em MP3 com título, artista e capa
        return ['-f', 'ba/b', '-x', '--audio-format', 'mp3', '--audio-quality', '0',
                '--embed-metadata', '--embed-thumbnail', '--convert-thumbnails', 'jpg']
    fmt = ['--merge-output-format', 'mp4']
    if q == 'worst':
        return ['-f', 'wv*+wa/w'] + fmt
    res = f'res:{q}' if q.isdigit() else 'res'
    return ['-f', 'bv*+ba/b', '-S', f'{res},ext:mp4:m4a'] + fmt   # prefere H.264/AAC, que cabem direto no MP4


kill_tree = plat.kill_tree


def ytdlp_output_name(url, out_dir):
    """YouTube: título do vídeo. Redes sociais: o "título" é a legenda inteira (emojis, hashtags,
    contagem de views) e vira um nome quebrado, então usa rede + data/hora: facebook_20261027-120501."""
    carousel = '%(playlist_index& - |)s%(playlist_index|)s'
    if YOUTUBE_PAGE.search(urllib.parse.urlsplit(url).hostname or ''):
        return '%(title).140B' + carousel + '.%(ext)s'
    host = re.sub(r'^(www|m|web|mobile)\.', '', (urllib.parse.urlsplit(url).hostname or 'video').lower())
    site = {'fb.watch': 'facebook', 'x.com': 'x', 'twitter.com': 'x'}.get(host, host.split('.')[0])
    name = f'{site}_{time.strftime("%Y%m%d-%H%M%S")}'
    n = 1
    while any(Path(out_dir).glob(glob_escape(name) + '*')):   # dois downloads no mesmo segundo
        n += 1
        name = f'{site}_{time.strftime("%Y%m%d-%H%M%S")}-{n}'
    return name + carousel + '.%(ext)s'


def glob_escape(text):
    return re.sub(r'([\[\]*?])', r'[\1]', text)


def run_youtube_job(job, job_dir, state, cancel):
    out_dir = Path(job.get('out_dir') or plat.default_downloads())
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = job_dir / 'segments'
    tmp_dir.mkdir(exist_ok=True)
    cmd = ytdlp_command(job_dir / 'cookies.txt') + ytdlp_sort(job.get('quality')) + [
        '-N', str(job.get('threads') or 8),
        '-P', f'home:{out_dir}', '-P', f'temp:{tmp_dir}', '-o', ytdlp_output_name(job['url'], out_dir),
        '--no-mtime', '--newline', '--progress',
        '--progress-template', 'download:PROG %(info.format_id)s %(progress.downloaded_bytes)s '
                               '%(progress.total_bytes)s %(progress.total_bytes_estimate)s %(progress.speed)s',
        '--progress-template', 'postprocess:POST %(progress.postprocessor)s %(progress.status)s',
        '--print', 'after_move:FILE %(filepath)s',
        job['url'],
    ]
    state.set(status='downloading', unit='percent', total=100, done=0, part=1)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                            errors='replace', env=tool_env(), **plat.background_popen_kwargs())
    lines, output = [], None
    reader = threading.Thread(target=lambda: lines.extend(iter(proc.stdout.readline, '')), daemon=True)
    reader.start()
    finished, last_down, seen, formats = 0, 0, 0, []
    try:
        while proc.poll() is None or reader.is_alive() or seen < len(lines):
            try:
                cancel.check()
            except Cancelled:
                kill_tree(proc)
                raise
            while seen < len(lines):
                line = lines[seen].strip()
                seen += 1
                parts = line.split()
                if parts[:1] == ['PROG'] and len(parts) == 6:
                    fmt, down, total, estimate, speed = (p if p not in ('NA', 'None') else '0' for p in parts[1:])
                    down, size = int(float(down)), int(float(total)) or int(float(estimate))
                    if fmt not in formats:   # começou o próximo arquivo (vídeo e depois áudio)
                        formats.append(fmt)
                        finished += last_down
                        last_down = 0
                        state.set(part=len(formats))
                    last_down = max(last_down, down)
                    with state.lock:
                        state.data['bytes'] = finished + last_down
                        state.data['done'] = min(100, int(last_down * 100 / size)) if size else 0
                elif line.startswith('FILE '):
                    output = line[5:].strip()
                elif parts[:1] == ['POST'] and ('Merger' in line or 'ExtractAudio' in line):
                    state.set(status='merging', done=100, speed=0)
            time.sleep(0.3)
    finally:
        if proc.poll() is None:
            kill_tree(proc)
    if proc.returncode != 0:
        raise StreamError(ytdlp_error('\n'.join(lines)))
    return Path(output) if output else out_dir


# ---------------------------------------------------------------- gravação pela página

def atempo_chain(factor):
    """atempo aceita 0.5..100 por filtro; encadeia para fatores menores (ex.: 0.25 = 0.5,0.5)."""
    parts = []
    while factor < 0.5:
        parts.append('atempo=0.5')
        factor /= 0.5
    parts.append(f'atempo={factor:.6f}')
    return ','.join(parts)


def finalize_recording(job, job_dir, state):
    src = next((p for p in job_dir.glob('recording.*') if p.stat().st_size > 0), None)
    if not src:
        raise StreamError(tr('nothing_recorded'))
    state.set(status='merging', unit='percent', total=100, done=100, speed=0)
    out_dir = Path(job.get('out_dir') or plat.default_downloads())
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / safe_name(job.get('title'))
    ff = find_ffmpeg()
    if not ff:
        out = unique_path(base.with_suffix(src.suffix))
        shutil.move(src, out)
        return out
    rate = float(job.get('rate') or 1)
    out = unique_path(base.with_suffix('.mp4'))
    if abs(rate - 1) > 0.01:
        # gravado acelerado: estica de volta para a duração original (precisa recodificar)
        cmd = [ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(src),
               '-filter:v', f'setpts={rate}*PTS', '-filter:a', atempo_chain(1 / rate),
               '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-c:a', 'aac', '-b:a', '192k',
               '-movflags', '+faststart', str(out)]
    else:
        cmd = [ff, '-hide_banner', '-loglevel', 'error', '-y', '-fflags', '+genpts', '-i', str(src),
               '-c', 'copy', '-movflags', '+faststart', str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=NO_WINDOW)
    if r.returncode != 0:
        out.unlink(missing_ok=True)
        if rate == 1 and src.suffix == '.webm':   # codecs que o MP4 não aceita: entrega o WebM como veio
            out = unique_path(base.with_suffix('.webm'))
            shutil.move(src, out)
            return out
        raise StreamError(tr('ffmpeg_rec_failed', err=r.stderr.strip()[-500:]))
    src.unlink(missing_ok=True)
    return out


# ---------------------------------------------------------------- execução

def run_job(job_dir):
    global LOG_FILE
    job_dir = Path(job_dir)
    LOG_FILE = job_dir / 'worker.log'
    job = json.loads((job_dir / 'job.json').read_text(encoding='utf-8'))
    set_lang(job.get('lang'))
    title = job.get('title') or 'video'
    headers = dict(job.get('headers') or {})
    headers['__origin_host__'] = (urllib.parse.urlsplit(job['url']).hostname or '').lower()
    cookie_jar = job_dir / 'host_cookies.json'   # cookies por servidor, lidos do navegador pela extensão
    if cookie_jar.exists():
        headers['__host_cookies__'] = json.loads(cookie_jar.read_text(encoding='utf-8'))
    player_headers = job_dir / 'host_headers.json'   # cabeçalhos que o player mandou para cada servidor
    if player_headers.exists():
        headers['__host_headers__'] = json.loads(player_headers.read_text(encoding='utf-8'))
    job['headers'] = headers
    for h, hs in (headers.get('__host_headers__') or {}).items():
        log(f'player headers for {h}: {", ".join(sorted(hs))}')   # só os nomes, nunca os valores
    state = State(job_dir, title)
    cancel = CancelFlag(job_dir)
    seg_dir = job_dir / 'segments'
    log(f'start: {short(job["url"])} quality={job.get("quality") or "best"}')
    try:
        if job.get('kind') == 'recording':
            output = finalize_recording(job, job_dir, state)
            state.set(status='done', output=str(output), unit='percent', total=100, done=100, speed=0)
            log(f'recording ready: {output}')
            return state.data
        if uses_ytdlp(job['url']):
            output = run_youtube_job(job, job_dir, state, cancel)
            shutil.rmtree(seg_dir, ignore_errors=True)
            (job_dir / 'cookies.txt').unlink(missing_ok=True)
            state.set(status='done', output=str(output), done=100, speed=0,
                      title=Path(output).name if Path(output).is_file() else title)   # lista mostra o arquivo final
            log(f'done: {output}')
            return state.data
        plan_file = job_dir / 'plan.json'
        if plan_file.exists():   # retomada: usa o plano salvo, o manifesto pode ter expirado
            plan = json.loads(plan_file.read_text(encoding='utf-8'))
        else:
            state.set(status='analyzing')
            plan = build_plan(job)
            plan_file.write_text(json.dumps(plan), encoding='utf-8')
        if 'hosts' not in job:   # para o Retomar mandar cookies novos desses servidores
            urls = [s['url'] for tr in plan['tracks'] for s in tr['segments'][:1] + ([tr['init']] if tr.get('init') else [])]
            saved = json.loads((job_dir / 'job.json').read_text(encoding='utf-8'))
            saved['hosts'] = url_hosts(urls + [job['url']] + [k for tr in plan['tracks'] for k in tr.get('keys') or []])
            (job_dir / 'job.json').write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding='utf-8')
        tasks = segment_tasks(plan, seg_dir)
        pending = [(s, p) for s, p in tasks if not p.exists()]
        state.set(status='downloading', total=len(tasks), done=len(tasks) - len(pending), error=None)
        log(f'{plan["kind"]}: {len(tasks)} segments, {len(pending)} to download, {job.get("threads") or 8} connections')

        throttle = Throttle(int(job.get('threads') or 8))
        throttle.ok = len(tasks) - len(pending)   # na retomada, o que já baixou conta como progresso
        with ThreadPoolExecutor(max_workers=throttle.limit) as pool:
            futures = [pool.submit(download_to, s, headers, p, state, cancel, throttle) for s, p in pending]
            finished, _ = wait(futures, return_when=FIRST_EXCEPTION)
            errors = [f.exception() for f in finished if f.exception()]
            if errors:
                cancel.abort.set()
                pool.shutdown(cancel_futures=True)
                raise next((e for e in errors if not isinstance(e, Cancelled)), errors[0])

        state.set(status='merging', speed=0)
        out_dir = Path(job.get('out_dir') or plat.default_downloads())
        out_dir.mkdir(parents=True, exist_ok=True)
        output = merge(plan, seg_dir, out_dir, title)
        shutil.rmtree(seg_dir, ignore_errors=True)
        cookie_jar.unlink(missing_ok=True)
        player_headers.unlink(missing_ok=True)
        state.set(status='done', output=str(output), speed=0)
        log(f'done: {output}')
    except Cancelled:
        shutil.rmtree(seg_dir, ignore_errors=True)
        (job_dir / 'cookies.txt').unlink(missing_ok=True)
        cookie_jar.unlink(missing_ok=True)
        player_headers.unlink(missing_ok=True)
        state.set(status='cancelled', speed=0)
        log('cancelled')
    except Exception as e:   # noqa: BLE001 - qualquer falha vira estado de erro, com os segmentos guardados
        msg = str(e) if isinstance(e, StreamError) else f'{type(e).__name__}: {e}'
        state.set(status='error', error=msg, speed=0)
        log('error: ' + msg + '\n' + traceback.format_exc())
    finally:
        state.close()
    return state.data


def new_job_dir(url, title, headers, quality, threads, out_dir, lang=None):
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r'[^\w-]+', '_', safe_name(title))[:40].strip('_') or 'video'
    stamp = time.strftime('%Y%m%d-%H%M%S')
    job_dir = JOBS_DIR / f'{stamp}-{slug}'
    n = 1
    while job_dir.exists():
        n += 1
        job_dir = JOBS_DIR / f'{stamp}-{slug}-{n}'
    job_dir.mkdir()
    job = {'url': url, 'title': title, 'headers': headers, 'quality': quality,
           'threads': threads, 'out_dir': out_dir, 'lang': lang or i18n.LANG, 'created': time.time()}
    (job_dir / 'job.json').write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding='utf-8')
    (job_dir / 'state.json').write_text(json.dumps({'status': 'queued', 'title': title, 'updated': time.time()},
                                                   ensure_ascii=False), encoding='utf-8')
    return job_dir


def main(argv):
    if len(argv) == 1 and Path(argv[0]).is_dir():
        return 0 if run_job(argv[0])['status'] == 'done' else 1
    ap = argparse.ArgumentParser(description='Baixa um stream HLS/DASH/arquivo em paralelo e junta em MP4.')
    ap.add_argument('--url', required=True)
    ap.add_argument('--title', default='')
    ap.add_argument('--quality', default='best', help='best, worst, altura máxima (ex.: 720) ou id da variante')
    ap.add_argument('--threads', type=int, default=8)
    ap.add_argument('--out', default='')
    ap.add_argument('--header', action='append', default=[], help='"Nome: valor", pode repetir')
    ap.add_argument('--probe', action='store_true', help='só mostra o que tem no stream')
    ap.add_argument('--lang', default='', help='idioma das mensagens: pt ou en')
    args = ap.parse_args(argv)
    if args.lang:
        set_lang(args.lang)
    headers = dict(h.split(':', 1) for h in args.header)
    headers = {k.strip(): v.strip() for k, v in headers.items()}
    if args.probe:
        print(json.dumps(probe(args.url, headers), indent=1, ensure_ascii=False))
        return 0
    title = args.title or Path(urllib.parse.urlsplit(args.url).path).stem or 'video'
    job_dir = new_job_dir(args.url, title, headers, args.quality, args.threads, args.out)
    print(f'job: {job_dir}')
    result = run_job(job_dir)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'done' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
