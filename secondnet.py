#!/usr/bin/env python3
"""Run an Xray client through one selected Windows network adapter.

V2Path never changes the Windows route table.  It generates a temporary Xray
configuration whose proxy outbound is bound to a chosen adapter and whose only
listeners are local SOCKS5 proxies.
"""

from __future__ import annotations

import argparse
import base64
import copy
import ctypes
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile


APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR)).resolve()
TOOLS_DIR = APP_DIR / "tools"
RUNTIME_DIR = APP_DIR / "runtime"
DEFAULT_XRAY = TOOLS_DIR / "xray.exe"
BUNDLED_XRAY = BUNDLE_DIR / "tools" / "xray.exe"
BUNDLED_NODE = BUNDLE_DIR / "tools" / "node.exe"
BUNDLED_DIRECT_SOCKS = BUNDLE_DIR / "tools" / "direct-socks.js"
DEFAULT_NODE = TOOLS_DIR / "node.exe"
DEFAULT_DIRECT_SOCKS = TOOLS_DIR / "direct-socks.js"
PROXIFYRE_SOURCE_DIR = BUNDLE_DIR / "tools" / "proxifyre"
PROXIFYRE_RUNTIME_DIR = RUNTIME_DIR / "proxifyre"
PACKET_FILTER_INSTALLER = BUNDLE_DIR / "tools" / "Windows.Packet.Filter.x64.msi"
MIN_PACKET_FILTER_VERSION = (3, 6, 2, 1)
MIN_PROXIFYRE_VERSION = (2, 3, 0, 0)
PROXIFYRE_FIREWALL_RULE = "V2Path Discord UDP"
GITHUB_LATEST = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
DIRECT_PROTOCOLS = {"freedom", "blackhole", "dns", "loopback"}
USER_AGENT = "V2Path/1.0 (+https://github.com/XTLS/Xray-core)"


class V2PathError(RuntimeError):
    pass


def parse_process_names(value: str) -> list[str]:
    """Parse and validate the executable names used by per-app tunnel mode."""
    names: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,;\r\n]+", value):
        name = raw.strip().strip('"')
        if not name:
            continue
        if "*" in name or "?" in name:
            raise V2PathError("Process entries cannot contain wildcards. Use an .exe name or full path.")
        if not name.lower().endswith(".exe"):
            raise V2PathError(f"Process entry must end in .exe: {name}")
        basename = Path(name).name.casefold()
        if basename in {"secondnet.exe", "v2path.exe", "xray.exe"}:
            raise V2PathError(f"{basename} cannot be tunneled because that would create a proxy loop.")
        key = name.casefold()
        if key not in seen:
            names.append(name)
            seen.add(key)
    if not names:
        raise V2PathError("Enter at least one process, for example discord.exe or obs64.exe.")
    return names


def xray_process_names(process_names: list[str]) -> list[str]:
    """Normalize GUI entries for Xray's case-sensitive process route matcher."""
    normalized: list[str] = []
    for name in process_names:
        if "/" in name or "\\" in name:
            value = name.replace("\\", "/")
        else:
            value = Path(name).stem
        if value not in normalized:
            normalized.append(value)
    return normalized


def build_proxifyre_config(process_names: list[str], socks_port: int) -> dict[str, object]:
    if not process_names:
        raise V2PathError("Per-app routing needs at least one process.")
    if not 1 <= socks_port <= 65535:
        raise V2PathError("The SOCKS5 port must be between 1 and 65535.")
    return {
        "logLevel": "Info",
        "bypassLan": True,
        "proxies": [
            {
                "appNames": process_names,
                "socks5ProxyEndpoint": f"127.0.0.1:{socks_port}",
                # TCP is intentionally left to Proxifier. ProxiFyre is used
                # only for Discord's native voice and screen-share UDP.
                "supportedProtocols": ["UDP"],
                # Discord voice currently uses IPv4 endpoints. Explicitly blocking
                # unsupported IPv6 destinations prevents an unproxied IPv6 leak and
                # lets Discord fall back to IPv4 on dual-stack machines.
                "supportedAddressFamilies": ["IPv4"],
            }
        ],
        "excludes": ["V2Path.exe", "xray.exe", "ProxiFyre.exe"],
    }


class _VsFixedFileInfo(ctypes.Structure):
    _fields_ = [
        ("signature", ctypes.c_uint32), ("struct_version", ctypes.c_uint32),
        ("file_version_ms", ctypes.c_uint32), ("file_version_ls", ctypes.c_uint32),
        ("product_version_ms", ctypes.c_uint32), ("product_version_ls", ctypes.c_uint32),
        ("file_flags_mask", ctypes.c_uint32), ("file_flags", ctypes.c_uint32),
        ("file_os", ctypes.c_uint32), ("file_type", ctypes.c_uint32),
        ("file_subtype", ctypes.c_uint32), ("file_date_ms", ctypes.c_uint32),
        ("file_date_ls", ctypes.c_uint32),
    ]


