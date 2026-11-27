#!/usr/bin/env python3
"""Local-only Spotify playlist to YouTube audio downloader."""

import base64
import hashlib
import json
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import defaultdict, deque
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


CLIENT_ID = "efe2b3ce91da4424afa63603e89d74c0"
HOST, PORT = "127.0.0.1", 8765
BASE = Path(__file__).resolve().parent
OUTPUT = BASE / "Spotify"
STATE_FILE = OUTPUT / ".state.json"
REDIRECT_URI = f"http://{HOST}:{PORT}/callback"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9]{22}$")
YOUTUBE_BROWSERS = {"", "safari", "chrome", "firefox", "brave", "edge", "chromium", "opera", "vivaldi"}


def youtube_options(options):
    """Use the selected local browser session for every YouTube request."""
    with APP.lock:
        browser = APP.youtube_browser
    if browser:
        return {**options, "cookiesfrombrowser": (browser,)}
    return options


def download_error(error):
    detail = re.sub(r"\x1b\[[0-9;]*m", "", str(error))
    if "Sign in to confirm you’re not a bot" in detail or "Sign in to confirm you're not a bot" in detail:
        return ("YouTube asked for a signed-in session. Sign in to YouTube in your browser, "
                "choose that browser under YouTube access, save it, and retry. "
                "If a browser is already selected, try another signed-in browser.")
    return detail[:300]


def playlist_id(value):
    value = value.strip()
    if value.startswith("spotify:playlist:"):
        candidate = value.split(":")[-1]
    else:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme != "https" or parsed.hostname != "open.spotify.com":
            raise ValueError("Paste a Spotify playlist link from open.spotify.com.")
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "playlist":
            raise ValueError("Paste a Spotify playlist link, not an album or track link.")
        candidate = parts[1]
    if not PLAYLIST_ID.fullmatch(candidate):
        raise ValueError("The Spotify playlist ID is invalid.")
    return candidate


def video_id(value):
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme != "https":
        raise ValueError("Use an HTTPS YouTube video link.")
    if parsed.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com") and parsed.path == "/watch":
        candidate = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
    elif parsed.hostname == "youtu.be":
        candidate = parsed.path.strip("/")
    else:
        raise ValueError("Use a youtube.com/watch or youtu.be video link.")
    if not VIDEO_ID.fullmatch(candidate):
        raise ValueError("The YouTube video ID is invalid.")
    return candidate


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    value = value[:80]
    while len(value.encode("utf-8")) > 100:
        value = value[:-1]
    return value or "Untitled"


def filename(track, extension):
    title = safe_name(track["title"])
    artist = safe_name(track["artist"])
    return f"{title} ({artist}).{'m4a' if extension == 'alac' else extension}"


def already_downloaded(track, extension):
    path = OUTPUT / filename(track, extension)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def spotify_metadata(item):
    album = item.get("album") or {}
    artists = item.get("artists") or []
    return {"album": album.get("name") or None,
            "release_date": album.get("release_date") or None,
            "track_number": item.get("track_number"),
            "artist_id": artists[0].get("id") if artists else None}


def write_audio_metadata(path, track):
    temp = path.with_name(f".{path.stem}.{secrets.token_hex(6)}{path.suffix}")
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(path), "-map", "0:a:0", "-c:a", "copy", "-map_metadata", "-1"]
    command.extend(metadata_options(track))
    command.extend(["-f", "ipod" if path.suffix == ".m4a" else "wav", str(temp)])
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode or not temp.exists() or not temp.stat().st_size:
            raise ValueError("Could not write audio metadata: " + (result.stderr.strip()[-250:] or "FFmpeg failed."))
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def write_wav_metadata(path, track):
    write_audio_metadata(path, track)


def write_alac_metadata(path, track):
    write_audio_metadata(path, track)


def metadata_options(track):
    tags = {"title": track.get("title"), "artist": track.get("artist"),
            "album": track.get("album"), "date": track.get("release_date"),
            "genre": track.get("genre"), "track": track.get("track_number")}
    options = []
    for key, value in tags.items():
        if value is not None and value != "":
            options.extend(["-metadata", f"{key}={value}"])
    return options


