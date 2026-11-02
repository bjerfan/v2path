"""Read-only GitHub release check."""
from __future__ import annotations

import json
import re
import subprocess

API = "https://api.github.com/repos/bjerfan/v2path/releases/latest"


def version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip(), re.IGNORECASE)
    return tuple(map(int, match.groups())) if match else None


def check(current: str, vpn_port: int | None = None) -> tuple[bool, str]:
    command = ["curl.exe", "--silent", "--show-error", "--fail", "--location", "--max-time", "12",
               "--header", "Accept: application/vnd.github+json", "--header", "User-Agent: V2Path-update-check", API]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode and vpn_port:
        command[1:1] = ["--proxy", f"socks5h://127.0.0.1:{vpn_port}", "--noproxy", ""]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("Could not reach GitHub to check for updates.")
    release = json.loads(result.stdout)
    tag = str(release.get("tag_name", ""))
    remote = version_tuple(tag)
    local = version_tuple(current)
    if not remote or not local or release.get("draft") or release.get("prerelease"):
        raise RuntimeError("GitHub returned an invalid latest release.")
    link = str(release.get("html_url", ""))
    if not re.fullmatch(r"https://github\.com/bjerfan/v2path/releases/tag/v?\d+\.\d+\.\d+", link, re.IGNORECASE):
        raise RuntimeError("GitHub returned an unexpected release link.")
    return remote > local, link
