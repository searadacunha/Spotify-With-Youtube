import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import sys

from common import Track, load_config, operation_guard, parallel, run
from main import Bridge
from smarttube_adb import SmartTube, ManualOverride
from spotify_source import from_local, from_web
from tv_source import TVSource, from_tv, page_metadata
from storage import Store
from youtube_match import candidate_artists, rank, resolve, score, song_title, video_details


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.track = Track("0" * 22, "Papaoutai", ("Stromae",), 232, "Racine carrée")
        self.video = dict(id="A" * 11, title="Stromae - Papaoutai (Official Music Video)",
                          channel="Stromae", channel_is_verified=True, duration=232,
                          live_status=None, view_count=500, details_checked=True)


class StorageAndLoopTests(Fixtures):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        (Path(self.tmp.name) / "logs").mkdir()
        root_patch = patch("main.ROOT", Path(self.tmp.name))
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.store = Store(Path(self.tmp.name) / "cache.sqlite3")
        self.addCleanup(self.store.close)
        self.source, self.tv = Mock(), Mock()
        self.tv.check_control.return_value = {"generation": "test"}
        def sent(video_id, *, track_id, generation):
            self.store.record_handled(track_id, (self.cfg["tv_target"], self.cfg["smarttube_package"]), video_id, "sent")
            return True
        self.tv.send.side_effect = sent
        self.source.current.return_value = self.track
        self.store.remember(self.track, self.cfg, self.video)

    def bridge(self, dry=False):
        return Bridge(self.cfg, self.source, self.tv, self.store, dry)

    def test_dispatch_metric_does_not_claim_playback_or_include_track_details(self):
        with self.assertLogs("main", level="INFO") as captured:
            self.assertEqual(self.bridge().step(), "sent")
        records = [line.split("DISPATCH_METRIC ", 1)[1] for line in captured.output
                   if "DISPATCH_METRIC " in line]
        self.assertEqual(len(records), 1)
        data = json.loads(records[0])
        self.assertEqual(data["event"], "android_intent_sent")
        self.assertEqual(data["source"], "cache")
        self.assertFalse(data["playback_on_tv_confirmed"])
        self.assertGreaterEqual(data["seconds_from_observation"], 0)
        self.assertNotIn(self.track.title, records[0])
        self.assertNotIn(self.track.id, records[0])

    def test_cache_rechecks_policy_and_track_metadata(self):
        self.assertTrue(self.store.lookup(self.track, self.cfg)[0])
        self.assertFalse(self.store.lookup(self.track, {**self.cfg, "min_score": 150})[0])
        t = Track(self.track.id, self.track.title, self.track.artists, 400)
        self.assertFalse(self.store.lookup(t, self.cfg)[0])

    def test_cache_survives_millisecond_duration_jitter(self):
        self.assertTrue(self.store.lookup(Track(self.track.id, self.track.title, self.track.artists, 232.001, self.track.album), self.cfg)[0])

    def test_expired_cache(self):
        self.store.db.execute("UPDATE matches SET expires=0")
        self.assertFalse(self.store.lookup(self.track, self.cfg)[0])

    def test_deduplication_including_restart(self):
        bridge = self.bridge()
        self.assertEqual(bridge.step(), "sent")
        self.assertEqual(bridge.step(), "duplicate")
        self.assertEqual(self.bridge().step(), "duplicate")
        self.tv.send.assert_called_once_with(self.video["id"], track_id=self.track.id, generation="test")

    def test_track_change_then_back(self):
        bridge = self.bridge()
        bridge.step()
        other = Track("1" * 22, "Papaoutai", ("Stromae",), 232)
        self.store.remember(other, self.cfg, self.video)
        self.source.current.return_value = other
        self.assertEqual(bridge.step(), "sent")
        self.source.current.return_value = self.track
        self.assertEqual(bridge.step(), "sent")
        self.assertEqual(self.tv.send.call_count, 3)

    def test_dry_run_never_touches_tv_or_playback_state(self):
        self.assertEqual(self.bridge(True).step(), "dry_run")
        self.assertEqual(self.tv.mock_calls, [])
        self.assertIsNone(self.store.get_state("last_done"))

    def test_track_changed_during_search(self):
        self.source.current.side_effect = [self.track, Track("1" * 22, "Other", ("Other",), 200)]
        self.assertEqual(self.bridge().step(), "stale")
        self.tv.send.assert_not_called()

    def test_pause_during_resolution(self):
        self.source.current.side_effect = [self.track, None]
        self.assertEqual(self.bridge().step(), "stale")
        self.tv.send.assert_not_called()

    def test_switch_during_active_resolution_cancels_before_dispatch(self):
        self.store.db.execute("DELETE FROM matches")
        other = Track("1" * 22, "Another", ("Artist",), 200)
        # Keep this fixture semantically valid: the old fixture cached the
        # unrelated Papaoutai clip, which the stricter matcher rightly rejects.
        valid_other = {**self.video, "title": "Artist - Another (Official Music Video)",
                       "channel": "Artist", "duration": 200}
        self.store.remember(other, self.cfg, valid_other)
        self.source.current.side_effect = [self.track, other]
        bridge = self.bridge()

        def search_with_pending_check(*args):
            bridge.check_pending()  # Called periodically by the real guarded search.
            raise AssertionError("stale resolution should have been cancelled")

        with patch("main.resolve", side_effect=search_with_pending_check):
            self.assertEqual(bridge.step(), "stale")
        self.tv.send.assert_not_called()
        self.assertEqual(bridge.retry_at, 0)
        self.source.current.side_effect = None
        self.source.current.return_value = other
        self.assertEqual(bridge.step(), "sent")
        self.tv.send.assert_called_once()

    def test_pause_during_active_resolution_cancels_without_retry(self):
        self.store.db.execute("DELETE FROM matches")
        self.source.current.side_effect = [self.track, None]
        bridge = self.bridge()
        with patch("main.resolve", side_effect=lambda *_: bridge.check_pending()):
            self.assertEqual(bridge.step(), "stale")
        self.tv.send.assert_not_called()
        self.assertEqual(bridge.failures, 0)

    def test_tv_failure_blocks_without_retry(self):
        self.tv.check_control.side_effect = ManualOverride("offline")
        bridge = self.bridge()
        self.assertEqual(bridge.step(), "manual_override")
        self.assertEqual(bridge.step(), "manual_override")
        self.assertIsNone(self.store.get_state("last_done"))
        self.assertEqual(bridge.retry_at, 0)
        self.tv.send.assert_not_called()

    def test_search_failure_is_not_negative_cached(self):
        self.store.db.execute("DELETE FROM matches")
        with patch("main.resolve", side_effect=RuntimeError("network")):
            self.assertEqual(self.bridge().step(), "error")
        self.assertEqual(self.store.lookup(self.track, self.cfg), (False, None))

    def test_no_match_does_not_loop_and_policy_change_reconsiders(self):
        self.store.remember(self.track, self.cfg, None)
        bridge = self.bridge()
        self.assertEqual(bridge.step(), "no_match")
        self.assertEqual(bridge.step(), "duplicate")
        self.tv.send.assert_not_called()
        self.cfg["min_score"] += 1
        match = score(self.track, self.video, self.cfg)
        with patch("main.resolve", return_value=(match, [])):
            self.assertEqual(self.bridge().step(), "sent")

    def test_scoring_upgrade_reconsiders_negative_cache_after_restart(self):
        with patch("storage.SCORING_VERSION", -1):
            self.store.remember(self.track, self.cfg, None)
            self.assertEqual(self.bridge().step(), "no_match")
        self.assertEqual(self.store.lookup(self.track, self.cfg), (False, None))
        match = score(self.track, self.video, self.cfg)
        with patch("main.resolve", return_value=(match, [])):
            self.assertEqual(self.bridge().step(), "sent")

    def test_policy_upgrade_does_not_replay_previous_sent_video(self):
        self.assertEqual(self.bridge().step(), "sent")
        self.cfg["min_score"] += 1
        match = score(self.track, self.video, self.cfg)
        with patch("main.resolve", return_value=(match, [])):
            self.assertEqual(self.bridge().step(), "duplicate")
        self.assertEqual(self.tv.send.call_count, 1)

    def test_ambiguity_sends_most_viewed_and_caches_it_one_day(self):
        from youtube_match import AmbiguousMatch
        self.store.db.execute("DELETE FROM matches")
        ranking = [{"candidate": {**self.video, "id": "C" * 11, "view_count": 10}},
                   {"candidate": {**self.video, "id": "D" * 11, "view_count": 90}}]
        with patch("main.resolve", side_effect=AmbiguousMatch("audio conflict", ranking)):
            self.assertEqual(self.bridge().step(), "sent")
        self.assertEqual(self.tv.send.call_args.args[0], "D" * 11)
        hit, cached = self.store.lookup(self.track, self.cfg)
        self.assertEqual((hit, cached["id"], cached["fallback"]), (True, "D" * 11, True))
        expires = self.store.db.execute("SELECT expires FROM matches").fetchone()[0]
        self.assertLess(expires - time.time(), 86401)
        report = json.loads((Path(self.tmp.name) / "logs/last-match.json").read_text())
        self.assertEqual(report["error"], "audio conflict")
        # The cached fallback is reused without a new search.
        self.store.db.execute("DELETE FROM handled_tracks")
        with patch("main.resolve") as resolve:
            self.assertEqual(self.bridge().step(), "sent")
        resolve.assert_not_called()

    def test_unrelated_fallback_never_dispatches_or_negative_caches_a_video(self):
        from youtube_match import AmbiguousMatch
        self.store.db.execute("DELETE FROM matches")
        wrong = {"id": "W" * 11, "title": "Stromae - Formidable",
                 "channel": "Stromae", "view_count": 10**9}
        with patch("main.resolve", side_effect=AmbiguousMatch("no reliable audio",
                                                               [{"candidate": wrong}])):
            self.assertEqual(self.bridge().step(), "no_match")
        self.tv.send.assert_not_called()
        hit, candidate = self.store.lookup(self.track, self.cfg)
        self.assertTrue(hit)
        self.assertIsNone(candidate)

    def test_official_audio_yields_to_most_viewed_clip(self):
        audio = score(self.track, {**self.video, "id": "E" * 11, "title": "Stromae - Papaoutai (Official Audio)", "view_count": 5}, self.cfg)
        clip = {**self.video, "id": "F" * 11, "title": "Stromae - Papaoutai", "channel": "Fan", "view_count": 9000}
        self.store.db.execute("DELETE FROM matches")
        with patch("main.resolve", return_value=(audio, [{"candidate": audio.candidate}, {"candidate": clip}])):
            self.assertEqual(self.bridge().step(), "sent")
        self.assertEqual((audio.tier, self.tv.send.call_args.args[0]), (2, "F" * 11))

    def test_old_reviewed_match_does_not_survive_policy_upgrade(self):
        candidate = {**self.video, "reviewed_track_id": self.track.id}
        with patch("storage.SCORING_VERSION", -1):
            self.store.remember(self.track, self.cfg, candidate)
        self.assertEqual(self.store.lookup(self.track, self.cfg), (False, None))
        self.cfg["min_score"] = 200
        with patch("main.resolve", return_value=(None, [])):
            self.assertEqual(self.bridge().step(), "no_match")
        self.tv.send.assert_not_called()

    def test_rate_limit_is_propagated_for_retry_after(self):
        error = RuntimeError("rate limit")
        error.retry_after = 900
        self.source.current.side_effect = [self.track, error]
        with self.assertRaises(RuntimeError) as caught:
            self.bridge().step()
        self.assertEqual(caught.exception.retry_after, 900)