def convert_to_alac(source, final, track):
    temp = final.with_name(f".{final.stem}.{secrets.token_hex(6)}.m4a")
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(source), "-map", "0:a:0", "-c:a", "alac", "-map_metadata", "-1"]
    command.extend(metadata_options(track))
    command.extend(["-f", "ipod", str(temp)])
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode or not temp.exists() or not temp.stat().st_size:
            raise ValueError("Could not create ALAC audio: " + (result.stderr.strip()[-250:] or "FFmpeg failed."))
        temp.replace(final)
    finally:
        temp.unlink(missing_ok=True)


def audio_codec(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                             "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", str(path)],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def match_score(track, result):
    title = normalize(track["title"])
    artist = normalize(track["artist"])
    candidate = normalize(result.get("title", ""))
    score = SequenceMatcher(None, f"{artist} {title}", candidate).ratio()
    if title and title in candidate:
        score += 0.18
    if artist and artist in candidate:
        score += 0.14
    if re.search(r"\bofficial\b", candidate):
        score += 0.22
    duration = result.get("duration")
    if duration and track.get("duration_ms"):
        delta = abs(duration * 1000 - track["duration_ms"])
        score += 0.12 if delta < 12000 else (-0.15 if delta > 60000 else 0)
    if "live" in candidate and "live" not in title:
        score -= 0.15
    if "cover" in candidate and "cover" not in title:
        score -= 0.25
    return round(score, 3)


