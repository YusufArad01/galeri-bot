import os
import asyncio
import random
from datetime import datetime
from playwright.async_api import async_playwright, Page, Route
from playwright_stealth import stealth_async
from loguru import logger
from typing import Dict, Any, Optional
from src.proxy.manager import ProxyManager

class ScraperEngine:
    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        # Ensure logs directory exists for diagnostics
        os.makedirs("logs", exist_ok=True)

    async def _intercept_route(self, route: Route):
        # Strict resource blocking for bandwidth optimization
        if route.request.resource_type in ["image", "font", "media", "stylesheet"]:
            await route.abort()
        else:
            await route.continue_()

    async def _human_interaction(self, page: Page):
        # Simulate jitter and human-like scroll steps
        await asyncio.sleep(random.uniform(1.0, 3.0))
        await page.mouse.move(random.uniform(100, 800), random.uniform(100, 800))
        await page.evaluate("window.scrollBy(0, window.innerHeight / 2);")
        await asyncio.sleep(random.uniform(2.5, 6.0))
        await page.evaluate("window.scrollBy(0, window.innerHeight / 2);")

    async def _extract_text(self, page: Page, selectors: list[str]) -> str | None:
        """Helper to extract text with graceful degradation and multiple fallback strategies."""
        for selector in selectors:
            try:
                count = await page.locator(selector).count()
                if count > 0:
                    text = await page.locator(selector).first.inner_text(timeout=2000)
                    if text:
                        return text.strip()
            except Exception:
                continue
        return None

    async def _capture_diagnostics(self, page: Page, reason: str):
        """Automatically captures screenshot and HTML dump for visual/HTML diagnostics."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = f"logs/debug_error_{reason}_{timestamp}.png"
        html_path = f"logs/debug_page_{reason}_{timestamp}.html"
        try:
            await page.screenshot(path=screenshot_path)
            content = await page.content()
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"Saved visual diagnostics to {screenshot_path} and HTML to {html_path}")
        except Exception as e:
            logger.error(f"Failed to capture diagnostics: {e}")

    async def fetch_category_links(self, category_url: str) -> list[str]:
        max_retries = 3
        
        for attempt in range(max_retries):
            proxy = await self.proxy_manager.get_proxy()
            proxy_url = proxy.url if proxy else None
            
            async with async_playwright() as p:
                is_headless = os.environ.get("HEADLESS", "false").lower() == "true"
                launch_kwargs = {
                    "headless": is_headless,
                    "ignore_default_args": ["--enable-automation"],
                    "args": ["--disable-blink-features=AutomationControlled"]
                }
                if proxy_url:
                    launch_kwargs["proxy"] = {"server": proxy_url}
                
                context_kwargs = {
                    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "viewport": {"width": 1280, "height": 720}
                }
                
                browser = await p.chromium.launch(**launch_kwargs)
                context = await browser.new_context(**context_kwargs)
                page = await context.new_page()
                await stealth_async(page)
                await page.route("**/*", self._intercept_route)

                try:
                    await page.goto(category_url, wait_until="domcontentloaded", timeout=45000)
                    await self._human_interaction(page)
                    
                    page_title = await page.title()
                    if "Giriş" in page_title or await page.locator(".px-captcha-container").count() > 0 or await page.locator("text=Bağlantınız kontrol ediliyor").count() > 0:
                        logger.warning("Captcha veya Giriş (Login) sayfası tespit edildi! Lütfen tarayıcıdan manuel çözün/giriş yapın. 60 saniye bekleniyor...")
                        await page.wait_for_timeout(60000)
                    
                    # Extract listing links with robust Arabam-specific CSS selectors
                    hrefs = await page.evaluate('''() => {
                        let extracted = Array.from(document.querySelectorAll('tr.listing-list-item a.listing-text-new')).map(a => a.href);
                        if (extracted.length === 0) {
                            extracted = Array.from(document.querySelectorAll('td.pr8 a')).map(a => a.href);
                        }
                        if (extracted.length === 0) {
                            extracted = Array.from(document.querySelectorAll('a')).map(a => a.href);
                        }
                        return extracted.filter(href => href.includes('/ilan/'));
                    }''')
                    links = list(set(hrefs))
                    
                    if not links:
                        logger.warning(f"No listing links found on {category_url}.")
                        await self._capture_diagnostics(page, "no_links")
                        raise ValueError("No links found on category page.")

                    if proxy:
                        await self.proxy_manager.mark_success(proxy.id)

                    return links

                except Exception as e:
                    logger.warning(f"Category scrape attempt {attempt + 1}/{max_retries} failed for {category_url}: {e}")
                    
                    if proxy:
                        await self.proxy_manager.mark_failure(proxy.id)
                    
                    if attempt == max_retries - 1:
                        logger.error(f"Exhausted retries for category {category_url}.")
                        await self._capture_diagnostics(page, "category_final_error")
                        return []
                    
                    backoff = (2 ** attempt) + random.uniform(1.5, 4.0)
                    logger.info(f"Rotating proxy and retrying in {backoff:.2f} seconds...")
                    await asyncio.sleep(backoff)
                finally:
                    await context.close()
        return []

    async def fetch_listing_data(self, url: str) -> Dict[str, Any]:
        max_retries = 3
        
        for attempt in range(max_retries):
            proxy = await self.proxy_manager.get_proxy()
            proxy_url = proxy.url if proxy else None
            
            async with async_playwright() as p:
                is_headless = os.environ.get("HEADLESS", "false").lower() == "true"
                launch_kwargs = {
                    "headless": is_headless,
                    "ignore_default_args": ["--enable-automation"],
                    "args": ["--disable-blink-features=AutomationControlled"]
                }
                if proxy_url:
                    launch_kwargs["proxy"] = {"server": proxy_url}
                
                context_kwargs = {
                    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "viewport": {"width": 1280, "height": 720}
                }
                
                browser = await p.chromium.launch(**launch_kwargs)
                context = await browser.new_context(**context_kwargs)
                page = await context.new_page()
                await stealth_async(page)
                await page.route("**/*", self._intercept_route)

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await self._human_interaction(page)
                    
                    page_title = await page.title()
                    if "Giriş" in page_title or await page.locator(".px-captcha-container").count() > 0 or await page.locator("text=Bağlantınız kontrol ediliyor").count() > 0:
                        logger.warning("Captcha veya Giriş (Login) sayfası tespit edildi! Lütfen tarayıcıdan manuel çözün/giriş yapın. 60 saniye bekleniyor...")
                        await page.wait_for_timeout(60000)
                    
                    # Robust fallback selector strategies for arabam.com
                    # Use evaluation to ensure we get a proper long title, not a subtitle like "150.000 KM"
                    title = await page.evaluate('''() => {
                        const candidates = [
                            document.querySelector('h1'),
                            document.querySelector('.product-name'),
                            document.querySelector('.listing-title'),
                            document.querySelector('.ad-title')
                        ];
                        for (let el of candidates) {
                            if (el && el.innerText.trim().length > 10) {
                                return el.innerText.trim();
                            }
                        }
                        return "";
                    }''')
                    
                    price_text = await self._extract_text(page, [".product-price", ".price", ".listing-price", "h3"])
                    
                    description = await page.evaluate('''() => {
                        let el = document.querySelector('.ilan-aciklamasi') || document.querySelector('#js-hook-for-observer-detail') || document.querySelector('.description');
                        return el ? el.innerText.trim() : "";
                    }''')
                    
                    if not description:
                        description = await self._extract_text(page, [".product-description", "#productDescription", ".listing-desc"])


                    if not title or not price_text or not description:
                        logger.warning(f"Could not parse element(s) on listing card {url}. Gracefully degrading.")
                        await self._capture_diagnostics(page, "missing_data")
                        # We skip only this specific fetch gracefully instead of crashing
                        raise ValueError("Failed to extract one or more critical fields.")

                    # Format cleaning
                    price_clean = price_text.replace(".", "").replace(",", ".").replace("TL", "").replace("₺", "").strip()
                    price = float(price_clean)

                    # Dynamic Property Extraction (Brand, Model, Year, KM, City)
                    import re
                    
                    year = 0
                    km = 0
                    city = "Bilinmiyor"
                    brand = "Bilinmiyor"
                    model = "Bilinmiyor"
                    
                    # 1. Try to extract structured data from DOM Properties Table
                    props = await page.evaluate(r'''() => {
                        let data = {};
                        document.querySelectorAll('.property-item, ul.listing-features li, ul.property-list li, table tr').forEach(el => {
                            // Extract key and value pairs, handle different layouts
                            let keyEl = el.querySelector('.name, .property-name, th, td:first-child');
                            let valEl = el.querySelector('.value, .property-value, td:last-child');
                            
                            if (keyEl && valEl) {
                                data[keyEl.innerText.trim().toLowerCase()] = valEl.innerText.trim();
                            } else {
                                let parts = el.innerText.split(/:|\n/);
                                if (parts.length >= 2) {
                                    data[parts[0].trim().toLowerCase()] = parts[1].trim();
                                }
                            }
                        });
                        return data;
                    }''')
                    
                    # Clean and assign properties
                    for key, val in props.items():
                        if "yıl" in key or "model" in key:
                            year_match = re.search(r'\d{4}', val)
                            if year_match and year == 0: year = int(year_match.group())
                        if "kilometre" in key or "km" in key:
                            km_match = re.search(r'[\d\.]+', val)
                            if km_match and km == 0: km = int(km_match.group().replace('.', ''))
                        if "marka" in key:
                            brand = val
                        if "seri" in key or "model" in key and brand != "Bilinmiyor":
                            # 'model' key might conflict with year's 'model' key, so we check carefully
                            if not re.match(r'^\d+$', val): model = val
                        if "il" in key or "şehir" in key:
                            city = val

                    # 2. Fallbacks based on title if DOM table failed
                    if year == 0:
                        year_match = re.search(r'(\d{4})\s+Model', title, re.IGNORECASE)
                        if year_match: year = int(year_match.group(1))

                    if km == 0:
                        km_match = re.search(r'([\d\.]+)\s+KM', title, re.IGNORECASE)
                        if km_match: km = int(km_match.group(1).replace('.', ''))

                    if city == "Bilinmiyor":
                        city_match = re.search(r'Model\s+([A-Za-zÇŞĞÜÖİçşğüöı\s]+?)\s+[\d\.]+\s+KM', title, re.IGNORECASE)
                        if city_match: city = city_match.group(1).strip()

                    if brand == "Bilinmiyor" or model == "Bilinmiyor":
                        breadcrumbs = await page.evaluate('''() => {
                            let items = Array.from(document.querySelectorAll('.breadcrumb li a, .bc-item a'));
                            return items.map(i => i.innerText.trim());
                        }''')
                        if len(breadcrumbs) >= 4:
                            if brand == "Bilinmiyor": brand = breadcrumbs[2]
                            if model == "Bilinmiyor": model = breadcrumbs[3]
                        else:
                            title_clean = re.sub(r'^(Galeriden|Sahibinden|Yetkili Bayiden)\s+', '', title, flags=re.IGNORECASE)
                            words = title_clean.split(' ')
                            if len(words) >= 2:
                                if brand == "Bilinmiyor": brand = words[0]
                                if model == "Bilinmiyor": model = words[1]

                    if proxy:
                        await self.proxy_manager.mark_success(proxy.id)

                    seller_type = "Bilinmiyor"
                    if "sahibinden" in title.lower():
                        seller_type = "Sahibinden"
                    elif "galeriden" in title.lower() or "yetkili" in title.lower():
                        seller_type = "Galeriden"
                        
                    is_disqualified = False
                    
                    # 1. Yaş ve Fiyat Filtresi: 2005 ve altı model olup 1 Milyon TL'den ucuzsa (sıradan eski arabaysa) direkt çöpe at
                    if year > 0 and year <= 2005 and price < 1000000.0:
                        is_disqualified = True

                    is_heavy_damage = False
                    # 1. Özellikler tablosundan (props) kontrol et
                    for k, v in props.items():
                        if "ağır hasarlı" in k.lower() or "agir hasarli" in k.lower():
                            if "evet" in v.lower():
                                is_heavy_damage = True
                        
                        # Boya/Değişen çok fazlaysa (Örn: 2 değişen, 4 boyalı) pert statüsüne sokup eleyelim
                        if "boya" in k.lower() or "değişen" in k.lower():
                            degisen_match = re.search(r'(\d+)\s+değişen', v.lower())
                            boyali_match = re.search(r'(\d+)\s+boyalı', v.lower())
                            
                            degisen_count = int(degisen_match.group(1)) if degisen_match else 0
                            boyali_count = int(boyali_match.group(1)) if boyali_match else 0
                            
                            # 'Tamamı' veya 'Komple' kelimesi varsa direkt pert say
                            if "tamamı" in v.lower() or "komple" in v.lower():
                                is_heavy_damage = True
                            
                            # 2 veya daha fazla değişen VEYA 4 veya daha fazla boya varsa FİLTRELE
                            elif degisen_count >= 2 or boyali_count >= 4 or (degisen_count + boyali_count) >= 5:
                                is_heavy_damage = True
                                
                    # 2. Başlık ve Açıklamadan kontrol et
                    heavy_damage_keywords = ["ağır hasar", "agir hasar", "pert", "ağır hasarlı", "agir hasarli"]
                    search_text = (title + " " + description).lower()
                    if any(kw in search_text for kw in heavy_damage_keywords):
                        is_heavy_damage = True
                        
                    is_disqualified = is_disqualified or is_heavy_damage

                    return {
                        "source_listing_id": url.split("/")[-1].split("-")[-1],
                        "title": title,
                        "brand": brand, 
                        "model": model,
                        "year": year,
                        "km": km,
                        "price": price,
                        "city": city,
                        "description": description,
                        "url": url,
                        "seller_type": seller_type,
                        "is_heavy_damage": is_heavy_damage,
                        "is_disqualified": is_disqualified
                    }

                except Exception as e:
                    logger.warning(f"Scrape attempt {attempt + 1}/{max_retries} failed for {url}: {e}")
                    
                    if proxy:
                        await self.proxy_manager.mark_failure(proxy.id)
                    
                    if attempt == max_retries - 1:
                        logger.error(f"Exhausted retries for {url}.")
                        await self._capture_diagnostics(page, "final_error")
                        raise
                    
                    # Exponential backoff & rotation jitter
                    backoff = (2 ** attempt) + random.uniform(1.5, 4.0)
                    logger.info(f"Rotating proxy and retrying in {backoff:.2f} seconds...")
                    await asyncio.sleep(backoff)
                finally:
                    await context.close()
