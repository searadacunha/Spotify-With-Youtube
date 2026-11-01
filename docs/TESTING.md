# Testing

## Unit tests (no network, no hardware)

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The suite mocks yt-dlp, ADB and the sources, and uses synthetic
spectra for the audio verdicts, so they run anywhere, including GitHub Actions
(`.github/workflows/tests.yml`, Python 3.12 and 3.13; a second job rebuilds
`android/classes.dex` from source and checks it is byte-identical).

| Behaviour required of the bridge | Test |
| --- | --- |
| Exact official music video wins | `test_matching.test_exact_version_clip_available` |
| Official audio when no clip exists | `test_no_clip_uses_official_audio`, `test_official_audio_beats_unofficial_video` |
| Official lyric video / visualizer accepted as tier 2 | `test_audio_visualizer_and_official_lyrics_are_valid_fallbacks` |
| Wrong remix cannot qualify as a verified radio-edit match | `test_popular_remix_never_replaces_radio_edit` |
| Near-identical remix names stay distinct | `test_almost_identical_named_remixes_stay_distinct` |
| Radio edit / live / acoustic / remaster grammar | `test_version_parser_preserves_named_edit_remaster_and_live_venue` |
| Live, acoustic, slowed, extended rejected | `test_popular_remix_never_replaces_radio_edit` (sub-tests) |
| Wrong duration rejected, missing details not accepted | `test_duration_and_missing_metadata_guards` |
| Edition missing from the clip title → audio required | `test_missing_version_requires_real_audio_before_clip` |
| Apparently official clip with wrong audio → fallback | `test_wrong_audio_in_apparently_official_clip_falls_back` |
| Uncertain audio blocks the resolver's exact-match verdict; the bridge may still use its fallback | `test_uncertain_clip_blocks_all_sending` |
| Two references with different audio → no exact-match verdict | `test_ambiguous_references_must_be_compared_and_abstain_if_different` |
| Audio failure is never a verified match; search failure is retried, not cached | `test_audio_failure_is_not_no_match`, `test_bridge.test_search_failure_is_not_negative_cached` |
| No verified clip → most-viewed candidate, when one exists; weaker title/artist matches are allowed | `test_most_viewed_fallback_names_the_song_and_artist`, `test_bridge.test_official_audio_yields_to_most_viewed_clip`, `test_bridge.test_ambiguity_sends_most_viewed_and_caches_it_one_day` |
| Unofficial uploads need two independent channels | `test_unofficial_fallback_requires_independent_audio_agreement` |
| Detailed metadata contradictions (artist, track, album) | `test_wrong_artist_in_detailed_metadata_rejected`, `test_wrong_album_is_not_silently_trusted` |
| Audio matching on real spectra (offset, gain, noise, cuts) | `ActualAudioTests.*` |
| Track changes during the search → nothing sent | `test_bridge.test_track_changed_during_search`, `test_pause_during_resolution` |
| Deduplication across polls and restarts | `test_deduplication_including_restart`, `test_track_change_then_back` |
| Negative cache reconsidered on policy change | `test_no_match_does_not_loop_and_policy_change_reconsiders`, `test_scoring_upgrade_reconsiders_negative_cache_after_restart` |
| Dry-run bridge does not send a video or record a playback outcome | `test_dry_run_never_touches_tv_or_playback_state` |
| TV source parsing: video mode, ads, podcasts, missing duration, reconnect | `BoundaryTests.test_tv_*` |
| ADB targeting, reconnect, exit-code-0 errors, id injection | `test_all_adb_commands_have_target`, `test_adb_reconnect`, `test_android_zero_exit_error_is_failure`, `test_injection_id_never_reaches_adb` |
| `match` command builds a Spotify-like track, reports the most-viewed fallback | `test_cli.MatchCommandTests` |
| Clip end → Spotify's next track and its clip; autoplay only counts near the end; loading clip keeps control | `test_user_priority.test_clip_end_moves_spotify_to_next_track_and_sends_its_clip`, `test_autoplay_replacing_the_clip_hands_back_only_near_its_end`, `test_clip_still_loading_after_send_keeps_control` |
| Pause, app switch, seek backward or a new control session during the clip-end wait cancels that handoff | `test_app_switch_during_clip_end_wait_cancels_handoff`, `test_pause_during_clip_end_wait_cancels_handoff`, `test_seek_back_during_clip_end_wait_keeps_current_video`, `test_new_control_session_during_clip_end_wait_cancels_old_handoff` |
| Small backward seek, a frozen position or buffering cannot be mistaken for completed playback | `test_small_seek_back_during_clip_end_wait_does_not_skip_remaining_audio`, `test_frozen_position_during_clip_end_wait_does_not_advance_spotify`, `test_buffering_during_clip_end_wait_does_not_advance_spotify` |
| Queued metadata fetches use only the remaining resolution time | `test_queued_details_share_the_overall_resolution_deadline` |
| No personal paths, private IPs or credentials in published files | `test_repo_hygiene.RepoHygieneTests` |

