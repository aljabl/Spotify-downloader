#!/usr/bin/env python3
"""Build the downloadable Mac app archive from tracked source files."""

from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

BASE = Path(__file__).resolve().parent
DEST = BASE / "site" / "spotify-downloader-macos.zip"
FILES = ["README.md", "requirements.txt", "server.py", "Start Spotify Downloader.command",
         "ui/index.html", "ui/app.js", "ui/style.css"]

with ZipFile(DEST, "w", ZIP_DEFLATED) as archive:
    for name in FILES:
        source = BASE / name
        info = ZipInfo("Spotify-downloader/" + name)
        info.compress_type = ZIP_DEFLATED
        info.external_attr = (0o755 if name.endswith(".command") else 0o644) << 16
        archive.writestr(info, source.read_bytes())

print(DEST)
