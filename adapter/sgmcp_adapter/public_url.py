"""Public presentation origins only; never resolved or fetched over the network."""

import ipaddress
import os
import re
from urllib.parse import urlsplit


def normalize_public_base_url(value):
    """Return a canonical HTTPS origin, None for empty input, or raise ValueError."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("invalid public origin")
    if not value.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise ValueError("invalid public origin")
    value = value.strip()
    if not value:
        return None
    if any(char.isspace() for char in value) or "?" in value or "#" in value:
        raise ValueError("invalid public origin")
    parsed = urlsplit(value)
    if (parsed.scheme.lower() != "https" or not parsed.netloc
            or parsed.path not in ("", "/") or "@" in parsed.netloc):
        raise ValueError("invalid public origin")
    host = parsed.hostname
    port = parsed.port
    if not host or parsed.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535):
        raise ValueError("invalid public origin")
    if ":" in host:
        if re.fullmatch(r"\[[0-9a-fA-F:.]+\](?::[0-9]+)?", parsed.netloc) is None:
            raise ValueError("invalid public origin")
        host = "[" + ipaddress.IPv6Address(host).compressed + "]"
    else:
        if re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", parsed.netloc) is None:
            raise ValueError("invalid public origin")
        if re.fullmatch(r"[0-9.]+", host):
            host = str(ipaddress.IPv4Address(host))
        elif len(host) > 253 or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
            for label in host.split(".")
        ):
            raise ValueError("invalid public origin")
    return "https://" + host + (":" + str(port) if port not in (None, 443) else "")


def public_url_metadata(override):
    """Invalid/missing environment configuration cannot produce a relative link."""
    for source, candidate in (("configuration", override), ("environment", os.environ.get("APP_PUBLIC_BASE_URL"))):
        try:
            effective = normalize_public_base_url(candidate)
        except ValueError:
            effective = None
        if effective:
            return {"public_base_url": override, "effective_public_base_url": effective, "public_base_url_source": source}
    return {"public_base_url": override, "effective_public_base_url": None, "public_base_url_source": None}
