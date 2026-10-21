# Spotify to SmartTube

## Spotify plays the song. SmartTube opens the right video.

I wanted one simple thing:

**play music on Spotify, and have the matching music video appear automatically on my Android TV.**

Not a random YouTube result.

Not the wrong remix.

Not a live version because the title looked similar.

Not an old video relaunching after I had already changed tracks.

The exact recording playing on Spotify should map to the best available YouTube publication — preferably the official music video — and open in **SmartTube**.

That is what this project does.

---

## The result

On a real Android TV session, the bridge correctly handled examples such as:

| Spotify track | Selected on SmartTube | Result |
| --- | --- | --- |
| **Juice WRLD — Robbery** | official music video | audio verified across three windows: 0.978 / 0.979 / 0.982 |
| **The Underdog Project — Summer Jam - Radio Edit** | official Radio Edit audio | rejected the similarly titled VEVO upload because it was a different arrangement |
| **Marshmello & Juice WRLD — Bye Bye** | official lyric video | no official clip found; lyric video beat the visualizer after audio verification |

It also cancelled obsolete searches when Spotify changed tracks before the YouTube resolution finished.

And after the user-priority guard was added, the service became **fail-closed**:

> if SmartTube is no longer the foreground app, if playback was changed manually, or if the bridge can no longer prove that it still owns playback, it sends nothing.

The matching path has been exercised on real hardware.

The newer user-priority guard is implemented and unit-tested, but still needs a final end-to-end hardware validation.

---

# Why I Built It

Spotify already knows exactly what I am listening to.

YouTube usually has the official clip.

SmartTube already gives me the TV experience I want.

The missing piece was the intelligence between them.

A naïve implementation would do this:

```
artist + title
→ YouTube search
→ first result
→ open it
```

That works until it doesn't.

A track called:

**Summer Jam - Radio Edit**

is not automatically the same recording as:

**Summer Jam**

or:

**Summer Jam (Remix)**

or:

**Summer Jam (Live)**

or even a video carrying the same title but using another arrangement.

For a music automation, "close enough" is wrong.

So I built the bridge around one rule:

> **If the project cannot identify the same edition with enough confidence, it abstains.**

No reliable match is better than the wrong song.

---

# What Spotify to SmartTube Does

The pipeline is:

```
Spotify
   ↓
current track detected
   ↓
title / artist / edition normalized
   ↓
YouTube candidates searched
   ↓
official sources ranked first
   ↓
wrong remix / live / edit / cover rejected
   ↓
audio compared when metadata is ambiguous
   ↓
Spotify checked again for stale results
   ↓
TV ownership checked
   ↓
ADB VIEW intent
   ↓
SmartTube
```

The project is primarily designed for:

- **Spotify on Android TV / Google TV**
- **SmartTube as the playback target**
- **a Mac running the bridge in the background**

Spotify Desktop on macOS and the Spotify Web API also exist as optional sources.

---

# The Important Part — Matching the Right Version

This repository is not just an ADB launcher.

The interesting part is deciding **which video is actually the same recording as the Spotify track**.

The matcher uses:

- Spotify track metadata
- artist identity
- base-title normalization
- edition detection
- duration consistency
- official artist / VEVO / label-source preference
- explicit remix / live / radio-edit / remaster handling
- detailed YouTube metadata
- audio comparison when metadata is not enough
- abstention when confidence is insufficient

The selection order is:

1. **official music video of the exact Spotify edition**
2. **official audio / lyric video / visualizer of the exact edition**
3. **non-official upload only when independent evidence supports it**

Popularity and view count do not decide the match.

The most viewed upload is not necessarily the correct upload.

---

# Real Example — Summer Jam

The Spotify track was:

> **The Underdog Project — Summer Jam - Radio Edit**

A VEVO result looked attractive.

Same artist.

Same recognizable title.

Official source.

But the audio told a different story.

The measured similarities against the Radio Edit reference were approximately:

```
0.48
0.38
0.36
```

with offset drift of roughly **33 seconds**.

That candidate was rejected.

The system selected the official publication carrying the actual **Radio Edit** instead.

This is exactly the kind of mistake a normal title-only automation would make.

---

# Real Example — Juice WRLD, Robbery

The official music video is shorter than the Spotify track.

A duration-only rule could reject it.

Instead, the bridge compared three parts of the recordings.

Measured similarity:

```
0.978
0.979
0.982
```

with a stable offset.

The video was accepted.

That is the difference between checking metadata and checking identity.

---

# Audio Verification

When metadata cannot settle the decision, the bridge can compare YouTube publications directly.