class App:
    def __init__(self):
        self.lock = threading.RLock()
        self.csrf = secrets.token_urlsafe(24)
        self.oauth = None
        self.token = None
        self.playlist_title = ""
        self.playlist_id = None
        self.youtube_browser = ""
        self.tracks = []
        self.revision = 0
        self.pause_requested = False
        self.job = {"id": None, "kind": None, "running": False, "paused": False, "pausing": False,
                    "current": "", "done": 0, "total": 0, "errors": [], "last_result": None,
                    "pause_reason": ""}
        self.one_off = {"status": "idle", "filename": "", "error": ""}
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self.playlist_title = data.get("playlist_title", "")
                self.playlist_id = data.get("playlist_id")
                self.tracks = data.get("tracks", [])
                browser = data.get("youtube_browser", "")
                self.youtube_browser = browser if type(browser) is str and browser in YOUTUBE_BROWSERS else ""
            except (OSError, ValueError):
                pass

    def rescore_saved_matches(self):
        """Apply current ranking to saved auto picks without changing manual choices."""
        with self.lock:
            changed = False
            for track in self.tracks:
                if track.get("ignored"):
                    continue
                candidates = track.get("candidates") or []
                old_top_id = candidates[0].get("id") if candidates else None
                source = track.get("selection_source")
                if source is None and track.get("selected"):
                    source = "auto" if track["selected"] == old_top_id and track.get("status") == "matched" else "manual"
                    track["selection_source"] = source
                    changed = True
                for candidate in candidates:
                    score = match_score(track, candidate)
                    if candidate.get("score") != score:
                        candidate["score"] = score
                        changed = True
                reordered = sorted(candidates, key=lambda item: item["score"], reverse=True)
                if candidates != reordered:
                    track["candidates"] = reordered
                    changed = True
                if source == "auto" and track.get("status") == "matched" and reordered:
                    best = reordered[0]
                    if best["score"] >= 0.62 and track.get("selected") != best["id"]:
                        track["selected"] = best["id"]
                        changed = True
                elif not track.get("selected") and track.get("status") == "needs_review" and reordered:
                    best = reordered[0]
                    if best["score"] >= 0.62:
                        track["selected"] = best["id"]
                        track["selection_source"] = "auto"
                        track["status"] = "matched"
                        changed = True
            if changed:
                self.save()

    def save(self):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        temp = STATE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps({"playlist_title": self.playlist_title, "playlist_id": self.playlist_id,
                                    "tracks": self.tracks, "youtube_browser": self.youtube_browser},
                                   ensure_ascii=False), encoding="utf-8")
        temp.replace(STATE_FILE)
        self.revision += 1

    def snapshot(self):
        with self.lock:
            return {"connected": bool(self.token), "playlist_title": self.playlist_title,
                    "tracks": self.tracks, "job": self.job, "revision": self.revision,
                    "one_off": self.one_off,
                    "youtube_browser": self.youtube_browser,
                    "output_dir": str(OUTPUT)}

    def set_youtube_browser(self, browser):
        if type(browser) is not str or browser not in YOUTUBE_BROWSERS:
            raise ValueError("Choose a supported browser.")
        with self.lock:
            if self.job["running"] or self.one_off["status"] in ("working", "downloading"):
                raise ValueError("Wait for the current YouTube task to finish before changing browsers.")
            self.youtube_browser = browser
            self.save()

    def spotify_token(self):
        with self.lock:
            token = self.token
        if not token:
            raise ValueError("Connect your Spotify account first.")
        if time.time() < token["expires_at"] - 60:
            return token["access_token"]
        refresh = token.get("refresh_token")
        if not refresh:
            raise ValueError("Spotify access expired. Connect again.")
        new = post_form("https://accounts.spotify.com/api/token", {
            "grant_type": "refresh_token", "refresh_token": refresh, "client_id": CLIENT_ID})
        new["refresh_token"] = new.get("refresh_token", refresh)
        new["expires_at"] = time.time() + new["expires_in"]
        with self.lock:
            self.token = new
        return new["access_token"]

    def spotify_get(self, path):
        url = "https://api.spotify.com/v1/" + path
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.spotify_token()})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 403:
                raise ValueError("Spotify permits this app to read only playlists you own or collaborate on.") from error
            if error.code == 429:
                raise ValueError("Spotify rate limit reached. Try again later.") from error
            raise ValueError(f"Spotify returned HTTP {error.code}.") from error

    def load_playlist(self, link):
        with self.lock:
            if self.job["running"]:
                raise ValueError("Wait for the current job to finish before loading another playlist.")
        pid = playlist_id(link)
        details = self.spotify_get(f"playlists/{pid}?fields=name")
        previous = defaultdict(deque)
        with self.lock:
            for track in self.tracks:
                if track.get("spotify_id"):
                    previous[track["spotify_id"]].append(track)
        tracks = []
        offset = 0
        while True:
            page = self.spotify_get(f"playlists/{pid}/items?limit=50&offset={offset}")
            for entry in page.get("items", []):
                item = entry.get("track") or entry.get("item") or {}
                if item.get("type") != "track" or not item.get("name"):
                    continue
                artists = item.get("artists") or []
                artist = ", ".join(a.get("name", "") for a in artists if a.get("name")) or "Unknown artist"
                track = {"title": item["name"], "artist": artist,
                         "duration_ms": item.get("duration_ms"), "spotify_id": item.get("id"),
                         **spotify_metadata(item), "genre": None,
                         "candidates": [], "selected": None, "selection_source": None,
                         "status": "pending", "error": "", "ignored": False}
                if item.get("id") and previous[item["id"]]:
                    saved = previous[item["id"]].popleft()
                    for key in ("candidates", "selected", "selection_source", "status", "error", "genre", "ignored"):
                        track[key] = saved.get(key, track[key])
                tracks.append(track)
            offset += len(page.get("items", []))
            if offset >= page.get("total", 0) or not page.get("items"):
                break
        with self.lock:
            self.playlist_title = details.get("name") or "Spotify playlist"
            self.playlist_id = pid
            self.tracks = tracks
            self.save()
        self.start_auto_match()
        return len(tracks)

    def start_auto_match(self):
        with self.lock:
            if self.job["running"]:
                return 0
            target = [i for i, track in enumerate(self.tracks)
                      if track["status"] == "pending" and not track.get("selected") and not track.get("ignored")]
        if target:
            self.begin_job("match", target)
        return len(target)

    def begin_job(self, kind, target):
        with self.lock:
            if self.job["running"]:
                raise ValueError("Wait for the current job to finish.")
            self.pause_requested = False
            self.job = {"id": secrets.token_hex(6), "kind": kind, "running": bool(target),
                        "paused": False, "pausing": False, "current": "", "done": 0,
                        "total": len(target), "errors": [], "last_result": None,
                        "pause_reason": ""}
        if not target:
            return
        threading.Thread(target=self.run_job, args=(kind, target), daemon=True).start()

    def pause_match(self):
        with self.lock:
            if not self.job["running"] or self.job["kind"] != "match":
                raise ValueError("No YouTube search is running.")
            self.pause_requested = True
            self.job["pausing"] = True

    def start_one_off(self, url, extension, title="", artist=""):
        if type(url) is not str:
            raise ValueError("Paste a YouTube video link.")
        selected = video_id(url)
        if extension not in ("mp3", "wav", "alac"):
            raise ValueError("Choose MP3, WAV, or ALAC.")
        if any(type(value) is not str or len(value) > 200 for value in (title, artist)):
            raise ValueError("Title and artist must be text under 200 characters.")
        with self.lock:
            if self.one_off["status"] in ("working", "downloading"):
                raise ValueError("Wait for the current one-off download to finish.")
            self.one_off = {"status": "working", "filename": "", "error": ""}
            self.revision += 1
        threading.Thread(target=self.run_one_off, args=(selected, extension, title.strip(), artist.strip()), daemon=True).start()

    def run_one_off(self, selected, extension, title, artist):
        try:
            track = youtube_video_track(selected, title, artist)
            path = OUTPUT / filename(track, extension)
            existed = path.is_file() and path.stat().st_size > 0
            with self.lock:
                self.one_off = {"status": "downloading", "filename": path.name, "error": ""}
                self.revision += 1
            download_track(track, extension)
            with self.lock:
                self.one_off = {"status": "existing" if existed else "done", "filename": path.name, "error": ""}
                self.revision += 1
        except Exception as error:
            detail = download_error(error)
            with self.lock:
                self.one_off = {"status": "error", "filename": "", "error": detail}
                self.revision += 1

    def set_ignored(self, index, ignored):
        if type(index) is not int or type(ignored) is not bool:
            raise ValueError("Choose a valid song and Ignore or Restore action.")
        with self.lock:
            if index < 0 or index >= len(self.tracks):
                raise ValueError("Track index is invalid.")
            self.tracks[index]["ignored"] = ignored
            self.save()

    def enrich_metadata(self, target):
        """Fill older saved tracks and optional artist genres before writing WAVs."""
        if not self.token:
            return
        target = [i for i in target if not self.tracks[i].get("ignored")]
        missing = [(i, self.tracks[i].get("spotify_id")) for i in target
                   if self.tracks[i].get("spotify_id") and
                   (not self.tracks[i].get("album") or not self.tracks[i].get("artist_id"))]
        for start in range(0, len(missing), 50):
            batch = missing[start:start + 50]
            try:
                ids = ",".join(track_id for _, track_id in batch)
                response = self.spotify_get("tracks?" + urllib.parse.urlencode({"ids": ids}))
            except (ValueError, urllib.error.URLError, TimeoutError):
                break  # The deprecated catalog endpoint may not be available to this app.
            by_id = {item["id"]: item for item in response.get("tracks", []) if item and item.get("id")}
            with self.lock:
                changed = False
                for index, track_id in batch:
                    item = by_id.get(track_id)
                    if not item or self.tracks[index].get("spotify_id") != track_id:
                        continue
                    for key, value in spotify_metadata(item).items():
                        if value and not self.tracks[index].get(key):
                            self.tracks[index][key] = value
                            changed = True
                if changed:
                    self.save()
        artist_ids = list(dict.fromkeys(self.tracks[i].get("artist_id") for i in target
                                        if self.tracks[i].get("artist_id") and not self.tracks[i].get("genre")))
        for start in range(0, len(artist_ids), 50):
            batch = artist_ids[start:start + 50]
            try:
                response = self.spotify_get("artists?" + urllib.parse.urlencode({"ids": ",".join(batch)}))
            except (ValueError, urllib.error.URLError, TimeoutError):
                break  # Spotify may return no genres or disallow this deprecated endpoint.
            genres = {artist["id"]: artist["genres"][0] for artist in response.get("artists", [])
                      if artist and artist.get("id") and artist.get("genres")}
            with self.lock:
                changed = False
                for index in target:
                    track = self.tracks[index]
                    genre = genres.get(track.get("artist_id"))
                    if genre and not track.get("genre"):
                        track["genre"] = genre
                        changed = True
                if changed:
                    self.save()

    def run_job(self, kind, target):
        if kind in ("wav", "alac", "tag"):
            with self.lock:
                self.job["current"] = "Reading Spotify metadata"
            self.enrich_metadata(target)
        download_failures_in_row = 0
        for index in target:
            with self.lock:
                if kind == "match" and self.pause_requested:
                    self.job["paused"] = True
                    break
                if self.tracks[index].get("ignored"):
                    self.job["total"] -= 1
                    continue
                track = dict(self.tracks[index])
                track_label = f'{track["title"]} — {track["artist"]}'
                existing = kind in ("mp3", "wav", "alac") and already_downloaded(track, kind)
                self.job["current"] = "Finding where we left off..." if existing else track_label
            outcome = "existing" if existing else "success"
            try:
                if kind == "match":
                    candidates = search_youtube(track)
                    best = candidates[0] if candidates else None
                    with self.lock:
                        if not self.tracks[index].get("ignored"):
                            self.tracks[index]["candidates"] = candidates
                            if not self.tracks[index].get("selected"):
                                self.tracks[index]["selected"] = best["id"] if best and best["score"] >= 0.62 else None
                                self.tracks[index]["selection_source"] = "auto" if self.tracks[index]["selected"] else None
                                self.tracks[index]["status"] = "matched" if self.tracks[index]["selected"] else "needs_review"
                            self.tracks[index]["error"] = ""
                            self.save()
                elif kind == "tag":
                    write_wav_metadata(OUTPUT / filename(track, "wav"), track)
                    with self.lock:
                        self.tracks[index]["status"] = "downloaded"
                        self.tracks[index]["error"] = ""
                        self.save()
                else:
                    download_track(track, kind)
                    download_failures_in_row = 0
                    with self.lock:
                        self.tracks[index]["status"] = "downloaded"
                        self.tracks[index]["error"] = ""
                        self.save()
            except Exception as error:
                outcome = "error"
                detail = download_error(error)
                with self.lock:
                    if not self.tracks[index].get("ignored") and (kind != "match" or not self.tracks[index].get("selected")):
                        self.tracks[index]["status"] = "error"
                        self.tracks[index]["error"] = detail[:300]
                    self.job["errors"].append(track_label + ": " + detail[:300])
                    self.save()
                if kind in ("wav", "mp3", "alac"):
                    download_failures_in_row += 1
            with self.lock:
                self.job["done"] += 1
                self.job["last_result"] = {"sequence": self.job["done"],
                                           "title": track_label,
                                           "status": outcome}
                if download_failures_in_row > 3:
                    self.job["paused"] = True
                    self.job["pause_reason"] = ("Four downloads failed in a row. Downloads paused; "
                                                "check the errors and retry when ready.")
                    break
        with self.lock:
            self.job["running"] = False
            self.job["pausing"] = False
            self.job["current"] = ""


