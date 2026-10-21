import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import control
from common import load_config
from youtube_match import AmbiguousMatch, Match


class MatchCommandTests(unittest.TestCase):
    ARGS = ["--artist", "The Underdog Project", "--title", "Summer Jam - Radio Edit", "--duration", "214"]

    def run_match(self, argv, **resolve_behaviour):
        out = io.StringIO()
        with patch("youtube_match.resolve", **resolve_behaviour) as resolve, redirect_stdout(out):
            code = control.match(argv, load_config())
        return code, json.loads(out.getvalue()), resolve

    def test_match_builds_spotify_like_track_and_prints_selection(self):
        clip = dict(id="B" * 11, title="The Underdog Project - Summer Jam (Radio Edit) (Official Video)")
        code, report, resolve = self.run_match(self.ARGS, return_value=(Match(clip, 120, "video", True, []), [{"id": "B" * 11}]))
        self.assertEqual(code, 0)
        self.assertEqual((report["verdict"], report["video_id"], report["url"]),
                         ("match", "B" * 11, "https://www.youtube.com/watch?v=" + "B" * 11))
        track = resolve.call_args.args[0]
        self.assertEqual((track.title, track.artists, track.duration), ("Summer Jam - Radio Edit", ("The Underdog Project",), 214))
        self.assertRegex(track.id, r"^[A-Za-z0-9]{22}$")

    def test_match_keeps_several_artists_in_order(self):
        _, _, resolve = self.run_match(["--artist", "Marshmello", "--artist", "Juice WRLD", "--title", "Bye Bye",
                                        "--duration", "108.5", "--album", "Bye Bye"], return_value=(None, []))
        track = resolve.call_args.args[0]
        self.assertEqual((track.artists, track.album), (("Marshmello", "Juice WRLD"), "Bye Bye"))

    def test_match_reports_abstention_and_no_match_with_exit_code_1(self):
        code, report, _ = self.run_match(self.ARGS, side_effect=AmbiguousMatch("Isolated unofficial source", [{"id": "x"}]))
        self.assertEqual((code, report["verdict"], report["error"], report["ranking"]),
                         (1, "abstain", "Isolated unofficial source", [{"id": "x"}]))
        code, report, _ = self.run_match(self.ARGS, return_value=(None, []))
        self.assertEqual((code, report["verdict"], report["video_id"]), (1, "no_match", None))

    def test_cli_passes_match_options_through(self):
        with patch("sys.argv", ["control.py", "match", *self.ARGS]), patch("control.match", return_value=0) as match:
            self.assertEqual(control.main(), 0)
        match.assert_called_once_with(self.ARGS)

    def test_cli_rejects_stray_options_for_other_commands(self):
        with patch("sys.argv", ["control.py", "status", "--bogus"]):
            with self.assertRaises(SystemExit):
                control.main()


if __name__ == "__main__":
    unittest.main()
