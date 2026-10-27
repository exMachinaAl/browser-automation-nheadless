"""Small, dependency-free guards for local browser gateway boundaries.

Never use a substring test to determine whether a CDP endpoint is on loopback:
URL userinfo and lookalike hosts can make that unsafe.
"""
from __future__ import annotations

from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})


def validate_cdp_endpoint(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('cdp_endpoint_must_be_string')
    value = value.strip()
    if not value:
        return ''
    if len(value) > 2048 or any(ch.isspace() for ch in value):
        raise ValueError('invalid_cdp_endpoint')
    try:
        u = urlsplit(value)
        if (u.scheme not in ('http', 'ws') or u.hostname not in LOOPBACK_HOSTS
                or u.port is None or not 1 <= u.port <= 65535
                or u.username is not None or u.password is not None
                or u.query or u.fragment):
            raise ValueError('cdp_must_use_loopback_host_and_port')
    except ValueError as exc:
        raise ValueError('cdp_must_use_loopback_host_and_port') from exc
    return value


def valid_gateway_host_header(host: str, port: int) -> bool:
    if not isinstance(host, str) or len(host) > 100 or not host:
        return False
    try:
        u = urlsplit('http://' + host)
        return (u.hostname in LOOPBACK_HOSTS and u.port == port
                and u.username is None and u.password is None
                and not (u.path or u.query or u.fragment)
                and not any(ch.isspace() for ch in host))
    except ValueError:
        return False


def valid_browser_origin(value: str | None, port: int) -> bool:
    if value is None:
        return True  # non-browser CLI clients do not send Origin
    try:
        u = urlsplit(value)
        return (u.scheme == 'http' and u.hostname in LOOPBACK_HOSTS
                and u.port == port and u.username is None and u.password is None
                and not (u.path or u.query or u.fragment))
    except ValueError:
        return False


def navigation_message(exc: Exception) -> str:
    """Browser exceptions can include bearer-like URLs or private paths; do not leak them."""
    name = type(exc).__name__
    if name == 'TimeoutError':
        return 'navigation_timeout'
    return 'navigation_failed_' + ''.join(ch for ch in name if ch.isalnum())[:32]


def valid_browse_url(url: str) -> bool:
    """Navigation scheme check: not a permission to use automated DOM actions."""
    if not isinstance(url, str) or not (0 < len(url) <= 2048):
        return False
    if any(ord(ch) < 32 or ch.isspace() for ch in url):
        return False
    try:
        u = urlsplit(url)
        return (u.scheme in ('http', 'https') and bool(u.hostname)
                and u.username is None and u.password is None
                and (u.port is None or 1 <= u.port <= 65535))
    except ValueError:
        return False
