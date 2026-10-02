"""Database-backed proxy rotation with failure tracking."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from core.config import settings
from core.database import get_session
from core.logger import logger
from models.listing import ProxyModel

_SUPPORTED_SCHEMES: frozenset[str] = frozenset({"http", "https", "socks5"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def mask_proxy(proxy_url: str) -> str:
    """Return the proxy URL with its password hidden, safe for logging."""
    try:
        parts = urlsplit(proxy_url)
    except ValueError:
        return "<invalid proxy url>"
    if parts.password is None:
        return proxy_url
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{parts.username}:***@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _normalize_proxy_url(raw: str) -> str | None:
    candidate = raw.strip()
    if not candidate or candidate.startswith("#"):
        return None
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    try:
        parts = urlsplit(candidate)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in _SUPPORTED_SCHEMES or not parts.hostname or port is None:
        return None
    return candidate


class ProxyManager:
    """Stateless facade over the `proxies` table.

    Leasing uses `SELECT ... FOR UPDATE SKIP LOCKED` so concurrent workers
    never receive the same proxy in the same instant.
    """

    @staticmethod
    async def get_proxy() -> str | None:
        """Lease the least-recently-used active proxy and stamp its usage time."""
        try:
            async with get_session() as session:
                stmt = (
                    select(ProxyModel)
                    .where(ProxyModel.is_active.is_(True))
                    .order_by(ProxyModel.last_used_at.asc().nulls_first(), ProxyModel.id.asc())
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                proxy = (await session.execute(stmt)).scalar_one_or_none()
                if proxy is None:
                    logger.warning("No active proxy available; falling back to a direct connection")
                    return None
                proxy.last_used_at = _utcnow()
                proxy_url = proxy.url
        except SQLAlchemyError:
            logger.exception("Failed to lease a proxy from the database")
            return None

        logger.debug("Leased proxy {}", mask_proxy(proxy_url))
        return proxy_url

    @staticmethod
    async def mark_failure(proxy_url: str) -> None:
        """Increment the failure counter; deactivate once it reaches the limit."""
        max_failures = settings.PROXY_MAX_FAILURES
        try:
            async with get_session() as session:
                # SET expressions read pre-update values, so this is a single atomic statement.
                stmt = (
                    update(ProxyModel)
                    .where(ProxyModel.url == proxy_url)
                    .values(
                        failure_count=ProxyModel.failure_count + 1,
                        is_active=case(
                            (ProxyModel.failure_count + 1 >= max_failures, False),
                            else_=ProxyModel.is_active,
                        ),
                    )
                    .returning(ProxyModel.failure_count, ProxyModel.is_active)
                )
                row = (await session.execute(stmt)).one_or_none()
        except SQLAlchemyError:
            logger.exception("Failed to record failure for proxy {}", mask_proxy(proxy_url))
            return

        if row is None:
            logger.debug("mark_failure: proxy {} is not tracked in the database", mask_proxy(proxy_url))
            return

        failure_count, is_active = row
        if not is_active and failure_count >= max_failures:
            logger.warning(
                "Proxy {} deactivated after {} consecutive failures",
                mask_proxy(proxy_url),
                failure_count,
            )
        else:
            logger.info("Proxy {} failure {}/{}", mask_proxy(proxy_url), failure_count, max_failures)

    @staticmethod
    async def mark_success(proxy_url: str) -> None:
        """Reset the consecutive-failure counter."""
        try:
            async with get_session() as session:
                await session.execute(
                    update(ProxyModel)
                    .where(ProxyModel.url == proxy_url, ProxyModel.failure_count != 0)
                    .values(failure_count=0)
                )
        except SQLAlchemyError:
            logger.exception("Failed to record success for proxy {}", mask_proxy(proxy_url))
            return
        logger.debug("Proxy {} marked healthy", mask_proxy(proxy_url))

    @staticmethod
    async def sync_from_api(api_url: str | None = None, timeout: float = 20.0) -> int:
        """Import proxies from PROXY_API_URL. Returns the number of newly inserted rows.

        Accepted payloads: a JSON array of strings, a JSON object with a
        `proxies` array, or plain text with one proxy per line.
        """
        endpoint = api_url or settings.PROXY_API_URL
        if not endpoint:
            logger.debug("PROXY_API_URL not configured; skipping proxy sync")
            return 0

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(endpoint)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Proxy API request failed: {}", exc)
            return 0

        raw_entries: list[str]
        body = response.text
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            raw_entries = body.splitlines()
        else:
            if isinstance(parsed, dict):
                parsed = parsed.get("proxies", [])
            if not isinstance(parsed, list):
                logger.error("Proxy API returned an unsupported JSON structure")
                return 0
            raw_entries = [str(item) for item in parsed]

        proxy_urls = sorted({url for url in (_normalize_proxy_url(e) for e in raw_entries) if url is not None})
        if not proxy_urls:
            logger.warning("Proxy API returned no valid proxies")
            return 0

        try:
            async with get_session() as session:
                stmt = (
                    pg_insert(ProxyModel)
                    .values([{"url": url} for url in proxy_urls])
                    .on_conflict_do_nothing(index_elements=[ProxyModel.url])
                    .returning(ProxyModel.id)
                )
                inserted = len((await session.execute(stmt)).all())
        except SQLAlchemyError:
            logger.exception("Failed to persist proxies from API")
            return 0

        logger.info("Proxy sync: {} received, {} new", len(proxy_urls), inserted)
        return inserted
