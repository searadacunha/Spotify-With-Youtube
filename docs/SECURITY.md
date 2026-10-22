# Security and privacy

## Trust boundaries

- **TV over ADB.** With ADB debugging enabled, anyone on your LAN who is accepted by
  the TV's authorization dialog can control the TV. The bridge uses that access to
  read media sessions, read which window has the focus (`dumpsys window`)
  and start one `VIEW` intent; it never installs apps, changes settings, reads files
  or sends key events. Keep ADB debugging restricted to computers you accepted, and
  disable it when not needed.
- **YouTube.** Searches and downloads go through `yt-dlp` to YouTube's public
  endpoints, without cookies or an account. `yt-dlp-ejs` runs YouTube's JavaScript
  challenges in your local Node.js, as yt-dlp documents.
- **Spotify.** The default `tv` source reads metadata from the TV; no Spotify
  credentials are involved. The optional `web` source uses OAuth PKCE with only
  `user-read-currently-playing`; the token is stored in `cache/spotify-token.json`
  with mode `0600` and is never logged.

## Input validation

- Video ids must match `[A-Za-z0-9_-]{11}` before reaching ADB or yt-dlp; a test
  asserts an injected id never reaches `adb`.
- `tv_target` must be `host:port`, `smarttube_package` a valid Android package
  name; every numeric option has a range. Unknown keys are refused.
- Evidence fields (`audio_check`) coming from YouTube metadata are discarded; only
  the bridge's own comparisons are trusted.
- Subprocesses run with explicit argument lists (no shell), timeouts, and the
  audio processes are killed as a group on timeout.

## What is stored locally (all git-ignored)

| Path | Content |
| --- | --- |
| `config.json` | your TV address and options |
| `.env` | Spotify client id (web source only) |
| `cache/matches.sqlite3` | Spotify track ids → chosen YouTube ids, audio verdicts, last outcome |
| `cache/spotify-token.json` | OAuth token (web source only) |
| `logs/bridge.log*`, `logs/last-match.json` | track titles, ids, candidates, TV address |
| temporary audio | downloaded to a private temp directory during a comparison and deleted after decoding |

Logs are your listening history: treat them as personal data. Spotify Web API errors
are logged as status codes only.

## What is in this repository

Source code, tests, documentation, the prebuilt Android probe (`android/classes.dex`,
built from `android/MediaProbe.java` with `android/build.py`), example configuration
with placeholders. No media files, no cookies, no tokens, no device identifiers.
`tests/test_repo_hygiene.py` fails if a published file contains a user home path,
a private IPv4 address, a bearer token, a client secret or a private key.

## Reporting

Open a GitHub issue. Do not include your `config.json`, `.env`, cache or logs; if a
log excerpt is needed, replace the TV address and track ids.
