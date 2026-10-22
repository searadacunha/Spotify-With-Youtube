"""Recording identity precedes source priority; popularity is never identity evidence."""
import json
import logging
import math
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass

from common import run
from audio_match import AudioComparator

LOG = logging.getLogger(__name__)
SCORING_VERSION = 7
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
OFFICIAL_VIDEO = r"\bofficial (?:(?:4k|8k|hd|hq|remastered) )?(?:music )?video\b|\bclip officiel\b|\bvideo (?:musical )?oficial\b|\bvideo officiel\b|\b(?:video ?clipe|clipe) oficial\b"
VERSION = r"\b(?:remix|mix|edit|version|live|acoustic|unplugged|remaster(?:ed)?|slowed|sped up|speed up|nightcore|instrumental|extended|clean|explicit|bootleg|mashup)\b"
FORMAT = r"\b(?:official (?:(?:4k|8k|hd|hq|remastered) )?(?:music video|video|audio|lyric(?:s)?(?: video)?)|(?:audio|video|clipe|videoclipe|video clipe|clip|letra) (?:oficial|officiel)|music video|lyric(?:s)?(?: video)?|visuali[sz]er|video officiel|video oficial|clip officiel)\b"
FIELDS = ("id", "title", "channel", "channel_id", "channel_is_verified", "duration", "live_status", "view_count")
DETAIL_FIELDS = (*FIELDS, "categories", "description", "track", "artists", "artist", "album")


def norm(text):
    text = unicodedata.normalize("NFKD", text or "").casefold()
    return " ".join(re.sub(r"[^\w]+", " ", "".join(c for c in text if not unicodedata.combining(c)).replace("_", " ")).split())


def phrase(needle, text):
    return bool(needle) and f" {needle} " in f" {text} "


def candidate_artists(track, title, channel, metadata_artists=()):
    names = []
    for name in track.artists:
        parts = [p.strip() for p in name.split(",") if p.strip()]
        corroborated_parts = (all(norm(p) in {norm(a) for a in metadata_artists} for p in parts)
                              or any(channel.removesuffix(" topic").replace(" ", "") in
                                     {norm(p).replace(" ", ""), norm(p).replace(" ", "") + "vevo"} for p in parts))
        if (len(parts) > 1 and (not phrase(norm(name), title + " " + channel) or corroborated_parts)
                and all(phrase(norm(p), title + " " + channel) for p in parts)):
            names.extend(parts)
        else:
            names.append(name)
    return names


def identity(text, artists=()):
    """Keep edition descriptors separate, including the remixer's complete name."""
    text = re.sub(r"[\[(]\s*(?:with|feat\.?|ft\.?|featuring)\s+[^\])]+[\])]", "", text, flags=re.I)
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    # Strip the artist prefix before parsing '-' as edition separators.
    for _ in artists:
        for artist in sorted(artists, key=len, reverse=True):
            artist = "".join(c for c in unicodedata.normalize("NFKD", artist) if not unicodedata.combining(c))
            words = re.escape(artist).replace(r"\ ", r"\s+")
            text = re.sub(r"^\s*" + words + r"\s*(?:[-–—,:&]+\s*|$)", "", text, flags=re.I)
    chunks = re.split(r"[()[\]]|\s+[-–—]\s+", text)
    base, versions = [], []
    for chunk in chunks:
        part = norm(chunk)
        part = re.sub(FORMAT, "", part)
        part = re.sub(r"\b(?:hd|hq|4k|8k|1080p|official|audio)\b", "", part).strip()
        if not part:
            continue
        if base and re.search(VERSION, part):
            versions.append(part)
        else:
            base.append(part)
    title = " ".join(base)
    # Unbracketed conventional suffixes; do not erase a literal song called 'Radio Edit'.
    tail = re.search(r"\s+((?:radio|original|extended|album|single|club) (?:edit|mix|version)|remix|live|acoustic|unplugged|slowed(?: reverb)?|sped up|nightcore|instrumental|(?:\d{4} )?remaster(?:ed)?)$", title)
    if tail:
        versions.append(tail[1])
        title = title[:tail.start()]
    title = re.sub(r"\bos (?:mlk|muleke)\b", "os moleque", title)
    title = re.split(r"\b(?:feat|ft|featuring)\b", title)[0]
    # Formatting aliases only; radio, extended and named remixes remain distinct.
    versions = [re.sub(r"\bremastered\b", "remaster", v) for v in versions]
    return " ".join(title.split()), tuple(sorted(" ".join(v.split()) for v in versions))