def post_form(url, fields):
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise ValueError(f"Spotify authorization failed (HTTP {error.code}).") from error


def search_youtube(track):
    import yt_dlp
    query = f'{track["artist"]} {track["title"]} official audio'
    with yt_dlp.YoutubeDL(youtube_options({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True})) as ydl:
        result = ydl.extract_info("ytsearch3:" + query, download=False)
    candidates = []
    for entry in (result or {}).get("entries", []):
        if not entry or not VIDEO_ID.fullmatch(entry.get("id", "")):
            continue
        candidate = {"id": entry["id"], "title": entry.get("title", "Untitled"),
                     "channel": entry.get("channel") or entry.get("uploader") or "",
                     "duration": entry.get("duration")}
        candidate["score"] = match_score(track, candidate)
        candidates.append(candidate)
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def youtube_video_track(selected, title="", artist=""):
    import yt_dlp
    with yt_dlp.YoutubeDL(youtube_options({"quiet": True, "no_warnings": True, "noplaylist": True,
                                           "skip_download": True, "js_runtimes": {"deno": {}, "node": {}}})) as ydl:
        info = ydl.extract_info("https://www.youtube.com/watch?v=" + selected, download=False)
    if not info or info.get("id") != selected:
        raise ValueError("Could not read details for that YouTube video.")

    def first_text(*values):
        return next((value.strip() for value in values if isinstance(value, str) and value.strip()), "")

    return {"title": first_text(title, info.get("track"), info.get("title")) or "Untitled",
            "artist": first_text(artist, info.get("artist"), info.get("creator"),
                                 info.get("uploader"), info.get("channel")) or "Unknown artist",
            "selected": selected}


