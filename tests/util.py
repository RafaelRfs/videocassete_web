"""Apoio dos testes: pasta de dados temporária, servidor HTTP local e mídia gerada com ffmpeg."""
import functools
import http.server
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST_DIR = ROOT / 'host'

# precisa vir antes de importar os módulos do host: a pasta de dados é lida na importação
TMP_HOME = Path(tempfile.mkdtemp(prefix='vcw-test-'))
os.environ['VIDEOCASSETE_HOME'] = str(TMP_HOME)
sys.path.insert(0, str(HOST_DIR))

import platform_paths as plat  # noqa: E402

FFMPEG = plat.find_tool('ffmpeg')


def native_call(messages, launcher=None, timeout=60):
    """Manda mensagens pelo protocolo do Native Messaging e devolve as respostas."""
    cmd = [str(launcher)] if launcher else [sys.executable, '-u', str(HOST_DIR / 'stream_host.py')]
    data = b''
    for m in messages:
        body = json.dumps(m).encode('utf-8')
        data += struct.pack('<I', len(body)) + body
    r = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout, env=dict(os.environ),
                       creationflags=plat.NO_WINDOW)
    out, replies = r.stdout, []
    while len(out) >= 4:
        size = struct.unpack('<I', out[:4])[0]
        replies.append(json.loads(out[4:4 + size].decode('utf-8')))
        out = out[4 + size:]
    if not replies:
        raise AssertionError('sem resposta do host: ' + r.stderr.decode('utf-8', 'replace'))
    return replies


class Server:
    """http.server numa thread, servindo uma pasta; conta os pedidos e pode falhar de propósito."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.requests = []
        self.fail = set()   # nomes de arquivo que respondem 404
        server = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                name = self.path.split('?')[0].rsplit('/', 1)[-1]
                server.requests.append((name, dict(self.headers)))
                if name in server.fail:
                    self.send_error(404)
                    return
                super().do_GET()

        handler = functools.partial(Handler, directory=str(self.folder))
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, name):
        return f'http://127.0.0.1:{self.port}/{name}'

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def ffmpeg(*args, cwd=None):
    subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', *args], check=True, cwd=cwd,
                   creationflags=plat.NO_WINDOW)


def make_source(folder, seconds=6):
    """Vídeo de teste H.264 + AAC."""
    src = Path(folder) / 'src.mp4'
    ffmpeg('-f', 'lavfi', '-i', f'testsrc=duration={seconds}:size=320x240:rate=25',
           '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}',
           '-c:v', 'libx264', '-g', '25', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(src))
    return src


def make_hls(folder, src, aes=False):
    folder = Path(folder)
    args = ['-i', str(src), '-c', 'copy', '-f', 'hls', '-hls_time', '1', '-hls_playlist_type', 'vod',
            '-hls_segment_filename', str(folder / 'seg%03d.ts')]
    if aes:
        (folder / 'enc.key').write_bytes(bytes(range(16)))
        (folder / 'keyinfo.txt').write_text('enc.key\n' + str(folder / 'enc.key') + '\n', encoding='utf-8')
        args += ['-hls_key_info_file', str(folder / 'keyinfo.txt')]
    ffmpeg(*args, str(folder / 'index.m3u8'))
    return 'index.m3u8'


def make_dash(folder, src):
    folder = Path(folder)
    ffmpeg('-i', str(src), '-c', 'copy', '-map', '0:v', '-map', '0:a', '-f', 'dash', '-seg_duration', '1',
           '-use_template', '1', '-use_timeline', '1', 'manifest.mpd', cwd=folder)   # os pedaços saem na pasta atual
    return 'manifest.mpd'


def probe_duration(path):
    ffprobe = Path(FFMPEG).with_name('ffprobe' + ('.exe' if os.name == 'nt' else ''))
    if not ffprobe.exists():
        ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return None
    r = subprocess.run([str(ffprobe), '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                       capture_output=True, text=True, creationflags=plat.NO_WINDOW)
    return float(r.stdout.strip() or 0)