def windows_file_version(path: Path) -> tuple[int, int, int, int] | None:
    """Return a Windows executable/DLL version without launching the file."""
    if os.name != "nt":
        return None
    if not path.is_file():
        return None
    version = ctypes.WinDLL("version", use_last_error=True)
    ignored = ctypes.c_uint32()
    version.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    version.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    size = version.GetFileVersionInfoSizeW(str(path), ctypes.byref(ignored))
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    version.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = ctypes.c_int
    if not version.GetFileVersionInfoW(str(path), 0, size, data):
        return None
    pointer = ctypes.c_void_p()
    length = ctypes.c_uint32()
    version.VerQueryValueW.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32),
    ]
    version.VerQueryValueW.restype = ctypes.c_int
    if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return None
    info = ctypes.cast(pointer, ctypes.POINTER(_VsFixedFileInfo)).contents
    if info.signature != 0xFEEF04BD:
        return None
    return (
        info.file_version_ms >> 16, info.file_version_ms & 0xFFFF,
        info.file_version_ls >> 16, info.file_version_ls & 0xFFFF,
    )


def packet_filter_version() -> tuple[int, int, int, int] | None:
    driver = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "ndisrd.sys"
    return windows_file_version(driver)


def packet_filter_installed() -> bool:
    installed = packet_filter_version()
    return installed is not None and installed >= MIN_PACKET_FILTER_VERSION


def prepare_proxifyre(process_names: list[str], socks_port: int) -> Path:
    executable = PROXIFYRE_SOURCE_DIR / "ProxiFyre.exe"
    if not executable.is_file():
        raise V2PathError("The bundled Discord per-app helper is missing. Re-extract the complete ZIP.")
    installed = windows_file_version(executable)
    if installed is None or installed < MIN_PROXIFYRE_VERSION:
        found = ".".join(map(str, installed)) if installed else "unknown"
        required = ".".join(map(str, MIN_PROXIFYRE_VERSION[:3]))
        raise V2PathError(
            f"Discord UDP helper {found} is too old; version {required} or newer is required. "
            "Re-extract the latest complete V2Path ZIP."
        )
    PROXIFYRE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    for source in PROXIFYRE_SOURCE_DIR.iterdir():
        if source.is_file():
            shutil.copy2(source, PROXIFYRE_RUNTIME_DIR / source.name)
    write_config(build_proxifyre_config(process_names, socks_port), PROXIFYRE_RUNTIME_DIR / "app-config.json")
    return PROXIFYRE_RUNTIME_DIR / "ProxiFyre.exe"


