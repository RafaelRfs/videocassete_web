#!/usr/bin/env python3
"""Instalador do Videocassete Web (Windows, macOS e Linux). Só usa a biblioteca padrão.

    python install.py              instala ou atualiza (pergunta antes de instalar dependências)
    python install.py --yes        instala sem perguntar
    python install.py --no-deps    não instala ffmpeg/yt-dlp/deno, só registra o processo local
    python install.py --uninstall  remove o registro nos navegadores e os arquivos do programa

O que faz:
  1. confere ffmpeg, yt-dlp e Deno e instala o que faltar (winget, Homebrew, apt/dnf/pacman/zypper
     ou os binários oficiais do GitHub em <dados>/bin)
  2. copia host/ e extension/ para <dados>/app, uma pasta fixa (o zip baixado pode ser apagado)
  3. gera o launcher do processo local com o caminho absoluto deste Python
  4. registra o processo local no Chrome, Edge, Brave, Opera, Vivaldi, Chromium e Firefox
  5. testa a conexão (ping) e mostra como carregar a extensão
"""
import argparse
import base64
import hashlib
import io
import json
import locale
import os
import platform
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

if sys.version_info < (3, 9):
    sys.exit('Python 3.9+ is required / é preciso o Python 3.9 ou mais novo.')

for _stream in (sys.stdout, sys.stderr):   # console antigo ou saída redirecionada sem UTF-8
    try:
        _stream.reconfigure(errors='replace')
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'host'))
import platform_paths as plat  # noqa: E402

HOST_NAME = plat.HOST_NAME
GECKO_ID = 'videocassete_web@extension'
APP_DIR = plat.DATA_DIR / 'app'
DESCRIPTION = 'Videocassete Web: local helper that downloads HLS/DASH streams and videos'


# ---------------------------------------------------------------- mensagens

def _system_lang():
    for var in ('LC_ALL', 'LC_MESSAGES', 'LANG', 'LANGUAGE'):
        if os.environ.get(var):
            return os.environ[var]
    if plat.IS_WIN:
        try:
            import ctypes
            return locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), '')
        except Exception:  # noqa: BLE001
            pass
    try:
        return locale.getlocale()[0] or ''
    except ValueError:
        return ''


PT = _system_lang().lower().startswith('pt')


def msg(pt, en):
    return pt if PT else en


def say(pt, en=None):
    print(msg(pt, en if en is not None else pt), flush=True)


def ok(text):
    say(f'  ✔ {text}')


def warn(text):
    say(f'  ! {text}')


ASSUME_YES = False


def ask(pt, en):
    if ASSUME_YES or not sys.stdin or not sys.stdin.isatty():
        return True
    answer = input(msg(pt, en) + msg(' [S/n] ', ' [Y/n] ')).strip().lower()
    return answer in ('', 's', 'sim', 'y', 'yes')


def run(cmd, check=False):
    print('    $ ' + ' '.join(shlex.quote(str(c)) for c in cmd), flush=True)
    try:
        return subprocess.run(cmd, check=check).returncode == 0
    except (OSError, subprocess.CalledProcessError) as e:
        warn(str(e))
        return False


# ---------------------------------------------------------------- downloads

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'videocassete-web-installer'})
    with urllib.request.urlopen(req, timeout=120, context=plat.SSL_CONTEXT) as r:
        return r.read()


def save_exe(data, name):
    plat.BIN_DIR.mkdir(parents=True, exist_ok=True)
    path = plat.BIN_DIR / name
    path.write_bytes(data)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def extract_from_zip(data, wanted):
    """Extrai de um zip os arquivos cujo nome final está em wanted, para <dados>/bin."""
    found = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = info.filename.rsplit('/', 1)[-1]
            if name in wanted and not info.is_dir():
                found.append(save_exe(z.read(info), name))
    return found


def machine():
    m = platform.machine().lower()
    return 'aarch64' if m in ('arm64', 'aarch64') else 'x86_64'


GH = 'https://github.com/{repo}/releases/latest/download/{asset}'


def download_ytdlp():
    asset = {'win': 'yt-dlp.exe', 'mac': 'yt-dlp_macos',
             'linux': 'yt-dlp_linux_aarch64' if machine() == 'aarch64' else 'yt-dlp_linux'}[os_key()]
    say(f'    baixando {asset} do GitHub (yt-dlp/yt-dlp)…', f'    downloading {asset} from GitHub (yt-dlp/yt-dlp)…')
    return save_exe(fetch(GH.format(repo='yt-dlp/yt-dlp', asset=asset)), plat.exe_name('yt-dlp'))


