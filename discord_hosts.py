"""Explicit, reversible system-wide Discord hosts workaround."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime
from urllib.parse import urlencode

DOMAINS = ("updates.discord.com", "discord.com", "cdn.discordapp.com", "gateway.discord.gg")
BEGIN = b"# V2Path Discord DNS BEGIN\r\n"
END = b"# V2Path Discord DNS END\r\n"
HOSTS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/drivers/etc/hosts"


def make_manual_backup(path: Path = HOSTS) -> Path:
    """Save an independent, timestamped snapshot; never reuse the DNS-fix backup."""
    data = path.read_bytes()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    backup = path.with_name(f"hosts.v2path.manual.{stamp}.bak")
    with backup.open("xb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    return backup


def latest_manual_backup(path: Path = HOSTS) -> Path | None:
    backups = sorted(path.parent.glob("hosts.v2path.manual.*.bak"), reverse=True)
    return backups[0] if backups else None


def restore_manual_backup(path: Path = HOSTS) -> Path:
    backup = latest_manual_backup(path)
    if backup is None:
        raise RuntimeError("No manual hosts backup exists. Use Make Backup first.")
    restored = backup.read_bytes()
    # Keep a separate recovery copy of the pre-restore state.
    current = path.read_bytes()
    recovery = path.with_name("hosts.v2path.before-restore." + datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f") + ".bak")
    with recovery.open("xb") as file:
        file.write(current)
        file.flush()
        os.fsync(file.fileno())
    with path.open("r+b") as file:
        file.write(restored)
        file.truncate()
        file.flush()
        os.fsync(file.fileno())
    _flush_dns()
    return backup


def installed(path: Path = HOSTS) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return BEGIN in data and END in data


def _existing_conflicts(data: bytes) -> list[str]:
    text = data.decode("latin-1")
    names = set()
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) > 1:
            names.update(name.lower() for name in fields[1:] if name.lower() in DOMAINS)
    return sorted(names)


def resolve_via_proxy(domain: str, port: int) -> str:
    url = "https://cloudflare-dns.com/dns-query?" + urlencode({"name": domain, "type": "A"})
    command = ["curl.exe", "--silent", "--show-error", "--fail", "--max-time", "12", "--noproxy", "",
               "--proxy", f"socks5h://127.0.0.1:{port}", "--header", "accept: application/dns-json", url]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError(f"VPN DNS lookup failed for {domain}: {result.stderr.strip() or result.returncode}")
    response = json.loads(result.stdout)
    if response.get("Status") != 0:
        raise RuntimeError(f"VPN DNS lookup failed for {domain}: DNS status {response.get('Status')}")
    answers = response.get("Answer", [])
    # CDN records often return a CNAME followed by an A record for its target.
    allowed = {domain}
    for _ in range(len(answers)):
        before = len(allowed)
        for answer in answers:
            if answer.get("type") == 5 and answer.get("name", "").rstrip(".").lower() in allowed:
                allowed.add(str(answer.get("data", "")).rstrip(".").lower())
        if len(allowed) == before:
            break
    for answer in answers:
        if answer.get("type") != 1 or answer.get("name", "").rstrip(".").lower() not in allowed:
            continue
        try:
            address = ipaddress.IPv4Address(answer["data"])
        except (ValueError, KeyError):
            continue
        if address.is_global:
            return str(address)
    raise RuntimeError(f"VPN DNS returned no public IPv4 address for {domain}.")


def _flush_dns() -> None:
    subprocess.run(["ipconfig.exe", "/flushdns"], capture_output=True, timeout=15,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def apply(port: int, path: Path = HOSTS) -> dict[str, str]:
    if not 1 <= port <= 65535:
        raise ValueError("Connect the VPN SOCKS5 proxy before fixing Discord DNS.")
    original = path.read_bytes()
    if BEGIN in original or END in original:
        raise RuntimeError("V2Path hosts entries already exist. Undo them before refreshing.")
    conflicts = _existing_conflicts(original)
    if conflicts:
        raise RuntimeError("Existing hosts entries conflict with: " + ", ".join(conflicts) + ". Remove them manually first.")
    resolved = {domain: resolve_via_proxy(domain, port) for domain in DOMAINS}
    # Preserve the entire original byte stream. Never overwrite other owners' lines.
    backup = path.with_name("hosts.v2path.backup")
    if not backup.exists():
        with backup.open("xb") as file:
            file.write(original)
    prefix = b"" if not original or original.endswith((b"\n", b"\r")) else b"\r\n"
    block = BEGIN + b"".join(f"{ip} {domain}\r\n".encode("ascii") for domain, ip in resolved.items()) + END
    try:
        with path.open("ab") as file:
            file.write(prefix + block)
            file.flush()
            os.fsync(file.fileno())
    except OSError:
        # A short write must not strand a malformed managed block.
        with path.open("r+b") as file:
            if file.read(len(original)) == original:
                file.truncate(len(original))
        raise
    _flush_dns()
    return resolved


def undo(path: Path = HOSTS) -> None:
    data = path.read_bytes()
    if data.count(BEGIN) != 1 or data.count(END) != 1:
        raise RuntimeError("V2Path hosts block is missing or damaged; no changes were made.")
    start = data.index(BEGIN)
    end = data.index(END) + len(END)
    if end < start or not re.fullmatch(rb"(?:[0-9.]+ [a-z.]+\r\n)*", data[start + len(BEGIN):end - len(END)]):
        raise RuntimeError("V2Path hosts block was edited; no changes were made.")
    with path.open("r+b") as file:
        file.seek(0)
        file.write(data[:start] + data[end:])
        file.truncate()
        file.flush()
        os.fsync(file.fileno())
    _flush_dns()
