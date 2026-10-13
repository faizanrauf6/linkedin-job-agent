import ipaddress
import socket
from urllib.parse import urlsplit


def host_matches(host: str, domain: str) -> bool:
    host, domain = host.casefold().rstrip("."), domain.casefold().rstrip(".")
    return bool(domain) and (host == domain or host.endswith("." + domain))


def public_url(url: str, resolve: bool = True) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").casefold()
        if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 80, 443):
            return False
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host and ":" not in host:
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            if not resolve:
                return True
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
    except (ValueError, OSError):
        return False


def hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().rstrip(".")