def download_deno():
    target = {'win': 'x86_64-pc-windows-msvc', 'mac': f'{machine()}-apple-darwin',
              'linux': f'{machine()}-unknown-linux-gnu'}[os_key()]
    asset = f'deno-{target}.zip'
    say(f'    baixando {asset} do GitHub (denoland/deno)…', f'    downloading {asset} from GitHub (denoland/deno)…')
    return extract_from_zip(fetch(GH.format(repo='denoland/deno', asset=asset)), {plat.exe_name('deno')})


def download_ffmpeg():
    if plat.IS_WIN:
        url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
        say('    baixando o ffmpeg de gyan.dev (~100 MB)…', '    downloading ffmpeg from gyan.dev (~100 MB)…')
        return extract_from_zip(fetch(url), {'ffmpeg.exe', 'ffprobe.exe'})
    if plat.IS_MAC:
        say('    baixando o ffmpeg de evermeet.cx…', '    downloading ffmpeg from evermeet.cx…')
        files = []
        for tool in ('ffmpeg', 'ffprobe'):
            files += extract_from_zip(fetch(f'https://evermeet.cx/ffmpeg/getrelease/{tool}/zip'), {tool})
        return files
    return []


def os_key():
    return 'win' if plat.IS_WIN else 'mac' if plat.IS_MAC else 'linux'


# ---------------------------------------------------------------- dependências

TOOLS = {   # ferramenta: (winget id, fórmula brew, pacote linux, para quê)
    'ffmpeg': ('Gyan.FFmpeg', 'ffmpeg', 'ffmpeg',
               ('junta vídeo e áudio em MP4, decripta HLS AES-128, MP3 e gravações',
                'merges video and audio into MP4, decrypts HLS AES-128, MP3 and recordings')),
    'yt-dlp': ('yt-dlp.yt-dlp', 'yt-dlp', None,
               ('YouTube, Instagram, Facebook, TikTok, X, Vimeo', 'YouTube, Instagram, Facebook, TikTok, X, Vimeo')),
    'deno': ('DenoLand.Deno', 'deno', None,
             ('o yt-dlp precisa dele para o YouTube', 'yt-dlp needs it for YouTube')),
}


def linux_package_manager():
    for pm, cmd in (('apt-get', ['sudo', 'apt-get', 'install', '-y']), ('dnf', ['sudo', 'dnf', 'install', '-y']),
                    ('pacman', ['sudo', 'pacman', '-S', '--noconfirm']), ('zypper', ['sudo', 'zypper', 'install', '-y'])):
        if shutil.which(pm):
            return cmd
    return None


def install_tool(name):
    winget_id, brew, linux_pkg, _ = TOOLS[name]
    try:
        if plat.IS_WIN:
            if shutil.which('winget'):
                run(['winget', 'install', '-e', '--id', winget_id, '--accept-source-agreements',
                     '--accept-package-agreements', '--disable-interactivity'])
                if plat.find_tool(name):
                    return
            {'ffmpeg': download_ffmpeg, 'yt-dlp': download_ytdlp, 'deno': download_deno}[name]()
        elif plat.IS_MAC:
            brew_exe = shutil.which('brew') or next((p for p in ('/opt/homebrew/bin/brew', '/usr/local/bin/brew')
                                                     if os.path.exists(p)), None)
            if brew_exe:
                run([brew_exe, 'install', brew])
                if plat.find_tool(name):
                    return
            {'ffmpeg': download_ffmpeg, 'yt-dlp': download_ytdlp, 'deno': download_deno}[name]()
        else:
            if name == 'ffmpeg':
                pm = linux_package_manager()
                if pm:
                    run(pm + [linux_pkg])
                else:
                    warn(msg('instale o ffmpeg pelo gerenciador de pacotes da sua distribuição',
                             'install ffmpeg with your distribution package manager'))
            else:   # o yt-dlp das distribuições costuma estar velho demais para o YouTube
                {'yt-dlp': download_ytdlp, 'deno': download_deno}[name]()
    except Exception as e:  # noqa: BLE001 - falha de rede/instalação não pode derrubar o instalador
        warn(msg(f'não consegui instalar {name}: {e}', f'could not install {name}: {e}'))


