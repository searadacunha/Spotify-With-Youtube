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
  if none exists, the most-viewed fallback is sent.
- Tier 3 (non-official) uploads are accepted only with two independent channels
  agreeing on audio, which rarely happens; most non-official-only tracks get the
  most-viewed fallback.
- The most-viewed fallback can show another edit, a live version, a lyric video or a
  fan upload: view count does not prove the recording.
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
  or a YouTube change leads to the most-viewed fallback (cached 24 h), not to a
  proven clip.
- Tracks shorter than 30 s or longer than 900 s are never compared.

## Playback

- SmartTube takes the audio focus: Spotify TV **was paused** in the observed
  sessions. Playlists do not reliably advance by themselves: pick the next track in
  Spotify TV, which arms the bridge while Spotify is in front. On the TV
  (2026-10-22) one song went through this path; a second one was not observed (the
  TV went offline). Controlling the TV's Spotify from a phone (Spotify Connect)
  while SmartTube stays in the foreground has not been verified either.
- No synchronisation of pause, seek position or volume; every video starts from
  the beginning when the track changes.
- Delivery of the intent does not guarantee playback (removed, private or
  geo-blocked video).
- Only verified on one Android TV (Android 12, API 31) with SmartTube stable; the
  new control guard is verified on that TV for one Spotify-in-front send. Other target packages are
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
  from the player.
- The guard conservatively disarms for unknown, buffering, paused or stopped playback.
  Going back to Spotify TV (or an explicit resume) re-arms it.
- The same track is never sent twice in a row to a destination, even after a restart
  or a rules change; A → B → A sends A again. Playing one track on repeat therefore
  shows its clip once.
- Cached positive matches are kept 90 days without re-checking that the video is
  still available.
