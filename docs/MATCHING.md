# Matching pipeline

How `youtube_match.py` selects a YouTube video id from a Spotify track: an official
clip accepted by the current rules, or a most-viewed fallback when candidates exist
(section 14). Everything below is read from the code (rules version `SCORING_VERSION = 7`);
numbers in parentheses are the defaults from `config.example.json`.

Guiding rule: **recording identity precedes source priority, and popularity is never
identity evidence.** A candidate is first checked for *being the same recording and
edition*; only then do official sources rank above others. View counts are read only
by the most-viewed fallback (section 14), never as identity evidence.

## 1. Spotify metadata

A `Track` has `id` (22-character Spotify id), `title`, `artists` (tuple), `duration`
(seconds, float) and `album`. All fields except `album` are mandatory; the
constructor refuses an empty title, empty artists or a non-positive duration
(`common.py`). With the default `tv` source the artists arrive as **one string**
exactly as Spotify TV displays them, for example `"Marshmello, Juice WRLD"`; the
ranking splits it only when every part is corroborated by YouTube (see §6).

## 2. Cleaning artist and title: `identity()`

`identity(text, artists=())` returns `(base_title, editions)`:

1. bracketed collaborations are removed: `(with …)`, `(feat. …)`, `[ft. …]`;
2. accents are stripped (`Beyoncé` → `beyonce`), text is case-folded and
   punctuation collapsed (`norm()`);
3. when artists are given (YouTube titles), a leading `Artist - ` / `Artist: ` /
   `Artist & Other -` prefix is removed before the dash is read as an edition separator;
4. the title is split on brackets and on ` - ` separators; format words are deleted
   (`official video`, `official audio`, `lyrics`, `visualizer`, `clip officiel`,
   `videoclipe oficial`, `hd`, `4k`…);
5. a chunk that contains an edition word (`remix`, `mix`, `edit`, `version`, `live`,
   `acoustic`, `unplugged`, `remaster(ed)`, `slowed`, `sped up`, `nightcore`,
   `instrumental`, `extended`, `clean`, `explicit`, `bootleg`, `mashup`) becomes an
   edition; the first chunk is always the base title;
6. an unbracketed conventional suffix is also split off: `… Radio Edit`,
   `… Extended Mix`, `… Remix`, `… Live`, `… Acoustic`, `… 2011 Remastered`…;
7. `remastered` is normalised to `remaster`; `feat …` tails are dropped from the base.

| Input | base title | editions |
| --- | --- | --- |
| `Summer Jam - Radio Edit` | `summer jam` | `('radio edit',)` |
| `Summer Jam - Alle Farben Remix` | `summer jam` | `('alle farben remix',)` |
| `Hate Me (with Juice WRLD)` | `hate me` | `()` |
| `Song (Live at Wembley)` | `song` | `('live at wembley',)` |
| `Song - 2011 Remastered` | `song` | `('2011 remaster',)` |
| `Song (Sped Up)` | `song` | `('sped up',)` |
| `Song - Slowed + Reverb` | `song` | `('slowed reverb',)` |
| `Song (Clean)` / `Song (Explicit)` | `song` | `('clean',)` / `('explicit',)` |
| `Radio Edit` (a song literally called that) | `radio edit` | `()` |
| `With Or Without You` | `with or without you` | `()` |
| `The Underdog Project - Summer Jam (Radio Edit) (Official Video)` with artists | `summer jam` | `('radio edit',)` |
| `The Underdog Project - Summer Jam (Original 2000 Version)` | `summer jam` | `('original 2000 version',)` |

Remix names are kept **whole**: `alle farben remix`, `alle farben club remix` and
`alle farben radio remix` are three different editions. One Portuguese alias is
built in (`os mlk` / `os muleke` → `os moleque`), limited to that phrase.

## 3. Version detection

For a candidate, `version` is:

- `exact`: the YouTube editions equal the Spotify editions (both empty counts as exact, "unspecified studio");
- `missing`: one side has no edition (typically an official clip titled without
  `(Radio Edit)`), or the YouTube side only says `original (YYYY) version`;
- `conflict`: both sides name editions and they differ.

