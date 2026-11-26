import unittest
import json
import shutil
import subprocess
import server
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from server import App, download_track, filename, match_score, playlist_id, safe_name, spotify_metadata, video_id, write_wav_metadata


@contextmanager
def isolated_app():
    with TemporaryDirectory() as directory:
        output = Path(directory) / "Spotify"
        with patch("server.OUTPUT", output), patch("server.STATE_FILE", output / ".state.json"):
            yield App()


def sample_track(title="Example Song"):
    return {"title": title, "artist": "Artist", "selected": None, "selection_source": None, "status": "pending",
            "candidates": [], "error": "", "duration_ms": 180000}


class CoreTests(unittest.TestCase):
    def test_spotify_playlist_track_metadata(self):
        item = {"album": {"name": "The Album", "release_date": "2020-03"},
                "artists": [{"id": "artist-id", "name": "Artist"}], "track_number": 7}
        self.assertEqual(spotify_metadata(item), {"album": "The Album", "release_date": "2020-03",
                                                  "track_number": 7, "artist_id": "artist-id"})

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools required")
    def test_wav_metadata_is_readable(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "song.wav"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=0.1", str(path)], check=True)
            write_wav_metadata(path, {"title": "A Song", "artist": "An Artist", "album": "An Album",
                                      "release_date": "2020-03", "genre": "Pop", "track_number": 7})
            result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags",
                                     "-of", "json", str(path)], capture_output=True, text=True, check=True)
            tags = json.loads(result.stdout)["format"]["tags"]
            for key, value in {"title": "A Song", "artist": "An Artist", "album": "An Album",
                               "date": "2020-03", "genre": "Pop", "track": "7"}.items():
                self.assertEqual(tags[key], value)

    def test_existing_tracks_can_gain_album_and_artist_genre(self):
        with isolated_app() as app:
            track = sample_track()
            track["spotify_id"] = "track-id"
            app.tracks = [track]
            app.token = {"access_token": "test"}
            full = {"id": "track-id", "album": {"name": "The Album", "release_date": "2020"},
                    "artists": [{"id": "artist-id"}], "track_number": 2}
            with patch.object(app, "spotify_get", side_effect=[{"tracks": [full]},
                                                              {"artists": [{"id": "artist-id", "genres": ["Indie rock"]}]}]):
                app.enrich_metadata([0])
            self.assertEqual((track["album"], track["release_date"], track["track_number"], track["genre"]),
                             ("The Album", "2020", 2, "Indie rock"))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_existing_wav_is_tagged_without_redownloading(self):
        with isolated_app():
            track = dict(sample_track(), selected="YE7VzlLtp-4")
            path = server.OUTPUT / filename(track, "wav")
            path.parent.mkdir(parents=True)
            path.write_bytes(b"existing wav")
            with patch("server.write_wav_metadata") as tag, patch("yt_dlp.YoutubeDL") as youtube:
                download_track(track, "wav")
            tag.assert_called_once_with(path, track)
            youtube.assert_not_called()

    def test_repeated_unavailable_formats_stop_download_job(self):
        with isolated_app() as app:
            app.tracks = [dict(sample_track(), selected="YE7VzlLtp-4", status="matched") for _ in range(4)]
            app.job.update({"kind": "wav", "running": True, "total": 4})
            with patch.object(app, "enrich_metadata"), patch("server.download_track", side_effect=ValueError("Requested format is not available")):
                app.run_job("wav", [0, 1, 2, 3])
            self.assertEqual(app.job["done"], 3)
            self.assertTrue(app.job["paused"])
            self.assertEqual(app.tracks[3]["status"], "matched")

    def test_tag_job_does_not_download_from_youtube(self):
        with isolated_app() as app:
            app.tracks = [dict(sample_track(), selected="YE7VzlLtp-4", status="matched")]
            app.job.update({"kind": "tag", "running": True, "total": 1})
            with patch("server.write_wav_metadata") as tag, patch("server.download_track") as download:
                app.run_job("tag", [0])
            self.assertEqual(tag.call_count, 1)
            self.assertEqual(tag.call_args.args[0], server.OUTPUT / filename(app.tracks[0], "wav"))
            download.assert_not_called()
            self.assertEqual(app.tracks[0]["status"], "downloaded")

    def test_playlist_link_rejects_other_hosts_and_types(self):
        self.assertEqual(playlist_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc"), "37i9dQZF1DXcBWIGoYBM5M")
        for value in ("https://evil.example/playlist/37i9dQZF1DXcBWIGoYBM5M", "https://open.spotify.com/album/37i9dQZF1DXcBWIGoYBM5M"):
            with self.assertRaises(ValueError):
                playlist_id(value)

    def test_youtube_link_validation(self):
        self.assertEqual(video_id("https://www.youtube.com/watch?v=YE7VzlLtp-4"), "YE7VzlLtp-4")
        self.assertEqual(video_id("https://youtu.be/YE7VzlLtp-4"), "YE7VzlLtp-4")
        with self.assertRaises(ValueError):
            video_id("https://youtube.com.evil.example/watch?v=YE7VzlLtp-4")

    def test_output_name_cannot_escape_folder(self):
        track = {"title": "../Track: 100%", "artist": "A/B"}
        self.assertEqual(filename(track, "mp3"), "Track 100% (AB).mp3")
        self.assertLess(len(safe_name("🎵" * 200).encode("utf-8")), 101)

    def test_match_prefers_same_song_and_artist(self):
        track = {"title": "Example Song", "artist": "Artist", "duration_ms": 180000}
        right = {"title": "Artist - Example Song (Official Audio)", "duration": 180}
        wrong = {"title": "Different Song cover", "duration": 450}
        self.assertGreater(match_score(track, right), match_score(track, wrong))

    def test_match_prefers_official_title(self):
        track = {"title": "Never Gonna Give You Up", "artist": "Rick Astley", "duration_ms": 212000}
        plain = {"title": "Rick Astley - Never Gonna Give You Up (HQ Audio)", "duration": 212}
        official = {"title": "Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)", "duration": 214}
        self.assertGreater(match_score(track, official), match_score(track, plain))

    def test_rescore_updates_legacy_auto_pick_but_preserves_manual_pick(self):
        with isolated_app() as app:
            plain = {"id": "2rmYU0B3kCQ", "title": "Rick Astley - Never Gonna Give You Up (HQ Audio)", "duration": 212, "score": 1.326}
            official = {"id": "dQw4w9WgXcQ", "title": "Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)", "duration": 214, "score": 1.162}
            legacy = {"title": "Never Gonna Give You Up", "artist": "Rick Astley", "duration_ms": 212000,
                      "candidates": [dict(plain), dict(official)], "selected": plain["id"], "status": "matched"}
            manual = dict(legacy, candidates=[dict(plain), dict(official)], selection_source="manual")
            app.tracks = [legacy, manual]
            app.rescore_saved_matches()
            self.assertEqual(app.tracks[0]["selected"], official["id"])
            self.assertEqual(app.tracks[0]["selection_source"], "auto")
            self.assertEqual(app.tracks[1]["selected"], plain["id"])

    def test_loading_playlist_starts_automatic_search(self):
        with isolated_app() as app:
            item = {"track": {"type": "track", "name": "Example Song", "artists": [{"name": "Artist"}], "duration_ms": 180000}}
            with patch.object(app, "spotify_get", side_effect=[{"name": "My Playlist"}, {"items": [item], "total": 1}]):
                with patch.object(app, "start_auto_match", return_value=1) as start:
                    self.assertEqual(app.load_playlist("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"), 1)
                    start.assert_called_once_with()

    def test_reloading_playlist_keeps_match_and_adds_album(self):
        with isolated_app() as app:
            saved = dict(sample_track(), spotify_id="track-id", selected="YE7VzlLtp-4",
                         selection_source="manual", status="matched")
            app.tracks = [saved]
            item = {"track": {"type": "track", "id": "track-id", "name": "Example Song",
                              "artists": [{"id": "artist-id", "name": "Artist"}],
                              "album": {"name": "New Album", "release_date": "2021-09-03"}}}
            with patch.object(app, "spotify_get", side_effect=[{"name": "My Playlist"}, {"items": [item], "total": 1}]), \
                 patch.object(app, "start_auto_match", return_value=0):
                app.load_playlist("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
            self.assertEqual(app.tracks[0]["album"], "New Album")
            self.assertEqual(app.tracks[0]["release_date"], "2021-09-03")
            self.assertEqual(app.tracks[0]["selected"], "YE7VzlLtp-4")
            self.assertEqual(app.tracks[0]["selection_source"], "manual")

    def test_restart_searches_only_pending_tracks(self):
        with isolated_app() as app:
            app.tracks = [sample_track("First"), sample_track("Second")]
            app.tracks[1]["selected"] = "YE7VzlLtp-4"
            with patch.object(app, "begin_job") as begin:
                self.assertEqual(app.start_auto_match(), 1)
                begin.assert_called_once_with("match", [0])

    def test_manual_link_wins_over_search_in_progress(self):
        with isolated_app() as app:
            app.tracks = [sample_track()]
            app.job.update({"kind": "match", "running": True, "total": 1})
            def search(_):
                app.tracks[0]["selected"] = "YE7VzlLtp-4"
                app.tracks[0]["status"] = "matched"
                return [{"id": "dQw4w9WgXcQ", "score": 1.0}]
            with patch("server.search_youtube", side_effect=search):
                app.run_job("match", [0])
            self.assertEqual(app.tracks[0]["selected"], "YE7VzlLtp-4")

    def test_pause_stops_after_current_song(self):
        with isolated_app() as app:
            app.tracks = [sample_track("First"), sample_track("Second")]
            app.job.update({"kind": "match", "running": True, "total": 2})
            def search(_):
                app.pause_requested = True
                return [{"id": "YE7VzlLtp-4", "score": 1.0}]
            with patch("server.search_youtube", side_effect=search):
                app.run_job("match", [0, 1])
            self.assertEqual(app.job["done"], 1)
            self.assertTrue(app.job["paused"])
            self.assertEqual(app.tracks[1]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
