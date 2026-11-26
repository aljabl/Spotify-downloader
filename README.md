# Spotify Downloader

A local playlist downloader. It reads tracks from a Spotify playlist you own or collaborate on, searches YouTube for matches, and saves selected videos' audio as MP3 or WAV in `Spotify/`. Download only audio you have permission to save.

## macOS

1. Install Python 3.10 or newer and FFmpeg. If you use Homebrew, `brew install python ffmpeg`.
2. Double-click **Start Spotify Downloader.command**. The first launch installs the pinned `yt-dlp` release from PyPI into a local `.venv` folder.
3. The app opens at <http://127.0.0.1:8765>. Connect Spotify, paste a playlist link, find matches, review them, and choose MP3 or WAV.
4. Keep the Terminal window open until downloads finish. Files are written to the `Spotify` folder beside this README.

The Spotify app is currently in Development mode; Spotify may limit who can sign in. The current Spotify API only returns tracks for playlists owned by the signed-in user or playlists they collaborate on. The app cannot retrieve full audio from Spotify. It searches YouTube and uses `yt-dlp` for selected videos. YouTube availability and matching accuracy vary.

The local server listens only on `127.0.0.1`, keeps your Spotify token in memory, and stores playlist progress in `Spotify/.state.json` so you can resume. It does not send tokens to the website.