def check_dependencies(install):
    say('\n[1/5] Dependências', '\n[1/5] Dependencies')
    ok(f'Python {platform.python_version()} ({sys.executable})')
    found = {}
    for name, (*_, purpose) in TOOLS.items():
        path = plat.find_tool(name)
        if not path and install and ask(f'  {name} não encontrado ({purpose[0]}). Instalar agora?',
                                        f'  {name} not found ({purpose[1]}). Install it now?'):
            install_tool(name)
            path = plat.find_tool(name)
        if path:
            ok(f'{name}: {path}')
        else:
            warn(msg(f'{name} ausente: {purpose[0]}. Instale com: {plat.install_hint(name)}',
                     f'{name} missing: {purpose[1]}. Install with: {plat.install_hint(name)}'))
        found[name] = path
    if found.get('yt-dlp') and plat.BIN_DIR in Path(found['yt-dlp']).parents:
        run([found['yt-dlp'], '-U'])   # binário próprio: atualiza (o YouTube muda com frequência)
    return found


# ---------------------------------------------------------------- cópia e launcher

def copy_app():
    say('\n[2/5] Copiando o programa', '\n[2/5] Copying the program')
    if APP_DIR.resolve() == HERE.resolve():
        ok(str(APP_DIR))
        return
    APP_DIR.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', 'videocassete_host.*', f'{HOST_NAME}*.json')
    for part in ('host', 'extension'):
        dest = APP_DIR / part
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(HERE / part, dest, ignore=ignore)
    for doc in ('README.md', 'README.pt-BR.md', 'LICENSE'):
        if (HERE / doc).exists():
            shutil.copy2(HERE / doc, APP_DIR / doc)
    ok(str(APP_DIR))


def bat_text(python, script):
    body = (f'@echo off\r\nset "VIDEOCASSETE_HOME={plat.DATA_DIR}"\r\n'
            f'"{python}" -u "{script}" %*\r\n')
    try:   # o cmd lê o .bat na página de código OEM (cp850 no Brasil): acentos no caminho precisam dela
        import ctypes
        oem = f'cp{ctypes.windll.kernel32.GetOEMCP()}'
        return body.encode(oem)
    except (UnicodeEncodeError, LookupError, AttributeError, OSError):
        return ('@echo off\r\nchcp 65001 >nul\r\n' + body.split('\r\n', 1)[1]).encode('utf-8')


def write_launcher():
    say('\n[3/5] Launcher do processo local', '\n[3/5] Local helper launcher')
    python = Path(sys.executable)
    if plat.IS_WIN and 'WindowsApps' in str(python):
        sys.exit(msg('O Python da Microsoft Store não funciona com extensões (ele isola a pasta de dados). '
                     'Instale o do python.org: winget install Python.Python.3.13 e rode o instalador de novo.',
                     'Microsoft Store Python does not work with browser extensions (it sandboxes the data folder). '
                     'Install the python.org one: winget install Python.Python.3.13 and run the installer again.'))
    script = APP_DIR / 'host' / 'stream_host.py'
    if plat.IS_WIN:
        launcher = APP_DIR / 'host' / 'videocassete_host.bat'
        launcher.write_bytes(bat_text(python, script))
    else:
        launcher = APP_DIR / 'host' / 'videocassete_host.sh'
        launcher.write_text('#!/bin/sh\n'
                            f'VIDEOCASSETE_HOME={shlex.quote(str(plat.DATA_DIR))}\nexport VIDEOCASSETE_HOME\n'
                            f'exec {shlex.quote(str(python))} -u {shlex.quote(str(script))} "$@"\n', encoding='utf-8')
        launcher.chmod(0o755)
    ok(str(launcher))
    return launcher


def write_tools_file(found):
    plat.DATA_DIR.mkdir(parents=True, exist_ok=True)
    plat.TOOLS_FILE.write_text(json.dumps({k: v for k, v in found.items() if v}, indent=1), encoding='utf-8')


# ---------------------------------------------------------------- registro nos navegadores

