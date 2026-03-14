"""Создание Zep-клиента с поддержкой настраиваемого endpoint и прокси."""

from __future__ import annotations

import httpx
from zep_cloud.client import Zep

from ..config import Config
from .logger import get_logger

logger = get_logger("mirofish.zep_client")


def _normalize_zep_base_url(base_url: str | None) -> str | None:
    """Приводит URL Zep к полному API base URL."""
    if not base_url:
        return None

    normalized = base_url.rstrip("/")
    if normalized.endswith("/api/v2"):
        return normalized
    return f"{normalized}/api/v2"


def create_zep_client(api_key: str | None = None) -> Zep:
    """Создает Zep-клиент с optional proxy и custom base URL."""
    resolved_api_key = api_key or Config.ZEP_API_KEY
    if not resolved_api_key:
        raise ValueError("ZEP_API_KEY не настроен")

    base_url = _normalize_zep_base_url(Config.ZEP_API_URL)
    proxy_url = Config.ZEP_PROXY_URL or None
    timeout = Config.ZEP_TIMEOUT_SECONDS

    if proxy_url:
        logger.info("Инициализирую Zep-клиент через прокси")
        httpx_client = httpx.Client(
            proxy=proxy_url,
            timeout=timeout,
            follow_redirects=True,
            trust_env=True,
        )
        return Zep(
            api_key=resolved_api_key,
            base_url=base_url,
            timeout=timeout,
            follow_redirects=True,
            httpx_client=httpx_client,
        )

    return Zep(
        api_key=resolved_api_key,
        base_url=base_url,
        timeout=timeout,
        follow_redirects=True,
    )