def download_track(track, extension):
    import yt_dlp
    if not shutil.which("ffmpeg"):
        raise ValueError("FFmpeg is missing. Install it and restart the app.")
    selected = track.get("selected")
    if not selected or not VIDEO_ID.fullmatch(selected):
        raise ValueError("Choose a YouTube match first.")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    final = OUTPUT / filename(track, extension)
    if already_downloaded(track, extension):
        if extension == "wav":
            write_wav_metadata(final, track)
        elif extension == "alac":
            if audio_codec(final) == "alac":
                write_alac_metadata(final, track)
            else:
                convert_to_alac(final, final, track)
        return
    stem = f'{safe_name(track["title"])} ({safe_name(track["artist"])})'.replace("%", "%%")
    if extension == "alac":
        template = str(OUTPUT / (f".{stem}.{secrets.token_hex(6)}.source.%(ext)s"))
        opts = {"format": "bestaudio/best", "outtmpl": template, "noplaylist": True,
                "quiet": True, "no_warnings": True, "overwrites": False,
                "js_runtimes": {"deno": {}, "node": {}}}
        with yt_dlp.YoutubeDL(youtube_options(opts)) as ydl:
            info = ydl.extract_info("https://www.youtube.com/watch?v=" + selected, download=True)
        downloads = (info or {}).get("requested_downloads") or []
        source_name = (downloads[0].get("filepath") if downloads else None) or (info or {}).get("filepath")
        if not source_name:
            raise ValueError("The source audio file was not created.")
        source = Path(source_name)
        if source.resolve().parent != OUTPUT.resolve() or not source.is_file():
            raise ValueError("The source audio file was not created in the output folder.")
        convert_to_alac(source, final, track)
        source.unlink(missing_ok=True)
        return
    template = str(OUTPUT / (stem + ".%(ext)s"))
    opts = {"format": "bestaudio/best", "outtmpl": template, "noplaylist": True,
            "quiet": True, "no_warnings": True, "overwrites": False,
            "js_runtimes": {"deno": {}, "node": {}},
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": extension,
                                **({"preferredquality": "192"} if extension == "mp3" else {})}]}
    with yt_dlp.YoutubeDL(youtube_options(opts)) as ydl:
        ydl.download(["https://www.youtube.com/watch?v=" + selected])
    if not final.exists():
        raise ValueError("The audio file was not created.")
    if extension == "wav":
        write_wav_metadata(final, track)


