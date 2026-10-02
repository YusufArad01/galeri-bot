"""Playwright-based listing scraper with stealth context and proxy rotation."""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from urllib.parse import unquote, urljoin, urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from core.config import settings
from core.logger import logger
from proxy.manager import ProxyManager, mask_proxy

# playwright-stealth changed its public API in 2.x; support both.
try:
    from playwright_stealth import stealth_async as _stealth_v1  # type: ignore[attr-defined]

    async def apply_stealth(page: Page) -> None:
        await _stealth_v1(page)

except ImportError:
    from playwright_stealth import Stealth  # type: ignore[attr-defined,no-redef]

    _STEALTH_V2 = Stealth()

    async def apply_stealth(page: Page) -> None:
        await _STEALTH_V2.apply_stealth_async(page)


_LAUNCH_ARGS: Final[list[str]] = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--lang=tr-TR",
    "--window-size=1920,1080",
]

_USER_AGENTS: Final[tuple[str, ...]] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
)

_BLOCKED_RESOURCE_TYPES: Final[frozenset[str]] = frozenset({"image", "media", "font"})

_CHALLENGE_TITLE_MARKERS: Final[tuple[str, ...]] = (
    "just a moment",
    "bir dakika",
    "attention required",
    "checking your browser",
    "lütfen bekleyin",
)
_CHALLENGE_SELECTOR: Final[str] = (
    "#challenge-form, #challenge-running, #cf-challenge-running, "
    "iframe[src*='challenges.cloudflare.com']"
)

_BLOCKING_HTTP_STATUSES: Final[frozenset[int]] = frozenset({403, 429, 503})

_LISTING_ID_FROM_URL: Final[re.Pattern[str]] = re.compile(r"(\d{6,})(?:/detay)?/?(?:[?#].*)?$")
_YEAR_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(19[5-9]\d|20\d{2})$")
_PRICE_PATTERN: Final[re.Pattern[str]] = re.compile(r"(\d{1,3}(?:[.\s]\d{3})+|\d+)(?:,(\d{1,2}))?")

_TITLE_MAX_LEN: Final[int] = 512
_FIELD_MAX_LEN: Final[int] = 128


class ScraperError(Exception):
    """Base error for a failed scrape attempt."""


class BlockedError(ScraperError):
    """The target served an anti-bot challenge or a blocking status code."""


@dataclass(frozen=True, slots=True)
class SelectorConfig:
    """CSS selectors, ordered by preference. The first matching one wins."""

    rows: tuple[str, ...] = (
        "tr.searchResultsItem[data-id]",
        "tbody.searchResultsRowClass > tr[data-id]",
        "[data-listing-id]",
        "li.classified-list-item",
    )
    title: tuple[str, ...] = (
        "a.classifiedTitle",
        "td.searchResultsTitleValue a",
        "a[class*='title']",
        "h3 a",
    )
    price: tuple[str, ...] = (
        "td.searchResultsPriceValue span",
        "td.searchResultsPriceValue",
        "[class*='price']",
    )
    location: tuple[str, ...] = (
        "td.searchResultsLocationValue",
        "[class*='location']",
    )
    attribute: str = "td.searchResultsAttributeValue"
    tag: str = "td.searchResultsTagAttributeValue"
    detail_link: str = "a[href*='/ilan/'], a[href*='detay']"


_EXTRACT_JS: Final[str] = """
(cfg) => {
  const rows = Array.from(document.querySelectorAll(cfg.row));
  const text = (el) => el ? (el.innerText || el.textContent || '').trim() : null;
  return rows.map((row) => {
    const pick = (selectors) => {
      for (const s of selectors) {
        const el = row.querySelector(s);
        if (el) return el;
      }
      return null;
    };
    const titleEl = pick(cfg.title);
    const linkEl = (titleEl && titleEl.tagName === 'A') ? titleEl : row.querySelector(cfg.detailLink);
    return {
      id: row.getAttribute('data-id') || row.getAttribute('data-listing-id') || null,
      title: text(titleEl) || (titleEl ? titleEl.getAttribute('title') : null),
      href: linkEl ? linkEl.getAttribute('href') : null,
      price: text(pick(cfg.price)),
      location: text(pick(cfg.location)),
      attributes: Array.from(row.querySelectorAll(cfg.attribute)).map(text).filter(Boolean),
      tags: Array.from(row.querySelectorAll(cfg.tag)).map(text).filter(Boolean),
    };
  });
}
"""


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def _clean(value: str | None, max_len: int = _FIELD_MAX_LEN) -> str | None:
    if value is None:
        return None
    collapsed = re.sub(r"\s+", " ", value).strip()
    return collapsed[:max_len] if collapsed else None


