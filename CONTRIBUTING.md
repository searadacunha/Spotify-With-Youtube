# Contributing

Thanks for helping. A few rules keep the bridge trustworthy.

## Run the tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

All tests must pass locally and in GitHub Actions before a pull request is reviewed.

## Changing the matching rules

- Every change to `youtube_match.py` or `audio_match.py` must add or update a test in
  `tests/test_matching.py`, modelled on a **real track** (Spotify title, candidates as
  yt-dlp returns them, expected decision). Say in the PR which real case motivated it.
- Never add per-track, per-channel or per-video exceptions. The rules are generic;
  if a case needs a whitelist, it needs a better rule or an abstention.
- Changes to the user-priority guard (`smarttube_adb.py`, `storage.py`, the control
  checks in `main.py`) must keep `tests/test_user_priority.py` passing and add a case
  for the new situation. The guard must fail closed: when in doubt, send nothing.
- Never weaken an assertion to make the suite pass. If a behaviour changes on
  purpose, change the test and explain why in the PR.
- Bump `SCORING_VERSION` when a rule change can alter past decisions; it invalidates
  the cache for everyone.
- Prefer abstention over a confident wrong answer. A rule that sends more videos
  but sometimes the wrong version is a regression.

## Changing hardware-facing code

ADB, the TV probe (`android/MediaProbe.java`) and the sources cannot be tested in
CI. Run the manual checks in [docs/TESTING.md](docs/TESTING.md#integration-checks-manual-need-the-tv)
and paste the relevant log lines (with your TV address replaced) in the PR. If you
change the Java source, rebuild `android/classes.dex` with `android/build.py` and
commit both.

## Documentation and hygiene

- Keep the documentation honest: label behaviour as verified, implemented,
  experimental or planned, as the README does.
- Never commit personal data: `tests/test_repo_hygiene.py` scans for home paths,
  private IPs and credentials; keep it passing.
- English for code, messages and docs.
