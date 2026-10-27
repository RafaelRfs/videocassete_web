#!/usr/bin/env python3
"""Host de Native Messaging da extensão Videocassete Web.

O Chrome abre este script a cada mensagem da extensão (chrome.runtime.sendNativeMessage),
manda um JSON pelo stdin e espera um JSON no stdout. Os downloads em si rodam em
processos stream_worker.py separados, que continuam mesmo depois que o Chrome fecha.
Na gravação pela página, a extensão usa connectNative: a conexão fica aberta e cada
pedaço gravado chega como rec_chunk e é escrito direto no disco.

Comandos: ping, probe, download, list, cancel, resume, remove, open,
rec_start, rec_chunk, rec_status, rec_stop.
"""
import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stream_worker as worker  # noqa: E402
import platform_paths as plat  # noqa: E402
from i18n import set_lang, tr  # noqa: E402

VERSION = '2.0.0'
STALE_SECONDS = 15   # worker sem atualizar o estado por mais que isso = interrompido
ACTIVE = ('queued', 'starting', 'analyzing', 'downloading', 'merging', 'recording')


def read_message():
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    size = struct.unpack('<I', raw)[0]
    return json.loads(sys.stdin.buffer.read(size).decode('utf-8'))


def send_message(obj):
    data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    sys.stdout.buffer.write(struct.pack('<I', len(data)) + data)
    sys.stdout.buffer.flush()


def job_dir(job_id):
    d = (worker.JOBS_DIR / str(job_id)).resolve()
    if d.parent != worker.JOBS_DIR.resolve() or not (d / 'job.json').exists():
        raise ValueError(tr('job_not_found', id=job_id))
    return d


def read_json(path, default=None):
    for _ in range(3):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            time.sleep(0.05)
    return default


def spawn_worker(d):
    """Dispara o worker desligado do Chrome, para sobreviver ao navegador fechar."""
    py = Path(sys.executable)
    pyw = py.with_name('pythonw.exe')
    args = [str(pyw if pyw.exists() else py), str(Path(__file__).with_name('stream_worker.py')), str(d)]
    (d / 'cancel').unlink(missing_ok=True)
    common = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  cwd=str(d), close_fds=True)
    if os.name != 'nt':
        subprocess.Popen(args, start_new_session=True, **common)
        return
    detached = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    for flags in (detached | 0x01000000, detached):  # + CREATE_BREAKAWAY_FROM_JOB, se o Chrome permitir
        try:
            subprocess.Popen(args, creationflags=flags, **common)
            return
        except OSError:
            continue
    raise RuntimeError(tr('spawn_failed'))


def save_host_cookies(d, host_cookies, host_headers=None):
    """Cookies {host: "a=1; b=2"} e cabeçalhos do player {host: {nome: valor}} lidos pela extensão.
    Ficam fora do job.json (têm tokens) e somem quando o job termina."""
    if isinstance(host_cookies, dict):
        clean = {str(h).lower(): str(c) for h, c in host_cookies.items() if h and c}
        if clean:
            (d / 'host_cookies.json').write_text(json.dumps(clean), encoding='utf-8')
    if isinstance(host_headers, dict):
        clean = {str(h).lower(): {str(k): str(v) for k, v in hs.items()}
                 for h, hs in host_headers.items() if h and isinstance(hs, dict) and hs}
        if clean:
            (d / 'host_headers.json').write_text(json.dumps(clean), encoding='utf-8')


def job_summary(d):
    job = read_json(d / 'job.json', {})
    st = read_json(d / 'state.json', {}) or {}
    status = st.get('status', 'queued')
    if status in ACTIVE and time.time() - st.get('updated', 0) > STALE_SECONDS:
        status = 'interrupted'
    total, done = st.get('total') or 0, st.get('done') or 0
    return {
        'id': d.name,
        'title': st.get('title') or job.get('title') or d.name,
        'status': status,
        'done': done, 'total': total,
        'percent': (done if st.get('unit') == 'percent' else round(done * 100 / total, 1)) if total else 0,
        'bytes': st.get('bytes') or 0,
        'speed': st.get('speed') or 0,
        'output': st.get('output'),
        'error': st.get('error'),
        'unit': st.get('unit') or 'segments',
        'part': st.get('part') or 1,
        'kind': job.get('kind') or 'download',
        'url': job.get('url') or '',
        'quality': job.get('quality') or '',
        'hosts': job.get('hosts') or [],
        'position': st.get('position') or 0,
        'duration': st.get('duration') or 0,
        'note': st.get('note') or '',
        'created': job.get('created', 0),
    }


