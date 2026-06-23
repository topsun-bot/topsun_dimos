"""Strip SOCKS proxy URLs from the process environment.

Clash/V2Ray often set ``ALL_PROXY=socks://127.0.0.1:7897``. Many DimOS
dependencies (httpx, requests, huggingface_hub) cannot use that scheme and
crash at import or client init.
"""

from __future__ import annotations

import os

_PROXY_ENV_KEYS = (
    "all_proxy",
    "ALL_PROXY",
    "http_proxy",
    "HTTP_PROXY",
    "https_proxy",
    "HTTPS_PROXY",
    "ftp_proxy",
    "FTP_PROXY",
)


def _is_socks_proxy(value: str) -> bool:
    """Check if a proxy value uses the SOCKS scheme."""
    return isinstance(value, str) and value.strip().lower().startswith(
        ("socks://", "socks4://", "socks5://", "socks4a://", "socks5h://")
    )


def strip_socks_proxy_env() -> dict[str, str]:
    """Remove SOCKS-named proxy vars from os.environ; return the removed values."""
    removed: dict[str, str] = {}
    for key in _PROXY_ENV_KEYS:
        value = os.environ.get(key)
        if value and _is_socks_proxy(value):
            removed[key] = value
            del os.environ[key]
    return removed


def restore_proxy_env(removed: dict[str, str]) -> None:
    """Restore proxy environment variables previously removed by strip_socks_proxy_env."""
    for key, value in removed.items():
        os.environ[key] = value