`load_config()` reads `config.json` when present, so tests on a configured machine
use that TV target; assertions compare against the loaded value, never a literal.

## User priority regression coverage

`test_user_priority.py` mocks every ADB call and covers explicit activation, Spotify TV
in front claiming control, official
YouTube/other apps/unknown focus, interrupted or replaced playback, persisted override,
Spotify changes and A → B → A during override, matching policy changes, cancellation
during resolution and backoff, killed subprocesses, uncertain sends, restart disarming,
legacy history migration and `resume` without launching a video or starting a service.

## Recorded hardware observations

The original project notes reported these observations on one Android TV. They
are retained as historical evidence, not results from the automated suite:

| Case | Recorded observation |
| --- | --- |
| Juice WRLD — Robbery | Official music video accepted; three audio similarities of 0.978 / 0.979 / 0.982 |
| The Underdog Project — Summer Jam - Radio Edit | A similarly titled VEVO arrangement rejected; similarities around 0.48 / 0.38 / 0.36 and about 33 s of offset drift |
| Marshmello & Juice WRLD — Bye Bye | Official lyric video selected over a visualizer after audio comparison |
| End-of-clip control, 2026-10-23 | Five consecutive clip → Spotify next track → next clip handoffs, with Spotify in front arming the bridge |

These examples do not establish a catalogue-wide error rate. The current
fallback policy can select a different edition even when the resolver rejects
that edition as an exact match. See [Matching](MATCHING.md#14-most-viewed-fallback).

## Integration checks (manual, need the TV)

Not run in CI. The historical notes cover matching, delivery, Spotify-in-front
activation and five handoffs on one Android TV. **The full manual-override
checklist, including the new handoff rechecks, still needs hardware validation.**
Run these after installation and after any change
touching ADB, the probe or the sources:

1. `./smarttube check`: tools resolved, probe present, Spotify track read, TV
   reachable, package installed.
2. Manual intent ([SMARTTUBE.md](SMARTTUBE.md#manual-test-do-this-first)).
3. `./smarttube test` while Spotify TV plays a track with a known official video:
   expect `SELECTED <id>` and `DRY-RUN`.
4. `./smarttube start` while SmartTube or another app is in front: expect
   `MANUAL_OVERRIDE_ACTIVE` and no intent, whatever Spotify plays (startup is disarmed).
5. Pick a track in Spotify on the TV: expect `MANUAL_OVERRIDE_EXIT Spotify in front`
   then `SENT`. Or open SmartTube, play any video, `./smarttube resume`: expect
   `MANUAL_OVERRIDE_EXIT explicit resume video=<id>`.
6. Change track in Spotify: expect `SENT` within the resolution time; replay the same
   track right away: expect `SEND_BLOCKED_ALREADY_HANDLED`; A → B → A: expect `SENT`.
7. Open another app on the TV: expect `SEND_BLOCKED_FOREGROUND_APP` then
   `MANUAL_OVERRIDE_ENTER`; go back to SmartTube: nothing is sent until step 5 again.
8. `./smarttube stop && ./smarttube start` while a video plays: the service must be
   disarmed again and send nothing.
9. Disconnect ADB (`adb disconnect TV_IP:PORT`) then `./smarttube resume`: expect
   `Resume blocked` until the TV is reachable again.
10. During the final seconds of a controlled clip, pause it, seek backward or
    switch apps: no pending handoff should advance Spotify or steal the focus.

## Network check without a TV

```sh
./smarttube match --artist "The Underdog Project" --title "Summer Jam - Radio Edit" --duration 214
```

Needs the network, `ffmpeg` and `node`. Prints a JSON report; exit 0 for a selected
clip or fallback, 1 for no match or an operational error. Inspect `verdict` to
distinguish a verified selection from a popularity fallback.

## Adding tests

Matching changes must add or update a case in `tests/test_matching.py` modelled on a
real track (title grammar, candidates as yt-dlp would return them, expected
decision). Keep assertions on **decisions** (accepted / tier / abstain), not on
score arithmetic alone, and never weaken an existing assertion to make a suite pass.


## Intent dispatch latency — reproducible measurement interface

Every successful ADB intent now emits one `DISPATCH_METRIC` JSON log record with
`seconds_from_observation` and whether the result came from the cache or
from a fresh search. The record deliberately excludes track titles and IDs.

```sh
python scripts/analyze_dispatch_logs.py logs/bridge.log
python -m unittest tests.test_dispatch_metrics -v
```

The output reports counts, median and nearest-rank p95 from **observed
successful sends only**. It does not measure Android video startup or audible
playback, user-perceived delay since pressing Skip, or any failed/suppressed
attempt. Current CI exercises the format with synthetic events; it does not
supply genuine TV latency results. Logs may be rotated: combine preserved log
files externally before analysis if a longer observation window is required.