def handle(msg):
    cmd = msg.get('cmd')
    if cmd == 'ping':
        return {'ok': True, 'version': VERSION, 'ffmpeg': worker.find_ffmpeg(), 'ytdlp': worker.find_ytdlp(),
                'deno': worker.find_deno(), 'python': sys.version.split()[0], 'platform': sys.platform,
                'jobs_dir': str(worker.JOBS_DIR), 'downloads': str(plat.default_downloads()),
                'hints': {t: plat.install_hint(t) for t in ('ffmpeg', 'yt-dlp', 'deno')}}

    if cmd == 'probe':
        return {'ok': True, **worker.probe(msg['url'], msg.get('headers') or {}, msg.get('cookies'))}

    if cmd == 'download':
        d = worker.new_job_dir(msg['url'], msg.get('title') or 'video', msg.get('headers') or {},
                               msg.get('quality') or 'best', int(msg.get('threads') or 8), msg.get('out_dir') or '')
        if msg.get('cookies'):   # fica fora do job.json e é apagado quando o download termina
            worker.write_cookie_file(msg['cookies'], d / 'cookies.txt')
        save_host_cookies(d, msg.get('host_cookies'), msg.get('host_headers'))
        spawn_worker(d)
        return {'ok': True, 'id': d.name}

    if cmd == 'list':
        worker.JOBS_DIR.mkdir(parents=True, exist_ok=True)
        dirs = sorted((p for p in worker.JOBS_DIR.iterdir() if (p / 'job.json').exists()),
                      key=lambda p: p.name, reverse=True)[:int(msg.get('limit') or 30)]
        return {'ok': True, 'jobs': [job_summary(d) for d in dirs]}

    if cmd == 'cancel':
        d = job_dir(msg['id'])
        info = job_summary(d)
        if info['kind'] == 'recording' and info['status'] in ('recording', 'interrupted'):
            for f in d.glob('recording.*'):   # gravação órfã (o navegador já não está mandando pedaços)
                f.unlink(missing_ok=True)
            st = read_json(d / 'state.json', {}) or {}
            st.update(status='cancelled', speed=0, updated=time.time())
            (d / 'state.json').write_text(json.dumps(st, ensure_ascii=False), encoding='utf-8')
        elif info['status'] in ACTIVE:
            (d / 'cancel').write_text('1')
        else:
            shutil.rmtree(d / 'segments', ignore_errors=True)
            st = read_json(d / 'state.json', {}) or {}
            st.update(status='cancelled', speed=0)
            (d / 'state.json').write_text(json.dumps(st, ensure_ascii=False), encoding='utf-8')
        return {'ok': True}

    if cmd == 'resume':
        d = job_dir(msg['id'])
        if job_summary(d)['status'] in ACTIVE:
            return {'ok': False, 'error': tr('still_running')}
        save_host_cookies(d, msg.get('host_cookies'), msg.get('host_headers'))   # cookies e tokens atualizados
        if msg.get('headers'):   # cabeçalhos novos do manifesto (tokens renovados pela página)
            job = read_json(d / 'job.json', {})
            job['headers'] = msg['headers']
            (d / 'job.json').write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding='utf-8')
        spawn_worker(d)
        return {'ok': True}

    if cmd == 'remove':
        d = job_dir(msg['id'])
        if job_summary(d)['status'] in ACTIVE:
            return {'ok': False, 'error': tr('cancel_first')}
        shutil.rmtree(d, ignore_errors=True)
        return {'ok': True}

    if cmd == 'open':
        d = job_dir(msg['id'])
        out = job_summary(d).get('output')
        if out and Path(out).exists():
            plat.reveal(out)
        else:
            job = read_json(d / 'job.json', {})
            folder = Path(job.get('out_dir') or plat.default_downloads())
            folder.mkdir(parents=True, exist_ok=True)
            plat.open_dir(folder)
        return {'ok': True}

    return {'ok': False, 'error': tr('unknown_cmd', cmd=cmd)}


