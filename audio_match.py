"""Compare actual public audio; no microphone, browser, Spotify stream or cookies."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import numpy as np
from common import check_operation, communicate_checked

RATE, HOP, FRAME = 16000, 256, 1024
METHOD = "spectral-3-windows-v1"


def features(samples):
    if samples.size < RATE * 30 or not np.isfinite(samples).all():
        raise RuntimeError("Audio too short or invalid for three independent windows")
    frames = np.lib.stride_tricks.sliding_window_view(samples, FRAME)[::HOP]
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(FRAME))) ** 2
    edges = np.unique(np.geomspace(4, 500, 49).astype(int))
    bands = np.stack([np.log1p(spectrum[:, a:b].mean(axis=1)) for a, b in zip(edges, edges[1:])], axis=1)
    if float(bands.std()) < 1e-5:
        raise RuntimeError("Silent/non-discriminating audio")
    return ((bands - bands.mean(axis=0)) / (bands.std(axis=0) + 1e-6)).astype(np.float32)


def compare_features(reference, candidate):
    """Three 12–20 s windows, fixed tempo, bounded offset; cuts/drift cause abstention."""
    seconds = HOP / RATE
    window = min(round(20 / seconds), len(reference) // 5)
    if window < round(6 / seconds):
        raise RuntimeError("Audio too short")
    results = []
    for fraction in (.2, .5, .8):
        start = int((len(reference) - window) * fraction)
        segment = reference[start:start + window].astype(np.float64)
        lo, hi = max(0, start - round(45 / seconds)), min(len(candidate), start + window + round(45 / seconds))
        search = candidate[lo:hi].astype(np.float64)
        if len(search) < window:
            return {"method": METHOD, "verdict": "uncertain", "windows": results, "reason": "truncated audio"}
        size = 1 << (len(search) + window - 1).bit_length()
        cross = np.fft.irfft((np.fft.rfft(search, n=size, axis=0)
                             * np.fft.rfft(segment[::-1], n=size, axis=0)).sum(axis=1), n=size)
        dots = cross[window - 1:len(search)]
        energy = np.r_[0., np.cumsum((search * search).sum(axis=1))]
        denom = np.sqrt(np.maximum(energy[window:] - energy[:-window], 1e-12) * (segment * segment).sum())
        correlations = dots / np.maximum(denom, 1e-12)
        best = int(correlations.argmax())
        results.append({"reference_start": round(start * seconds, 3), "seconds": round(window * seconds, 3),
                        "similarity": round(float(correlations[best]), 6), "offset": round((lo + best - start) * seconds, 3)})
    similarities = [r["similarity"] for r in results]
    drift = max(r["offset"] for r in results) - min(r["offset"] for r in results)
    # shortcut: conservative spectral agreement, not a certified whole-recording fingerprint.
    # Upgrade with a Spotify-owned reference when its API exposes usable audio for this account.
    verdict = "match" if min(similarities) >= .82 and drift <= 1.5 else "mismatch" if min(similarities) <= .45 else "uncertain"
    return {"method": METHOD, "verdict": verdict, "windows": results, "offset_drift": round(drift, 3)}


def bounded_process(args, timeout):
    """Kill yt-dlp AND its ffmpeg children on timeout or service stop."""
    check_operation()
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as process:
        out, err = communicate_checked(process, timeout)
        if process.returncode:
            raise RuntimeError(f"Audio {Path(args[0]).name}: {err.decode(errors='replace')[-400:]}")
        return out


class AudioComparator:
    def __init__(self, cfg):
        self.cfg = cfg
        self.temp = None
        self.decoded = {}

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="spotify-smarttube-audio-")
        return self

    def __exit__(self, *exc):
        self.temp.cleanup()

    def load(self, candidate, deadline):
        identifier = candidate.get("id") or ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
            raise ValueError("invalid audio videoId")
        if identifier in self.decoded:
            return self.decoded[identifier]
        if not self.temp or not 30 <= candidate["duration"] <= 900:
            raise RuntimeError("Audio comparison limited to tracks between 30 and 900 seconds")
        def budget():
            value = min(self.cfg["audio_timeout_seconds"], deadline - time.monotonic())
            if value <= 0:
                raise RuntimeError("Audio deadline exhausted")
            return value
        directory = Path(self.temp.name)
        try:
            bounded_process([sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-update",
                             "--js-runtimes", "node:" + self.cfg["node_path"],
                             "--no-playlist", "--no-progress", "--socket-timeout", "10", "--retries", "1",
                             "--extractor-retries", "1", "--max-filesize", "25M", "-f", "bestaudio",
                             "-o", str(directory / (identifier + ".%(ext)s")),
                             f"https://www.youtube.com/watch?v={identifier}"], budget())
            files = [p for p in directory.glob(identifier + ".*") if p.suffix not in {".part", ".ytdl"}]
            if len(files) != 1 or files[0].stat().st_size > 25 * 1024 * 1024:
                raise RuntimeError("Audio download missing/incomplete/too large")
            pcm = bounded_process([self.cfg["ffmpeg_path"], "-v", "error", "-i", str(files[0]),
                                   "-t", "901", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"], budget())
            samples = np.frombuffer(pcm, dtype="<f4")
            if abs(len(samples) / RATE - candidate["duration"]) > 4:
                raise RuntimeError("Downloaded audio truncated or inconsistent duration")
            self.decoded[identifier] = features(samples)
            files[0].unlink()
            return self.decoded[identifier]
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Audio comparison timeout; nothing sent") from exc

    def compare(self, reference, candidate, duration, timeout):
        deadline = time.monotonic() + timeout
        return compare_features(self.load(reference, deadline), self.load(candidate, deadline))
