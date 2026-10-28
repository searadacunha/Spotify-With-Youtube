# Spotify With YouTube

**Choose a track in Spotify. Watch a corresponding video in SmartTube.**

A local Python bridge for **macOS + Android TV / Google TV**. It reads Spotify
playback, searches YouTube for the right title and edition, and opens the selected
video in SmartTube over ADB. It can return to Spotify for the next track when a
controlled clip ends.

[![Tests](https://github.com/searadacunha/Spotify-With-Youtube/actions/workflows/tests.yml/badge.svg)](https://github.com/searadacunha/Spotify-With-Youtube/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](requirements.txt)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[Quick start](#quick-start) · [Commands](#commands) · [Matching](#how-selection-works) ·
[Verification](#verification-and-limits) · [Documentation](#documentation)

## What it does

- **Edition-aware search:** checks artist, title, remix/live/radio-edit markers,
  duration and source metadata before accepting a clip.
- **Audio comparison when needed:** compares three windows against a YouTube
  reference publication. It does not capture Spotify's audio stream.
- **Conservative fallback:** when no verified clip is available, considers
  a popular result only if artist, song and explicit edition agree. Otherwise
  it does not send a video. `most_viewed` is never proof of audio identity.
- **Playback safeguards:** rechecks the Spotify track before sending, suppresses
  consecutive duplicate sends and pauses automation when TV control is lost.
- **Local operation:** SQLite cache, rotating logs, dry-run mode and a macOS
  LaunchAgent. The default TV source needs no Spotify token or YouTube API key.

**A fallback can still be an unverified recording, including a fan upload.**
Unknown or contradictory metadata, empty searches and network failures can leave
a track without a video; unrelated titles are no longer used as fallbacks.
The matching rules and audio scores are evidence, not a guarantee of identity.

## Quick start

### 1. Install the host tools and project

You need a Mac, Python 3.12+, and a TV on the same network with Spotify, SmartTube
and ADB debugging enabled. The Mac must remain awake while the bridge runs.

```sh
brew install python@3.12 android-platform-tools ffmpeg node

git clone https://github.com/searadacunha/Spotify-With-Youtube.git
cd Spotify-With-Youtube
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

### 2. Configure and connect the TV

```sh
cp config.example.json config.json
```

Edit `tv_target` in `config.json` to the TV's actual `host:port`. Keep the other
settings initially. Then connect, replacing the placeholder below:

```sh
adb connect TV_IP:PORT
adb devices
```

Wireless debugging may require pairing first; its pairing port and connection
port can differ. See [ADB setup](docs/ADB.md) and
[SmartTube installation](docs/SMARTTUBE.md).

### 3. Check and preview

Play a track in Spotify on the TV, then run:

```sh
./smarttube check
./smarttube test
```

`check` inspects dependencies, configuration and the TV connection. `test` runs
one matching pass without launching a video or recording a playback outcome.
It still reads the TV and writes local diagnostic logs.

### 4. Start the bridge

```sh
./smarttube start
./smarttube status
./smarttube logs
```

The service starts disarmed. **Choose a song in Spotify on the TV to activate it.**
You can also open SmartTube, play a video, then run `./smarttube resume` to adopt
that session explicitly. Returning to SmartTube alone does not re-enable it.

Full setup, optional Spotify Web API login and updates:
[Installation guide](docs/INSTALLATION.md).

## Commands

Run commands from the checkout; the launcher uses its `.venv`.

| Command | Purpose |
| --- | --- |
| `./smarttube check` | Inspect tools, source, TV and target package |
| `./smarttube test` | Resolve the current track once without sending a video |
| `./smarttube run` | Run in the foreground; stop with Ctrl-C |
| `./smarttube start` | Enable the macOS service now and at login |
| `./smarttube stop` | Stop the service and disable automatic start |
| `./smarttube status` | Show service and persisted playback state |
| `./smarttube resume` | Explicitly adopt the current SmartTube session |
| `./smarttube logs` | Follow the log; Ctrl-C stops only the log viewer |
| `./smarttube login` | Authorize the optional Spotify Web API source |

### Try the matcher without Spotify or a TV

```sh
./smarttube match \
  --artist "The Underdog Project" \
  --title "Summer Jam - Radio Edit" \
  --duration 214
```

This uses the network and the matching dependencies, but sends nothing to a TV.
Its JSON report includes the ranking, selected video URL and verdict:

| Verdict | Meaning |
| --- | --- |
| `match` | An official clip passed the current metadata/audio rules |
| `most_viewed` | A popularity-based fallback; the edition is not verified |
| `no_match` | No selectable result; exit code 1 |

Repeat `--artist` for multiple artists and add `--album` when known. Duration is
in seconds. Network or configuration errors also return a nonzero exit code.

## How selection works

1. Search for the Spotify title, artists and edition.
2. Rank candidates using title, duration and publication metadata.
3. Fetch detailed metadata; compare audio when the available evidence requires it.
4. Select an accepted official clip, or use the fallback policy below.
5. Recheck that Spotify still names the same track and that TV control is valid.
6. Open the video in SmartTube and persist the attempt to prevent duplicate sends.

The fallback selects the highest reported view count **only among candidates
consistent with song, first artist and any explicitly requested edition**.
Known mismatches, covers and contradictory metadata are excluded. If no
candidate survives, nothing is sent. This is a bounded search, not a
guarantee that Spotify's exact recording was identified.

Positive clip matches are cached for 90 days; fallback and negative decisions for
one day. Matching-policy or track-metadata changes invalidate matching decisions.
A → B → A can send A again; repeating A consecutively does not relaunch it.

Full scoring rules, reference selection and examples:
[Matching documentation](docs/MATCHING.md).

## Playback control

Spotify on the TV, or an explicit `resume`, authorizes the bridge to act. Leaving
SmartTube, pausing, losing readable state or replacing a clip normally disarms it.
A short startup grace period allows SmartTube to load after a send.

At a controlled clip's end, the bridge rechecks focus, playback position and the
control session before advancing Spotify. A pause, app switch, seek backward or
new session during the end-of-clip wait cancels that pending handoff. The checks
remain polling snapshots; inspection and dispatch are not atomic.

SmartTube autoplay within the final 30 seconds is treated as a clip ending. A
manual video change in that same window can be mistaken for autoplay. There is
no continuous synchronization of pause, volume or seek position between the apps.

## Verification and limits

| Area | Evidence and remaining work |
| --- | --- |
| Matching and delivery | Earlier author-reported Android TV runs; examples and dates are retained in [Testing](docs/TESTING.md#recorded-hardware-observations) |
| End-of-clip sequence | Five consecutive handoffs were reported on 2026-10-23; not a large-device compatibility study |
| Manual override | Automated tests cover control loss and handoff races; the full hardware checklist remains open |
| Automated checks | GitHub Actions is configured for Python 3.12/3.13 and rebuilding the Android probe |
| Other Spotify sources | macOS Desktop and Web API parsers are implemented and unit-tested; full integration validation remains limited |

Audio similarity compares YouTube publications, not Spotify's actual stream. The
thresholds are empirical and no catalogue-wide error rate has been established.
A delivered Android intent also does not prove the video actually played.

The service targets one TV and macOS. Official YouTube TV is not supported with
the same ownership checks as SmartTube. See [all limitations](docs/LIMITATIONS.md).

## Development

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests mock network requests, Spotify and ADB; audio tests use synthetic spectra.
They cover matching, time budgets, caching, stale tracks, duplicate suppression,
manual control, CLI behavior and repository hygiene. They do not certify a live
TV or the current availability of a YouTube upload.

| Module | Responsibility |
| --- | --- |
| `main.py` | Service loop, stale checks and retry handling |
| `youtube_match.py`, `audio_match.py` | Candidate scoring and audio comparison |
| `tv_source.py`, `spotify_source.py` | TV, macOS and Web API sources |
| `smarttube_adb.py` | TV ownership, handoffs and delivery |
| `storage.py` | SQLite cache and persistent state |
| `control.py` | CLI and macOS service management |

## Documentation

- [Installation](docs/INSTALLATION.md) · [ADB](docs/ADB.md) · [SmartTube](docs/SMARTTUBE.md)
- [Architecture](docs/ARCHITECTURE.md) · [Matching](docs/MATCHING.md)
- [Tests and hardware checklist](docs/TESTING.md) · [Troubleshooting](docs/TROUBLESHOOTING.md)
- [macOS service](docs/MACOS_SERVICE.md) · [Limitations](docs/LIMITATIONS.md)
- [Security and local data](docs/SECURITY.md) · [Contributing](CONTRIBUTING.md)

Local configuration, cache and logs are git-ignored. Logs can contain track names,
track IDs and the TV's network address; review them before sharing diagnostics.

## License

[MIT](LICENSE) · Copyright Benjamin Da Cunha.

Not affiliated with Spotify, Google, YouTube or SmartTube. This repository does
not distribute SmartTube or media files.
