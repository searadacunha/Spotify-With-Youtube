import json
import os
import re

from common import ROOT, Track, run

SCRIPT = '''
var s = Application("com.spotify.client");
if (!s.running() || s.playerState() !== "playing") {
    JSON.stringify(null);
} else {
    var t = s.currentTrack();
    JSON.stringify({id:t.id(), title:t.name(), artist:t.artist(),
                    duration:t.duration(), album:t.album()});
}
'''


def from_local(data):
    if not data or not re.fullmatch(r"spotify:track:[A-Za-z0-9]{22}", data.get("id", "")):
        return None
    return Track(data["id"].split(":")[-1], data["title"], (data["artist"],),
                 data["duration"] / 1000, data.get("album", ""))


def from_web(data):
    if not data or not data.get("is_playing") or data.get("currently_playing_type") != "track":
        return None
    t = data.get("item")
    if not t or t.get("is_local") or not t.get("id"):
        return None
    return Track(t["id"], t["name"], tuple(a["name"] for a in t["artists"]),
                 t["duration_ms"] / 1000, (t.get("album") or {}).get("name", ""))


def client_id():
    value = os.environ.get("SPOTIFY_CLIENT_ID", "")
    path = ROOT / ".env"
    if not value and path.exists():
        for line in path.read_text().splitlines():
            key, sep, val = line.partition("=")
            if sep and key.strip() == "SPOTIFY_CLIENT_ID":
                value = val.strip().strip("\"'")
    return value


def oauth():
    from spotipy.oauth2 import SpotifyPKCE
    if not client_id():
        raise RuntimeError("Set SPOTIFY_CLIENT_ID in the project .env, then run ./smarttube login")
    return SpotifyPKCE(client_id=client_id(), redirect_uri="http://127.0.0.1:8765/callback",
                       scope="user-read-currently-playing",
                       cache_path=str(ROOT / "cache/spotify-token.json"),
                       requests_timeout=10, open_browser=False)


def login():
    # Only this explicit interactive command may open a browser.
    import secrets
    import time
    import urllib.parse
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    auth = oauth()
    auth.state = secrets.token_urlsafe(32)
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(url.query)
            valid = url.path == "/callback" and q.get("state") == [auth.state]
            if valid:
                result.update(q)
            self.send_response(200 if valid else 400)
            self.end_headers()
            self.wfile.write(b"You can close this page." if valid else b"Invalid callback")

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 8765), Callback) as server:
        server.timeout = 1
        webbrowser.open(auth.get_authorize_url())
        print("Authorize Spotify in the browser (5 minutes max).")
        deadline = time.monotonic() + 300
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result.get("code"):
        raise RuntimeError("Authorization missing or denied; no token saved")
    auth.get_access_token(code=result["code"][0], check_cache=False)
    (ROOT / "cache/spotify-token.json").chmod(0o600)
    print("Spotify Web API authorized. ./smarttube start to run the service.")


class SpotifySource:
    def __init__(self, cfg):
        mode = cfg["spotify_source"]
        self.name = mode
        if mode == "auto":
            self.name = "web" if client_id() and (ROOT / "cache/spotify-token.json").exists() else "macos"
        self.auth = oauth() if self.name == "web" else None
        if self.name == "tv":
            from tv_source import TVSource
            self.tv_source = TVSource(cfg)

    def current(self):
        if self.name == "tv":
            return self.tv_source.current()
        if self.name == "macos":
            return from_local(json.loads(run(["/usr/bin/osascript", "-l", "JavaScript", "-e", SCRIPT], 10)))
        import spotipy
        token = self.auth.validate_token(self.auth.cache_handler.get_cached_token())
        if not token:
            raise RuntimeError("Spotify Web API: authorization expired/missing; ./smarttube login")
        api = spotipy.Spotify(auth=token["access_token"], requests_timeout=10,
                              retries=0, status_retries=0)
        try:
            return from_web(api.current_user_playing_track())
        except spotipy.SpotifyException as exc:
            # Never log HTTP headers, tokens or OAuth responses.
            if exc.http_status == 429:
                delay = (exc.headers or {}).get("Retry-After", "60")
                error = RuntimeError(f"Spotify rate limited: retry after {delay}s")
                try:
                    error.retry_after = max(60, min(86400, float(delay)))
                except (TypeError, ValueError):
                    error.retry_after = 60
                raise error from None
            raise RuntimeError(f"Spotify Web API HTTP {exc.http_status}") from None