def ensure_proxifyre_firewall_rule(executable: Path) -> None:
    """Allow ProxiFyre's local UDP relay on every Windows network profile.

    Transparent UDP redirection is reinjected into the Windows TCP/IP stack and
    therefore reaches ProxiFyre as inbound UDP, even though the original packet
    was outbound from Discord. The relay uses an ephemeral port, so the rule is
    scoped to the signed helper executable instead of a fixed port.
    """
    if os.name != "nt":
        return
    executable = executable.resolve()
    if not executable.is_file():
        raise V2PathError("The Discord UDP helper is missing before firewall configuration.")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        [
            "netsh.exe", "advfirewall", "firewall", "delete", "rule",
            f"name={PROXIFYRE_FIREWALL_RULE}", "dir=in",
        ],
        capture_output=True, text=True, creationflags=flags,
    )
    result = subprocess.run(
        [
            "netsh.exe", "advfirewall", "firewall", "add", "rule",
            f"name={PROXIFYRE_FIREWALL_RULE}", "dir=in", "action=allow",
            f"program={executable}", "protocol=UDP", "profile=any", "enable=yes",
        ],
        capture_output=True, text=True, creationflags=flags,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise V2PathError(
            "Windows Firewall would not allow the Discord UDP relay. "
            + (detail or f"netsh returned code {result.returncode}.")
        )


class _JobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class WindowsChildJob:
    """Kill assigned child processes automatically when this process exits."""

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows Job Objects are only available on Windows.")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self.kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self.kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
        ]
        self.kernel32.SetInformationJobObject.restype = ctypes.c_int
        self.kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        self.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel32.CloseHandle.restype = ctypes.c_int
        self.handle = self.kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        information = _JobExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel32.SetInformationJobObject(
            self.handle,
            self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, process: subprocess.Popen[object]) -> None:
        if not self.handle:
            raise OSError("The child-process job is closed.")
        if not self.kernel32.AssignProcessToJobObject(self.handle, ctypes.c_void_p(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None

    def __del__(self) -> None:
        self.close()


def eprint(*values: object) -> None:
    print(*values, file=sys.stderr)


def powershell_json(script: str) -> object:
    command = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "$ErrorActionPreference='Stop'; " + script,
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise V2PathError("This application requires Windows PowerShell.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout).strip()
        raise V2PathError(f"Could not query Windows network adapters: {detail}") from exc
    text = result.stdout.lstrip("\ufeff").strip()
    return json.loads(text) if text else []


def get_adapters() -> list[dict[str, object]]:
    script = r"""
Get-NetAdapter | ForEach-Object {
  $a = $_
  try { $c = Get-NetIPConfiguration -InterfaceIndex $a.ifIndex -ErrorAction Stop }
  catch { $c = $null }
  [pscustomobject]@{
    name = $a.Name
    guid = [string]$a.InterfaceGuid
    description = $a.InterfaceDescription
    status = [string]$a.Status
    ifIndex = $a.ifIndex
    physical = [bool]$a.HardwareInterface
    mac = $a.MacAddress
    linkSpeed = [string]$a.LinkSpeed
    ipv4 = @($c.IPv4Address | ForEach-Object { $_.IPAddress })
    gateways = @($c.IPv4DefaultGateway | ForEach-Object { $_.NextHop })
    dnsServers = @(
      Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        ForEach-Object { $_.ServerAddresses } | Where-Object { $_ }
    )
  }
} | ConvertTo-Json -Depth 4 -Compress
"""
    raw = powershell_json(script)
    if isinstance(raw, dict):
        raw = [raw]
    return list(raw)


def get_default_ipv4_interface_name() -> str:
    script = r"""
$best = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop |
  Where-Object { $_.State -eq 'Alive' -and $_.NextHop -ne '0.0.0.0' } |
  ForEach-Object {
    $ipif = Get-NetIPInterface -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4 -ErrorAction Stop
    [pscustomobject]@{ name = $ipif.InterfaceAlias; total = $_.RouteMetric + $ipif.InterfaceMetric }
  } | Sort-Object total | Select-Object -First 1
if (-not $best) { throw 'No active IPv4 default route was found.' }
$best.name | ConvertTo-Json -Compress
"""
    value = powershell_json(script)
    if not isinstance(value, str) or not value:
        raise V2PathError("Could not determine the normal Windows default internet adapter.")
    return value


def configure_windows_tun_routes(name: str = "V2Path TUN") -> None:
    escaped = name.replace("'", "''")
    script = rf"""
$adapter = Get-NetAdapter -IncludeHidden -Name '{escaped}' -ErrorAction Stop
$index = $adapter.ifIndex
Get-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
New-NetIPAddress -InterfaceIndex $index -IPAddress '10.66.0.1' -PrefixLength 30 -Type Unicast -ErrorAction Stop | Out-Null
foreach ($prefix in @('0.0.0.0/1', '128.0.0.0/1')) {{
  Get-NetRoute -InterfaceIndex $index -DestinationPrefix $prefix -ErrorAction SilentlyContinue |
    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
  New-NetRoute -InterfaceIndex $index -DestinationPrefix $prefix -NextHop '0.0.0.0' `
    -RouteMetric 1 -PolicyStore ActiveStore -ErrorAction Stop | Out-Null
}}
[pscustomobject]@{{ name = $adapter.Name; ifIndex = $index; address = '10.66.0.1/30' }} |
  ConvertTo-Json -Compress
"""
    powershell_json(script)


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [str(item) for item in values if item is not None and str(item)]


def adapter_ipv4_address(adapter: dict[str, object]) -> str:
    """Choose the best usable IPv4 source address from a selected adapter."""
    fallback = ""
    for value in _as_list(adapter.get("ipv4")):
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version != 4 or address.is_loopback or address.is_unspecified:
            continue
        if not address.is_link_local:
            return str(address)
        fallback = fallback or str(address)
    if fallback:
        return fallback
    raise V2PathError(
        f"Adapter {adapter.get('name', 'selected adapter')!r} has no usable IPv4 address."
    )


def adapter_is_wireless(adapter: dict[str, object]) -> bool:
    words = f"{adapter.get('name', '')} {adapter.get('description', '')}".lower()
    return any(token in words for token in ("wi-fi", "wifi", "wireless", "802.11", "wlan"))


def find_cached_adapter(
    adapters: list[dict[str, object]], settings: dict[str, object],
) -> dict[str, object] | None:
    """Match a remembered adapter by stable identity, with legacy fallbacks."""
    saved_guid = str(settings.get("adapter_guid", "")).strip().strip("{}").casefold()
    saved_mac = re.sub(r"[^0-9a-f]", "", str(settings.get("adapter_mac", "")).casefold())
    saved_name = str(settings.get("adapter", "")).strip().casefold()
    saved_index = str(settings.get("adapter_ifindex", "")).strip()

    matchers = (
        lambda adapter: saved_guid and str(adapter.get("guid", "")).strip().strip("{}").casefold() == saved_guid,
        lambda adapter: saved_mac and re.sub(r"[^0-9a-f]", "", str(adapter.get("mac", "")).casefold()) == saved_mac,
        lambda adapter: saved_name and str(adapter.get("name", "")).strip().casefold() == saved_name,
        lambda adapter: saved_index and str(adapter.get("ifIndex", "")).strip() == saved_index,
    )
    for matches in matchers:
        found = next((adapter for adapter in adapters if matches(adapter)), None)
        if found is not None:
            return found
    return None


def select_adapter(adapters: list[dict[str, object]], selector: str | None) -> dict[str, object]:
    up = [a for a in adapters if str(a.get("status", "")).lower() == "up"]
    if selector:
        needle = selector.strip().lower()
        matches: list[dict[str, object]] = []
        for adapter in adapters:
            values = {
                str(adapter.get("name", "")).lower(),
                str(adapter.get("ifIndex", "")).lower(),
                *[x.lower() for x in _as_list(adapter.get("ipv4"))],
                *[x.lower() for x in _as_list(adapter.get("gateways"))],
            }
            if needle in values:
                matches.append(adapter)
        if not matches:
            raise V2PathError(
                f"No adapter matches {selector!r}. Run 'secondnet adapters' to list valid names, IPs, gateways, and indexes."
            )
        if len(matches) > 1:
            names = ", ".join(str(a.get("name")) for a in matches)
            raise V2PathError(f"{selector!r} matches more than one adapter: {names}. Use its name or interface index.")
        chosen = matches[0]
        if str(chosen.get("status", "")).lower() != "up":
            raise V2PathError(f"Adapter {chosen.get('name')!r} is not connected (status: {chosen.get('status')}).")
        return chosen

    candidates = [
        a
        for a in up
        if a.get("physical")
        and adapter_is_wireless(a)
        and _as_list(a.get("ipv4"))
        and _as_list(a.get("gateways"))
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise V2PathError("No connected Wi-Fi adapter with an IPv4 gateway was found. Specify one with --interface.")
    names = ", ".join(str(a.get("name")) for a in candidates)
    raise V2PathError(f"More than one connected Wi-Fi adapter was found ({names}). Specify one with --interface.")


def print_adapters(adapters: list[dict[str, object]], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(adapters, indent=2))
        return
    rows = []
    for a in adapters:
        rows.append(
            [
                str(a.get("ifIndex", "")),
                str(a.get("name", "")),
                str(a.get("status", "")),
                ",".join(_as_list(a.get("ipv4"))) or "-",
                ",".join(_as_list(a.get("gateways"))) or "-",
                "yes" if a.get("physical") else "no",
            ]
        )
    headers = ["INDEX", "NAME", "STATUS", "IPV4", "GATEWAY", "PHYSICAL"]
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments plus trailing commas without touching strings."""
    output: list[str] = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
        elif char == '"':
            in_string = True
            output.append(char)
            i += 1
        elif char == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
        elif char == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            if end < 0:
                raise V2PathError("The JSON config contains an unterminated block comment.")
            output.append(" " if "\n" not in text[i : end + 2] else "\n" * text[i : end + 2].count("\n"))
            i = end + 2
        else:
            output.append(char)
            i += 1
    return re.sub(r",\s*([}\]])", r"\1", "".join(output))


def _b64decode(value: str) -> bytes:
    value = value.strip()
    value += "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value)
    except (ValueError, UnicodeError) as exc:
        raise V2PathError("The share link contains invalid base64 data.") from exc


def _network_settings(query: dict[str, list[str]]) -> tuple[str, dict[str, object]]:
    get = lambda name, default="": query.get(name, [default])[-1]
    link_type = get("type", "tcp").lower()
    network = {
        "tcp": "raw",
        "raw": "raw",
        "ws": "websocket",
        "websocket": "websocket",
        "kcp": "mkcp",
    }.get(link_type, link_type)
    extra: dict[str, object] = {}
    if network == "websocket":
        settings: dict[str, object] = {"path": urllib.parse.unquote(get("path", "/"))}
        host = get("host")
        if host:
            settings["host"] = host
        extra["wsSettings"] = settings
    elif network == "grpc":
        settings = {"serviceName": urllib.parse.unquote(get("serviceName"))}
        if get("authority"):
            settings["authority"] = get("authority")
        if get("mode").lower() == "multi":
            settings["multiMode"] = True
        extra["grpcSettings"] = settings
    elif network == "xhttp":
        settings = {}
        for source, target in (("host", "host"), ("path", "path"), ("mode", "mode")):
            if get(source):
                settings[target] = urllib.parse.unquote(get(source))
        extra["xhttpSettings"] = settings
    elif network == "httpupgrade":
        settings = {"path": urllib.parse.unquote(get("path", "/"))}
        if get("host"):
            settings["host"] = get("host")
        extra["httpupgradeSettings"] = settings
    elif network == "mkcp":
        settings = {}
        if get("headerType"):
            settings["header"] = {"type": get("headerType")}
        if get("seed"):
            settings["seed"] = get("seed")
        extra["kcpSettings"] = settings
    elif network == "raw" and get("headerType").lower() == "http":
        request: dict[str, object] = {
            "path": [urllib.parse.unquote(get("path", "/") or "/")],
        }
        host = get("host")
        if host:
            request["headers"] = {"Host": [host]}
        extra["rawSettings"] = {
            "header": {
                "type": "http",
                "request": request,
            }
        }
    return network, extra


def _stream_settings(query: dict[str, list[str]]) -> dict[str, object]:
    get = lambda name, default="": query.get(name, [default])[-1]
    network, extra = _network_settings(query)
    security = (get("security", "none") or "none").lower()
    stream: dict[str, object] = {"network": network, "security": security, **extra}
    if security == "tls":
        tls: dict[str, object] = {}
        if get("sni"):
            tls["serverName"] = get("sni")
        if get("fp"):
            tls["fingerprint"] = get("fp")
        if get("alpn"):
            tls["alpn"] = [part for part in get("alpn").split(",") if part]
        if get("allowInsecure").lower() in {"1", "true"}:
            tls["allowInsecure"] = True
        stream["tlsSettings"] = tls
    elif security == "reality":
        reality: dict[str, object] = {}
        for source, target in (
            ("sni", "serverName"),
            ("fp", "fingerprint"),
            ("pbk", "publicKey"),
            ("sid", "shortId"),
            ("spx", "spiderX"),
        ):
            if get(source):
                reality[target] = urllib.parse.unquote(get(source))
        stream["realitySettings"] = reality
    return stream


def parse_standard_link(link: str) -> dict[str, object]:
    parsed = urllib.parse.urlsplit(link)
    scheme = parsed.scheme.lower()
    if scheme not in {"vless", "trojan"}:
        raise V2PathError(f"Unsupported share-link scheme: {scheme or '(missing)'}. Use JSON, VLESS, VMess, or Trojan.")
    if not parsed.hostname or not parsed.port or not parsed.username:
        raise V2PathError(f"The {scheme.upper()} share link is missing credentials, server, or port.")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    user = urllib.parse.unquote(parsed.username)
    if scheme == "vless":
        account: dict[str, object] = {
            "id": user,
            "encryption": query.get("encryption", ["none"])[-1],
        }
        flow = query.get("flow", [""])[-1]
        if flow:
            account["flow"] = flow
        settings = {"vnext": [{"address": parsed.hostname, "port": parsed.port, "users": [account]}]}
    else:
        settings = {"servers": [{"address": parsed.hostname, "port": parsed.port, "password": user}]}
    return {
        "log": {"loglevel": "warning"},
        "outbounds": [
            {
                "tag": "secondnet-vpn",
                "protocol": scheme,
                "settings": settings,
                "streamSettings": _stream_settings(query),
            }
        ],
    }


def parse_vmess_link(link: str) -> dict[str, object]:
    try:
        data = json.loads(_b64decode(link[len("vmess://") :]).decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise V2PathError("The VMess share link payload is not valid JSON.") from exc
    try:
        address = str(data["add"])
        port = int(data["port"])
        user_id = str(data["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise V2PathError("The VMess share link is missing add, port, or id.") from exc
    query: dict[str, list[str]] = {
        "type": [str(data.get("net", "tcp"))],
        "security": [str(data.get("tls", "none") or "none")],
        "host": [str(data.get("host", ""))],
        "path": [str(data.get("path", ""))],
        "sni": [str(data.get("sni", ""))],
        "fp": [str(data.get("fp", ""))],
        "alpn": [str(data.get("alpn", ""))],
        "headerType": [str(data.get("type", ""))],
    }
    account = {
        "id": user_id,
        "alterId": int(data.get("aid", 0) or 0),
        "security": str(data.get("scy", "auto") or "auto"),
    }
    return {
        "log": {"loglevel": "warning"},
        "outbounds": [
            {
                "tag": "secondnet-vpn",
                "protocol": "vmess",
                "settings": {"vnext": [{"address": address, "port": port, "users": [account]}]},
                "streamSettings": _stream_settings(query),
            }
        ],
    }


def load_source(value: str) -> dict[str, object]:
    stripped = value.strip()
    lower = stripped.lower()
    if lower.startswith("vmess://"):
        return parse_vmess_link(stripped)
    if lower.startswith(("vless://", "trojan://")):
        return parse_standard_link(stripped)
    path = Path(value).expanduser()
    if not path.is_file():
        raise V2PathError(f"Config file not found: {path}")
    try:
        config = json.loads(strip_jsonc(path.read_text(encoding="utf-8-sig")))
    except (OSError, json.JSONDecodeError) as exc:
        raise V2PathError(f"Could not read Xray JSON config {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise V2PathError("The Xray config root must be a JSON object.")
    return config


def choose_outbound(config: dict[str, object], requested_tag: str | None) -> tuple[dict[str, object], str]:
    outbounds = config.get("outbounds")
    if not isinstance(outbounds, list) or not outbounds:
        raise V2PathError("The config does not contain any outbounds.")
    if requested_tag:
        matches = [o for o in outbounds if isinstance(o, dict) and o.get("tag") == requested_tag]
        if len(matches) != 1:
            raise V2PathError(f"No unique outbound has tag {requested_tag!r}.")
        outbound = matches[0]
    else:
        candidates = [
            o
            for o in outbounds
            if isinstance(o, dict) and str(o.get("protocol", "")).lower() not in DIRECT_PROTOCOLS
        ]
        if len(candidates) != 1:
            details = ", ".join(
                f"{o.get('tag', '<untagged>')} ({o.get('protocol', '?')})" for o in candidates
            ) or "none"
            raise V2PathError(
                f"Could not choose one VPN outbound; candidates: {details}. Pass --outbound-tag explicitly."
            )
        outbound = candidates[0]
    protocol = str(outbound.get("protocol", "")).lower()
    if protocol in DIRECT_PROTOCOLS or not protocol:
        raise V2PathError(f"Outbound {requested_tag!r} is not a VPN/proxy outbound (protocol: {protocol or 'missing'}).")
    tag = str(outbound.get("tag") or "secondnet-vpn")
    outbound["tag"] = tag
    return outbound, tag


def _local_proxy_inbounds(
    vpn_socks_port: int, direct_socks_port: int,
) -> tuple[list[dict[str, object]], list[str]]:
    """Build local SOCKS5 listeners for the VPN and selected-adapter paths."""
    if vpn_socks_port == 0 and direct_socks_port == 0:
        raise V2PathError("At least one local proxy must be enabled (port cannot be 0 for both).")
    for label, port in (("VPN SOCKS5", vpn_socks_port), ("DIRECT SOCKS5", direct_socks_port)):
        if not 0 <= port <= 65535:
            raise V2PathError(f"{label} port must be between 0 and 65535.")
    if vpn_socks_port and vpn_socks_port == direct_socks_port:
        raise V2PathError("VPN SOCKS5 and DIRECT SOCKS5 ports must be different.")

    inbounds: list[dict[str, object]] = []
    inbound_tags: list[str] = []
    sniffing = {"enabled": True, "routeOnly": True, "destOverride": ["http", "tls", "quic"]}
    if vpn_socks_port:
        inbound_tags.append("secondnet-socks")
        inbounds.append(
            {
                "tag": "secondnet-socks",
                "listen": "127.0.0.1",
                "port": vpn_socks_port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True, "ip": "127.0.0.1"},
                "sniffing": sniffing,
            }
        )
    if direct_socks_port:
        inbound_tags.append("secondnet-direct-socks")
        inbounds.append(
            {
                "tag": "secondnet-direct-socks",
                "listen": "127.0.0.1",
                "port": direct_socks_port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True, "ip": "127.0.0.1"},
                "sniffing": sniffing,
            }
        )
    return inbounds, inbound_tags


def build_config(
    source: dict[str, object],
    interface_name: str,
    vpn_socks_port: int,
    direct_socks_port: int,
    outbound_tag: str | None = None,
    force_processes: list[str] | None = None,
    direct_interface_name: str | None = None,
) -> tuple[dict[str, object], str]:
    config = copy.deepcopy(source)
    outbound, tag = choose_outbound(config, outbound_tag)
    outbound.pop("sendThrough", None)
    stream = outbound.setdefault("streamSettings", {})
    if not isinstance(stream, dict):
        raise V2PathError(f"Outbound {tag!r} has invalid streamSettings.")
    sockopt = stream.setdefault("sockopt", {})
    if not isinstance(sockopt, dict):
        raise V2PathError(f"Outbound {tag!r} has invalid streamSettings.sockopt.")
    sockopt["interface"] = interface_name

    inbounds, inbound_tags = _local_proxy_inbounds(vpn_socks_port, direct_socks_port)
    tunnel_processes = xray_process_names(force_processes or [])
    if tunnel_processes:
        inbounds.append(
            {
                "tag": "secondnet-tun",
                "protocol": "tun",
                "settings": {
                    "name": "V2Path TUN",
                    # Leave headroom for encapsulation. A full 1500-byte TUN
                    # packet can otherwise fragment on PPPoE/VPN uplinks and
                    # produce latency bursts under load.
                    "mtu": 1400,
                },
            }
        )
    config["inbounds"] = inbounds
    old_routing = config.get("routing")
    domain_strategy = old_routing.get("domainStrategy", "AsIs") if isinstance(old_routing, dict) else "AsIs"
    outbounds = config.get("outbounds")
    assert isinstance(outbounds, list)
    existing_tags = {
        str(item.get("tag")) for item in outbounds if isinstance(item, dict) and item.get("tag")
    }

    def unique_tag(base: str) -> str:
        candidate = base
        suffix = 2
        while candidate in existing_tags:
            candidate = f"{base}-{suffix}"
            suffix += 1
        existing_tags.add(candidate)
        return candidate

    # CLI callers may still request Xray's direct SOCKS5 listener. The GUI
    # passes port 0 here because its direct listener is the separate Node.js
    # dispatcher, leaving Xray with only the VPN listener.
    local_direct_tag = ""
    if "secondnet-direct-socks" in inbound_tags:
        local_direct_tag = unique_tag("secondnet-local-direct")
        outbounds.append(
            {
                "tag": local_direct_tag,
                "protocol": "freedom",
                "settings": {},
                "streamSettings": {"sockopt": {"interface": interface_name}},
            }
        )
    rules: list[dict[str, object]] = []
    if "secondnet-socks" in inbound_tags:
        rules.append(
            {
                "type": "field",
                "inboundTag": ["secondnet-socks"],
                "outboundTag": tag,
                "ruleTag": "Local SOCKS5 through VPN",
            }
        )
    if "secondnet-direct-socks" in inbound_tags:
        assert local_direct_tag
        rules.append(
            {
                "type": "field",
                "inboundTag": ["secondnet-direct-socks"],
                "outboundTag": local_direct_tag,
                "ruleTag": "Local SOCKS5 direct through selected adapter",
            }
        )
    if tunnel_processes:
        direct_tag = unique_tag("secondnet-direct")
        direct_outbound: dict[str, object] = {
            "tag": direct_tag, "protocol": "freedom", "settings": {},
        }
        if direct_interface_name:
            direct_outbound["streamSettings"] = {
                "sockopt": {"interface": direct_interface_name}
            }
        outbounds.append(direct_outbound)
        block_tag = unique_tag("secondnet-block-local")
        outbounds.append({"tag": block_tag, "protocol": "blackhole", "settings": {}})
        rules.extend(
            [
                {
                    "type": "field", "inboundTag": ["secondnet-tun"],
                    "ip": ["169.254.0.0/16", "224.0.0.0/4", "255.255.255.255/32", "fe80::/10", "ff00::/8"],
                    "outboundTag": block_tag, "ruleTag": "Drop link-local and multicast noise",
                },
                {
                    "type": "field", "inboundTag": ["secondnet-tun"],
                    "process": tunnel_processes, "outboundTag": tag,
                    "ruleTag": "Selected applications through VPN",
                },
                {
                    "type": "field", "inboundTag": ["secondnet-tun"],
                    "outboundTag": direct_tag, "ruleTag": "Other applications direct",
                },
            ]
        )
    config["routing"] = {
        "domainStrategy": domain_strategy,
        "rules": rules,
    }
    return config, tag


def write_config(config: dict[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False, suffix=".tmp") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, output)


def find_xray(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None
    if DEFAULT_XRAY.is_file():
        return DEFAULT_XRAY
    if BUNDLED_XRAY.is_file():
        return BUNDLED_XRAY
    found = shutil.which("xray") or shutil.which("xray.exe")
    return Path(found) if found else None


def find_node() -> Path | None:
    """Find the bundled Node.js runtime, then fall back to a system install."""
    for candidate in (DEFAULT_NODE, BUNDLED_NODE):
        if candidate.is_file():
            return candidate
    found = shutil.which("node") or shutil.which("node.exe")
    return Path(found).resolve() if found else None


def find_direct_socks_script() -> Path | None:
    for candidate in (DEFAULT_DIRECT_SOCKS, BUNDLED_DIRECT_SOCKS):
        if candidate.is_file():
            return candidate
    return None


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})


def install_xray(version: str | None = None, force: bool = False) -> Path:
    if DEFAULT_XRAY.is_file() and not force and version is None:
        print(f"Xray is already installed: {DEFAULT_XRAY}")
        return DEFAULT_XRAY
    release_url = GITHUB_LATEST if not version else f"https://api.github.com/repos/XTLS/Xray-core/releases/tags/{version}"
    try:
        with urllib.request.urlopen(_request(release_url), timeout=30) as response:
            release = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise V2PathError(f"Could not query the official Xray release: {exc}") from exc
    assets = {asset["name"]: asset["browser_download_url"] for asset in release.get("assets", [])}
    archive_name = "Xray-windows-64.zip"
    digest_name = archive_name + ".dgst"
    if archive_name not in assets or digest_name not in assets:
        raise V2PathError(f"Release {release.get('tag_name', '?')} has no {archive_name} or checksum asset.")
    print(f"Downloading official Xray {release.get('tag_name', '')} for Windows x64...")
    try:
        with urllib.request.urlopen(_request(assets[digest_name]), timeout=30) as response:
            digest_text = response.read().decode("ascii")
        expected_match = re.search(r"(?im)^SHA2-256=\s*([0-9a-f]{64})\s*$", digest_text)
        if not expected_match:
            raise V2PathError("The official Xray checksum file has an unrecognized format.")
        expected = expected_match.group(1).lower()
        with tempfile.TemporaryDirectory(prefix="secondnet-xray-") as temp_dir:
            archive = Path(temp_dir) / archive_name
            with urllib.request.urlopen(_request(assets[archive_name]), timeout=120) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
            actual = hashlib.sha256(archive.read_bytes()).hexdigest()
            if actual != expected:
                raise V2PathError("The downloaded Xray archive failed SHA-256 verification.")
            with zipfile.ZipFile(archive) as bundle:
                wanted = {"xray.exe", "geoip.dat", "geosite.dat", "wintun.dll"}
                available = {Path(name).name.lower(): name for name in bundle.namelist() if not name.endswith("/")}
                if "xray.exe" not in available:
                    raise V2PathError("The official Xray archive does not contain xray.exe.")
                TOOLS_DIR.mkdir(parents=True, exist_ok=True)
                for name in wanted & available.keys():
                    target = TOOLS_DIR / name
                    with bundle.open(available[name]) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
    except V2PathError:
        raise
    except (OSError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        raise V2PathError(f"Could not download/install Xray: {exc}") from exc
    print(f"Installed and verified: {DEFAULT_XRAY}")
    return DEFAULT_XRAY


def check_ports(ports: list[int]) -> None:
    for port in ports:
        if not port:
            continue
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise V2PathError(f"Local port {port} is already in use: {exc}") from exc
        finally:
            probe.close()


def xray_test(xray: Path, config_path: Path) -> None:
    result = subprocess.run([str(xray), "run", "-test", "-config", str(config_path)], text=True)
    if result.returncode:
        raise V2PathError(f"Xray rejected the generated config (exit code {result.returncode}).")


def prepare(args: argparse.Namespace) -> tuple[Path, dict[str, object], str]:
    adapters = get_adapters()
    adapter = select_adapter(adapters, args.interface)
    source = load_source(args.config)
    config, outbound_tag = build_config(
        source, str(adapter["name"]), args.socks_port, args.direct_socks_port, args.outbound_tag
    )
    output = Path(args.output).expanduser().resolve()
    write_config(config, output)
    return output, adapter, outbound_tag


def add_common_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("config", help="Xray/V2Ray JSON file, or a VLESS/VMess/Trojan share link")
    parser.add_argument("--interface", "--adapter", help="adapter name, interface index, IPv4 address, or gateway (auto-detects Wi-Fi)")
    parser.add_argument("--socks-port", type=int, default=10808, help="local SOCKS5 port; 0 disables it (default: 10808)")
    parser.add_argument(
        "--direct-socks-port", "--http-port", dest="direct_socks_port", type=int, default=10809,
        help="local SOCKS5 port that exits directly through the selected adapter; 0 disables it (default: 10809)",
    )
    parser.add_argument("--outbound-tag", help="VPN outbound tag when the config contains multiple proxy outbounds")
    parser.add_argument("--output", default=str(RUNTIME_DIR / "secondnet-config.json"), help="generated config path")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="secondnet",
        description="Expose a local Xray proxy whose VPN connection is forced through one Windows adapter.",
    )
    parser.add_argument("--version", action="version", version="V2Path 2.2.2")
    commands = parser.add_subparsers(dest="command", required=True)
    adapters = commands.add_parser("adapters", help="list Windows adapters and selector values")
    adapters.add_argument("--json", action="store_true", help="print machine-readable JSON")
    install = commands.add_parser("install", help="download and SHA-256 verify official Xray")
    install.add_argument("--xray-version", help="release tag, for example v26.3.27 (default: latest)")
    install.add_argument("--force", action="store_true", help="replace an existing bundled Xray")
    prep = commands.add_parser("prepare", help="generate a bound local-proxy config without starting Xray")
    add_common_config_args(prep)
    start = commands.add_parser("start", help="generate the config and run Xray")
    add_common_config_args(start)
    start.add_argument("--xray", help="path to xray.exe (default: bundled copy or PATH)")
    start.add_argument("--no-install", action="store_true", help="do not download Xray when it is missing")
    start.add_argument("--skip-test", action="store_true", help="skip Xray's config validation before starting")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        if args.command == "adapters":
            print_adapters(get_adapters(), args.json)
            return 0
        if args.command == "install":
            install_xray(args.xray_version, args.force)
            return 0
        output, adapter, outbound_tag = prepare(args)
        ipv4 = ", ".join(_as_list(adapter.get("ipv4"))) or "unknown"
        gateway = ", ".join(_as_list(adapter.get("gateways"))) or "unknown"
        print(f"Adapter : {adapter['name']} (index {adapter['ifIndex']}, IP {ipv4}, gateway {gateway})")
        print(f"Outbound: {outbound_tag} (socket-bound to {adapter['name']})")
        print(f"Config  : {output}")
        if args.command == "prepare":
            return 0
        check_ports([args.socks_port, args.direct_socks_port])
        xray = find_xray(args.xray)
        if xray is None:
            if args.xray:
                raise V2PathError(f"Xray executable not found: {args.xray}")
            if args.no_install:
                raise V2PathError("Xray was not found. Run 'secondnet install' or omit --no-install.")
            xray = install_xray()
        if not args.skip_test:
            print("Validating generated config with Xray...")
            xray_test(xray, output)
        if args.socks_port:
            print(f"SOCKS5  : 127.0.0.1:{args.socks_port}")
        if args.direct_socks_port:
            print(f"DIRECT SOCKS5 : 127.0.0.1:{args.direct_socks_port}")
        print("Xray is running. Press Ctrl+C to stop. Windows routes were not changed.")
        process = subprocess.Popen([str(xray), "run", "-config", str(output)])
        try:
            return process.wait()
        except KeyboardInterrupt:
            print("\nStopping Xray...")
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return 0
    except V2PathError as exc:
        eprint(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())