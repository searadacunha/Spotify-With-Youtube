# ADB from zero

ADB (Android Debug Bridge) is the command-line tool the bridge uses to talk to the
TV: read the Spotify media session, and start SmartTube with a YouTube URL. No app
is installed on the TV and no setting is changed by the bridge itself; you only
have to allow debugging once.

## Install on macOS

```sh
brew install android-platform-tools
adb --version
```

## Prepare the TV (Android TV / Google TV)

1. **Developer options**: Settings → System (or Device Preferences) → About → press
   **Build** seven times.
2. Settings → System → Developer options → enable **USB debugging** (on many TVs this
   also enables network debugging) and, on Android 11 or newer, **Wireless debugging**.
3. Find the TV's IP: Settings → Network & Internet → your network, or the Wireless
   debugging screen itself. Give the TV a fixed address in your router (DHCP
   reservation) so `tv_target` does not change.
4. Keep the TV awake for the first connection: a dialog **"Allow USB debugging?"**
   appears on screen; tick *Always allow from this computer* and accept.

## Connect

Android 11+ with Wireless debugging (pairing is required once per computer):

```sh
adb pair TV_IP:PAIRING_PORT        # port and code shown under Wireless debugging → Pair device with pairing code
adb connect TV_IP:ADB_PORT         # the port shown on the Wireless debugging screen (changes after reboots)
adb devices
```

Older Android TV, or TVs that expose the classic network ADB on port 5555:

```sh
adb connect 192.168.x.x:5555
adb devices
# 192.168.x.x:5555   device
```

Put the exact `host:port` from `adb devices` into `config.json` → `tv_target`.
The bridge always passes `-s <tv_target>`, so other devices or emulators attached to
your Mac are never targeted by mistake.

Notes:

- `unauthorized` in `adb devices` means the on-screen dialog was not accepted.
- `offline` usually means the TV slept or the port changed; `adb disconnect` then
  `adb connect` again. The bridge reconnects on its own when a command fails.
- The pairing step (`adb pair`) is different from the connection step; the pairing
  port and the connection port are different numbers.
- The port 5555 variant depends on the TV firmware; many Google TV devices only
  offer Wireless debugging with a random port, which the bridge accepts as any
  `host:port`.

## Useful read-only commands

```sh
adb -s TV_IP:PORT get-state
adb -s TV_IP:PORT shell pm list packages | grep -iE "smarttube|youtube|spotify"
adb -s TV_IP:PORT shell dumpsys media_session | head -50
adb -s TV_IP:PORT shell dumpsys activity activities | grep -E "mResumedActivity|topResumedActivity"
```

## More than one device

```
adb: more than one device/emulator
```

happens when you run `adb` without `-s` while several devices are attached. Use
`adb -s <serial> …` for every command, exactly as the bridge does, or disconnect the
others (`adb disconnect`).

## What the bridge runs over ADB

- `get-state`, `connect <tv_target>`: connection management.
- `shell pm path <package>`: the target app is installed.
- `push android/classes.dex /data/local/tmp/spotify-smarttube-<hash>.dex` and
  `shell CLASSPATH=… app_process /system/bin MediaProbe`: read the media sessions
  (default `tv` source only).
- `shell dumpsys window windows`: verify Spotify TV or SmartTube owns the focused window before sending.
- `shell am start -W -a android.intent.action.VIEW -d https://www.youtube.com/watch?v=<id> -p <package>`:
  open the clip from Spotify TV, or replace the video inside the SmartTube session the bridge owns.

No key event is sent (no wake-up): if the TV is asleep or SmartTube is not the
focused app with the expected video playing, the bridge disarms itself instead of
taking over the screen.
