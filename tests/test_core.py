import unittest
import json
import shutil
import subprocess
import server
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from server import App, convert_to_alac, download_error, download_track, filename, match_score, playlist_id, safe_name, search_youtube, spotify_metadata, video_id, write_alac_metadata, write_wav_metadata, youtube_video_track


@contextmanager
def isolated_app():
    with TemporaryDirectory() as directory:
        output = Path(directory) / "Spotify"
        with patch("server.OUTPUT", output), patch("server.STATE_FILE", output / ".state.json"):
            yield App()


def sample_track(title="Example Song"):
    return {"title": title, "artist": "Artist", "selected": None, "selection_source": None, "status": "pending",
            "candidates": [], "error": "", "duration_ms": 180000, "ignored": False}


class CoreTests(unittest.TestCase):
    def test_browser_setting_is_saved_and_used_for_all_youtube_requests(self):
        with isolated_app() as app, patch("server.APP", app):
            app.set_youtube_browser("firefox")
            self.assertEqual(App().youtube_browser, "firefox")
            self.assertEqual(app.snapshot()["youtube_browser"], "firefox")
            with patch("yt_dlp.YoutubeDL") as youtube:
                youtube.return_value.__enter__.return_value.extract_info.return_value = {"entries": []}
                search_youtube(sample_track())
                self.assertEqual(youtube.call_args.args[0]["cookiesfrombrowser"], ("firefox",))
                youtube.return_value.__enter__.return_value.extract_info.return_value = {"id": "YE7VzlLtp-4"}
                youtube_video_track("YE7VzlLtp-4")
                self.assertEqual(youtube.call_args.args[0]["cookiesfrombrowser"], ("firefox",))
                with patch("server.shutil.which", return_value="/usr/bin/ffmpeg"):
                    for extension in ("mp3", "alac"):
                        with self.assertRaises(ValueError):
                            download_track(dict(sample_track(), selected="YE7VzlLtp-4"), extension)
                        self.assertEqual(youtube.call_args.args[0]["cookiesfrombrowser"], ("firefox",))
            app.set_youtube_browser("")
            self.assertEqual(App().youtube_browser, "")
            with self.assertRaises(ValueError):
                app.set_youtube_browser("unknown")

    def test_bot_challenge_gives_browser_instructions(self):
        message = download_error("ERROR: [youtube] Sign in to confirm you're not a bot. Use --cookies-from-browser")
        self.assertIn("choose that browser under YouTube access", message)

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

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools required")
    def test_alac_codec_and_metadata_are_readable(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.m4a"
            path = Path(directory) / "song.m4a"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=0.1", "-c:a", "aac", str(source)], check=True)
            tags = {"title": "A Song", "artist": "An Artist", "album": "An Album",
                    "release_date": "2020-03", "genre": "Pop", "track_number": 7}
            convert_to_alac(source, path, tags)
            write_alac_metadata(path, tags)
            result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags:stream=codec_name",
                                     "-of", "json", str(path)], capture_output=True, text=True, check=True)
            details = json.loads(result.stdout)
            self.assertEqual(details["streams"][0]["codec_name"], "alac")
            for key, value in {"title": "A Song", "artist": "An Artist", "album": "An Album",
                               "date": "2020-03", "genre": "Pop", "track": "7"}.items():
                self.assertEqual(details["format"]["tags"][key], value)

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

    def test_alac_filename_and_existing_file_metadata(self):
        with isolated_app():
            track = dict(sample_track(), selected="YE7VzlLtp-4")
            path = server.OUTPUT / filename(track, "alac")
            self.assertEqual(path.suffix, ".m4a")
            path.parent.mkdir(parents=True)
            path.write_bytes(b"existing alac")
            with patch("server.audio_codec", return_value="alac"), patch("server.write_alac_metadata") as tag, patch("yt_dlp.YoutubeDL") as youtube:
                download_track(track, "alac")
            tag.assert_called_once_with(path, track)
            youtube.assert_not_called()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools required")
    def test_existing_aac_m4a_is_converted_to_alac(self):
        with isolated_app():
            track = dict(sample_track(), selected="YE7VzlLtp-4")
            path = server.OUTPUT / filename(track, "alac")
            path.parent.mkdir(parents=True)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=0.1", "-c:a", "aac", str(path)], check=True)
            with patch("yt_dlp.YoutubeDL") as youtube:
                download_track(track, "alac")
            youtube.assert_not_called()
            self.assertEqual(server.audio_codec(path), "alac")

    def test_alac_download_converts_source_and_removes_it(self):
        with isolated_app():
            track = dict(sample_track(), selected="YE7VzlLtp-4")
            path = server.OUTPUT / filename(track, "alac")
            source = server.OUTPUT / ".source.m4a"
            with patch("server.convert_to_alac") as convert, patch("yt_dlp.YoutubeDL") as youtube:
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"compressed source")
                youtube.return_value.__enter__.return_value.extract_info.return_value = {
                    "requested_downloads": [{"filepath": str(source)}]}
                download_track(track, "alac")
            options = youtube.call_args.args[0]
            self.assertNotIn("postprocessors", options)
            self.assertEqual(path.suffix, ".m4a")
            convert.assert_called_once_with(source, path, track)
            self.assertFalse(source.exists())

    def test_four_consecutive_download_failures_pause_job(self):
        with isolated_app() as app:
            app.tracks = [dict(sample_track(), selected="YE7VzlLtp-4", status="matched") for _ in range(5)]
            app.job.update({"kind": "wav", "running": True, "total": 5})
            with patch.object(app, "enrich_metadata"), patch("server.download_track", side_effect=ValueError("YouTube blocked this video")) as download:
                app.run_job("wav", range(5))
            self.assertEqual(download.call_count, 4)
            self.assertEqual(app.job["done"], 4)
            self.assertTrue(app.job["paused"])
            self.assertEqual(len(app.job["errors"]), 4)
            self.assertIn("Four downloads failed in a row", app.job["pause_reason"])
            self.assertEqual(app.tracks[4]["status"], "matched")
            self.assertEqual(app.job["last_result"]["status"], "error")

    def test_success_resets_consecutive_download_failures(self):
        with isolated_app() as app:
            app.tracks = [dict(sample_track(), selected="YE7VzlLtp-4", status="matched") for _ in range(7)]
            app.job.update({"kind": "mp3", "running": True, "total": 7})
            failures = [ValueError("Unavailable"), RuntimeError("Blocked"), ValueError("Timed out")]
            with patch("server.download_track", side_effect=failures + [None] + failures) as download:
                app.run_job("mp3", range(7))
            self.assertEqual(download.call_count, 7)
            self.assertEqual(app.job["done"], 7)
            self.assertFalse(app.job["paused"])
            self.assertEqual(app.job["pause_reason"], "")

    def test_existing_downloads_show_resume_status_until_new_download(self):
        with isolated_app() as app:
            app.tracks = [dict(sample_track(title), selected="YE7VzlLtp-4", status="matched")
                          for title in ("First", "Second")]
            existing = server.OUTPUT / filename(app.tracks[0], "wav")
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"existing wav")
            app.job.update({"kind": "wav", "running": True, "total": 2})
            observed = []
            def download(track, extension):
                observed.append((app.job["current"], app.job["last_result"]))
            with patch.object(app, "enrich_metadata"), patch("server.download_track", side_effect=download):
                app.run_job("wav", [0, 1])
            self.assertEqual(observed[0][0], "Finding where we left off...")
            self.assertEqual(observed[1][0], "Second — Artist")
            self.assertEqual(observed[1][1]["status"], "existing")
            self.assertEqual(app.job["last_result"]["status"], "success")
            app.job.update({"running": True, "done": 0, "errors": [], "last_result": None})
            with patch.object(app, "enrich_metadata"), patch("server.download_track", side_effect=ValueError("Tag failed")):
                app.run_job("wav", [0])
            self.assertTrue(app.job["errors"][0].startswith("First — Artist: Tag failed"))

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
        self.assertEqual(video_id("https://music.youtube.com/watch?v=YE7VzlLtp-4&list=abc"), "YE7VzlLtp-4")
        with self.assertRaises(ValueError):
            video_id("https://youtube.com.evil.example/watch?v=YE7VzlLtp-4")

    def test_one_off_uses_youtube_details_and_optional_overrides(self):
        info = {"id": "YE7VzlLtp-4", "title": "Video title", "track": "Track title",
                "artist": "Track artist", "uploader": "Channel"}
        with patch("yt_dlp.YoutubeDL") as youtube:
            youtube.return_value.__enter__.return_value.extract_info.return_value = info
            self.assertEqual(youtube_video_track("YE7VzlLtp-4"),
                             {"title": "Track title", "artist": "Track artist", "selected": "YE7VzlLtp-4"})
            self.assertEqual(youtube_video_track("YE7VzlLtp-4", "My title", "My artist"),
                             {"title": "My title", "artist": "My artist", "selected": "YE7VzlLtp-4"})

    def test_one_off_download_does_not_change_playlist(self):
        with isolated_app() as app:
            app.tracks = [sample_track()]
            app.job["running"] = True  # A playlist search may continue independently.
            with patch("server.threading.Thread") as thread:
                app.start_one_off("https://youtu.be/YE7VzlLtp-4", "mp3")
            thread.return_value.start.assert_called_once()
            self.assertEqual(app.one_off["status"], "working")
            with self.assertRaises(ValueError):
                app.start_one_off("https://youtu.be/YE7VzlLtp-4", "mp3")
            with patch("server.youtube_video_track", return_value={"title": "New song", "artist": "New artist",
                                                                  "selected": "YE7VzlLtp-4"}), \
                 patch("server.download_track") as download:
                app.run_one_off("YE7VzlLtp-4", "mp3", "", "")
            download.assert_called_once()
            self.assertEqual(app.one_off, {"status": "done", "filename": "New song (New artist).mp3", "error": ""})
            self.assertEqual(app.tracks, [sample_track()])
            self.assertTrue(app.job["running"])

    def test_one_off_failure_is_reported(self):
        with isolated_app() as app:
            with patch("server.youtube_video_track", side_effect=ValueError("YouTube video is unavailable")):
                app.run_one_off("YE7VzlLtp-4", "wav", "", "")
            self.assertEqual(app.one_off["status"], "error")
            self.assertIn("unavailable", app.one_off["error"])

    def test_one_off_rejects_bad_link_and_format(self):
        with isolated_app() as app:
            for url, format in (("https://evil.example/watch?v=YE7VzlLtp-4", "mp3"),
                                ("https://youtu.be/YE7VzlLtp-4", "flac")):
                with self.assertRaises(ValueError):
                    app.start_one_off(url, format)
            self.assertEqual(app.one_off["status"], "idle")

    def test_one_off_accepts_alac(self):
        with isolated_app() as app, patch("server.threading.Thread") as thread:
            app.start_one_off("https://youtu.be/YE7VzlLtp-4", "alac")
            thread.return_value.start.assert_called_once()
            self.assertEqual(app.one_off["status"], "working")

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
                         selection_source="manual", status="matched", ignored=True)
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
            self.assertTrue(app.tracks[0]["ignored"])

    def test_restart_searches_only_pending_tracks(self):
        with isolated_app() as app:
            app.tracks = [sample_track("First"), sample_track("Second"), dict(sample_track("Third"), ignored=True)]
            app.tracks[1]["selected"] = "YE7VzlLtp-4"
            with patch.object(app, "begin_job") as begin:
                self.assertEqual(app.start_auto_match(), 1)
                begin.assert_called_once_with("match", [0])

    def test_ignore_and_restore_survive_restart(self):
        with isolated_app() as app:
            app.tracks = [sample_track()]
            app.set_ignored(0, True)
            restarted = App()
            self.assertTrue(restarted.tracks[0]["ignored"])
            restarted.set_ignored(0, False)
            self.assertFalse(App().tracks[0]["ignored"])

    def test_ignored_song_is_skipped_in_queued_search(self):
        with isolated_app() as app:
            app.tracks = [dict(sample_track("First"), ignored=True), sample_track("Second")]
            app.job.update({"kind": "match", "running": True, "total": 2})
            candidate = {"id": "YE7VzlLtp-4", "score": 1.0}
            with patch("server.search_youtube", return_value=[candidate]) as search:
                app.run_job("match", [0, 1])
            search.assert_called_once()
            self.assertEqual(app.tracks[0]["status"], "pending")
            self.assertEqual(app.tracks[1]["status"], "matched")
            self.assertEqual((app.job["done"], app.job["total"]), (1, 1))

    def test_ignoring_song_during_search_discards_result(self):
        with isolated_app() as app:
            app.tracks = [sample_track()]
            app.job.update({"kind": "match", "running": True, "total": 1})
            def search(_):
                app.set_ignored(0, True)
                return [{"id": "YE7VzlLtp-4", "score": 1.0}]
            with patch("server.search_youtube", side_effect=search):
                app.run_job("match", [0])
            self.assertTrue(app.tracks[0]["ignored"])
            self.assertEqual(app.tracks[0]["status"], "pending")
            self.assertEqual(app.tracks[0]["candidates"], [])

    def test_ignored_song_is_skipped_in_queued_download(self):
        with isolated_app() as app:
            app.tracks = [dict(sample_track("First"), selected="YE7VzlLtp-4", status="matched", ignored=True),
                          dict(sample_track("Second"), selected="YE7VzlLtp-4", status="matched")]
            app.job.update({"kind": "mp3", "running": True, "total": 2})
            with patch("server.download_track") as download:
                app.run_job("mp3", [0, 1])
            download.assert_called_once()
            self.assertEqual(app.tracks[0]["status"], "matched")
            self.assertEqual(app.tracks[1]["status"], "downloaded")
            self.assertEqual((app.job["done"], app.job["total"]), (1, 1))
            self.assertEqual(app.job["last_result"], {"sequence": 1, "title": "Second — Artist", "status": "success"})

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