def extension_id():
    """ID que o Chrome calcula da 'key' do manifest.json: sha256 da chave pública, letras a-p."""
    manifest = json.loads((HERE / 'extension' / 'manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    return ''.join(chr(ord('a') + int(c, 16)) for c in digest)


CHROMIUM_WIN = {
    'Chrome / Opera': r'Software\Google\Chrome\NativeMessagingHosts',
    'Chromium': r'Software\Chromium\NativeMessagingHosts',
    'Edge': r'Software\Microsoft\Edge\NativeMessagingHosts',
    'Brave': r'Software\BraveSoftware\Brave-Browser\NativeMessagingHosts',
    'Vivaldi': r'Software\Vivaldi\NativeMessagingHosts',
}
FIREFOX_WIN = r'Software\Mozilla\NativeMessagingHosts'


def posix_targets():
    """(nome, pasta NativeMessagingHosts, é Firefox, sempre criar)."""
    home = Path.home()
    if plat.IS_MAC:
        base = home / 'Library' / 'Application Support'
        return [('Chrome', base / 'Google/Chrome', False, True),
                ('Chrome Beta', base / 'Google/Chrome Beta', False, False),
                ('Chromium', base / 'Chromium', False, False),
                ('Edge', base / 'Microsoft Edge', False, False),
                ('Brave', base / 'BraveSoftware/Brave-Browser', False, False),
                ('Vivaldi', base / 'Vivaldi', False, False),
                ('Opera', base / 'com.operasoftware.Opera', False, False),
                ('Firefox', base / 'Mozilla', True, True)]
    cfg = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    return [('Chrome', cfg / 'google-chrome', False, True),
            ('Chrome Beta', cfg / 'google-chrome-beta', False, False),
            ('Chromium', cfg / 'chromium', False, False),
            ('Chromium (snap)', home / 'snap/chromium/common/chromium', False, False),
            ('Edge', cfg / 'microsoft-edge', False, False),
            ('Brave', cfg / 'BraveSoftware/Brave-Browser', False, False),
            ('Vivaldi', cfg / 'vivaldi', False, False),
            ('Opera', cfg / 'opera', False, False),
            ('Firefox', home / '.mozilla', True, True)]


def manifests(launcher):
    common = {'name': HOST_NAME, 'description': DESCRIPTION, 'path': str(launcher), 'type': 'stdio'}
    return ({**common, 'allowed_origins': [f'chrome-extension://{extension_id()}/']},
            {**common, 'allowed_extensions': [GECKO_ID]})


def register(launcher):
    say('\n[4/5] Registrando nos navegadores', '\n[4/5] Registering with the browsers')
    chromium, firefox = manifests(launcher)
    if plat.IS_WIN:
        import winreg
        files = {}
        for kind, data in (('chromium', chromium), ('firefox', firefox)):
            path = APP_DIR / 'host' / f'{HOST_NAME}.{kind}.json'
            path.write_text(json.dumps(data, indent=2), encoding='utf-8')
            files[kind] = path
        for name, key in [*CHROMIUM_WIN.items(), ('Firefox', FIREFOX_WIN)]:
            target = files['firefox' if name == 'Firefox' else 'chromium']
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf'{key}\{HOST_NAME}') as k:
                winreg.SetValue(k, '', winreg.REG_SZ, str(target))
            ok(f'{name}: HKCU\\{key}\\{HOST_NAME}')
        return
    for name, base, is_firefox, always in posix_targets():
        if not always and not base.exists():
            continue
        folder = base / ('native-messaging-hosts' if is_firefox and not plat.IS_MAC else 'NativeMessagingHosts')
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{HOST_NAME}.json').write_text(json.dumps(firefox if is_firefox else chromium, indent=2),
                                                  encoding='utf-8')
        ok(f'{name}: {folder / (HOST_NAME + ".json")}')


def unregister():
    if plat.IS_WIN:
        import winreg
        for name, key in [*CHROMIUM_WIN.items(), ('Firefox', FIREFOX_WIN)]:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf'{key}\{HOST_NAME}')
                ok(msg(f'{name}: removido', f'{name}: removed'))
            except FileNotFoundError:
                pass
        return
    for name, base, is_firefox, _ in posix_targets():
        folder = base / ('native-messaging-hosts' if is_firefox and not plat.IS_MAC else 'NativeMessagingHosts')
        f = folder / f'{HOST_NAME}.json'
        if f.exists():
            f.unlink()
            ok(msg(f'{name}: removido', f'{name}: removed'))


# ---------------------------------------------------------------- autoteste

