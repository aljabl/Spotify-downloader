# Spotify Downloader

A local playlist downloader. It reads tracks from a Spotify playlist you own or collaborate on, searches YouTube for matches, and saves selected videos' audio as MP3 or WAV in `Spotify/`. Download only audio you have permission to save.

## macOS

1. Install Python 3.10 or newer, FFmpeg, and Deno 2.3+ or Node.js 22+. If you use Homebrew, `brew install python ffmpeg deno`.
2. Double-click **Start Spotify Downloader.command**. The launcher installs the pinned `yt-dlp` release and its YouTube JavaScript support from PyPI into a local `.venv` folder.
3. The app opens at <http://127.0.0.1:8765>. Connect Spotify and paste a playlist link. YouTube matching starts automatically and prefers video titles containing the word "Official". Review uncertain results or paste a video link to override one, then choose MP3 or WAV.
4. Keep the Terminal window open until downloads finish. Files are written to the `Spotify` folder beside this README. WAV files include Spotify title, artist, album, release date, track number, and the primary artist's genre when Spotify provides it. To tag WAV files already in the folder without accessing YouTube, reconnect Spotify, reload the same playlist, and click **Update existing WAV tags**.

The Spotify app is currently in Development mode; Spotify may limit who can sign in. The current Spotify API only returns tracks for playlists owned by the signed-in user or playlists they collaborate on. The app cannot retrieve full audio from Spotify. It searches YouTube and uses `yt-dlp` for selected videos. YouTube availability and matching accuracy vary.

The local server listens only on `127.0.0.1`, keeps your Spotify token in memory, and stores playlist progress in `Spotify/.state.json` so matching resumes after restart. Use **Pause YouTube search** to stop after the current track. It does not send tokens to the website.

If YouTube returns no audio formats for three songs in a row, the app stops the download job to avoid marking the rest as failures. This can happen when YouTube restricts extraction; updating yt-dlp cannot guarantee access. Reconnect Spotify before downloading an older saved playlist so the app can fill in missing album and date metadata. Artist genres are optional and may be unavailable from Spotify.
