import unittest

from server import filename, match_score, playlist_id, safe_name, video_id


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


if __name__ == "__main__":
    unittest.main()
