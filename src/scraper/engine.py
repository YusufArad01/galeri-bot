import os
import sys
import asyncio
import random
from pathlib import Path
from urllib.parse import unquote, urlsplit
from datetime import datetime
from typing import Any, Dict

from loguru import logger
from playwright.async_api import async_playwright, Page, Route

from src.config import settings
from src.parsing import build_listing, parse_list_row, source_id_from_url
from src.proxy.manager import ProxyManager

try:  # requirements.txt playwright-stealth==1.0.6 sabitler; 2.x API'si farklı
    from playwright_stealth import stealth_async as _stealth_async
except ImportError:  # pragma: no cover
    _stealth_async = None
    try:
        from playwright_stealth import Stealth
    except ImportError:
        Stealth = None

# UA, işletim sistemiyle tutarlı olmalı: Linux sunucuda "Windows Chrome" demek bot izi bırakır.
_IS_LINUX = sys.platform.startswith("linux")
_OS_UA = "X11; Linux x86_64" if _IS_LINUX else "Windows NT 10.0; Win64; x64"


def identity(browser_version: str) -> tuple[str, dict[str, str]]:
    """Gerçek tarayıcı sürümüyle uyumlu kimlik: "HeadlessChrome" ifadesi ve sürüm uyuşmazlığı bot izidir."""
    major = (browser_version or "123").split(".")[0]
    ua = f"Mozilla/5.0 ({_OS_UA}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    hints = {
        "sec-ch-ua": f'"Google Chrome";v="{major}", "Not:A-Brand";v="8", "Chromium";v="{major}"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"' if _IS_LINUX else '"Windows"',
    }
    return ua, hints
CHALLENGE_TITLES = ("bir dakika lütfen", "just a moment", "checking your browser")
CHALLENGE_WAIT_S = 30
STATE_PATH = Path(settings.DB_PATH).resolve().parent / "browser_state.json"  # Cloudflare çerezi kalıcı

LISTING_JS = r'''() => {
    const text = (el) => (el && el.innerText ? el.innerText.trim() : "");
    const pick = (sels) => { for (const s of sels) { const t = text(document.querySelector(s)); if (t) return t; } return ""; };

    let title = "";
    const h1 = document.querySelector('h1');
    if (h1 && text(h1).length > 10) title = text(h1);
    else title = pick(['.product-name', '.listing-title', '.ad-title']);

    const priceText = pick(['.product-price', '.listing-price', '.price']);
    const description = pick(['.ilan-aciklamasi', '#js-hook-for-observer-detail', '.description',
                              '.product-description', '#productDescription', '.listing-desc']);

    const props = {};
    let items = document.querySelectorAll('.property-item');
    if (!items.length) items = document.querySelectorAll('ul.listing-features li, ul.property-list li');
    if (!items.length) items = document.querySelectorAll('table tr');
    items.forEach(el => {
        const keyEl = el.querySelector('.name, .property-name, .property-key, th, td:first-child');
        const valEl = el.querySelector('.value, .property-value, td:last-child');
        if (keyEl && valEl && keyEl !== valEl) {
            props[text(keyEl).toLowerCase()] = text(valEl);
            return;
        }
        const parts = text(el).split('\n').map(s => s.trim()).filter(Boolean);
        if (parts.length >= 2) props[parts[0].toLowerCase()] = parts.slice(1).join(' ');
    });

    const breadcrumbs = Array.from(document.querySelectorAll('.breadcrumb li a, .bc-item a')).map(i => text(i));
    // marka > seri > motor > donanım sayfalarına giden göreli bağlantılar (aynı modelin güncel ilanları orada)
    const crumbs = Array.from(document.querySelectorAll('a[href^="/ikinci-el/otomobil/"]')).map(a => a.getAttribute('href'));
    const location = text(document.querySelector('.product-location'));  // "Mahalle, İlçe, İl"
    return { title, priceText, description, props, breadcrumbs, crumbs, location };
}'''

LINKS_JS = r'''() => {
    let found = Array.from(document.querySelectorAll('tr.listing-list-item a.listing-text-new')).map(a => a.href);
    if (!found.length) found = Array.from(document.querySelectorAll('td.pr8 a')).map(a => a.href);
    if (!found.length) found = Array.from(document.querySelectorAll('a')).map(a => a.href);
    return found.filter(h => h.includes('/ilan/'));
}'''


