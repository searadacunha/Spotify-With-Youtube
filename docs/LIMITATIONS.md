# Current limitations

Honest list of what the bridge does not do, as of rules version 7.

## Host

- **The Mac must stay awake and on the TV's network.** The service runs on the Mac;
  if it sleeps or shuts down, nothing is matched or sent until it is back. The bridge
  does not use `caffeinate` and does not change Energy settings; see
  [MACOS_SERVICE.md](MACOS_SERVICE.md#keeping-the-mac-awake) if you want to keep it awake.
- macOS only for the service and the `macos` source (launchd, AppleScript). The
  matching code is portable Python but no Linux or Windows service exists.
- One TV target per configuration.

## Sources

- The `tv` source reads **Spotify's TV app** (`com.spotify.tv.android`) only, with the
  package names compiled into the probe. Spotify TV's video mode is refused because
  it hides the artist; use audio mode.
- The `macos` and `web` sources are implemented and unit-tested but were not
  exercised end to end during this project's validation (the TV source was the one
  in use).

## Matching

- The search is bounded (up to 4 queries × 12 results): **not finding** an official
  video does not prove it does not exist.
- Decisions rest on public YouTube metadata (verified badges, channel names, Topic
  descriptions, categories). They indicate a probable official publication; they
  are not a certification of rights or authenticity.
- Spotify sometimes gives two editions the same title. Then the only separators are
  duration and audio against a YouTube reference that carries the Spotify edition;
  if none exists, the bridge abstains.
- Tier 3 (non-official) uploads are accepted only with two independent channels
  agreeing on audio, which rarely happens; most non-official-only tracks abstain.
- Unknown error rate on a large catalogue: the rules were refined on a few dozen
  real tracks.

## Audio verification

- The reference is a **YouTube publication**, never Spotify's stream (the TV does not
  expose it). The check proves "this candidate sounds like the official publication of
  the edition Spotify names", not "this candidate equals what Spotify is playing".
- Three windows of 20 s do not certify every second; a difference outside the
  windows is missed. The 0.82 / 0.45 thresholds and the 1.5 s drift limit are
  empirical, not validated on a corpus.
- Requires Node.js for yt-dlp downloads and FFmpeg for decoding; a blocked download
  or a YouTube change leads to abstention and retries, not to a guess.
- Tracks shorter than 30 s or longer than 900 s are never compared.

## Playback

- SmartTube takes the audio focus: Spotify TV **was paused** in the observed
  sessions. Playlists do not reliably advance by themselves. Opening Spotify TV
  now disarms sync; returning to SmartTube requires explicit resume. With the
  default `tv` source this means choosing tracks from the TV's own Spotify screen
  disarms the bridge every time; controlling the TV's Spotify from a phone (Spotify
  Connect) while SmartTube stays in the foreground is the intended workflow, but it
  has not been verified.
- No synchronisation of pause, seek position or volume; every video starts from
  the beginning when the track changes.
- Delivery of the intent does not guarantee playback (removed, private or
  geo-blocked video).
- Only verified on one Android TV (Android 12, API 31) with SmartTube stable; the
  new control guard is verified with mocked ADB only. Other target packages are
  blocked because the bundled probe does not expose their video identity.

## Timing and state

- Resolution is synchronous. A track change during a long resolution is only noticed
  when it finishes (then the result is discarded, see README "Stale searches"). The
  first resolution of a track can take tens of seconds; cached tracks are near
  instant.
- Attempts are persisted before dispatch. A crash or uncertain reply can therefore
  suppress a track that was not actually delivered; it cannot authorize a resend.
- Foreground/playback checks are polling snapshots (default 3 s, plus ADB latency),
  including during external matching processes and immediately before sending.
  ADB inspection and intent dispatch are not an atomic Android operation: a switch
  in the last interval cannot be ruled out, nor a leave/return between observations.
  A strict instantaneous guarantee needs TV-side event handling and atomic cooperation
  from the player. This change is verified with mocked ADB only, not on hardware.
- The guard conservatively disarms for unknown, buffering, paused or stopped playback.
  It may require explicit resume after a non-user interruption.
- A → B → A never replays A automatically for the same destination. Changing matching
  rules does not reset history. Only the old last sent outcome can be migrated;
  the previous database did not retain a complete send history.
- Cached positive matches are kept 90 days without re-checking that the video is
  still available.
