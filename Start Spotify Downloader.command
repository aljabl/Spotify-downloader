#!/bin/zsh
set -e
cd "$(dirname "$0")"

for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done

if [[ -z "${PYTHON:-}" ]]; then
  echo "Python 3.10 or newer is required. Install it from python.org, then run this file again."
  read -k 1 '?Press any key to close...'
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "FFmpeg is required for MP3 and WAV conversion. Install it, then run this file again."
  echo "On macOS with Homebrew: brew install ffmpeg"
  read -k 1 '?Press any key to close...'
  exit 1
fi

if ! command -v deno >/dev/null 2>&1 && ! command -v node >/dev/null 2>&1; then
  echo "YouTube extraction needs Deno 2.3+ or Node.js 22+. Install one, then run this file again."
  echo "On macOS with Homebrew: brew install deno"
  read -k 1 '?Press any key to close...'
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON" -m venv .venv
fi
if ! .venv/bin/python -c 'from importlib.metadata import version; import yt_dlp_ejs; assert version("yt-dlp") == "2026.11.23"' >/dev/null 2>&1; then
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
fi

echo "Spotify Downloader is running at http://127.0.0.1:8765"
echo "Keep this window open while downloading. Press Control-C to stop."
.venv/bin/python server.py
