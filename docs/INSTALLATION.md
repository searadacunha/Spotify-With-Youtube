# Installation

Target: a fresh macOS machine, an Android TV / Google TV on the same network with
SmartTube and Spotify installed, and no prior setup. Follow the steps in order;
each one can be verified before the next.

## 1. Host tools

```sh
brew install android-platform-tools ffmpeg node
python3 --version        # 3.12 or newer is required (numpy pin); brew install python@3.13 otherwise
adb --version
ffmpeg -version | head -1
node --version
```

The service runs under launchd with `PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`.
Tools installed elsewhere must be given as absolute paths in `config.json`
(`adb_path`, `ffmpeg_path`, `node_path`).

## 2. Project

```sh
git clone https://github.com/searadacunha/spotify-smarttube-bridge.git
cd spotify-smarttube-bridge
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests
```

Expected: `OK` with no failures. The unit tests touch neither the network nor the TV.

## 3. TV side

1. Install SmartTube from its official source and open it once:
   [docs/SMARTTUBE.md](SMARTTUBE.md).
2. Enable developer options, ADB debugging and (Android 11+) wireless debugging,
   then pair and connect from the Mac: [docs/ADB.md](ADB.md). End state:

   ```sh
   adb devices
   # 192.168.x.x:5555   device
   ```
3. Send one video by hand to prove the TV side independently of this project:
   [docs/SMARTTUBE.md, "Manual test"](SMARTTUBE.md#manual-test-do-this-first).

## 4. Configuration

```sh
cp config.example.json config.json
```

Edit `config.json`: set `tv_target` to the exact `host:port` shown by `adb devices`.
Leave the rest unless you know why. Every key is documented in the README. The
file is git-ignored.

Optional, only for the Spotify Web API source: `cp .env.example .env`, set
`SPOTIFY_CLIENT_ID`, run `./smarttube login`.

## 5. Checks

```sh
./smarttube check
```

Prints: whether `config.json` exists, the resolved paths of `adb`, `ffmpeg`, `node`,
the TV probe presence, the yt-dlp version, the Spotify source and the track currently
playing (play something in the Spotify app of the TV first, in **audio** mode), and
confirms the TV is reachable with the target package installed.

```sh
./smarttube test
```

One dry run: reads Spotify, searches, ranks, verifies audio if needed, prints
`SELECTED …` and `DRY-RUN: no video launched`. Nothing is sent, nothing is cached.
The first resolution of a track can take tens of seconds.

## 6. Service

```sh
./smarttube start      # creates ~/Library/LaunchAgents/local.spotify-smarttube.plist for this checkout
./smarttube status
./smarttube logs       # Ctrl-C stops the tail only
```

The service starts disarmed. Pick a song in Spotify on the TV: Spotify in front arms
it. Or open SmartTube yourself, play a video, then run `./smarttube resume` to
explicitly adopt that playback (this command launches no video). Switching to any
other app disarms sync again; going back to Spotify TV re-arms it. Details and
uninstallation: [docs/MACOS_SERVICE.md](MACOS_SERVICE.md).

## Upgrading

```sh
./smarttube stop
git pull
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
./smarttube start
```

After an upgrade/start, pick a song in Spotify TV (or resume while SmartTube plays).
A new `SCORING_VERSION` invalidates cached matching decisions but never the last sent track. If yt-dlp stops
working because YouTube changed, bump `yt-dlp` **and** `yt-dlp-ejs` together to the
versions yt-dlp declares compatible.

## Rebuilding the TV probe (optional)

Only if you edit `android/MediaProbe.java`. Needs a JDK (17 recommended) and the
Android SDK command-line tools with one platform and one build-tools package:

```sh
brew install --cask android-commandlinetools
brew install openjdk@17
sdkmanager "platforms;android-34" "build-tools;35.0.0"
.venv/bin/python android/build.py      # autodetects; override with JAVA_HOME, ANDROID_SDK_ROOT, ANDROID_PLATFORM, ANDROID_BUILD_TOOLS
```

The script writes `android/classes.dex`; commit it together with the Java change.
With JDK 17, platform `android-34` and build-tools `35.0.0` the output is byte-for-byte
identical to the committed file (verified locally and in CI); newer toolchains produce
an equivalent but different binary.