def parse_price(raw: str | None) -> Decimal | None:
    """Parse Turkish-formatted prices such as '1.250.000 TL' or '985.500,50 TL'."""
    if not raw:
        return None
    match = _PRICE_PATTERN.search(raw.replace("\xa0", " "))
    if match is None:
        return None
    integer_part = re.sub(r"[.\s]", "", match.group(1))
    fraction_part = match.group(2) or "0"
    try:
        value = Decimal(f"{integer_part}.{fraction_part}")
    except InvalidOperation:
        return None
    return value if value > 0 else None


def parse_int(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _extract_year_and_km(attributes: list[str]) -> tuple[int | None, int | None]:
    max_year = datetime.now(timezone.utc).year + 1
    year: int | None = None
    year_index = -1
    for index, value in enumerate(attributes):
        candidate = value.strip()
        if _YEAR_PATTERN.match(candidate) and 1950 <= int(candidate) <= max_year:
            year = int(candidate)
            year_index = index
            break

    km: int | None = None
    # Prefer an explicit "km" cell, otherwise the first numeric cell after the year.
    for value in attributes:
        if "km" in value.lower():
            km = parse_int(value)
            break
    if km is None:
        for value in attributes[year_index + 1:]:
            if re.fullmatch(r"[\d.\s]+", value.strip()):
                km = parse_int(value)
                break
    return year, km


def _extract_city(location: str | None) -> str | None:
    if not location:
        return None
    first_line = next((line.strip() for line in location.splitlines() if line.strip()), None)
    return _clean(first_line)


def _resolve_brand_model(
    tags: list[str],
    title: str,
    brand_hint: str | None,
    model_hint: str | None,
) -> tuple[str | None, str | None]:
    brand = brand_hint
    model = model_hint
    if brand is None:
        if len(tags) >= 2:
            brand = tags[0]
            model = model or tags[1]
        else:
            tokens = title.split()
            brand = tokens[0] if tokens else None
    if model is None:
        if tags:
            model = tags[0]
        else:
            tokens = title.split()
            model = tokens[1] if len(tokens) > 1 else None
    return _clean(brand), _clean(model)


class ScraperEngine:
    """Scrapes search-result pages into normalized listing dictionaries."""

    def __init__(self, selectors: SelectorConfig | None = None) -> None:
        self.selectors: SelectorConfig = selectors or SelectorConfig()

    # ------------------------------------------------------------------ #
    # Browser setup
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_proxy_settings(proxy_url: str) -> dict[str, str]:
        parts = urlsplit(proxy_url)
        if not parts.hostname or parts.port is None:
            raise ScraperError(f"Invalid proxy URL: {mask_proxy(proxy_url)}")
        proxy: dict[str, str] = {"server": f"{parts.scheme or 'http'}://{parts.hostname}:{parts.port}"}
        if parts.username:
            proxy["username"] = unquote(parts.username)
        if parts.password:
            proxy["password"] = unquote(parts.password)
        return proxy

    @staticmethod
    async def _route_handler(route: Route) -> None:
        try:
            if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()
        except PlaywrightError:
            # The page may already be closed; nothing left to route.
            return

    async def create_stealth_context(
        self, p: Playwright, proxy_url: str | None
    ) -> tuple[Browser, BrowserContext, Page]:
        """Launch Chromium with anti-detection settings and return a ready page."""
        launch_kwargs: dict[str, Any] = {
            "headless": settings.SCRAPER_HEADLESS,
            "args": _LAUNCH_ARGS,
            "ignore_default_args": ["--enable-automation"],
        }
        if proxy_url:
            launch_kwargs["proxy"] = self._build_proxy_settings(proxy_url)

        browser = await p.chromium.launch(**launch_kwargs)
        try:
            context = await browser.new_context(
                user_agent=random.choice(_USER_AGENTS),
                locale="tr-TR",
                timezone_id="Europe/Istanbul",
                viewport={"width": 1920, "height": 1080},
                screen={"width": 1920, "height": 1080},
                device_scale_factor=1,
                is_mobile=False,
                has_touch=False,
                java_script_enabled=True,
                color_scheme="light",
                extra_http_headers={
                    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            context.set_default_navigation_timeout(settings.SCRAPER_NAVIGATION_TIMEOUT_MS)
            context.set_default_timeout(30_000)
            await context.route("**/*", self._route_handler)

            page = await context.new_page()
            await apply_stealth(page)
        except BaseException:
            await browser.close()
            raise
        return browser, context, page

    # ------------------------------------------------------------------ #
    # Page interaction
    # ------------------------------------------------------------------ #
    @staticmethod
    async def _is_challenge_page(page: Page) -> bool:
        try:
            title = (await page.title()).lower()
            if any(marker in title for marker in _CHALLENGE_TITLE_MARKERS):
                return True
            return await page.locator(_CHALLENGE_SELECTOR).count() > 0
        except PlaywrightError:
            # Execution context destroyed mid-navigation: the challenge is usually redirecting.
            return True

    async def _await_challenge_clearance(self, page: Page) -> bool:
        """Wait for an interstitial challenge to resolve. Returns True if one was seen."""
        if not await self._is_challenge_page(page):
            return False
        logger.info("Anti-bot challenge detected; waiting up to {}s", settings.SCRAPER_CHALLENGE_TIMEOUT_S)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.SCRAPER_CHALLENGE_TIMEOUT_S
        while await self._is_challenge_page(page):
            if loop.time() >= deadline:
                raise BlockedError("Anti-bot challenge did not clear in time")
            await asyncio.sleep(random.uniform(1.0, 2.0))
        logger.info("Challenge cleared")
        return True

    async def _wait_for_rows(self, page: Page) -> str | None:
        combined = ", ".join(self.selectors.rows)
        try:
            await page.wait_for_selector(combined, state="attached", timeout=20_000)
        except PlaywrightTimeoutError:
            return None
        for selector in self.selectors.rows:
            if await page.locator(selector).count() > 0:
                return selector
        return None

    @staticmethod
    async def _human_scroll(page: Page) -> None:
        """Scroll in irregular steps with pauses and mouse movement."""
        steps = random.randint(6, 12)
        for _ in range(steps):
            await page.mouse.move(
                random.randint(200, 1700),
                random.randint(150, 950),
                steps=random.randint(5, 20),
            )
            delta = random.randint(250, 700)
            await page.evaluate("(d) => window.scrollBy({ top: d, left: 0, behavior: 'smooth' })", delta)
            await asyncio.sleep(random.uniform(0.4, 1.3))

            if random.random() < 0.15:
                await page.evaluate(
                    "(d) => window.scrollBy({ top: -d, left: 0, behavior: 'smooth' })",
                    random.randint(80, 250),
                )
                await asyncio.sleep(random.uniform(0.3, 0.8))

            at_bottom = await page.evaluate(
                "() => (window.innerHeight + window.scrollY) >= (document.body ? document.body.scrollHeight : 0) - 50"
            )
            if at_bottom:
                break

    # ------------------------------------------------------------------ #
    # Extraction
    # ------------------------------------------------------------------ #
    def _normalize(
        self,
        raw_rows: list[dict[str, Any]],
        base_url: str,
        brand_hint: str | None,
        model_hint: str | None,
    ) -> list[dict[str, Any]]:
        listings: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        skipped = 0

        for raw in raw_rows:
            title = _clean(raw.get("title"), _TITLE_MAX_LEN)
            href = raw.get("href")
            url = urljoin(base_url, href) if href else None

            source_id = _clean(raw.get("id"), 64)
            if source_id is None and url:
                match = _LISTING_ID_FROM_URL.search(urlsplit(url).path)
                source_id = match.group(1) if match else None

            attributes = [str(a) for a in raw.get("attributes") or []]
            tags = [str(t) for t in raw.get("tags") or []]
            price = parse_price(raw.get("price"))
            year, km = _extract_year_and_km(attributes)

            if not (source_id and title and url and price is not None and year is not None):
                skipped += 1
                continue
            if source_id in seen_ids:
                continue

            brand, model = _resolve_brand_model(tags, title, brand_hint, model_hint)
            if not brand or not model:
                skipped += 1
                continue

            seen_ids.add(source_id)
            listings.append(
                {
                    "source_listing_id": source_id,
                    "title": title,
                    "brand": brand,
                    "model": model,
                    "year": year,
                    "km": km,
                    "price": price,
                    "city": _extract_city(raw.get("location")),
                    "url": url[:1024],
                }
            )

        if skipped:
            logger.debug("Skipped {} rows with incomplete data (ads, separators or missing fields)", skipped)
        return listings

    async def _fetch_once(
        self,
        target_url: str,
        proxy_url: str | None,
        brand_hint: str | None,
        model_hint: str | None,
    ) -> list[dict[str, Any]]:
        async with async_playwright() as p:
            browser, context, page = await self.create_stealth_context(p, proxy_url)
            try:
                response = await page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=settings.SCRAPER_NAVIGATION_TIMEOUT_MS,
                )
                status = response.status if response is not None else None
                challenged = await self._await_challenge_clearance(page)

                row_selector = await self._wait_for_rows(page)
                if row_selector is None:
                    if status in _BLOCKING_HTTP_STATUSES and not challenged:
                        raise BlockedError(f"HTTP {status} with no listing content")
                    if await self._is_challenge_page(page):
                        raise BlockedError("Challenge page re-appeared")
                    logger.warning("No listing rows found on {} (HTTP {})", target_url, status)
                    return []

                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeoutError:
                    logger.debug("networkidle not reached; continuing with loaded DOM")

                await self._human_scroll(page)

                raw_rows: list[dict[str, Any]] = await page.evaluate(
                    _EXTRACT_JS,
                    {
                        "row": row_selector,
                        "title": list(self.selectors.title),
                        "price": list(self.selectors.price),
                        "location": list(self.selectors.location),
                        "attribute": self.selectors.attribute,
                        "tag": self.selectors.tag,
                        "detailLink": self.selectors.detail_link,
                    },
                )
                listings = self._normalize(raw_rows, page.url, brand_hint, model_hint)
                logger.info(
                    "Parsed {}/{} rows from {} (selector='{}')",
                    len(listings),
                    len(raw_rows),
                    target_url,
                    row_selector,
                )
                return listings
            finally:
                await self._safe_close(context.close, "context")
                await self._safe_close(browser.close, "browser")

    @staticmethod
    async def _safe_close(closer: Callable[[], Awaitable[None]], label: str) -> None:
        try:
            await closer()
        except PlaywrightError as exc:
            logger.debug("Ignoring error while closing {}: {}", label, exc)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def fetch_listings(
        self,
        target_url: str,
        brand_hint: str | None = None,
        model_hint: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch and parse listings, rotating proxies on failure.

        Returns an empty list if every attempt fails; errors are logged and
        reported to ProxyManager rather than raised.
        """
        max_attempts = settings.SCRAPER_MAX_RETRIES
        for attempt in range(1, max_attempts + 1):
            proxy_url = await ProxyManager.get_proxy()
            proxy_label = mask_proxy(proxy_url) if proxy_url else "direct"
            logger.info("Fetching {} (attempt {}/{}, via {})", target_url, attempt, max_attempts, proxy_label)

            try:
                listings = await self._fetch_once(target_url, proxy_url, brand_hint, model_hint)
            except (ScraperError, PlaywrightError) as exc:
                kind = "blocked" if isinstance(exc, BlockedError) else type(exc).__name__
                logger.warning("Attempt {} failed via {} [{}]: {}", attempt, proxy_label, kind, str(exc).splitlines()[0] if str(exc) else kind)
                if proxy_url:
                    await ProxyManager.mark_failure(proxy_url)
                if attempt < max_attempts:
                    backoff = min(30.0, 2.0 ** attempt) + random.uniform(0.0, 1.5)
                    await asyncio.sleep(backoff)
                continue

            if proxy_url:
                await ProxyManager.mark_success(proxy_url)
            return listings

        logger.error("Giving up on {} after {} attempts", target_url, max_attempts)
        return []
