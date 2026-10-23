# SmartTube

## Why SmartTube?

SmartTube is an independent, open-source YouTube client for Android TV
(https://github.com/yuliskov/SmartTube). It is the recommended target of this
project because, in testing:

- it is built for the television form factor (remote navigation, playback screen);
- it accepts standard Android `VIEW` intents for `https://www.youtube.com/watch?v=…`
  URLs and goes straight to playback (`PlaybackActivity` in the foreground, media
  session in state *playing*);
- its behaviour on receiving an intent while already playing was predictable across
  every delivery observed during development: the new video starts, from the beginning.

SmartTube is **not** an official YouTube application and is not affiliated with
Google. This repository does not distribute its APK and does not include its code.

## Install SmartTube (from its official source)

Follow the instructions of the SmartTube project itself:
https://github.com/yuliskov/SmartTube#install. In short, on the TV you install the
APK published by that project, either through its web installer or by sideloading
with ADB (`adb -s TV_IP:PORT install SmartTube_stable.apk`). Open SmartTube once
and accept its first-run dialogs.

Check the package name the bridge needs:

```sh
adb -s TV_IP:PORT shell pm list packages | grep smarttube
# package:org.smarttube.stable
```

`org.smarttube.stable` is the default `smarttube_package`. The beta build uses a
different package name; set whatever `pm list packages` shows.

## Manual test, do this first

Before running any automation, prove that SmartTube opens a video on an ADB intent
by itself. This isolates TV, network and SmartTube from Spotify and the matching
logic: if this step fails, nothing in the bridge can work, and if the bridge later
misbehaves you know the TV side is fine.

```sh
adb -s TV_IP:PORT shell input keyevent 224
adb -s TV_IP:PORT shell am start -W \
  -a android.intent.action.VIEW \
  -d "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  -p org.smarttube.stable
```

Expected: the TV wakes, SmartTube comes to the front and plays the video; the
command prints `Status: ok` and an `Activity: org.smarttube.stable/…PlaybackActivity`
line. This is a manual diagnostic you run yourself. The automatic bridge never
wakes the TV and never opens SmartTube from another app than Spotify TV: its pre-send
guard requires Spotify TV in front, or a SmartTube session you explicitly enabled with
`./smarttube resume`, still in the foreground and still playing the video the bridge
last sent.

If `am start` prints `Error: Activity not started, unable to resolve Intent`, the
package name is wrong or SmartTube is not installed. Note that `am start` exits with
code 0 even then; read the text.

## Using the official YouTube app instead

Set in `config.json`:

```json
"smarttube_package": "com.google.android.youtube.tv"
```

**Status: planned, not usable yet.** The user-priority guard must read the target
app's media session (which video is playing) before every intent, and the bundled
probe (`android/MediaProbe.java`) only lists SmartTube stable and Spotify TV. With
any other package the guard finds no playback and fails closed, so nothing is ever
sent. Making this work needs the probe extended to the target package (then rebuilt
with `android/build.py`) and a check that the app exposes the YouTube video id as its
media id, as SmartTube does. What is known so far:

- **Verified (read-only, one Android TV 12 device):** the official YouTube TV app
  declares `android.intent.action.VIEW` intent filters for `http`/`https` with the
  authorities `youtube.com`, `www.youtube.com`, `m.youtube.com` and `youtu.be`
  (`adb shell dumpsys package com.google.android.youtube.tv`). The intent sent by the
  bridge therefore resolves to it.
- **Not verified:** whether playback starts immediately, whether the app shows a
  landing page first, how it behaves when it already plays something, and how it
  handles the audio focus against Spotify TV. None of this was tested here. Run the
  manual test above with `-p com.google.android.youtube.tv` and watch the TV.
- The Spotify side is unaffected: the probe still reads Spotify TV whatever the
  target package is.

Other YouTube clients are in the same situation: a compatible media-session probe
is required before automatic sending can be enabled for them.

## Audio focus and Spotify TV

When SmartTube starts playing, Android gives it the audio focus and Spotify TV
**is paused** (sometimes both sessions report *playing* briefly): the clip plays
in place of the song. So that songs keep following each other in Spotify, not in
SmartTube's autoplay:

- about a second before the clip it sent ends, the bridge moves Spotify TV to its
  next track (`MediaProbe next`) and brings Spotify back in front, which sends the
  next clip;
- if SmartTube's autoplay replaces the clip within its last 30 seconds (SponsorBlock
  can skip an outro), the bridge does the same;
- Spotify is never skipped while it is already playing; pause, seek and volume are
  not synchronised.

Verified on the TV on 2026-10-23: five clips in a row ended and each time Spotify
moved to its next track, whose clip was sent 14 to 22 s later. See [LIMITATIONS.md](LIMITATIONS.md).