It does **not** capture the Spotify audio stream.

Instead it compares a candidate against a YouTube reference publication corroborated by the Spotify metadata.

Current method:

- three windows
- 20 seconds each
- sampled around 20%, 50% and 80% of the reference
- 48 log-spaced spectral bands
- normalized cross-correlation
- offset search up to ±45 seconds

A match requires strong similarity across all windows and stable timing.

Ambiguous results do not send.

Temporary audio files are deleted after decoding.

---

# User Priority Comes First

This project originally proved that the automation could send the correct video to SmartTube.

Then I found the more important problem:

**automation must never fight the person using the TV.**

So the delivery path now starts disarmed.

After starting the service, the user must explicitly run:

```sh
./smarttube resume
```

The bridge then adopts the current SmartTube playback.

If the user:

- leaves SmartTube
- pauses or stops playback
- replaces the current video manually
- moves to another TV app
- creates an unreadable playback state

the bridge enters a persistent manual override.

It does not wake the TV.

It does not force SmartTube back to the foreground.

It does not keep sending videos.

Returning to SmartTube alone does not re-enable automation.

The user must explicitly resume it.

---

# Protection Against Old or Duplicate Sends

Music matching can take time.

Searches, metadata reads and audio comparisons may finish after Spotify has already changed tracks.

Before sending anything, the bridge reads Spotify again.

If the track changed:

```
STALE
```

The result is discarded.

The project also keeps handled-track history so that a track already sent or attempted for a destination is not automatically resent across:

- restarts
- A → B → A playback
- matching-rule changes

This exists specifically to prevent an old result from unexpectedly taking over the TV later.

---

# Architecture

```mermaid
flowchart TD
    S[Spotify] --> D[Track detection]
    D --> N[Normalize title, artist and edition]
    N --> Y[YouTube search]
    Y --> R[Candidate ranking]
    R --> A{Identity clear?}
    A -->|yes| V[Selected candidate]
    A -->|no| C[Audio verification]
    C -->|match| V
    C -->|uncertain / mismatch| X[Abstain]
    V --> ST{Spotify still on same track?}
    ST -->|no| stale[Discard stale result]
    ST -->|yes| G{SmartTube control still valid?}
    G -->|no| M[Manual override]
    G -->|yes| I[ADB VIEW intent]
    I --> TV[SmartTube]
```

Main modules:

| File | Role |
| --- | --- |
| `main.py` | service loop, stale checks, deduplication |
| `tv_source.py` | Spotify metadata from Android TV |
| `spotify_source.py` | optional macOS / Spotify Web API sources |
| `youtube_match.py` | YouTube search, scoring and edition logic |
| `audio_match.py` | spectral audio verification |
| `smarttube_adb.py` | Android / SmartTube delivery |
| `storage.py` | SQLite cache and persistent state |
| `control.py` | CLI and macOS LaunchAgent |

Full architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

# Quick Start

## 1. Requirements

You need:

- macOS
- Python 3.12+
- Android TV / Google TV
- ADB debugging enabled
- SmartTube installed
- Spotify available as a source
- FFmpeg
- Node.js

Install the host tools:

```sh
brew install android-platform-tools ffmpeg node
```

## 2. Clone

```sh
git clone https://github.com/searadacunha/spotify-smarttube-bridge.git
cd spotify-smarttube-bridge

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 3. Configure the TV

```sh
cp config.example.json config.json
```

Set your TV target in `config.json`:

```json
{
  "tv_target": "192.168.x.x:5555"
}
```

Then connect:

```sh
adb connect 192.168.x.x:5555
```

Android 11+ may require wireless-debugging pairing first.

See [docs/ADB.md](docs/ADB.md).

## 4. Check everything

```sh
./smarttube check
```

This checks the local dependencies, configuration, ADB target, SmartTube package and current Spotify track.

## 5. Test without sending anything

```sh
./smarttube test
```

The entire matching path runs, but no video is launched.

## 6. Start the service

```sh
./smarttube start
```

It starts **disarmed**.

Open SmartTube yourself and play a video.

Then:

```sh
./smarttube resume
```

Now the bridge is allowed to control that SmartTube session.

Logs:

```sh
./smarttube logs
```

Full installation guide: [docs/INSTALLATION.md](docs/INSTALLATION.md)

---

# Match Any Track Without a TV

The matching engine can also be used directly:

```sh
./smarttube match \
  --artist "The Underdog Project" \
  --title "Summer Jam - Radio Edit" \
  --duration 214
