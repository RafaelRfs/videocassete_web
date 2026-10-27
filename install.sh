#!/bin/sh
# Videocassete Web - instalador para macOS e Linux:  sh install.sh
# Garante um Python 3.9+ e roda install.py (que instala ffmpeg, yt-dlp e Deno se faltarem).
set -e
cd "$(dirname "$0")"

find_python() {
  for p in python3 python3.13 python3.12 python3.11 python3.10 python3.9 \
           /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if command -v "$p" >/dev/null 2>&1 &&
       "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
      command -v "$p"
      return 0
    fi
  done
  return 1
}

if ! PY=$(find_python); then
  echo "Python 3.9+ não encontrado / not found."
  if [ "$(uname)" = "Darwin" ]; then
    if command -v brew >/dev/null 2>&1; then
      brew install python3
    else
      echo "Instale com / install with:  xcode-select --install   (ou / or https://www.python.org/downloads/)"
      xcode-select --install 2>/dev/null || true
      exit 1
    fi
  elif command -v apt-get >/dev/null 2>&1; then sudo apt-get install -y python3
  elif command -v dnf >/dev/null 2>&1; then sudo dnf install -y python3
  elif command -v pacman >/dev/null 2>&1; then sudo pacman -S --noconfirm python
  elif command -v zypper >/dev/null 2>&1; then sudo zypper install -y python3
  fi
  PY=$(find_python) || { echo "Instale o Python 3.9+ e rode de novo / install Python 3.9+ and run again."; exit 1; }
fi

exec "$PY" ./install.py "$@"