class BoundaryTests(unittest.TestCase):
    def tv_session(self):
        return dict(package="com.spotify.tv.android", state=3,
                    id="spotify:track:" + "2" * 22, title="Already Dead",
                    artist="Juice WRLD", duration_ms=231000, album="Fighting Demons")

    def test_tv_rejects_radio_edit_video_metadata_with_album_as_artist(self):
        session = {**self.tv_session(), "title": "Summer Jam - Radio Edit",
                   "artist": "Summer Jam", "album": "Summer Jam"}
        self.assertIsNone(from_tv([session]))

    def test_tv_reads_real_track_fields(self):
        t = from_tv([self.tv_session()])
        self.assertEqual((t.id, t.title, t.artists, t.duration, t.album),
                         ("2" * 22, "Already Dead", ("Juice WRLD",), 231, "Fighting Demons"))

    def test_tv_video_metadata_does_not_invent_artist(self):
        bad = {**self.tv_session(), "title": "Righteous", "artist": "Righteous", "album": "Righteous"}
        self.assertIsNone(from_tv([bad]))

    def test_tv_video_mode_reads_artist_from_track_page(self):
        video = {**self.tv_session(), "title": "Lucid Dreams", "artist": "Lucid Dreams", "album": "Lucid Dreams"}
        t = from_tv([video], lambda track_id: ("Juice WRLD", "Lucid Dreams") if track_id == "2" * 22 else None)
        self.assertEqual((t.id, t.title, t.artists, t.duration, t.album),
                         ("2" * 22, "Lucid Dreams", ("Juice WRLD",), 231, "Lucid Dreams"))
        self.assertIsNone(from_tv([video], lambda track_id: None))
        remix = {**video, "title": "SHAKE IT TO THE MAX (FLY) - REMIX", "artist": "SHAKE IT TO THE MAX (FLY) [REMIX]",
                 "album": "SHAKE IT TO THE MAX (FLY) [REMIX]"}
        self.assertEqual(from_tv([remix], lambda track_id: ("MOLIY", "")).artists, ("MOLIY",))

    def test_track_page_metadata_matches_tv_format(self):
        page = ('<meta property="og:description" content="Marshmello, Juice WRLD · One More Drink She Said · Song · 2023"/>'
                '<meta name="music:musician_description" content="Marshmello, Juice WRLD"/>')
        self.assertEqual(page_metadata(page), ("Marshmello, Juice WRLD", "One More Drink She Said"))
        self.assertEqual(page_metadata('<meta name="music:musician_description" content="Simon &amp; Garfunkel"/>'),
                         ("Simon & Garfunkel", ""))
        with self.assertRaises(ValueError):
            page_metadata("<html></html>")

    def test_track_page_read_once_per_track(self):
        with patch("tv_source.SmartTube"), patch("tv_source.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = \
                b'<meta name="music:musician_description" content="Juice WRLD"/>'
            source = TVSource(load_config())
            self.assertEqual(source.track_page("2" * 22), ("Juice WRLD", ""))
            self.assertEqual(source.track_page("2" * 22), ("Juice WRLD", ""))
            urlopen.side_effect = OSError("offline")
            self.assertIsNone(source.track_page("3" * 22))
            self.assertIsNone(source.track_page("3" * 22))
            self.assertEqual(urlopen.call_count, 2)

    def test_parallel_keeps_order_and_dies_with_the_tv_guard(self):
        self.assertEqual(parallel(lambda x: x * 2, [3, 1, 2]), [6, 2, 4])
        self.assertEqual(parallel(lambda x: x, []), [])

        def user_took_the_tv():
            raise ManualOverride("Foreground app is com.google.android.youtube.tv")
        started = time.monotonic()
        with operation_guard(user_took_the_tv), self.assertRaises(ManualOverride):
            parallel(lambda _: run([sys.executable, "-c", "import time; time.sleep(30)"], 60), [1, 2])
        self.assertLess(time.monotonic() - started, 5)

    def test_tv_source_does_not_use_macos_or_oauth(self):
        from spotify_source import SpotifySource
        cfg = {**load_config(), "spotify_source": "tv"}
        with patch("tv_source.TVSource") as tv, patch("spotify_source.run") as mac, patch("spotify_source.oauth") as auth:
            tv.return_value.current.return_value = from_tv([self.tv_session()])
            source = SpotifySource(cfg)
            self.assertEqual(source.name, "tv")
            self.assertEqual(source.current().title, "Already Dead")
            mac.assert_not_called()
            auth.assert_not_called()

    def test_tv_ignores_paused_ads_podcasts_and_other_players(self):
        for changes in ({"state": 2}, {"advertisement": True}, {"id": "spotify:ad:abc"},
                        {"id": "spotify:episode:" + "2" * 22}, {"package": "org.smarttube.stable"}):
            self.assertIsNone(from_tv([{**self.tv_session(), **changes}]))

    def test_tv_rejects_missing_duration_and_ambiguous_sessions(self):
        for sessions in ([{**self.tv_session(), "duration_ms": 0}],
                         [{**self.tv_session(), "duration_ms": float("nan")}],
                         [self.tv_session(), self.tv_session()], {}, [None]):
            with self.assertRaises(ValueError):
                from_tv(sessions)

    def test_tv_probe_recovers_after_disconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "android").mkdir(parents=True)
            (root / "android/classes.dex").write_bytes(b"fixture dex")
            with patch("tv_source.ROOT", root), patch("tv_source.SmartTube") as cls:
                tv = cls.return_value
                tv.cfg = load_config()
                tv.adb.side_effect = ["pushed", json.dumps([self.tv_session()]),
                                      RuntimeError("offline"), "pushed", json.dumps([self.tv_session()])]
                source = TVSource(load_config())
                self.assertEqual(source.current().duration, 231)
                with self.assertRaises(RuntimeError):
                    source.current()
                self.assertEqual(source.current().duration, 231)
                self.assertEqual(tv.prepare.call_count, 2)
                self.assertEqual(sum(c.args[0] == "push" for c in tv.adb.call_args_list), 2)

    def test_ads_paused_podcasts_are_ignored(self):
        for value in (None, {}, {"is_playing": False}, {"is_playing": True, "currently_playing_type": "ad"},
                      {"is_playing": True, "currently_playing_type": "episode"}):
            self.assertIsNone(from_web(value))
        self.assertIsNone(from_local({"id": "spotify:ad:advert"}))

    def test_local_json_duration_and_unicode(self):
        t = from_local(dict(id="spotify:track:" + "0" * 22, title='Été "bleu"', artist="A", duration=232000))
        self.assertEqual(t.duration, 232)
        self.assertEqual(t.title, 'Été "bleu"')

    def test_all_adb_commands_have_target(self):
        cfg = load_config()
        store = Mock()
        with patch("smarttube_adb.run", side_effect=["device", "package:/app/base.apk", "Status: ok"]) as runner:
            tv = SmartTube(cfg, store)
            tv.prepare()
            with patch.object(tv, "check_control", return_value={"generation": "test"}):
                tv.send("A" * 11, track_id="0" * 22, generation="test")
            for call in runner.call_args_list:
                self.assertEqual(call.args[0][:3], [cfg["adb_path"], "-s", cfg["tv_target"]])
            self.assertFalse(any("keyevent" in call.args[0] for call in runner.call_args_list))

    def test_adb_reconnect(self):
        cfg = load_config()
        with patch("smarttube_adb.run", side_effect=[RuntimeError("offline"), "connected", "device", "package:/app/base.apk"]) as runner:
            SmartTube(cfg).prepare()
            self.assertEqual(runner.call_args_list[1].args[0][-2:], ["connect", cfg["tv_target"]])

    def test_foreground_read_reconnects_after_tv_was_off(self):
        cfg = load_config()
        focus = "mCurrentFocus=Window{a u0 com.spotify.tv.android/.MainActivity}"
        with patch("smarttube_adb.run", side_effect=[RuntimeError("device not found"), RuntimeError("not found"),
                                                     "connected", "device", "package:/app/base.apk", focus]) as runner:
            self.assertEqual(SmartTube(cfg).foreground_app(), "com.spotify.tv.android")
            self.assertEqual(runner.call_args_list[2].args[0][-2:], ["connect", cfg["tv_target"]])

    def test_android_zero_exit_error_is_failure(self):
        tv = SmartTube(load_config(), Mock())
        with patch("smarttube_adb.run", return_value="Error: Activity not started, unable to resolve Intent"), \
             patch.object(tv, "check_control", return_value={"generation": "test"}):
            with self.assertRaises(ManualOverride):
                tv.send("A" * 11, track_id="0" * 22, generation="test")

    def test_missing_package(self):
        with patch("smarttube_adb.run", side_effect=["device", ""]):
            with self.assertRaises(RuntimeError):
                SmartTube(load_config()).prepare()

    def test_injection_id_never_reaches_adb(self):
        with patch("smarttube_adb.run") as runner:
            with self.assertRaises(ValueError):
                SmartTube(load_config()).send("bad;reboot", track_id="0" * 22, generation="test")
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
