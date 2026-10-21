"""Offline clipboard filtering and credential-free profile labels."""
from functools import lru_cache

import secondnet


def clipboard_links(text: str) -> list[str]:
    """Only accept individual raw share links, never fetch subscriptions or files."""
    return list(dict.fromkeys(
        line.strip().lstrip("\ufeff") for line in text.splitlines()
        if line.strip().lstrip("\ufeff").lower().startswith(("vless://", "vmess://", "trojan://"))
    ))


@lru_cache(maxsize=256)
def profile_details(source: str) -> tuple[str, str]:
    try:
        outbound, _ = secondnet.choose_outbound(secondnet.load_source(source), None)
        protocol = str(outbound.get("protocol", "unknown")).upper()
        settings = outbound.get("settings", {})
        servers = settings.get("vnext") or settings.get("servers") or []
        server = servers[0] if servers else {}
        address = str(server.get("address", "Unknown server"))
        if ":" in address and not address.startswith("["):
            address = f"[{address}]"
        port = server.get("port")
        endpoint = f"{address}:{port}" if port else address
        stream = outbound.get("streamSettings", {})
        security = str(stream.get("security", "none")).upper()
        network = str(stream.get("network", "tcp")).upper()
        network = {"RAW": "TCP", "WEBSOCKET": "WS"}.get(network, network)
        labels = [protocol]
        if security not in {"", "NONE"}:
            labels.append(security)
        labels.append(network)
        return endpoint, " · ".join(labels)
    except (secondnet.V2PathError, ValueError, TypeError, KeyError, IndexError, AttributeError, OSError):
        return "Server details unavailable", "Invalid or unavailable profile"
