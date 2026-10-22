# Testing

## Unit tests (no network, no hardware)

```sh
.venv/bin/python -m unittest discover -s tests -v
```

93 tests in five files. They mock yt-dlp, ADB and the sources, and use synthetic
spectra for the audio verdicts, so they run anywhere, including GitHub Actions
(`.github/workflows/tests.yml`, Python 3.12 and 3.13; a second job rebuilds
`android/classes.dex` from source and checks it is byte-identical).

| Behaviour required of the bridge | Test |
| --- | --- |
| Exact official music video wins | `test_matching.test_exact_version_clip_available` |
| Official audio when no clip exists | `test_no_clip_uses_official_audio`, `test_official_audio_beats_unofficial_video` |
| Official lyric video / visualizer accepted as tier 2 | `test_audio_visualizer_and_official_lyrics_are_valid_fallbacks` |
| Wrong remix never replaces the radio edit, even with huge views | `test_popular_remix_never_replaces_radio_edit` |
| Near-identical remix names stay distinct | `test_almost_identical_named_remixes_stay_distinct` |
| Radio edit / live / acoustic / remaster grammar | `test_version_parser_preserves_named_edit_remaster_and_live_venue` |
| Live, acoustic, slowed, extended rejected | `test_popular_remix_never_replaces_radio_edit` (sub-tests) |
| Wrong duration rejected, missing details not accepted | `test_duration_and_missing_metadata_guards` |
| Edition missing from the clip title → audio required | `test_missing_version_requires_real_audio_before_clip` |
| Apparently official clip with wrong audio → fallback | `test_wrong_audio_in_apparently_official_clip_falls_back` |
| Uncertain audio blocks sending | `test_uncertain_clip_blocks_all_sending` |
| Two references with different audio → abstain | `test_ambiguous_references_must_be_compared_and_abstain_if_different` |
| Audio failure is retried, not cached as no match | `test_audio_failure_is_not_no_match`, `test_bridge.test_search_failure_is_not_negative_cached` |
| Unofficial uploads need two independent channels | `test_unofficial_fallback_requires_independent_audio_agreement` |
| Detailed metadata contradictions (artist, track, album) | `test_wrong_artist_in_detailed_metadata_rejected`, `test_wrong_album_is_not_silently_trusted` |
| Audio matching on real spectra (offset, gain, noise, cuts) | `ActualAudioTests.*` |
| Track changes during the search → nothing sent | `test_bridge.test_track_changed_during_search`, `test_pause_during_resolution` |
| Deduplication across polls and restarts | `test_deduplication_including_restart`, `test_track_change_then_back` |
| Negative cache reconsidered on policy change | `test_no_match_does_not_loop_and_policy_change_reconsiders`, `test_scoring_upgrade_reconsiders_negative_cache_after_restart` |
| Dry run touches neither TV nor state | `test_dry_run_never_touches_tv_or_playback_state` |
| TV source parsing: video mode, ads, podcasts, missing duration, reconnect | `BoundaryTests.test_tv_*` |
| ADB targeting, reconnect, exit-code-0 errors, id injection | `test_all_adb_commands_have_target`, `test_adb_reconnect`, `test_android_zero_exit_error_is_failure`, `test_injection_id_never_reaches_adb` |
| `match` command builds a Spotify-like track, reports abstention | `test_cli.MatchCommandTests` |
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

## Integration checks (manual, need the TV)

Not run in CI. The matching, delivery and deduplication path was exercised on one
Android TV before the user-priority guard existed; **the guard itself has not been
run against a real TV yet**. Run these after installation and after any change
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

## Network check without a TV

```sh
./smarttube match --artist "The Underdog Project" --title "Summer Jam - Radio Edit" --duration 214
```

Needs the network, `ffmpeg` and `node`. Prints a JSON report; exit 0 on a match.

## Adding tests

Matching changes must add or update a case in `tests/test_matching.py` modelled on a
real track (title grammar, candidates as yt-dlp would return them, expected
decision). Keep assertions on **decisions** (accepted / tier / abstain), not on
score arithmetic alone, and never weaken an existing assertion to make a suite pass.
