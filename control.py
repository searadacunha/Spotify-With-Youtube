import argparse
import hashlib
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

from common import ROOT, load_config, run

LABEL = "local.spotify-smarttube"
DOMAIN = f"gui/{os.getuid()}"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
PYTHON = str(ROOT / ".venv/bin/python")
COMMANDS = ["start", "stop", "status", "resume", "test", "logs", "run", "match", "login", "check"]


def launchctl(*args):
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True)


def loaded():
    return launchctl("print", f"{DOMAIN}/{LABEL}")


def match(argv, cfg=None):
    """Resolve one track typed on the command line: no Spotify, no TV, no cache write."""
    parser = argparse.ArgumentParser(prog="smarttube match",
                                     description="Rank YouTube candidates for one track without Spotify or a TV")
    parser.add_argument("--artist", action="append", required=True, help="repeat for several artists, Spotify order")
    parser.add_argument("--title", required=True, help="exact Spotify title, e.g. 'Summer Jam - Radio Edit'")
    parser.add_argument("--duration", type=float, required=True, help="Spotify duration in seconds")
    parser.add_argument("--album", default="", help="Spotify album name (optional)")
    args = parser.parse_args(argv)
    from common import Track
    from youtube_match import AmbiguousMatch, most_viewed, resolve
    # Synthetic id: this track never comes from Spotify, so it never enters the cache.
    fake_id = hashlib.sha256("|".join([*args.artist, args.title, str(args.duration)]).encode()).hexdigest()[:22]
    track = Track(fake_id, args.title, tuple(args.artist), args.duration, args.album)
    report = {"track": asdict(track)}
    try:
        selected, ranking = resolve(track, cfg or load_config())
    except AmbiguousMatch as exc:
        selected, ranking = None, exc.ranking
        report["error"] = str(exc)
    verdict = "match" if selected and selected.tier == 1 else "most_viewed"
    if verdict == "most_viewed":
        selected = most_viewed(track, ranking, cfg or load_config()) or selected
    video_id = selected.candidate["id"] if selected else None
    report.update(verdict=verdict if selected else "no_match", video_id=video_id,
                  url="https://www.youtube.com/watch?v=" + video_id if video_id else None, ranking=ranking)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if selected else 1


def main():
    parser = argparse.ArgumentParser(description="Control the Spotify → SmartTube bridge")
    parser.add_argument("command", choices=COMMANDS)
    args, extra = parser.parse_known_args()
    if extra and args.command not in {"run", "test", "match"}:
        parser.error("Unknown options: " + " ".join(extra))
    os.umask(0o077)
    (ROOT / "logs").mkdir(exist_ok=True, mode=0o700)
    (ROOT / "cache").mkdir(exist_ok=True, mode=0o700)
    if args.command == "start":
        load_config()
        if loaded().returncode == 0:
            print("Service already loaded. ./smarttube status to inspect it.")
            return 0
        spec = {"Label": LABEL, "ProgramArguments": [PYTHON, str(ROOT / "main.py"), "--service"],
                "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
                "ThrottleInterval": 15, "ProcessType": "Background", "Nice": 5,
                "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "PYTHONUNBUFFERED": "1"},
                "StandardOutPath": str(ROOT / "logs/launchd.log"),
                "StandardErrorPath": str(ROOT / "logs/launchd.log")}
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        PLIST.write_bytes(plistlib.dumps(spec))
        for command in [("enable", f"{DOMAIN}/{LABEL}"), ("bootstrap", DOMAIN, str(PLIST))]:
            result = launchctl(*command)
            if result.returncode:
                raise RuntimeError(result.stderr.strip())
        print("Service enabled now and at every login.")
    elif args.command == "stop":
        result = launchctl("disable", f"{DOMAIN}/{LABEL}")
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        if loaded().returncode == 0:
            result = launchctl("bootout", f"{DOMAIN}/{LABEL}")
            if result.returncode:
                raise RuntimeError(result.stderr.strip())
        print("Service stopped and automatic start disabled.")
    elif args.command == "resume":
        from main import setup_logging
        from storage import Store
        from smarttube_adb import SmartTube, ManualOverride
        setup_logging()
        store = Store(ROOT / "cache/matches.sqlite3")
        try:
            SmartTube(load_config(), store).resume()
        except ManualOverride as exc:
            print(f"Resume blocked: {exc}", file=sys.stderr)
            return 1
        finally:
            store.close()
        print("TV control explicitly resumed. No video launched; service state unchanged.")
    elif args.command == "status":
        result = loaded()
        print("Service LOADED" if result.returncode == 0 else "Service STOPPED")
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if any(k in line for k in ("state =", "pid =", "last exit code =", "runs =")):
                    print(line.strip())
        print(f"Logs: {ROOT / 'logs/bridge.log'}")
        from storage import Store
        if (ROOT / "cache/matches.sqlite3").exists():
            store = Store(ROOT / "cache/matches.sqlite3")
            print("Last check:", json.dumps(store.get_state("runtime"), ensure_ascii=False))
            print("Last outcome:", json.dumps(store.get_state("last_done"), ensure_ascii=False))
            print("TV control:", json.dumps(store.get_state("tv_control") or {"manual_override": True}, ensure_ascii=False))
            store.close()
    elif args.command in {"run", "test"}:
        options = ["--once", "--dry-run"] if args.command == "test" else []
        os.execv(PYTHON, [PYTHON, str(ROOT / "main.py"), *options, *extra])
    elif args.command == "match":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
        return match(extra)
    elif args.command == "logs":
        path = ROOT / "logs/bridge.log"
        path.touch(exist_ok=True)
        os.execv("/usr/bin/tail", ["tail", "-n", "60", "-F", str(path)])
    elif args.command == "login":
        from spotify_source import login
        login()
    elif args.command == "check":
        from spotify_source import SpotifySource
        from smarttube_adb import SmartTube
        cfg = load_config()
        print("config.json:", "present" if (ROOT / "config.json").exists() else "MISSING (cp config.example.json config.json)")
        for key in ("adb_path", "ffmpeg_path", "node_path"):
            print(f"{key}:", shutil.which(cfg[key]) or f"NOT FOUND ({cfg[key]})")
        print("TV probe android/classes.dex:", "present" if (ROOT / "android/classes.dex").is_file() else "MISSING (python android/build.py)")
        print(run([PYTHON, "-m", "yt_dlp", "--ignore-config", "--version"]).strip())
        source = SpotifySource(cfg)
        print("Spotify source:", source.name)
        print("Now playing:", source.current())
        SmartTube(cfg).prepare()
        print("TV reachable and target package installed:", cfg["tv_target"], cfg["smarttube_package"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
