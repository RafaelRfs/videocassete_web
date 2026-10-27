#!/usr/bin/env python3
"""Gera os pacotes de uma versão em dist/:

  videocassete_web-<versão>.zip          o que o usuário baixa: instaladores, host/ e extension/
  videocassete_web-<versão>-firefox.zip  só a extensão, para assinar no addons.mozilla.org (web-ext sign)

Uso: python tools/build_release.py
"""
import json
import stat
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'
FILES = ['install.cmd', 'install.sh', 'install.command', 'install.py', 'README.md', 'README.pt-BR.md',
         'LICENSE', 'CHANGELOG.md']
DIRS = ['host', 'extension']
EXECUTABLE = {'install.sh', 'install.command', 'install.py'}


def walk(folder):
    for p in sorted(folder.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc' \
                and not p.name.startswith(('videocassete_host.', 'videocassete_web.')):
            yield p


def add(z, path, arcname, executable=False):
    info = zipfile.ZipInfo.from_file(path, arcname)
    mode = 0o755 if executable else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(info, path.read_bytes())


def main():
    manifest = json.loads((ROOT / 'extension' / 'manifest.json').read_text(encoding='utf-8'))
    version = manifest['version']
    DIST.mkdir(exist_ok=True)
    prefix = f'videocassete_web-{version}'

    full = DIST / f'{prefix}.zip'
    with zipfile.ZipFile(full, 'w') as z:
        for name in FILES:
            if (ROOT / name).exists():
                add(z, ROOT / name, f'{prefix}/{name}', name in EXECUTABLE)
        for d in DIRS:
            for p in walk(ROOT / d):
                add(z, p, f'{prefix}/{p.relative_to(ROOT).as_posix()}')

    firefox = DIST / f'{prefix}-firefox.zip'
    ff_manifest = {k: v for k, v in manifest.items() if k != 'key'}   # a "key" só serve ao Chrome
    ff_manifest['background'] = {'scripts': manifest['background']['scripts']}
    with zipfile.ZipFile(firefox, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in walk(ROOT / 'extension'):
            rel = p.relative_to(ROOT / 'extension').as_posix()
            if rel == 'manifest.json':
                z.writestr(rel, json.dumps(ff_manifest, indent=2, ensure_ascii=False) + '\n')
            else:
                add(z, p, rel)

    for f in (full, firefox):
        print(f'{f.relative_to(ROOT)}  {f.stat().st_size / 1024:.0f} KB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