```

This does not need Spotify playback or an Android TV.

It returns the candidate ranking and the selected result — or abstains.

---

# What Is Verified

The repository separates hardware observations from code that only exists in tests.

## VERIFIED on real hardware

- Spotify track detection from the TV over ADB
- version-aware YouTube matching
- official-source preference
- audio verification
- stale-result cancellation
- SmartTube delivery by Android VIEW intent
- positive / negative matching cache
- macOS background service
- dry-run mode

## IMPLEMENTED and unit-tested

- persistent user-priority / manual-override guard
- handled-track history across restarts
- Spotify Desktop source on macOS
- Spotify Web API source
- direct `./smarttube match` command

The user-priority guard was added after the earlier real-TV matching runs.

It has mocked-ADB coverage but has not yet completed its final hardware validation.

That distinction is deliberate.

---

# Tests

Run:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Current suite:

**93 unit tests**

covering:

- title and edition grammar
- remix / live / radio-edit handling
- scoring
- official-source rules
- duration checks
- album conflicts
- audio-verification verdicts
- abstention
- cache
- deduplication
- stale-result cancellation
- dry-run isolation
- ADB targeting
- ADB failures
- user-priority guard
- repository hygiene

GitHub Actions runs the suite on Python **3.12 and 3.13** and rebuilds the Android probe from source.

See [docs/TESTING.md](docs/TESTING.md).

---

# Logs Tell You Why

A normal decision is explainable.

Example:

```
SPOTIFY ... | The Underdog Project — Summer Jam - Radio Edit
CANDIDATE 01 ... OK
CANDIDATE 02 ... REJECTED | audio mismatch
SELECTED c0AwLOvF9MA | Summer Jam (Radio Edit)
SENT video=c0AwLOvF9MA
```

Other important outcomes:

```
NO RELIABLE MATCH
STALE
DRY-RUN
MANUAL_OVERRIDE_ENTER
MANUAL_OVERRIDE_ACTIVE
SEND_BLOCKED_FOREGROUND_APP
SEND_BLOCKED_ALREADY_HANDLED
```

The goal is not just automation.

It is automation that can explain why it acted — or why it refused to act.

---

# Why SmartTube?

SmartTube is the recommended target because it works well as an Android TV playback destination and exposes enough playback state for the current control guard.

The official YouTube TV app can receive Android VIEW intents, but the current bundled media-session probe does not provide everything required to give it the same ownership guarantees.

So official YouTube support is not claimed as equivalent today.

See [docs/SMARTTUBE.md](docs/SMARTTUBE.md).

---

# Limitations

This is not magic.

Current limits include:

- the Mac must stay awake while it hosts the service
- YouTube search is bounded and cannot prove that no better upload exists anywhere
- audio verification compares YouTube publications, not Spotify's own stream
- the audio thresholds are empirical
- Spotify → SmartTube handoff means SmartTube takes TV playback focus
- the latest manual-override guard still needs final real-TV validation
- ADB foreground-state verification and intent dispatch are separate operations, so they are not perfectly atomic

The project therefore fails closed whenever state cannot be trusted.

See [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

---

# Security

The repository contains no music or video files.

It does not require:

- a YouTube API key
- YouTube cookies
- browser automation
- a Spotify account token when using the default TV source

Local logs can contain:

- track names
- track IDs
- TV network address

They are git-ignored.

See [docs/SECURITY.md](docs/SECURITY.md).

---

# Roadmap

Possible next steps:

- final hardware validation of the user-priority guard
- Linux host support
- Raspberry Pi / always-on host
- Windows support
- multi-TV routing
- improved artist / channel verification
- optional web UI
- official YouTube target support with equivalent playback-state guarantees

---

# The Principle Behind the Project

The code is not built around:

> "Find something that looks like the song and play it."

It is built around:

> **Identify the recording. Respect the user. Send only when both are clear.**

That is the entire idea behind **Spotify to SmartTube**.

---

# Documentation

- [Installation](docs/INSTALLATION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [ADB](docs/ADB.md)
- [SmartTube](docs/SMARTTUBE.md)
- [Matching](docs/MATCHING.md)
- [Testing](docs/TESTING.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Limitations](docs/LIMITATIONS.md)
- [Security](docs/SECURITY.md)

---

# Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

Changes to matching logic should come with tests.

Per-track or per-channel hard-coded exceptions should not be used to make examples pass.

---

# License

MIT — see [LICENSE](LICENSE).

This project is not affiliated with Spotify, Google, YouTube or SmartTube.

Spotify, YouTube, Android and other trademarks belong to their respective owners.

SmartTube is an independent project. This repository does not distribute SmartTube or any media.