`conflict` rejects the candidate. `missing` keeps it but **requires audio
verification** (§11). A YouTube edition that Spotify does not have (Spotify
`Summer Jam`, YouTube `Summer Jam (Remix)`) is rejected ("YouTube variant absent
from Spotify") unless it is an `original … version` label.

## 4. Search queries

`search()` runs up to four `yt-dlp` flat searches, each `ytsearch<search_results>:`
(12), `--flat-playlist --dump-single-json --skip-download`, without cookies, config
files or a JavaScript runtime:

1. `<first artist> <title> official music video`
2. `<first artist> <title> official audio`
3. `<first artist> "<base title>" "<editions>" Topic`
4. only when the title carries an edition: `<first artist> <base title> official music video`
   (the official clip of a radio edit is often titled without the edition).

Results are merged by video id. Kept fields: `id, title, channel, channel_id,
channel_is_verified, duration, live_status, view_count` (`view_count` is kept for
the reports and the most-viewed fallback). One query with no result is skipped; an
invalid response, or no result at all over every query, raises an error that is
**retried later, never cached as "no match"**. Each call is bounded by
`search_timeout_seconds` (35) and by the overall `matching_timeout_seconds` (240).

## 5. Candidate details

Every candidate that passes a permissive identity pre-filter (title and artists
plausible) is fetched individually with `yt-dlp --dump-single-json --skip-download`
to obtain `categories`, `description`, `track`, `artists`, `artist`, `album` and the
authoritative `channel_id`. The detail must return the same video id and a
consistent channel: YouTube search groups "Topic" and VEVO releases under the
official artist channel, so a differing channel id is accepted only when the
detail proves it is such an aggregation (same channel name plus auto-generated
"Provided to YouTube" description, or same title with both sides verified and in
the `Music` category). Anything else is an inconsistency and the candidate is
dropped. More than 16 plausible candidates aborts the resolution as ambiguous
rather than verifying an arbitrary subset.

## 6. Scoring: `score()`

| Criterion | Points | Reject when |
| --- | ---: | --- |
| Base title identical after normalisation | +40 | different |
| Artists: every artist appears in the title, or the first artist owns the channel (name, `… Official`, `… VEVO`, `… - Topic`) | +25 | missing/different |
| Edition identical (§3) | +20 | conflict |
| Corroborated official source (§10) | +20 | |
| Official **music video** and `prefer_official` | +10 | |
| Duration gap ≤5 s / ≤15 s / within limit | +15 / +10 / +5 | gap > `min(45 s, 20 %)` or missing |

Minimum score to send: `min_score` (100). Examples of maximum scores: official clip
of the exact edition with an exact duration = 130; official audio of the exact
edition = 120; official clip without the edition in its title, later confirmed by
audio = 130 (+20 for the verified edition).

### 7. Penalties and hard rejections

Independently of the score, a candidate is rejected when:

- the detailed `artists` contradict the Spotify artists, or the detailed `track`
  names another song or another edition;
- the title contains `cover`, `tribute`, `karaoke`, `reaction`, `parody`/`paródia`,
  `mashup`, `full album`, `compilation` or `fortnite` while the Spotify title does not;
- the duration is missing, non-finite or outside the limit;
- it is a live stream or upcoming premiere;
- the video id is malformed;
- audio verification returned `mismatch` or `uncertain`;
- `allow_audio_fallback` is false and the candidate is not a music video;
- its *potential* score (current points, plus 20 if a missing edition could still be
  confirmed by audio) stays below `min_score`.

A different **album** is not a rejection (compilations and reissues exist) but it
flags `album_conflict`: such a candidate needs an independent audio corroboration
and the conflict stays visible in the log.

### 8. Official-source bonus and tiers

`official` is true when one of these holds:

- the channel name equals the artist (optionally with `official`/`vevo`) **and** the
  channel is verified;
- the channel is the artist's VEVO channel (a leading "The" may be dropped);
- a Topic/auto-generated release: channel `<Artist> - Topic` or the artist channel,
  with `Provided to YouTube by` **and** `Auto-generated by YouTube` in the description;
- a verified **publisher** (label, director's channel…): verified badge, an explicit
  official-video marker in the title, every artist in the title, a valid channel id,
  and category `Music` (or `Entertainment` with production credits such as
  `Directed by` and the artist in the description).

`kind` is `audio` (Topic, auto-generated, `audio`, `lyrics`, `letra`, `visualizer`),
`video` (official-video marker, `music video`, `Music video by` description) or
`unknown`. Tiers: **1** official video, **2** official audio/lyric/visualizer,
**3** everything else. Ranking order is: eligible first, then tier, then score, then
video id (deterministic). A tier-2 official audio therefore always outranks a
tier-3 fan upload even with a higher score.

### 9. Duration

`limit = min(max_duration_delta_seconds, track.duration × max_duration_delta_ratio)`
= `min(45, 0.2 × duration)`. Beyond the limit the candidate is rejected. Within it,
a gap above **5 s** still forces audio verification even for official sources: a
music video with an intro, or an extended cut, must prove it contains the same
recording.

### 10. Official channels

There is **no list** of blessed channels, track ids or video ids anywhere in the
code. Everything is decided from public YouTube metadata as described in §8. This
is a heuristic about publications, not a legal certification of who owns a channel.

## 11. Audio verification

Implemented in `audio_match.py` (`METHOD = "spectral-3-windows-v1"`).

**Why.** Titles lie by omission: the official clip of a radio edit is usually
titled with the base name only, a VEVO upload titled `Summer Jam` may be a
different arrangement, two "official audio" uploads may be two masters. Metadata
can establish *plausibility*; the audio establishes *identity*.

**When.** `resolve()` compares audio when at least one of these holds:
a candidate's edition is `missing`; a duration gap is above 5 s; an album conflict
exists; the candidate is non-official (tier 3); more than one reference is
plausible; or another candidate of the same or better tier competes with the
reference. When one official candidate carries the exact edition, the exact
duration and no competitor, no audio is downloaded.

**Reference.** The reference must *explicitly* carry the Spotify edition and have a
duration within ±5 s. Preference: official audio (tier 2) → official video (tier 1)
→ any exact-edition candidate; among those, the ones without album conflict. No
reference means abstention ("No reliable reference for the Spotify edition"). A
tier-3 reference needs at least two independent channels among the exact-edition
candidates, otherwise abstention ("Isolated unofficial source").

**Method.**

1. `yt-dlp -f bestaudio --max-filesize 25M` downloads the public audio of each
   video into a private temporary directory (`--js-runtimes node:<node_path>`;
   Node.js is required by yt-dlp for YouTube downloads).
2. `ffmpeg` decodes to mono 16 kHz float PCM, at most 901 s; the decoded length must
   match the video's declared duration within 4 s, and only videos between 30 s and
   900 s are compared.
3. Features: Hann-windowed 1024-point FFT every 256 samples (16 ms), power spectrum
   averaged into 48 log-spaced bands between roughly 60 Hz and 7.8 kHz, `log1p`,
   then each band is standardised over the whole track. Silent or constant audio is
   rejected as non-discriminating.
4. Three windows of 20 s (shorter for very short tracks, never under 6 s) are taken
   at 20 %, 50 % and 80 % of the **reference**. For each window the candidate is
   searched within **±45 s** around the same position with a normalised
   cross-correlation computed by FFT (no tempo stretching). The best similarity and
   its offset are recorded.
5. Verdict: `match` if the **minimum** of the three similarities is ≥ 0.82 **and**
   the three offsets drift by ≤ 1.5 s; `mismatch` if the minimum is ≤ 0.45;
   `uncertain` otherwise, or if the candidate is too short around a window
   ("truncated audio").

The constant-offset requirement is what separates "same recording with a different
lead-in" (constant offset, accepted) from "an edit with a cut inside" (offsets
drift, refused). Encoding differences (codec, bitrate, loudness) are absorbed by
the per-band standardisation and the normalised correlation.

**Outcomes in `resolve()`.** `mismatch` rejects the candidate. `uncertain` rejects it
and, when the uncertain candidate had a better tier than the reference, aborts the
whole resolution ("Higher-priority video with undecidable audio"). Two plausible
references whose audio differs abort ("Same-edition references with
contradictory/undecidable audio"). A tier-3 or album-conflicted reference must be
corroborated by a second publication from another channel.

**Limits.** The reference is a YouTube publication, not Spotify's stream: the TV
exposes metadata, not audio. Three windows do not certify every second. The 0.82 /
0.45 thresholds were chosen conservatively on a handful of verified cases and have
not been validated on a large corpus. A blocked download, timeout or decode error
aborts the exact-match resolution. The bridge can still use already-found
candidates for the popularity fallback; it does not label that fallback as a
verified recording. A search failure before candidates exist is retried later.

Historical measured values are retained in
[Testing](TESTING.md#recorded-hardware-observations).

## 12. Abstention

`resolve()` raises `AmbiguousMatch` (ranking and error saved in
`logs/last-match.json`) for: search deadline or budget
exhausted; more than 16 plausible candidates; no reliable reference; album conflict
without a second reference; isolated unofficial source; contradictory references;
undecidable higher-priority video; audio download or decode failure; and
"insufficiently corroborated" when, after comparisons, no candidate is `accepted`.

It returns `None` when no candidate is even eligible after the detail checks.
In both cases, and when the accepted match is not a tier-1 official clip, the bridge
uses the most-viewed result when a playable candidate exists (section 14).

## 13. Cache

`storage.py` keeps `track_id → candidate` in SQLite with a fingerprint of
`SCORING_VERSION`, the track's fields and the matching options (`min_score`,
`prefer_official`, `allow_audio_fallback`, duration limits, `search_results`).
Accepted official clips expire after 90 days; most-viewed fallbacks and negative entries after
24 hours, so a clip missed through a transient error is searched again the next day.
Before use, a cached candidate is re-scored with the current configuration and
dropped unless it is still an accepted tier-1 clip or was a fallback. The audio verdict stored with the candidate is only trusted
when it was produced under the same rules version for the same Spotify track id
and names a valid reference video; evidence coming from YouTube metadata fields is
never read (a test asserts that an `audio_check` key returned by the network is
discarded).

## 14. Most-viewed fallback

When `resolve()` does not return an accepted tier-1 official
clip (no clip, official audio or lyric video only, abstention, audio download
failure), `most_viewed()` picks among the candidates already found, live and upcoming
streams excluded:

1. results whose title names the song (raw or normalized) and whose title or channel
   names the first artist;
2. otherwise results naming the song;
3. otherwise any result.

The highest `view_count` of the first non-empty group wins (log `NO VERIFIED CLIP …:
most viewed video instead`). Popularity only picks; it proves nothing about the
edition, so the fallback is cached 24 h. `NO RELIABLE MATCH` remains only when the
search returned no playable result. `./smarttube match` reports `verdict: most_viewed`.
The fallback cannot guarantee a video for every track: an empty search, network
failure or unavailable upload can prevent selection or playback.

## Worked examples

**Radio edit with a mis-titled VEVO upload.** Spotify: `Summer Jam - Radio Edit`,
213.9 s. Candidates: `Summer Jam (Radio Edit)` on `The Underdog Project - Topic`
(214 s, auto-generated) and `Summer Jam` on the VEVO channel (216 s). Both pass the
title and artist checks. The Topic upload is an exact-edition official audio with a
0.1 s gap: reference. The VEVO upload has a *missing* edition and a better tier, so
it is compared: similarities 0.48 / 0.38 / 0.36 with 33 s of offset drift →
`mismatch` → rejected. The official audio is the verified match (tier 2, score 120);
not being a clip, it yields to the most-viewed result (on 2026-10-22: `J7drvhZwPcs`,
Kontor.TV official video, 102 M views).

**Official clip shorter than the track.** Spotify: `Robbery`, 240.1 s. The official
music video is 218 s: 22 s gap, within `min(45, 48)` but above 5 s, so the clip
needs audio. Reference: the official audio (exact duration). Three windows at
0.978 / 0.979 / 0.982 with a constant 0.43 s offset → `match` → the clip is sent
(tier 1, score 120: +5 for the duration).

**No clip at all.** Spotify: `Bye Bye`, 108.5 s. The visualizer and the official
lyric video are both tier 2 exact-edition candidates; two references are compared
(0.996 similarity) and the first by rank is the verified match; not being a clip, it
yields to the most-viewed result, as it would have had they differed.

**Wrong remix, huge view count.** Spotify: `Summer Jam - Radio Edit`. Candidate:
`Summer Jam (Alle Farben Remix) (Official Video)`, 10 billion views. Editions
`('radio edit',)` vs `('alle farben remix',)` → `conflict` → rejected before any
score is considered (`tests/test_matching.py::test_popular_remix_never_replaces_radio_edit`).
It can still be sent by the most-viewed fallback when no clip of the exact edition
is proven.

**Live, acoustic, slowed, extended.** Same track, candidates titled `(Live)`,
`(Acoustic)`, `(Slowed)`, `(Extended Mix)`: all `conflict`, all rejected.

**Reissue on another album.** The official audio exists only on a compilation
album: `album_conflict`. It can still be the reference, but only if a second
exact-edition publication agrees on audio; alone it aborts
("Album conflict without a second audio reference").
