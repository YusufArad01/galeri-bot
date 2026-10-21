import os
import re
import asyncio
import random
from typing import Dict, Any
from loguru import logger
from playwright.async_api import async_playwright, Page, Route
from src.parsing import parse_tl
from src.proxy.manager import ProxyManager

try:
    from playwright_stealth import stealth_async as _stealth_async
except ImportError:  # pragma: no cover
    _stealth_async = None


class VavaCarsEngine:
    """VavaCars (kurumsal, hasarsız satış). Pipeline ile aynı arayüz: async with + source_id."""

    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        self.last_blocked = False
        self._pw = None
        self._browser = None
        os.makedirs("logs", exist_ok=True)

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        return self

    async def __aexit__(self, *exc):
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    # Eski sürümde bu metot yoktu: her çağrıda AttributeError -> sessizce 0 ilan dönüyordu.
    async def _intercept_route(self, route: Route):
        if route.request.resource_type in ("image", "font", "media", "stylesheet"):
            await route.abort()
        else:
            await route.continue_()

    async def _new_page(self) -> Page:
        page = await self._browser.new_page()
        if _stealth_async:
            await _stealth_async(page)
        await page.route("**/*", self._intercept_route)
        return page

    async def _human_interaction(self, page: Page):
        await asyncio.sleep(random.uniform(1.0, 3.0))
        await page.mouse.move(random.uniform(100, 800), random.uniform(100, 800))
        await page.evaluate("window.scrollBy(0, window.innerHeight / 2);")
        await asyncio.sleep(random.uniform(2.0, 5.0))

    async def _extract_text(self, page: Page, selectors: list[str]) -> str | None:
        for selector in selectors:
            try:
                if await page.locator(selector).count() > 0:
                    text = await page.locator(selector).first.inner_text(timeout=2000)
                    if text:
                        return text.strip()
            except Exception:
                continue
        return None

    def source_id(self, url: str) -> str:
        return "vava-" + url.rstrip("/").split("/")[-1][:8]

    async def fetch_category_links(self, category_url: str) -> list[str]:
        logger.info(f"VavaCars motoru {category_url} adresini taramaya başlıyor...")
        self.last_blocked = False
        page = await self._new_page()
        try:
            await page.goto(category_url, wait_until="domcontentloaded", timeout=45000)
            await self._human_interaction(page)
            hrefs = await page.evaluate('''() => {
                return Array.from(document.querySelectorAll('a')).map(a => a.href).filter(href => href.includes('/buy/cars/') && href.split('/').length >= 7);
            }''')
            links = list(dict.fromkeys(hrefs))
            if not links:
                logger.warning(f"No VavaCars listing links found on {category_url}.")
            return links
        except Exception as e:
            logger.error(f"VavaCars fetch_category_links failed: {e}")
            return []
        finally:
            await page.close()

    async def fetch_listing_data(self, url: str) -> Dict[str, Any]:
        page = await self._new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await self._human_interaction(page)

            price_text = await self._extract_text(page, [
                'div[data-test-id="vehicle-price-display-discounted-price"]',
                'div[data-test-id="vehicle-price-display-original-price"]',
                '.text-h3', '.price'])
            if not price_text:
                raise ValueError("Fiyat bulunamadı.")
            price = parse_tl(price_text)

            parts = url.split('/')
            brand = parts[-3] if len(parts) >= 6 else "Bilinmiyor"
            model = parts[-2] if len(parts) >= 6 else "Bilinmiyor"

            props = await page.evaluate(r'''() => {
                let data = {};
                document.querySelectorAll('li, div, tr').forEach(el => {
                    let text = el.innerText.trim().toLowerCase();
                    if (text.length < 80 && (text.includes('yıl') || text.includes('km') || text.includes('kilometre'))) {
                        let parts = text.split(/\n|:/);
                        if (parts.length >= 2) data[parts[0].trim()] = parts[1].trim();
                    }
                });
                return data;
            }''')

            year = 0
            km = 0
            for key, val in props.items():
                if "yıl" in key or "model" in key:
                    m = re.search(r'\b(19|20)\d{2}\b', val)
                    if m and year == 0:
                        year = int(m.group())
                if "km" in key or "kilometre" in key:
                    m = re.search(r'\d[\d.]*', val)
                    if m and km == 0:
                        km = int(m.group().replace('.', ''))

            return {
                "source_listing_id": self.source_id(url),
                "title": f"VavaCars {brand} {model}",
                "brand": brand,
                "model": model,
                "trim": "",
                "year": year,
                "km": km,
                "price": price,
                "city": "İstanbul",
                "description": "VavaCars İlanı",
                "url": url,
                "seller_type": "Kurumsal",
                "is_heavy_damage": False,  # VavaCars ağır hasarlı satmaz
                "damage_reasons": [],
                "is_old_cheap": False,
                "is_disqualified": False,
            }
        except Exception as e:
            logger.error(f"VavaCars fetch_listing_data failed for {url}: {e}")
            raise
        finally:
            await page.close()