ROWS_JS = r'''() => {
    return Array.from(document.querySelectorAll('tr.listing-list-item')).map(tr => {
        const a = tr.querySelector('a[href*="/ilan/"]');
        const lines = (tr.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
        return a ? { href: a.href.split('#')[0], lines } : null;
    }).filter(Boolean);
}'''


def split_proxy(url: str) -> dict[str, str]:
    """http://kullanici:sifre@host:port -> Playwright'in beklediği {server, username, password}.
    Chromium kullanıcı adı/şifreyi adresin içinden okumaz, ayrı verilmesi gerekir."""
    u = urlsplit(url if "://" in url else f"http://{url}")
    out = {"server": f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")}
    if u.username:
        out["username"] = unquote(u.username)
        out["password"] = unquote(u.password or "")
    return out


class BlockedError(Exception):
    """Captcha / bot koruması / giriş duvarı."""


class ScraperEngine:
    """Döngü boyunca TEK tarayıcı açık tutar, her sayfa için yeni context (ve proxy) kullanır.

    Eski sürüm her ilan için Chromium'u baştan başlatıyordu (bellek baskısı + bot imzası).
    Kullanım:  async with ScraperEngine(pm) as scraper: ...
    """

    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        self.last_blocked = False
        self.last_block_note = ""
        self.last_status = 0
        self._shared: dict[str, Any] = {}  # proxy adresi (proxy'siz = "") -> ortak context
        self._real_chrome = False
        self._headless_new = True
        self._pw = None
        self._browser = None
        os.makedirs("logs", exist_ok=True)

    # -- yaşam döngüsü ----------------------------------------------------------
    async def __aenter__(self):
        self._pw = await async_playwright().start()
        await self._launch()
        return self

    async def __aexit__(self, *exc):
        try:
            if self._browser:
                await self._save_state()
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    async def _launch(self):
        self._per_context_proxy = await self.proxy_manager.has_active()
        def build(channel: str) -> dict[str, Any]:
            kw: dict[str, Any] = {
                "headless": False,
                "ignore_default_args": ["--enable-automation"],
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if channel:
                kw["channel"] = channel
            # Eski headless kolayca tanınır; "new" gerçek Chrome gibi davranır (ekran gerekmez).
            # HEADLESS=false ise gerçek pencere açılır; Linux'ta ekran (xvfb) yoksa çökmesin diye "new"e düşer.
            if settings.HEADLESS or (_IS_LINUX and not os.environ.get("DISPLAY")):
                kw["args"].append("--headless=new")
            if self._per_context_proxy:
                kw["proxy"] = {"server": "per-context"}
            return kw

        self._real_chrome = False
        self._headless_new = settings.HEADLESS or (_IS_LINUX and not os.environ.get("DISPLAY"))
        if settings.BROWSER_CHANNEL:
            try:
                self._browser = await self._pw.chromium.launch(**build(settings.BROWSER_CHANNEL))
                self._real_chrome = True
                logger.info(f"Tarayıcı: gerçek {settings.BROWSER_CHANNEL} (headless={settings.HEADLESS})")
                return
            except Exception as e:
                logger.warning(f"{settings.BROWSER_CHANNEL} açılamadı, paketli Chromium'a dönülüyor: {str(e)[:160]}")
        self._browser = await self._pw.chromium.launch(**build(""))

    async def _new_page(self, proxy_url: str | None) -> tuple[Any, Page]:
        """(kapatılacak nesne, sayfa). Proxy yoksa tek ortak context kullanılır: Cloudflare
        doğrulamasını bir kez geçince çerez diğer sayfalarda ve sonraki turlarda da geçerli kalır."""
        if not self._browser or not self._browser.is_connected():
            logger.warning("Tarayıcı bağlantısı kopmuş, yeniden başlatılıyor.")
            self._shared = {}
            await self._launch()
        # Türkiye'deki bir kullanıcı gibi görün: sunucu varsayılanı (UTC, en-US) bot izi bırakır
        ctx_kwargs: dict[str, Any] = {
            "viewport": {"width": 1280, "height": 720},
            "locale": "tr-TR", "timezone_id": "Europe/Istanbul",
            "extra_http_headers": {"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5"},
        }
        # Ekranlı gerçek Chrome kendini doğru tanıtır; ekransız/paketli olanlar "Headless" der, düzelt
        if self._headless_new or not self._real_chrome:
            ua, hints = identity(self._browser.version)
            ctx_kwargs["user_agent"] = ua
            ctx_kwargs["extra_http_headers"].update(hints)
        key = proxy_url if (proxy_url and self._per_context_proxy) else ""
        if key not in self._shared:
            if key:
                ctx_kwargs["proxy"] = split_proxy(key)
            if STATE_PATH.exists():
                ctx_kwargs["storage_state"] = str(STATE_PATH)
            self._shared[key] = await self._browser.new_context(**ctx_kwargs)
        context = self._shared[key]
        page = await context.new_page()
        closer = page
        if _stealth_async:
            await _stealth_async(page)
        elif Stealth:
            await Stealth().apply_stealth_async(page)
        await page.route("**/*", self._intercept_route)
        return closer, page

    # -- yardımcılar ------------------------------------------------------------
    async def _save_state(self):
        if self._shared:
            try:
                await next(iter(self._shared.values())).storage_state(path=str(STATE_PATH))
            except Exception as e:
                logger.warning(f"Tarayıcı durumu kaydedilemedi: {e}")

    async def _on_challenge(self, page: Page) -> bool:
        try:
            title = ((await page.title()) or "").lower()
            if any(t in title for t in CHALLENGE_TITLES):
                return True
            return await page.locator("text=Güvenlik doğrulaması").count() > 0
        except Exception:  # sayfa yönlenirken bağlam yok olabilir: doğrulama bitiyor demektir
            return False

    async def _wait_challenge(self, page: Page) -> bool:
        """Cloudflare "Bir dakika lütfen" sayfası kendiliğinden geçebilir; beklemeden 'ilan yok' deme."""
        if not await self._on_challenge(page):
            return True
        logger.info("Cloudflare doğrulaması göründü, bekleniyor...")
        for _ in range(CHALLENGE_WAIT_S):
            await asyncio.sleep(1)
            if not await self._on_challenge(page):
                logger.info("Cloudflare doğrulaması geçildi.")
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                return True
        return False

    async def _intercept_route(self, route: Route):
        if route.request.resource_type in ("image", "media"):
            await route.abort()
        else:
            await route.continue_()

    async def _human_interaction(self, page: Page):
        await asyncio.sleep(random.uniform(1.0, 2.0))
        await page.mouse.move(random.uniform(100, 800), random.uniform(100, 600))
        await page.evaluate("window.scrollBy(0, window.innerHeight / 2);")
        await asyncio.sleep(random.uniform(1.0, 2.5))

    async def _is_blocked(self, page: Page) -> bool:
        title = (await page.title()) or ""
        if any(w in title for w in ("Giriş", "Just a moment", "Access Denied", "Erişim")):
            return True
        if await page.locator(".px-captcha-container").count() > 0:
            return True
        return await page.locator("text=Bağlantınız kontrol ediliyor").count() > 0

    async def _capture_diagnostics(self, page: Page, reason: str):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = f"logs/debug_error_{reason}_{timestamp}.png"
        html_path = f"logs/debug_page_{reason}_{timestamp}.html"
        try:
            body = await page.evaluate("document.body ? document.body.innerText : ''")
            self.last_block_note = (f"http={self.last_status}; başlık={(await page.title())[:80]!r}; "
                                    f"metin={' '.join(body.split())[:140]!r}")
            logger.warning(f"Sayfa ipucu ({reason}): {self.last_block_note}")
        except Exception:
            pass
        try:
            await page.screenshot(path=screenshot_path)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(await page.content())
            logger.info(f"Saved visual diagnostics to {screenshot_path} and HTML to {html_path}")
        except Exception as e:
            logger.error(f"Failed to capture diagnostics: {e}")

    async def _open(self, page: Page, url: str):
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self.last_status = resp.status if resp else 0
        if not await self._wait_challenge(page):
            raise BlockedError("Cloudflare doğrulaması geçilemedi")
        await self._human_interaction(page)
        if await self._is_blocked(page):
            raise BlockedError(f"Captcha/giriş sayfası: {await page.title()}")

    def source_id(self, url: str) -> str:
        return source_id_from_url(url)

    # -- genel API --------------------------------------------------------------
    async def fetch_category_links(self, category_url: str) -> list[str]:
        """Liste sayfasındaki ilan linkleri, SAYFADAKİ SIRAYLA (en yeni önce) ve tekrarsız."""
        self.last_blocked = False
        max_retries = 3
        blocked_attempts = 0
        for attempt in range(max_retries):
            proxy = await self.proxy_manager.get_proxy()
            context, page = await self._new_page(proxy.url if proxy else None)
            try:
                await self._open(page, category_url)
                hrefs = await page.evaluate(LINKS_JS)
                links = list(dict.fromkeys(h.split("#")[0] for h in hrefs))
                if not links:
                    await self._capture_diagnostics(page, "no_links")
                    raise ValueError("No links found on category page.")
                if proxy:
                    await self.proxy_manager.mark_success(proxy.id)
                await self._save_state()
                return links
            except Exception as e:
                if isinstance(e, BlockedError):
                    blocked_attempts += 1
                logger.warning(f"Category scrape attempt {attempt + 1}/{max_retries} failed for {category_url}: {e}")
                if proxy:
                    await self.proxy_manager.mark_failure(proxy.id)
                if attempt == max_retries - 1:
                    logger.error(f"Exhausted retries for category {category_url}.")
                    await self._capture_diagnostics(page, "category_final_error")
                    self.last_blocked = blocked_attempts > 0
                    return []
                backoff = (2 ** attempt) + random.uniform(1.5, 4.0)
                logger.info(f"Rotating proxy and retrying in {backoff:.2f} seconds...")
                await asyncio.sleep(backoff)
            finally:
                await context.close()
        return []

    async def fetch_listing_data(self, url: str) -> Dict[str, Any]:
        max_retries = 2
        for attempt in range(max_retries):
            proxy = await self.proxy_manager.get_proxy()
            context, page = await self._new_page(proxy.url if proxy else None)
            try:
                await self._open(page, url)
                raw = await page.evaluate(LISTING_JS)
                if not raw["title"] or not raw["priceText"]:
                    await self._capture_diagnostics(page, "missing_data")
                    raise ValueError("Failed to extract title/price.")
                data = build_listing(
                    url=url, title=raw["title"], price_text=raw["priceText"],
                    description=raw["description"], props=raw["props"], breadcrumbs=raw["breadcrumbs"],
                    max_degisen=settings.MAX_DEGISEN, max_boyali=settings.MAX_BOYALI,
                    crumbs=raw.get("crumbs") or [], location=raw.get("location") or "",
                )
                if proxy:
                    await self.proxy_manager.mark_success(proxy.id)
                return data
            except Exception as e:
                logger.warning(f"Scrape attempt {attempt + 1}/{max_retries} failed for {url}: {e}")
                if proxy:
                    await self.proxy_manager.mark_failure(proxy.id)
                if attempt == max_retries - 1:
                    logger.error(f"Exhausted retries for {url}.")
                    raise
                await asyncio.sleep((2 ** attempt) + random.uniform(1.5, 4.0))
            finally:
                await context.close()

    async def fetch_list_rows(self, list_url: str) -> list[dict]:
        """Liste sayfasındaki ilan satırları (fiyat/yıl/km/il). Karşılaştırma verisi için tek istek yeter.
        Başarısızlıkta boş liste döner (engel/yapı değişikliği karşılaştırmayı durdurur, botu değil)."""
        proxy = await self.proxy_manager.get_proxy()
        context, page = await self._new_page(proxy.url if proxy else None)
        try:
            await self._open(page, list_url)
            raw = await page.evaluate(ROWS_JS)
            rows = [r for r in (parse_list_row(x["href"], x["lines"]) for x in raw) if r]
            if proxy:
                await self.proxy_manager.mark_success(proxy.id)
            return rows
        except Exception as e:
            logger.warning(f"Liste satırları okunamadı {list_url}: {e}")
            if proxy:
                await self.proxy_manager.mark_failure(proxy.id)
            return []
        finally:
            await context.close()