def song_title(text, artists=()):
    return identity(text, artists)[0]


@dataclass
class Match:
    candidate: dict
    score: float
    kind: str
    accepted: bool
    reasons: list[str]
    tier: int = 3
    eligible: bool = False
    needs_audio: bool = False
    version: str = "unknown"
    album_conflict: bool = False


def score(track, candidate, cfg):
    title, channel = norm(candidate.get("title")), norm(candidate.get("channel"))
    artists = candidate_artists(track, title, channel, candidate.get("artists") or [])
    primary = norm(artists[0])
    need, edition = identity(track.title)
    found, actual = identity(candidate.get("title") or "", artists)
    reasons, rejected = [], []
    points = 0
    if need == found and need:
        points += 40
        reasons.append("exact title +40")
    else:
        rejected.append(f"different title: {found!r} / {need!r}")
    compact = channel.replace(" ", "")
    artist_compact = primary.replace(" ", "")
    artist_channels = {norm(a).replace(" ", "") + suffix for a in artists for suffix in ("", "official", "vevo")}
    aligned = compact in artist_channels
    # VEVO frequently omits an initial 'The'; this is source evidence, never version evidence.
    vevo = any(compact in {norm(a).replace(" ", "") + "vevo", norm(a).removeprefix("the ").replace(" ", "") + "vevo"} for a in artists)
    topic = any(channel == norm(a) + " topic" for a in artists)
    if all(phrase(norm(a), title) or (i == 0 and (aligned or vevo or topic)) for i, a in enumerate(artists)):
        points += 25
        reasons.append("artists match +25")
    else:
        rejected.append("artist missing/different")
    supplied_artists = candidate.get("artists") or ([candidate["artist"]] if candidate.get("artist") else [])
    if supplied_artists and not all(any(norm(a) == norm(b) for b in supplied_artists) for a in artists):
        rejected.append("detailed metadata artists contradict")
    if candidate.get("track"):
        detail_title, detail_edition = identity(candidate["track"])
        if detail_title != need or (detail_edition and edition and detail_edition != edition):
            rejected.append("detailed metadata track/edition contradicts")
        if not actual:
            actual = detail_edition
    version = "exact" if edition == actual else "missing" if not edition or not actual else "conflict"
    # A generic 'original version' can be tested against a radio edit, never assumed identical.
    if version == "conflict" and all(re.fullmatch(r"original (?:(?:19|20)\d{2} )?version", x) for x in actual):
        version = "missing"
    if version == "conflict":
        rejected.append(f"different edition: {actual} / {edition}")
    elif version == "exact":
        points += 20
        reasons.append(f"exact edition {edition or ('unspecified studio',)} +20")
    else:
        reasons.append(f"incomplete edition {actual} / {edition}: audio comparison required")
    if not edition and actual and not all(x.startswith("original ") for x in actual):
        rejected.append("YouTube variant absent from Spotify")
    if re.search(r"\b(?:cover|tribute|karaoke|reaction|parody|parodia|mashup|full album|compilation|fortnite)\b", title) and not re.search(r"\b(?:cover|karaoke|mashup)\b", norm(track.title)):
        rejected.append("substituted content/compilation")
    desc = candidate.get("description") or ""
    official_marker = bool(re.search(OFFICIAL_VIDEO, title))
    audio_marker = bool(re.search(r"\baudio\b|\blyrics?\b|\bletra\b|\bvisuali[sz]er\b", title))
    provided = (topic or aligned) and "provided to youtube by" in desc.casefold() and "auto-generated by youtube" in desc.casefold()
    production_credits = (phrase(primary, norm(desc)) and bool(re.search(
        r"\bdirected(?:\s*\+?\s*edited)?\s+by\b|\bdirector of photography\b", desc, re.I)))
    publisher = (candidate.get("channel_is_verified") is True and official_marker
                 and (candidate.get("categories") == ["Music"] or
                      (candidate.get("categories") == ["Entertainment"] and production_credits))
                 and all(phrase(norm(a), title) for a in artists)
                 and bool(re.fullmatch(r"UC[A-Za-z0-9_-]{22}", candidate.get("channel_id") or "")))
    official = ((aligned and candidate.get("channel_is_verified") is True) or vevo or provided or publisher)
    kind = "audio" if topic or provided or audio_marker else "video" if official_marker or "music video by" in desc.casefold() or phrase("music video", title) else "unknown"
    tier = 1 if official and kind == "video" else 2 if official else 3
    if official:
        points += 20
        reasons.append("corroborated official source +20")
    if tier == 1 and cfg["prefer_official"]:
        points += 10
        reasons.append("official music video +10")
    if kind != "video" and not cfg["allow_audio_fallback"]:
        rejected.append("audio fallback disabled")
    duration = candidate.get("duration")
    delta = math.inf
    if type(duration) not in {int, float} or not math.isfinite(duration) or duration <= 0:
        rejected.append("duration missing/invalid")
    else:
        delta = abs(duration - track.duration)
        limit = min(cfg["max_duration_delta_seconds"], track.duration * cfg["max_duration_delta_ratio"])
        if delta > limit:
            rejected.append(f"duration Δ{delta:.1f}s > {limit:.1f}s")
        else:
            points += 15 if delta <= 5 else 10 if delta <= 15 else 5
            reasons.append(f"duration Δ{delta:.1f}s")
    album_conflict = bool(track.album and candidate.get("album") and norm(track.album) != norm(candidate["album"]))
    if album_conflict:
        reasons.append("different album: independent reference required (possible reissue)")
    if candidate.get("live_status") in {"is_live", "is_upcoming"}:
        rejected.append("live stream/upcoming")
    if not VIDEO_ID.fullmatch(candidate.get("id") or ""):
        rejected.append("invalid videoId")
    evidence = candidate.get("audio_check") or {}
    checked = (evidence.get("policy") == SCORING_VERSION and evidence.get("track_id") == track.id
               and evidence.get("verdict") == "match" and VIDEO_ID.fullmatch(evidence.get("reference_id") or ""))
    needs_audio = version != "exact" or delta > 5 or album_conflict or tier == 3
    if checked:
        needs_audio = False
        if version != "exact":
            points += 20
        reasons.append("audio matches on three windows against " + evidence["reference_id"])
    potential = points + (20 if version == "missing" and not checked else 0)
    if potential < cfg["min_score"]:
        rejected.append(f"score {points} < {cfg['min_score']}")
    if evidence.get("verdict") in {"mismatch", "uncertain"}:
        rejected.append("audio mismatch or undecidable")
    if not candidate.get("details_checked"):
        reasons.append("detailed metadata required")
    eligible = not rejected
    return Match(candidate, points, kind, eligible and not needs_audio and points >= cfg["min_score"] and bool(candidate.get("details_checked")),
                 reasons + rejected, tier, eligible, needs_audio, version, album_conflict)


