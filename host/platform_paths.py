"""Tudo o que muda entre Windows, macOS e Linux: pastas, busca de ferramentas, abrir pasta, TLS.

O navegador abre o processo local com um PATH mínimo (no mac, um Chrome aberto pelo Dock não
enxerga o Homebrew), então as ferramentas são procuradas nesta ordem:
  1. tools.json gravado pelo instalador (caminhos absolutos)
  2. <dados>/bin (binários oficiais baixados pelo instalador)
  3. PATH
  4. pastas conhecidas de winget, Homebrew, Snap, ~/.local/bin, ~/.deno/bin
"""
import json
import os
import shutil
import signal
import ssl
import subprocess
import sys
from pathlib import Path

APP_NAME = 'VideocasseteWeb'
HOST_NAME = 'videocassete_web'
IS_WIN = os.name == 'nt'
IS_MAC = sys.platform == 'darwin'
NO_WINDOW = 0x08000000 if IS_WIN else 0   # CREATE_NO_WINDOW


def data_dir():
    override = os.environ.get('VIDEOCASSETE_HOME')
    if override:
        return Path(override)
    if IS_WIN:
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / APP_NAME
    if IS_MAC:
        return Path.home() / 'Library' / 'Application Support' / APP_NAME
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / HOST_NAME


DATA_DIR = data_dir()
JOBS_DIR = DATA_DIR / 'jobs'
BIN_DIR = DATA_DIR / 'bin'
TOOLS_FILE = DATA_DIR / 'tools.json'


def default_downloads():
    if not IS_WIN and not IS_MAC:   # Linux: respeita XDG_DOWNLOAD_DIR (pasta traduzida, ex.: ~/Transferências)
        try:
            for line in (Path.home() / '.config' / 'user-dirs.dirs').read_text(encoding='utf-8').splitlines():
                if line.startswith('XDG_DOWNLOAD_DIR='):
                    value = line.split('=', 1)[1].strip().strip('"').replace('$HOME', str(Path.home()))
                    if value:
                        return Path(value)
        except OSError:
            pass
    return Path.home() / 'Downloads'


# ---------------------------------------------------------------- ferramentas externas

WINGET_GLOBS = {
    'ffmpeg': ['*FFmpeg*/*/bin/ffmpeg.exe'],
    'yt-dlp': ['yt-dlp.yt-dlp*/yt-dlp.exe'],
    'deno': ['*Deno*/deno.exe'],
}
INSTALL_HINTS = {
    'win': {'ffmpeg': 'winget install Gyan.FFmpeg', 'yt-dlp': 'winget install yt-dlp.yt-dlp',
            'deno': 'winget install DenoLand.Deno'},
    'mac': {'ffmpeg': 'brew install ffmpeg', 'yt-dlp': 'brew install yt-dlp', 'deno': 'brew install deno'},
    'linux': {'ffmpeg': 'sudo apt install ffmpeg', 'yt-dlp': './install.sh', 'deno': './install.sh'},
}


def exe_name(name):
    return name + '.exe' if IS_WIN else name


def install_hint(tool):
    return INSTALL_HINTS['win' if IS_WIN else 'mac' if IS_MAC else 'linux'][tool]


def load_tools():
    try:
        return json.loads(TOOLS_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def known_dirs():
    home = Path.home()
    if IS_WIN:
        local = Path(os.environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
        return [local / 'Microsoft' / 'WinGet' / 'Links', home / 'scoop' / 'shims',
                Path(os.environ.get('ProgramData') or r'C:\ProgramData') / 'chocolatey' / 'bin',
                home / '.deno' / 'bin']
    return [Path('/opt/homebrew/bin'), Path('/usr/local/bin'), Path('/home/linuxbrew/.linuxbrew/bin'),
            Path('/usr/bin'), Path('/snap/bin'), home / '.local' / 'bin', home / '.deno' / 'bin']


def find_tool(name):
    """Caminho absoluto de ffmpeg, yt-dlp ou deno, ou None."""
    configured = load_tools().get(name)
    if configured and Path(configured).is_file():
        return configured
    exe = exe_name(name)
    if (BIN_DIR / exe).is_file():
        return str(BIN_DIR / exe)
    found = shutil.which(exe)
    if found:
        return found
    for d in known_dirs():
        if (d / exe).is_file():
            return str(d / exe)
    if IS_WIN:
        packages = Path(os.environ.get('LOCALAPPDATA') or '') / 'Microsoft' / 'WinGet' / 'Packages'
        for pattern in WINGET_GLOBS.get(name, []):
            for p in packages.glob(pattern):
                if p.is_file():
                    return str(p)
    return None


# ---------------------------------------------------------------- processos

def background_popen_kwargs():
    """Processo filho num grupo próprio, para poder encerrar ele e os netos (yt-dlp -> ffmpeg)."""
    return {'creationflags': NO_WINDOW} if IS_WIN else {'start_new_session': True}


def kill_tree(proc):
    if IS_WIN:
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, creationflags=NO_WINDOW)
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def open_dir(path):
    path = str(path)
    if IS_WIN:
        os.startfile(path)   # noqa: S606 - abre a pasta no Explorer
    elif IS_MAC:
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


def reveal(path):
    """Abre o gerenciador de arquivos com o arquivo selecionado (no Linux, abre a pasta dele)."""
    if IS_WIN:
        subprocess.Popen(['explorer', '/select,', str(path)])
    elif IS_MAC:
        subprocess.Popen(['open', '-R', str(path)])
    else:
        open_dir(Path(path).parent)


# ---------------------------------------------------------------- TLS

CA_BUNDLES = ('/etc/ssl/cert.pem', '/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt',
              '/etc/ssl/ca-bundle.pem', '/opt/homebrew/etc/openssl@3/cert.pem', '/usr/local/etc/openssl@3/cert.pem')


def _fallback_context():
    """O Python do python.org no mac vem sem certificados raiz (precisa do 'Install Certificates').
    Se o contexto padrão não tiver nenhuma raiz, usa o pacote do sistema. A verificação nunca é desligada.
    No Windows retorna None: o contexto padrão lê o repositório do Windows a cada conexão."""
    if IS_WIN:
        return None
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get('x509_ca'):
        return None
    paths = ssl.get_default_verify_paths()
    if paths.capath and os.path.isdir(paths.capath) and any(Path(paths.capath).glob('*.0')):
        return None   # Linux: raízes num diretório com hash, carregadas sob demanda
    for bundle in CA_BUNDLES:
        if os.path.isfile(bundle):
            ctx.load_verify_locations(bundle)
            return ctx
    return None


SSL_CONTEXT = _fallback_context()
