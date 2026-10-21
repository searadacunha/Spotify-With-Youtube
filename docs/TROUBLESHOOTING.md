# Troubleshooting

Start with `./smarttube check` and `./smarttube logs`. Status words in the log
(`duplicate`, `stale`, `no_match`, `error`…) are explained in
[ARCHITECTURE.md](ARCHITECTURE.md#data-flow-of-one-polling-step).

## ADB

**`adb devices` shows `offline` or nothing** — The TV slept, rebooted, or the
wireless-debugging port changed. `adb disconnect`, check the port on the TV's
Wireless debugging screen, `adb connect TV_IP:PORT`, update `tv_target` if the port
differs. The bridge reconnects by itself only to the configured `tv_target`.

**`unauthorized`** — Accept the "Allow USB debugging?" dialog on the TV (tick
*Always allow*). If it never appears, toggle USB debugging off and on.

**`adb: more than one device/emulator`** — You ran `adb` without `-s` with several
devices attached. Use `adb -s TV_IP:PORT …`. The bridge always does.

**`TV unreachable over ADB or not authorized`** in the log — Same causes as above;
the bridge retries with backoff (15 s → 300 s) and logs each attempt.

**ADB wireless connection lost after a while** — Some TVs drop network ADB when
they go to standby. Give the TV a DHCP reservation, disable aggressive standby for
testing, or expect the first poll after wake-up to reconnect.

**TV IP changed** — Edit `tv_target` in `config.json`, then
`./smarttube stop && ./smarttube start`. The deduplication state is keyed by
destination. Startup disarms sync; explicit resume is required before any sending.

## SmartTube

**SmartTube does not open** — Run the manual intent from
[SMARTTUBE.md](SMARTTUBE.md#manual-test-do-this-first). `Target package not installed`
means `smarttube_package` is wrong (`adb shell pm list packages | grep smarttube`).
`Intent rejected by Android: … unable to resolve Intent` means the package does not
handle YouTube URLs. A black screen with `Status: ok` is usually a screensaver or a
sleeping panel. The bridge never wakes the TV; wake it yourself, open SmartTube,
play a video, then explicitly `./smarttube resume` while the service is running.

**The video starts but Spotify on the TV stops** — Expected: Android gives the
audio focus to SmartTube. Opening Spotify TV disarms sync; return to SmartTube
and explicitly resume to grant control again.

**Intent accepted but the video is not available** — The intent only proves
delivery. Removed, private or geo-blocked videos still "succeed". The cache will
reuse the same id for 90 days; delete the entry (stop the service, then
`sqlite3 cache/matches.sqlite3 "DELETE FROM matches WHERE track_id='…'"`) or lower
nothing: open an issue with `logs/last-match.json`.

## Matching

**Wrong video selected** — Look at `logs/last-match.json` for the ranking and the
reasons. Check the Spotify title first: in Spotify TV's **video mode** the artist and
album fields repeat the title; switch Spotify TV to audio mode. If the candidate
has the exact edition, an official source and a close duration, the rules have no
way to prefer another: open an issue with the report. Do not add a per-track
exception; the rules are generic by design.

**Wrong version selected (remix instead of radio edit…)** — Should not happen when
the Spotify title names the edition: a conflicting edition is rejected before
scoring. If Spotify's title does **not** name the edition (same base title for two
editions, which Spotify sometimes does), the bridge can only rely on duration and
audio against an official reference of what Spotify calls the track. Report the
Spotify id, the chosen id and the report.

**No candidate found (`NO RELIABLE MATCH`)** — The bounded search (up to 4 queries
× 12 results) found no official or corroborated publication of this exact edition.
Try `./smarttube match --artist … --title … --duration …` to see the ranking and the
rejection reasons. Common causes: the official clip exists only on a label channel
without an explicit "official video" marker; Spotify names a collaboration
differently from YouTube; a live-only release. Negative decisions are reconsidered
after 24 h.

**`FAILED … retry in Ns` with an ambiguity message** — The resolution abstained
(`More than 16 plausible candidates`, `No reliable reference`, `contradictory
audio`, `Isolated unofficial source`…). Nothing is cached; the bridge retries with
backoff. The ranking is in `logs/last-match.json`.

**Audio verification failed** — `Audio yt_dlp: …` errors come from YouTube
(HTTP 403, sign-in required, format unavailable): update `yt-dlp` and `yt-dlp-ejs`
together, and make sure `node_path` resolves (`./smarttube check`). `Audio ffmpeg: …`
means `ffmpeg_path` is wrong or the download was truncated. `Audio comparison
limited to tracks between 30 and 900 seconds` and `Audio too short` are deliberate
guards. `uncertain` verdicts are not failures: the bridge refuses to guess.

**Everything is slow** — A first resolution downloads two or more audio files
(tens of seconds). Later plays of the same track hit the cache. Lower
`search_results` or raise `matching_timeout_seconds` only if resolutions time out.

## User priority (manual override)

**The service runs, Spotify changes tracks, nothing is sent; the log says
`MANUAL_OVERRIDE_ACTIVE explicit resume required`** — Normal after every start.
Open SmartTube on the TV, play any video, then `./smarttube resume` on the Mac.

**`MANUAL_OVERRIDE_ENTER <reason>` appeared and sync stopped** — You (or the TV)
left SmartTube, paused, stopped or replaced the video the bridge sent, or the TV
state could not be read. This is the intended behaviour; nothing resumes on its own.
`./smarttube status` shows the `TV control` state and the reason.

**`Resume blocked: Open SmartTube yourself before resuming`** — `resume` only
adopts a SmartTube session that is already in the foreground and playing a video
with a readable id. Start a video in SmartTube first, then run the command again.

**`SEND_BLOCKED_ALREADY_HANDLED track=…`** — This track was already sent (or an
attempt was recorded) for this TV target and package. The bridge never resends it
automatically, even after a rules change or A → B → A.

**`SEND_BLOCKED_FOREGROUND_APP app=…`** — Another app owned the screen at the moment
of the check (or the focus could not be determined); the bridge entered manual
override instead of taking over.

## Spotify

**Spotify track not detected (`IDLE`)** — With the `tv` source: Spotify TV must be
**playing** (not paused) a music track, not an ad or a podcast, and only one Spotify
session may be active. `./smarttube check` prints what the probe sees; `TV media
probe missing` means `android/classes.dex` is absent (rebuild, see INSTALLATION).
With `macos`: the desktop app must be running and playing locally; grant the
Automation permission macOS asks for. With `web`: `./smarttube login` again if the
token expired.

**`Spotify TV reports the title as artist and album`** — Spotify TV's video mode.
Choose *Switch to audio* in Spotify TV.

**`Spotify rate limited: retry after Ns`** — Web API only; the bridge waits as told.

## Host

**Mac asleep** — The log stops and resumes at wake-up; tracks played meanwhile are
missed. See [MACOS_SERVICE.md](MACOS_SERVICE.md#keeping-the-mac-awake).

**Service not running / LaunchAgent failure** — `./smarttube status`, then
`logs/launchd.log`. See [MACOS_SERVICE.md](MACOS_SERVICE.md#troubleshooting-the-agent).

**`Missing …/.venv/bin/python`** — Create the virtualenv as in INSTALLATION.md. After
a Homebrew Python upgrade, delete `.venv` and recreate it.

**`yt-dlp`, `ffmpeg` or `node` missing** — `./smarttube check` prints `NOT FOUND`
for the failing key. Install with Homebrew or set an absolute path in `config.json`.
Remember the service's `PATH` is limited to `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`.

**`Another instance is already running`** — The service holds the lock; use
`./smarttube test` for dry runs, or `./smarttube stop` before `./smarttube run`.

**`Unknown config options`** — A key in `config.json` is not in
`config.example.json` (typo or an option from another version).