def search(track, cfg, deadline=None):
    deadline = deadline or time.monotonic() + cfg["matching_timeout_seconds"]
    queries = [f"{track.artists[0]} {track.title} official music video",
               f"{track.artists[0]} {track.title} official audio",
               f'{track.artists[0]} "{song_title(track.title)}" "{" ".join(identity(track.title)[1])}" Topic']
    # An edition may be absent from the original clip's title.
    if identity(track.title)[1]:
        queries.append(f"{track.artists[0]} {song_title(track.title)} official music video")
    candidates = {}
    for query in dict.fromkeys(queries):
        timeout = min(cfg["search_timeout_seconds"], deadline - time.monotonic())
        if timeout <= 0:
            raise RuntimeError("Search deadline exhausted; nothing sent")
        output = run([sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-update",
                      "--flat-playlist", "--dump-single-json", "--skip-download",
                      "--socket-timeout", "10", "--retries", "1", "--extractor-retries", "1",
                      f"ytsearch{cfg['search_results']}:{query}"], timeout)
        data = json.loads(output)
        if not isinstance(data.get("entries"), list):
            raise RuntimeError("YouTube: invalid search; deferred retry")
        # The strict Topic query is often empty; the other queries still count.
        for entry in data["entries"]:
            if entry:
                candidates[entry["id"]] = {k: entry.get(k) for k in FIELDS}
    if not candidates:
        raise RuntimeError("YouTube: empty search; deferred retry")
    return list(candidates.values())


