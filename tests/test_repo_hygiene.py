"""The published tree must not carry personal paths, private IPs or credentials."""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = [r"/Users/[a-z]", r"/home/[a-z]", r"192\.168\.\d+\.\d+", r"\b10\.\d+\.\d+\.\d+\b",
             r"\bBearer\s+[A-Za-z0-9._-]{10,}", r"client_secret\s*[=:]\s*[A-Za-z0-9]{8,}",
             r"SPOTIFY_CLIENT_ID\s*=\s*[A-Za-z0-9]{20,}", r"\bgh[pousr]_[A-Za-z0-9]{20,}",
             r"\bsk-[A-Za-z0-9]{20,}", r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY"]
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".java", ".sh", ".toml", ".cfg", ""}


def published_files():
    """Tracked plus untracked-but-not-ignored files: exactly what a commit would publish."""
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return [ROOT / line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in ROOT.rglob("*") if p.is_file() and not {".git", ".venv"} & set(p.parts)]


class RepoHygieneTests(unittest.TestCase):
    def test_no_personal_paths_private_ips_or_credentials(self):
        offenders = []
        for path in published_files():
            if path.suffix not in TEXT_SUFFIXES or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in FORBIDDEN:
                offenders += [f"{path.relative_to(ROOT)}: {m.group(0)}" for m in re.finditer(pattern, text)]
        self.assertEqual(offenders, [])

    def test_example_config_has_only_placeholders(self):
        import json
        cfg = json.loads((ROOT / "config.example.json").read_text())
        self.assertEqual(cfg["tv_target"], "192.168.x.x:5555")
        for key in ("adb_path", "ffmpeg_path", "node_path"):
            self.assertFalse(Path(cfg[key]).is_absolute(), key)


if __name__ == "__main__":
    unittest.main()
