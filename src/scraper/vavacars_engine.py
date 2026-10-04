import os
import asyncio
import random
from typing import Dict, Any
from loguru import logger
from playwright.async_api import async_playwright, Page, Route
from playwright_stealth import stealth_async
from src.proxy.manager import ProxyManager

class VavaCarsEngine:
    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        os.makedirs("logs", exist_ok=True)

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

    async def fetch_category_links(self, category_url: str) -> list[str]:
        # category_url is expected to be https://tr.vava.cars/buy/cars
        logger.info(f"VavaCars motoru {category_url} adresini taramaya başlıyor...")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await stealth_async(page)
            await page.route("**/*", self._intercept_route)

            try:
                await page.goto(category_url, wait_until="domcontentloaded", timeout=45000)
                await self._human_interaction(page)
                
                # Sitedeki ilan linklerini çek (UUID içeren /buy/cars/Marka/Model/UUID)
                hrefs = await page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('a')).map(a => a.href).filter(href => href.includes('/buy/cars/') && href.split('/').length >= 7);
                }''')
                links = list(set(hrefs))
                
                if not links:
                    logger.warning(f"No VavaCars listing links found on {category_url}.")
                    
                return links
            except Exception as e:
                logger.error(f"VavaCars fetch_category_links failed: {e}")
                return []
            finally:
                await browser.close()

    async def fetch_listing_data(self, url: str) -> Dict[str, Any]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await stealth_async(page)
            await page.route("**/*", self._intercept_route)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await self._human_interaction(page)
                
                # Fiyat Çıkarma
                price_text = await self._extract_text(page, ['div[data-test-id="vehicle-price-display-discounted-price"]', 'div[data-test-id="vehicle-price-display-original-price"]', '.text-h3', '.price', 'h3'])
                if not price_text:
                    raise ValueError("Fiyat bulunamadı.")
                    
                price_clean = price_text.replace(".", "").replace(",", ".").replace("TL", "").replace("₺", "").strip()
                price = float(price_clean)
                
                # Marka Model Çıkarma (URL'den)
                parts = url.split('/')
                brand = "Bilinmiyor"
                model = "Bilinmiyor"
                if len(parts) >= 6:
                    brand = parts[-3]
                    model = parts[-2]
                
                title = f"VavaCars {brand} {model}"
                
                # Özellikleri Çıkarma
                props = await page.evaluate(r'''() => {
                    let data = {};
                    document.querySelectorAll('li, div, tr').forEach(el => {
                        let text = el.innerText.trim().toLowerCase();
                        if (text.includes('yıl') || text.includes('km') || text.includes('kilometre')) {
                            let parts = text.split(/\n|:/);
                            if (parts.length >= 2) {
                                data[parts[0].trim()] = parts[1].trim();
                            }
                        }
                    });
                    return data;
                }''')
                
                import re
                year = 0
                km = 0
                city = "İstanbul" # VavaCars genellikle büyük şehirlerde
                
                for key, val in props.items():
                    if "yıl" in key or "model" in key:
                        year_match = re.search(r'\d{4}', val)
                        if year_match and year == 0: year = int(year_match.group())
                    if "km" in key or "kilometre" in key:
                        km_match = re.search(r'[\d\.]+', val)
                        if km_match and km == 0: km = int(km_match.group().replace('.', ''))

                return {
                    "source_listing_id": "vava-" + parts[-1][:8],
                    "title": title,
                    "brand": brand, 
                    "model": model,
                    "year": year,
                    "km": km,
                    "price": price,
                    "city": city,
                    "description": "VavaCars İlanı",
                    "url": url,
                    "seller_type": "Kurumsal",
                    "is_heavy_damage": False # VavaCars ağır hasarlı satmaz
                }

            except Exception as e:
                logger.error(f"VavaCars fetch_listing_data failed for {url}: {e}")
                raise
            finally:
                await browser.close()