def video_details(candidate, timeout):
    identifier = candidate.get("id") or ""
    if not VIDEO_ID.fullmatch(identifier):
        raise ValueError("invalid videoId for YouTube detail check")
    output = run([sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-update",
                  "--dump-single-json", "--skip-download", "--ignore-no-formats-error",
                  "--socket-timeout", "10", "--retries", "1", "--extractor-retries", "1",
                  f"https://www.youtube.com/watch?v={identifier}"], timeout)
    data = json.loads(output)
    # YouTube search groups Topic releases under the Official Artist Channel ID.
    topic_alias = (norm(data.get("channel")) == norm(candidate.get("channel"))
                   and "provided to youtube by" in (data.get("description") or "").casefold()
                   and "auto-generated by youtube" in (data.get("description") or "").casefold()
                   and any(norm(a) == norm(data.get("channel")).removesuffix(" topic") for a in data.get("artists") or []))
    artist_alias = (norm(data.get("channel")) == norm(candidate.get("channel"))
                    and norm(data.get("title")) == norm(candidate.get("title"))
                    and data.get("channel_is_verified") is True and candidate.get("channel_is_verified") is True
                    and data.get("categories") == ["Music"])
    if (data.get("id") != identifier or
            (data.get("channel_id") != candidate.get("channel_id") and not (topic_alias or artist_alias))
            or not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", data.get("channel_id") or "")
            or not isinstance(data.get("categories"), list) or not data["categories"]):
        raise RuntimeError("YouTube: identity or category missing/inconsistent")
    if data.get("channel_id") != candidate.get("channel_id"):
        LOG.info("Aggregated artist channel (Topic/VEVO): %s → %s", candidate.get("channel_id"), data["channel_id"])
    return {**{k: data.get(k) for k in DETAIL_FIELDS}, "details_checked": True}


def rank(track, candidates, cfg):
    return sorted((score(track, c, cfg) for c in candidates),
                  key=lambda m: (not m.eligible, m.tier, -m.score, m.candidate.get("id") or ""))


class AmbiguousMatch(RuntimeError):
    def __init__(self, message, ranking):
        super().__init__(message)
        self.ranking = ranking


