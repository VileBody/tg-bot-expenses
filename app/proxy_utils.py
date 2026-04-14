from __future__ import annotations

import os
from urllib.parse import urlparse


def apply_outbound_proxy_environment(proxy_url: str | None) -> None:
    if not proxy_url:
        return

    os.environ["HTTP_PROXY"] = proxy_url
    os.environ["HTTPS_PROXY"] = proxy_url
    os.environ["ALL_PROXY"] = proxy_url
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url
    os.environ["all_proxy"] = proxy_url


def build_telethon_proxy(proxy_url: str | None):
    """Convert proxy URL into Telethon/PySocks tuple."""
    if not proxy_url:
        return None

    try:
        import socks  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on runtime environment
        raise RuntimeError(
            "Proxy is configured, but PySocks is not installed. Run: pip install -r requirements.txt"
        ) from exc

    parsed = urlparse(proxy_url)
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        raise ValueError("OUTBOUND_PROXY must be full URL, e.g. socks5://user:pass@host:port")

    scheme = parsed.scheme.lower()
    if scheme in {"socks5", "socks5h"}:
        proxy_type = socks.SOCKS5
    elif scheme == "socks4":
        proxy_type = socks.SOCKS4
    elif scheme in {"http", "https"}:
        proxy_type = socks.HTTP
    else:
        raise ValueError(f"Unsupported proxy scheme in OUTBOUND_PROXY: {parsed.scheme}")

    return (
        proxy_type,
        parsed.hostname,
        parsed.port,
        True,
        parsed.username,
        parsed.password,
    )
