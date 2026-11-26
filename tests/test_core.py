import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from server import App, filename, match_score, playlist_id, safe_name, video_id


@contextmanager
def isolated_app():
    with TemporaryDirectory() as directory:
        output = Path(directory) / "Spotify"
        with patch("server.OUTPUT", output), patch("server.STATE_FILE", output / ".state.json"):
            yield App()


def sample_track(title="Example Song"):
    return {"title": title, "artist": "Artist", "selected": None, "status": "pending",
            "candidates": [], "error": "", "duration_ms": 180000}


class CoreTests(unittest.TestCase):
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

    def test_loading_playlist_starts_automatic_search(self):
        with isolated_app() as app:
            item = {"track": {"type": "track", "name": "Example Song", "artists": [{"name": "Artist"}], "duration_ms": 180000}}
            with patch.object(app, "spotify_get", side_effect=[{"name": "My Playlist"}, {"items": [item], "total": 1}]):
                with patch.object(app, "start_auto_match", return_value=1) as start:
                    self.assertEqual(app.load_playlist("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"), 1)
                    start.assert_called_once_with()

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