def resolve(track, cfg, candidates=None, comparator=None):
    deadline = time.monotonic() + cfg["matching_timeout_seconds"]
    candidates = [dict(c) for c in (search(track, cfg, deadline) if candidates is None else candidates)]
    analysis_cfg = {**cfg, "allow_audio_fallback": True}
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise RuntimeError("Resolution budget exhausted; nothing sent")
        return value
    def report():
        matches = rank(track, candidates, cfg)
        for i, m in enumerate(matches, 1):
            LOG.info("CANDIDATE %02d tier=%s score=%s %s | %s | %s | %s", i, m.tier, m.score,
                     "OK" if m.accepted else "PENDING" if m.eligible else "REJECTED",
                     m.candidate.get("id"), m.candidate.get("title"), "; ".join(m.reasons))
        return [asdict(m) for m in matches]
    try:
        remaining()
        # Details can establish a Topic source or publisher, so prefilter identity only.
        pending = []
        for c in candidates:
            probe = score(track, {**c, "details_checked": True, "categories": ["Music"],
                                 "description": "Provided to YouTube by label\nAuto-generated by YouTube."},
                          {**cfg, "min_score": 0, "allow_audio_fallback": True})
            if probe.eligible and not c.get("details_checked"):
                pending.append(c)
        if len(pending) > 16:
            raise RuntimeError("More than 16 plausible candidates: ambiguous resolution, nothing sent")
        for c in pending:
            LOG.info("DETAILS %s | %s", c["id"], c.get("title"))
            c.update(video_details(c, min(cfg["search_timeout_seconds"], remaining())))
        pool = [m for m in rank(track, candidates, analysis_cfg) if m.eligible]
        if not pool:
            return None, report()
        # The reference must explicitly match the edition and close duration; no album conflict.
        refs = [m for m in pool if m.version == "exact"
                and abs(m.candidate["duration"] - track.duration) <= 5]
        official_audio = [m for m in refs if m.tier == 2]
        official_other = [m for m in refs if m.tier == 1]
        references = official_audio or official_other or refs
        clean_refs = [m for m in references if not m.album_conflict]
        if clean_refs:
            references = clean_refs
        if not references:
            raise RuntimeError("No reliable reference for the Spotify edition; nothing sent")
        reference = references[0]
        corroborators = [m for m in refs if m != reference]
        if reference.album_conflict and not corroborators:
            raise RuntimeError("Album conflict without a second audio reference; nothing sent")
        needs_comparison = (len(references) > 1 or any(m.needs_audio for m in pool)
                            or any(m.tier <= reference.tier and m != reference for m in pool))
        if reference.tier == 3 and len({m.candidate.get('channel_id') for m in references}) < 2:
            raise RuntimeError("Isolated unofficial source: audio not corroborated; nothing sent")
        def compare_all(audio):
            for m in pool:
                if m.candidate["id"] == reference.candidate["id"]:
                    continue
                # Lower-priority unofficial uploads cannot displace an established official recording.
                if m.tier > reference.tier and reference.tier < 3 and not (reference.album_conflict and m in corroborators):
                    continue
                LOG.info("AUDIO COMPARE %s ↔ %s", m.candidate["id"], reference.candidate["id"])
                result = audio.compare(reference.candidate, m.candidate, track.duration, remaining())
                m.candidate["audio_check"] = {**result, "policy": SCORING_VERSION,
                    "track_id": track.id, "reference_id": reference.candidate["id"],
                    "reference_basis": "official_metadata" if reference.tier < 3 else "independent_uploads"}
                LOG.info("AUDIO %s : %s", m.candidate["id"], json.dumps(result, ensure_ascii=False))
                # Two equally plausible references with different audio cannot identify Spotify's recording.
                if m in references and result["verdict"] != "match":
                    raise RuntimeError("Same-edition references with contradictory/undecidable audio; nothing sent")
                if result["verdict"] == "uncertain" and m.tier < reference.tier:
                    raise RuntimeError("Higher-priority video with undecidable audio; nothing sent")
            if reference.tier == 3 or reference.album_conflict:
                other = next((m for m in corroborators if m.candidate.get("audio_check", {}).get("verdict") == "match"
                              and (reference.tier < 3 or m.candidate.get("channel_id") != reference.candidate.get("channel_id"))), None)
                if other is None:
                    raise RuntimeError("Album/source not corroborated by a second audio publication; nothing sent")
                check = other.candidate["audio_check"]
                reference.candidate["audio_check"] = {**check, "reference_id": other.candidate["id"],
                    "reference_basis": "independent_corroboration", "comparison_reference_id": reference.candidate["id"]}
        if needs_comparison:
            if comparator is not None:
                compare_all(comparator)
            else:
                with AudioComparator(cfg) as audio:
                    compare_all(audio)
        ranked = rank(track, candidates, cfg)
        chosen = next((m for m in ranked if m.accepted), None)
        if not chosen:
            raise RuntimeError("Audio/edition insufficiently corroborated; nothing sent")
        return chosen, report()
    except (RuntimeError, OSError, ValueError) as exc:
        raise AmbiguousMatch(str(exc), report()) from exc


def most_viewed(track, ranking):
    """Every song gets a video: without a verified official clip, the most-viewed result for the song.
    Popularity picks here, it never proves the recording."""
    song, artist = song_title(track.title), norm(track.artists[0].split(",")[0]).replace(" ", "")
    playable = [m["candidate"] for m in ranking if VIDEO_ID.fullmatch(m.get("candidate", {}).get("id") or "")
                and m["candidate"].get("live_status") not in {"is_live", "is_upcoming"}]
    named = [c for c in playable if phrase(song, norm(c.get("title"))) or phrase(song, song_title(c.get("title") or "", track.artists))]
    pool = [c for c in named if artist in norm(f"{c.get('title')} {c.get('channel')}").replace(" ", "")] or named or playable
    if not pool:
        return None
    best = max(pool, key=lambda c: c.get("view_count") or 0)
    return Match({**best, "fallback": True}, 0, "most_viewed", True, [f"most viewed fallback: {best.get('view_count') or 0} views"])
