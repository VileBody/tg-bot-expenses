from __future__ import annotations

import os

from aiogram.client.session.aiohttp import AiohttpSession


def apply_outbound_proxy_environment(proxy_url: str | None) -> None:
    if not proxy_url:
        return

    os.environ["HTTP_PROXY"] = proxy_url
    os.environ["HTTPS_PROXY"] = proxy_url
    os.environ["ALL_PROXY"] = proxy_url
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url
    os.environ["all_proxy"] = proxy_url


def build_aiogram_session(proxy_url: str | None) -> AiohttpSession:
    if not proxy_url:
        return AiohttpSession()
    return AiohttpSession(proxy=proxy_url)