APP = App()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def valid_host(self):
        return self.headers.get("Host") == f"{HOST}:{PORT}"

    def json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self):
        if not self.valid_host():
            self.json_response({"error": "Request not allowed"}, 403)
            return
        path = urllib.parse.urlparse(self.path)
        if path.path == "/auth":
            verifier = secrets.token_urlsafe(64)
            state = secrets.token_urlsafe(24)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            APP.oauth = {"state": state, "verifier": verifier, "created": time.time()}
            params = {"client_id": CLIENT_ID, "response_type": "code", "redirect_uri": REDIRECT_URI,
                      "scope": "playlist-read-private", "code_challenge_method": "S256",
                      "code_challenge": challenge, "state": state}
            self.redirect("https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params))
            return
        if path.path == "/callback":
            params = urllib.parse.parse_qs(path.query)
            pending = APP.oauth
            APP.oauth = None
            if not pending or time.time() - pending["created"] > 600 or params.get("state", [None])[0] != pending["state"]:
                self.json_response({"error": "Spotify sign-in state expired. Try again."}, 400)
                return
            try:
                token = post_form("https://accounts.spotify.com/api/token", {
                    "grant_type": "authorization_code", "code": params.get("code", [""])[0],
                    "redirect_uri": REDIRECT_URI, "client_id": CLIENT_ID,
                    "code_verifier": pending["verifier"]})
                token["expires_at"] = time.time() + token["expires_in"]
                APP.token = token
                self.redirect("/")
            except ValueError as error:
                self.json_response({"error": str(error)}, 400)
            return
        if path.path == "/api/state":
            self.json_response(APP.snapshot())
            return
        static = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
        if path.path in static:
            file = BASE / "ui" / static[path.path]
            body = file.read_bytes()
            if path.path == "/":
                body = body.replace(b"__CSRF__", APP.csrf.encode())
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8" if path.path == "/" else
                             "text/javascript; charset=utf-8" if path.path.endswith(".js") else "text/css; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.json_response({"error": "Not found"}, 404)

    def do_POST(self):
        if not self.valid_host() or self.headers.get("Origin") != f"http://{HOST}:{PORT}" or self.headers.get("X-App-Token") != APP.csrf:
            self.json_response({"error": "Request not allowed"}, 403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 100000 or size < 0:
                raise ValueError("Request is too large.")
            data = json.loads(self.rfile.read(size) or b"{}")
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/playlist":
                count = APP.load_playlist(data.get("url", ""))
                self.json_response({"count": count})
            elif path == "/api/youtube-browser":
                APP.set_youtube_browser(data.get("browser"))
                self.json_response({"ok": True})
            elif path == "/api/match":
                with APP.lock:
                    target = [i for i, t in enumerate(APP.tracks) if t["status"] in ("pending", "needs_review", "error")
                              and not t.get("selected") and not t.get("ignored")]
                APP.begin_job("match", target)
                self.json_response({"count": len(target)})
            elif path == "/api/pause":
                APP.pause_match()
                self.json_response({"ok": True})
            elif path == "/api/ignore":
                APP.set_ignored(data.get("index"), data.get("ignored"))
                self.json_response({"ok": True})
            elif path == "/api/choose":
                index = data.get("index")
                if type(index) is not int or index < 0 or index >= len(APP.tracks):
                    raise ValueError("Track index is invalid.")
                selected = data.get("video_id", "")
                if selected.startswith("https://"):
                    selected = video_id(selected)
                if not VIDEO_ID.fullmatch(selected):
                    raise ValueError("Choose a valid YouTube video.")
                with APP.lock:
                    if APP.tracks[index].get("ignored"):
                        raise ValueError("Restore this song before choosing a YouTube match.")
                    APP.tracks[index]["selected"] = selected
                    APP.tracks[index]["selection_source"] = "manual"
                    APP.tracks[index]["status"] = "matched"
                    APP.tracks[index]["error"] = ""
                    APP.save()
                self.json_response({"ok": True})
            elif path == "/api/download":
                extension = data.get("format")
                if extension not in ("mp3", "wav", "alac"):
                    raise ValueError("Choose MP3, WAV, or ALAC.")
                with APP.lock:
                    target = [i for i, t in enumerate(APP.tracks) if t.get("selected") and not t.get("ignored") and
                              (extension in ("wav", "alac") or not (OUTPUT / filename(t, extension)).exists())]
                APP.begin_job(extension, target)
                self.json_response({"count": len(target)})
            elif path == "/api/one-off":
                APP.start_one_off(data.get("url", ""), data.get("format"),
                                  data.get("title", ""), data.get("artist", ""))
                self.json_response({"ok": True})
            elif path == "/api/tag-wav":
                with APP.lock:
                    target = [i for i, track in enumerate(APP.tracks)
                              if not track.get("ignored") and (OUTPUT / filename(track, "wav")).is_file() and
                              (OUTPUT / filename(track, "wav")).stat().st_size > 0]
                    if target and not APP.token and any(not APP.tracks[i].get("album") or
                                                        not APP.tracks[i].get("release_date") for i in target):
                        raise ValueError("Connect Spotify and reload the same playlist to fill album and release date first.")
                APP.begin_job("tag", target)
                self.json_response({"count": len(target)})
            else:
                self.json_response({"error": "Not found"}, 404)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.json_response({"error": str(error)}, 400)


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    APP.rescore_saved_matches()
    APP.start_auto_match()
    webbrowser.open(f"http://{HOST}:{PORT}")
    print(f"Spotify Downloader: http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