class Recordings:
    """Gravações pela página: cada pedaço que chega pelo connectNative vai direto para o disco.
    Quando a gravação para (ou o navegador fecha a conexão), o worker converte o arquivo em MP4."""

    def __init__(self):
        self.open = {}

    def start(self, msg):
        ext = 'mp4' if msg.get('ext') == 'mp4' else 'webm'
        d = worker.new_job_dir(msg.get('url') or '', msg.get('title') or tr('rec_default_title'), {}, 'rec', 1,
                               msg.get('out_dir') or '')
        job = read_json(d / 'job.json', {})
        job.update(kind='recording', ext=ext, rate=float(msg.get('rate') or 1))
        (d / 'job.json').write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding='utf-8')
        self.open[d.name] = {'dir': d, 'file': open(d / f'recording.{ext}', 'ab'), 'bytes': 0, 't0': time.time(),
                             'pos': 0, 'dur': float(msg.get('duration') or 0), 'note': '', 'title': job['title']}
        self.save_state(d.name, 'recording')
        return {'ok': True, 'id': d.name}

    def save_state(self, rec_id, status, **extra):
        rec = self.open[rec_id]
        elapsed = max(time.time() - rec['t0'], 1)
        st = {'status': status, 'title': rec['title'], 'unit': 'percent', 'total': 100,
              'done': int(rec['pos'] * 100 / rec['dur']) if rec['dur'] else 0, 'bytes': rec['bytes'],
              'speed': int(rec['bytes'] / elapsed), 'position': rec['pos'], 'duration': rec['dur'],
              'note': rec['note'], 'updated': time.time(), 'pid': os.getpid(), 'output': None, 'error': None}
        st.update(extra)
        tmp = rec['dir'] / 'state.tmp'
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, rec['dir'] / 'state.json')

    def chunk(self, msg):
        rec = self.open.get(msg.get('id'))
        if not rec:
            return
        data = base64.b64decode(msg.get('data') or '')
        rec['file'].write(data)
        rec['file'].flush()
        rec['bytes'] += len(data)
        rec['pos'] = float(msg.get('pos') or rec['pos'])
        rec['dur'] = float(msg.get('dur') or rec['dur'])
        rec['note'] = ''
        self.save_state(msg['id'], 'recording')

    def status(self, msg):
        rec = self.open.get(msg.get('id'))
        if not rec:
            return
        if msg.get('pos') is not None:
            rec['pos'] = float(msg['pos'])
        if msg.get('dur'):
            rec['dur'] = float(msg['dur'])
        rec['note'] = msg.get('note') or ''
        self.save_state(msg['id'], 'recording')

    def stop(self, msg):
        rec_id = msg.get('id')
        rec = self.open.get(rec_id)
        if not rec:
            return {'ok': True}
        rec['file'].close()
        reason = msg.get('reason') or ''
        cancelled = msg.get('discard') and (msg.get('cancelled') or reason in ('cancelado', 'cancelled', ''))
        if msg.get('discard') or rec['bytes'] == 0:
            for f in rec['dir'].glob('recording.*'):
                f.unlink(missing_ok=True)
            if cancelled:
                self.save_state(rec_id, 'cancelled', speed=0)
            else:   # DRM, vídeo não encontrado ou nada gravado: mostra o motivo
                self.save_state(rec_id, 'error', error=reason or tr('nothing_recorded'), speed=0)
        else:
            self.save_state(rec_id, 'queued', note=reason, speed=0)
            spawn_worker(rec['dir'])
        del self.open[rec_id]
        return {'ok': True}

    def close_all(self):
        for rec_id in list(self.open):
            self.stop({'id': rec_id, 'reason': tr('rec_conn_closed')})


def main():
    recordings = Recordings()
    try:
        while True:   # sendNativeMessage manda 1 mensagem; connectNative (gravação) manda várias
            msg = read_message()
            if msg is None:
                return
            cmd = msg.get('cmd')
            if msg.get('lang'):
                set_lang(msg['lang'])
            try:
                if cmd == 'rec_chunk':
                    recordings.chunk(msg)
                    continue
                if cmd == 'rec_status':
                    recordings.status(msg)
                    continue
                if cmd == 'rec_start':
                    resp = recordings.start(msg)
                elif cmd == 'rec_stop':
                    resp = recordings.stop(msg)
                else:
                    resp = handle(msg)
            except worker.StreamError as e:
                resp = {'ok': False, 'error': str(e)}
            except Exception as e:  # noqa: BLE001 - qualquer erro volta para a extensão como mensagem
                resp = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
            if 'reqId' in msg:
                resp['reqId'] = msg['reqId']
            send_message(resp)
    finally:
        recordings.close_all()


if __name__ == '__main__':
    main()
