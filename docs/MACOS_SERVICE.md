# macOS service (launchd)

The bridge runs as a **user LaunchAgent**, label `local.spotify-smarttube`. Nothing
runs as root and no system setting is changed.

## How it is installed

`./smarttube start` generates `~/Library/LaunchAgents/local.spotify-smarttube.plist`
**from the location of the checkout** (no path is hard-coded in the repository):

| Key | Value |
| --- | --- |
| `ProgramArguments` | `<checkout>/.venv/bin/python <checkout>/main.py --service` |
| `WorkingDirectory` | `<checkout>` |
| `RunAtLoad` | true: starts at login and immediately when loaded |
| `KeepAlive` | true: restarted after a crash or a kill |
| `ThrottleInterval` | 15 s minimum between restarts |
| `ProcessType` / `Nice` | `Background` / 5 |
| `EnvironmentVariables` | `PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`, `PYTHONUNBUFFERED=1` |
| `StandardOutPath` / `StandardErrorPath` | `<checkout>/logs/launchd.log` |

Then `launchctl enable gui/<uid>/local.spotify-smarttube` and
`launchctl bootstrap gui/<uid> <plist>`. The command is idempotent: if the agent is
already loaded it says so and exits 0.

Moving the checkout requires `./smarttube stop` before and `./smarttube start` after,
so the plist is regenerated.

## Commands

| Action | Command |
| --- | --- |
| Install and start, enable at login | `./smarttube start` |
| Stop and disable at login | `./smarttube stop` |
| Restart (after a config or code change) | `./smarttube stop && ./smarttube start` |
| State, pid, last poll and last outcome | `./smarttube status` |
| Follow the log | `./smarttube logs` |
| Uninstall completely | `./smarttube stop && rm ~/Library/LaunchAgents/local.spotify-smarttube.plist` |

Manual equivalents: `launchctl print gui/$(id -u)/local.spotify-smarttube`,
`launchctl kickstart -k gui/$(id -u)/local.spotify-smarttube` (force restart).

## Behaviour

- One instance only: `cache/bridge.lock` is held with `flock`; a second `./smarttube run`
  exits with "Another instance is already running". `./smarttube test` (dry run)
  does not take the lock.
- After `SIGKILL`, launchd restarts the service (verified: pid replaced, `runs`
  counter incremented). The new process starts disarmed and requires `./smarttube resume`.
  Independent `handled_tracks` history prevents replay even after explicit resume.
- Logs: `logs/bridge.log` (application, rotating 2 MB × 4) and `logs/launchd.log`
  (anything printed before logging is initialised, for example a missing module).

## Keeping the Mac awake

The service **does not** prevent sleep and does not change Energy settings; a
sleeping Mac stops the bridge until it wakes (see [LIMITATIONS.md](LIMITATIONS.md)).
If you want the Mac to stay awake while you use the TV, do it knowingly, for example:

```sh
caffeinate -i -w $(pgrep -f "main.py --service")     # prevent idle sleep while the service lives
```

or System Settings → Energy / Battery → *Prevent automatic sleeping when the display
is off* (on power). On battery, a closed laptop lid still sleeps regardless of
`caffeinate`.

## Troubleshooting the agent

- `./smarttube start` fails with `Bootstrap failed: 5: Input/output error` or
  `Service is disabled`: run `launchctl enable gui/$(id -u)/local.spotify-smarttube`
  then `./smarttube start` again.
- Service loaded but `pid` absent and `last exit code` non-zero: read
  `logs/launchd.log`; typical causes are a missing `.venv`, a Python upgrade that
  broke the venv (recreate it), or a `config.json` error (the message is in the log).
- Nothing in `logs/bridge.log` after login: the agent may be disabled in launchd's
  override database; `launchctl print-disabled gui/$(id -u) | grep smarttube`.
