# Spotify Downloader

A local audio downloader. Paste a YouTube video link to save one song, or load a Spotify playlist you own or collaborate on and download its matched videos. Files are saved as MP3, WAV, or ALAC (`.m4a`) in `Spotify/`. Download only audio you have permission to save.

## macOS

1. Install Python 3.10 or newer, FFmpeg, and Deno 2.3+ or Node.js 22+. If you use Homebrew, `brew install python ffmpeg deno`.
2. Double-click **Start Spotify Downloader.command**. The launcher installs the pinned `yt-dlp` release and its YouTube JavaScript support from PyPI into a local `.venv` folder.
3. The app opens at <http://127.0.0.1:8765>. For one song, paste a YouTube video link in **Download one song** and choose MP3, WAV, or ALAC. Title and artist are optional; the app uses video details when they are blank. No Spotify sign-in is required for this. For a playlist, connect Spotify and paste a playlist link. YouTube matching starts automatically and prefers video titles containing the word "Official". Review uncertain results or paste a video link to override one. Use **Ignore** on a song to exclude it from matching, downloads, and WAV tag updates; **Restore** adds it back. Ignoring does not delete a file already saved. The app sections can be collapsed to their headings.
4. Keep the Terminal window open until downloads finish. Files are written to the `Spotify` folder beside this README. Playlist WAV and ALAC files include Spotify title, artist, album, release date, track number, and the primary artist's genre when Spotify provides it. One-off WAV and ALAC files receive the title and artist from the video or your optional edits. To tag playlist WAV files already in the folder without accessing YouTube, reconnect Spotify, reload the same playlist, and click **Update existing WAV tags**.

If a download says **“Sign in to confirm you’re not a bot”**, sign in to YouTube in Safari, Chrome, Firefox, or another browser listed under **YouTube access** in the app. Select that browser, click **Save YouTube access**, and retry. The app passes the browser session to yt-dlp for YouTube search, video details, and downloads. Only the browser choice is saved in `Spotify/.state.json`; the app does not export cookies to a file. Browser access may prompt for macOS permission. If it still fails, try another signed-in browser or check [yt-dlp's cookie guidance](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp). YouTube can still refuse access after cookies are supplied.

The Spotify app is currently in Development mode; Spotify may limit who can sign in. The current Spotify API only returns tracks for playlists owned by the signed-in user or playlists they collaborate on. The app cannot retrieve full audio from Spotify. It searches YouTube and uses `yt-dlp` for selected videos. YouTube availability and matching accuracy vary.

The local server listens only on `127.0.0.1`, keeps your Spotify token in memory, and stores playlist progress in `Spotify/.state.json` so matching resumes after restart. Use **Pause YouTube search** to stop after the current track. It does not send tokens to the website.

If four playlist audio downloads fail in a row, the app pauses the job to avoid marking the rest as failures. Any successful download resets the count. Check the errors and retry after resolving the cause. Reconnect Spotify before downloading an older saved playlist so the app can fill in missing album and date metadata. Artist genres are optional and may be unavailable from Spotify.

ALAC is encoded in an `.m4a` file. Like WAV, it cannot restore detail already lost in YouTube's compressed source audio.