def self_test(launcher):
    say('\n[5/5] Testando o processo local', '\n[5/5] Testing the local helper')
    body = json.dumps({'cmd': 'ping', 'lang': 'pt' if PT else 'en'}).encode('utf-8')
    try:
        r = subprocess.run([str(launcher)], input=struct.pack('<I', len(body)) + body, capture_output=True,
                           timeout=60, creationflags=plat.NO_WINDOW)
        size = struct.unpack('<I', r.stdout[:4])[0]
        info = json.loads(r.stdout[4:4 + size].decode('utf-8'))
    except Exception as e:  # noqa: BLE001
        warn(msg(f'o processo local não respondeu: {e}', f'the local helper did not respond: {e}'))
        if 'r' in locals() and r.stderr:
            print(r.stderr.decode('utf-8', 'replace')[-800:])
        return False
    ok(msg(f'respondeu: versão {info.get("version")}, Python {info.get("python")}',
           f'responded: version {info.get("version")}, Python {info.get("python")}'))
    return True


def next_steps():
    ext = APP_DIR / 'extension'
    say('\nPronto! Falta só carregar a extensão no navegador (uma vez):', '\nDone! Now load the extension in your browser (once):')
    say(f'\n  Pasta da extensão: {ext}', f'\n  Extension folder: {ext}')
    say('\n  Chrome / Edge / Brave / Opera / Vivaldi:\n'
        '    1. abra chrome://extensions (edge://extensions, brave://extensions, opera://extensions)\n'
        '    2. ligue o "Modo do desenvolvedor"\n'
        '    3. clique em "Carregar sem compactação" e escolha a pasta acima\n'
        '\n  Firefox:\n'
        '    instale o arquivo .xpi da página de Releases do projeto; ou, para testar,\n'
        '    abra about:debugging#/runtime/this-firefox → "Carregar extensão temporária…" → manifest.json da pasta acima\n'
        '\n  Já estava carregada? Clique em recarregar (↻) no card da extensão.',
        '\n  Chrome / Edge / Brave / Opera / Vivaldi:\n'
        '    1. open chrome://extensions (edge://extensions, brave://extensions, opera://extensions)\n'
        '    2. turn on "Developer mode"\n'
        '    3. click "Load unpacked" and pick the folder above\n'
        '\n  Firefox:\n'
        '    install the .xpi file from the project Releases page; or, to try it out,\n'
        '    open about:debugging#/runtime/this-firefox → "Load Temporary Add-on…" → manifest.json in the folder above\n'
        '\n  Already loaded? Click reload (↻) on the extension card.')
    try:
        plat.open_dir(ext)
    except Exception:  # noqa: BLE001 - sem interface gráfica (servidor, CI)
        pass


# ---------------------------------------------------------------- principal

def main():
    global ASSUME_YES
    ap = argparse.ArgumentParser(description='Videocassete Web installer')
    ap.add_argument('--yes', '-y', action='store_true', help='install dependencies without asking')
    ap.add_argument('--no-deps', action='store_true', help='do not install ffmpeg / yt-dlp / deno')
    ap.add_argument('--no-open', action='store_true', help='do not open the extension folder at the end')
    ap.add_argument('--uninstall', action='store_true')
    args = ap.parse_args()
    ASSUME_YES = args.yes
    print('Videocassete Web — ' + msg('instalador', 'installer') + f'  ({platform.system()} {platform.machine()})')
    print(msg('Pasta de dados: ', 'Data folder: ') + str(plat.DATA_DIR))

    if args.uninstall:
        unregister()
        for p in (APP_DIR, plat.BIN_DIR):
            shutil.rmtree(p, ignore_errors=True)
        plat.TOOLS_FILE.unlink(missing_ok=True)
        say(f'Removido. Downloads em andamento continuam em {plat.JOBS_DIR} (pode apagar).',
            f'Removed. Unfinished downloads remain in {plat.JOBS_DIR} (safe to delete).')
        return 0

    found = check_dependencies(install=not args.no_deps)
    copy_app()
    launcher = write_launcher()
    write_tools_file(found)
    register(launcher)
    healthy = self_test(launcher)
    if not args.no_open:
        next_steps()
    else:
        print(str(APP_DIR / 'extension'))
    return 0 if healthy else 1


if __name__ == '__main__':
    sys.exit(main())
