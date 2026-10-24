"""Tarama döngüsü: linkleri topla -> yenileri incele -> değerlendir -> kaydet -> bildir."""
from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime
from typing import Callable

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.health import CycleStats
from src.models.domain import BotState, Listing
from src.modes import dealer as dealer_mode
from src.modes import compare as compare_mode
from src.benchmark import BenchConfig, Benchmark
from src.modes import market as market_mode
from src.notifications import Telegram, enqueue_alert, flush_alerts
from src.pricing.perplexity import PriceService, PriceServiceError
from src.proxy.manager import ProxyManager

_LISTING_COLUMNS = {c.name for c in Listing.__table__.columns} - {"id", "scraped_at"}
_FATAL_PRICE_KINDS = {"no_key", "auth", "quota", "breaker"}
# Açılamayan ilan (kaldırılmış/satılmış) her turda yeniden denenip hata yazmasın: 3 başarısızlıktan sonra atlanır.
# shortcut: yalnız bellekte, yeniden başlatmada sıfırlanır (3 deneme daha yapılır); kalıcı olması gerekirse DB'ye taşı.
_SCRAPE_FAILS: dict[str, int] = {}
# Boş çıkan 2. ve sonraki sayfa (ilan sayısı o kadar sayfaya yetmiyor) 30 dk boyunca yeniden istenmez: boşuna istek/yeniden deneme olmasın.
_EMPTY_PAGES: dict[str, float] = {}
_EMPTY_PAGE_TTL = 1800
# 3. ve sonraki sayfalar derin tarama içindir (mevcut stoğu bir kez görmek); yeni ilanlar listenin başına düşer,
# bu yüzden bu sayfalar 6 saatte bir yenilenir. Çok kuralı olan galericide sayfa isteği sayısını ~yarıya indirir.
_DEEP_PAGE_TTL = 6 * 3600
_MAX_SCRAPE_FAILS = 3


def normalize_target(url: str) -> str:
    if "vava.cars" in url:
        return url if "/buy/cars" in url else "https://tr.vava.cars/buy/cars"
    if "sort=" in url:
        return url
    return url + ("&" if "?" in url else "?") + "sort=DateDesc"


def build_targets(target_urls: list[str], dealers: list[dealer_mode.Dealer], modes: set[str],
                  pages: int = 1, dealer_pages: int = 0) -> list[str]:
    """Galerici arama sayfaları ÖNCE gelir (turun ilan hakkı önce onlara harcanır), genel liste sonra.
    Galerici sayfaları daha derin taranır (dealer_pages): ilk turda mevcut stok topluca taranır,
    sonraki turlarda bilinen ilanlar atlandığı için yalnız yeniler açılır."""
    dealer_urls = dealer_mode.search_urls(dealers) if "dealer" in modes else []
    wanted = [(u, dealer_pages or pages) for u in dealer_urls] + [(u, pages) for u in target_urls]
    out: list[str] = []
    for raw, depth in wanted:
        url = normalize_target(raw)
        out.append(url)
        if "vava.cars" not in url and "page=" not in url:
            out += [f"{url}&page={n}" for n in range(2, depth + 1)]
    return list(dict.fromkeys(out))


def listing_row(data: dict) -> dict:
    row = {k: v for k, v in data.items() if k in _LISTING_COLUMNS}
    row["km"] = int(row.get("km") or 0)
    row["city"] = row.get("city") or "Bilinmiyor"
    row["description"] = row.get("description") or ""
    return row


async def forget_known(session: AsyncSession, raw: str) -> int:
    """Bir kez çalışır: başlığında verilen sözcüklerden (virgülle) biri geçen bilinen ilanları unutur ki yeni galerici
    kurallarıyla yeniden değerlendirilsin. Aynı `raw` değeri ikinci kez silme yapmaz (BotState'te işaretlenir)."""
    terms = [t.strip() for t in raw.split(",") if t.strip()]
    key = f"rescan:{raw.strip()}"
    if not terms or await session.get(BotState, key):
        return 0
    removed = 0
    for term in terms:
        res = await session.execute(delete(Listing).where(Listing.title.ilike(f"%{term}%")))
        removed += res.rowcount or 0
    session.add(BotState(key=key, value="done"))
    await session.commit()
    logger.info(f"Yeniden değerlendirme için {removed} bilinen ilan unutuldu ({raw}).")
    return removed


async def known_ids(session: AsyncSession, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    found: set[str] = set()
    for i in range(0, len(ids), 500):  # SQLite değişken limiti
        chunk = ids[i:i + 500]
        res = await session.execute(select(Listing.source_listing_id).where(Listing.source_listing_id.in_(chunk)))
        found.update(res.scalars().all())
    return found


async def process_listing(data: dict, *, session: AsyncSession, prices: PriceService,
                          stats: CycleStats, modes: set[str], dealers: list[dealer_mode.Dealer],
                          market_chat_id: str, threshold_pct: float, suspicious_pct: float,
                          bench: Benchmark | None = None,
                          compare_out: list[compare_mode.Entry] | None = None,
                          max_km: int = 0, min_year: int = 0) -> None:
    """İlanı değerlendirir, kaydeder, bildirimleri outbox'a yazar.

    Fiyat servisi hatasında ilan KAYDEDİLMEZ ve PriceServiceError yükseltilir; sonraki döngüde
    yeniden denenir (eski sürümde Perplexity hatası olan ilan kalıcı olarak 'görüldü' sayılıp
    kaybolurdu). Hata olsa bile başarıyla üretilmiş bildirimler önce kuyruğa alınır; tekrar
    denemede aynı bildirim alerts tablosundaki benzersizlik kısıtıyla mükerrer gitmez.
    """
    candidates: list[market_mode.Candidate] = []
    deferred: PriceServiceError | None = None

    if data.get("is_disqualified"):
        stats.disqualified += 1

    if "market" in modes and not data.get("is_disqualified"):
        stats.evaluated += 1
        try:
            cand = await market_mode.evaluate(data, prices, market_chat_id, threshold_pct, suspicious_pct)
            if cand:
                candidates.append(cand)
        except PriceServiceError as exc:
            deferred = exc

    if "dealer" in modes and dealers and data.get("year") and float(data["price"]) >= market_mode.MIN_VALID_PRICE:
        dealer_cands, dealer_err = await dealer_mode.evaluate(data, dealers, prices, market_chat_id, bench)
        candidates += dealer_cands
        deferred = deferred or dealer_err

    if "compare" in modes and bench is not None and compare_out is not None and not data.get("is_disqualified"):
        stats.evaluated += 1
        try:
            entry = await compare_mode.evaluate(data, bench, max_km, min_year)
            if entry:
                # her kıyas sonucu loga: günlük kaç ilan hangi eşiği geçerdi, buradan sayılır
                logger.info(f"Kıyas {data['source_listing_id']}: fark %{entry.discount_pct:.1f} "
                            f"({entry.median - float(data['price']):.0f} TL), {entry.comparables} benzer")
                compare_out.append(entry)
        except PriceServiceError as exc:  # BenchBudget de buradan geçer
            deferred = deferred or exc

    if deferred is None:
        session.add(Listing(**listing_row(data)))
    for c in candidates:
        alert = await enqueue_alert(session, source_listing_id=data["source_listing_id"], mode=c.mode,
                                    dealer_id=c.dealer_id, chat_id=c.chat_id, text=c.text, reason=c.reason)
        if alert is not None:
            stats.alerts_queued += 1
            logger.info(f"Bildirim kuyruğa alındı [{c.mode}/{c.dealer_id or '-'}]: {c.reason} -> {data['url']}")
    await session.commit()
    if deferred is not None:
        raise deferred


async def run_cycle(*, session: AsyncSession, engine_factory: Callable[[str, ProxyManager], object],
                    telegram: Telegram, prices: PriceService, dealers: list[dealer_mode.Dealer],
                    targets: list[str], modes: set[str], market_chat_id: str,
                    threshold_pct: float, suspicious_pct: float, max_listings: int,
                    jitter: tuple[float, float] = (3.0, 7.0),
                    stats: CycleStats | None = None,
                    compare: compare_mode.CompareConfig | None = None,
                    bench_cfg: BenchConfig | None = None) -> CycleStats:
    # stats dışarıdan verilirse zaman aşımında bile o ana kadarki sayaçlar kaybolmaz
    if stats is None:
        stats = CycleStats()
    prices.reset_cycle()

    # Önceki döngüde gönderilemeyen bildirimleri tekrar dene
    sent, _ = await flush_alerts(session, telegram)
    stats.alerts_sent += sent

    # Hedefleri motora göre grupla (arabam / vava)
    groups: dict[str, list[str]] = {}
    for t in targets:
        groups.setdefault("vava" if "vava.cars" in t else "arabam", []).append(t)

    first_fetch = True
    budget = max_listings
    compare_entries: list[compare_mode.Entry] = []
    compare_on = "compare" in modes and compare is not None
    min_year = datetime.now().year - compare.max_age_years if compare and compare.max_age_years else 0
    for kind, urls in groups.items():
        proxy_manager = ProxyManager(session)
        async with engine_factory(kind, proxy_manager) as scraper:
            links: list[str] = []
            for url in urls:
                if "&page=" in url and _EMPTY_PAGES.get(url, 0) > time.monotonic():
                    continue
                logger.info(f"Fetching category links from: {url}")
                got = await scraper.fetch_category_links(url)
                if "&page=" in url and not getattr(scraper, "last_blocked", False):
                    page_no = int(url.rsplit("&page=", 1)[1].split("&")[0] or 1)
                    if not got:
                        _EMPTY_PAGES[url] = time.monotonic() + _EMPTY_PAGE_TTL
                    elif page_no >= 3 and len(await known_ids(session, [scraper.source_id(g) for g in got])) == len(got):
                        # derin sayfa ancak içindeki TÜM ilanlar işlendiyse (bilinen) atlanır; işlenmemiş ilan kaldıysa
                        # her turda yeniden istenir (tur başına ilan hakkı yetmeyince geri kalanlar kaybolmasın)
                        _EMPTY_PAGES[url] = time.monotonic() + _DEEP_PAGE_TTL
                logger.info(f"Found {len(got)} listings on {url}")
                stats.links_found += len(got)
                stats.blocked = stats.blocked or getattr(scraper, "last_blocked", False)
                if not got:
                    stats.block_note = getattr(scraper, "last_block_note", "") or stats.block_note
                    if getattr(scraper, "last_blocked", False):
                        logger.warning("Site engelledi (Cloudflare): kalan sayfalar bu turda istenmiyor.")
                        break   # engelliyken kalan sayfalara istek atmak engeli uzatır ve turu zaman aşımına sokar
                links.extend(got)
            links = list(dict.fromkeys(links))

            wants_bench = compare_on or "dealer" in modes  # galerici piyasa kontrolü de aynı karşılaştırma sayfalarını kullanır
            bench = Benchmark(session, scraper, bench_cfg or BenchConfig()) \
                if wants_bench and hasattr(scraper, "fetch_list_rows") else None
            ids = {link: scraper.source_id(link) for link in links}
            known = await known_ids(session, list(ids.values()))
            new_links = [link for link in links if ids[link] not in known]
            stats.known_skipped += len(links) - len(new_links)
            logger.info(f"{len(new_links)} yeni ilan, {len(links) - len(new_links)} zaten bilinen.")

            for link in new_links[:budget]:
                if _SCRAPE_FAILS.get(link, 0) >= _MAX_SCRAPE_FAILS:
                    continue
                if not first_fetch:
                    await asyncio.sleep(random.uniform(*jitter))
                first_fetch = False
                try:
                    data = await scraper.fetch_listing_data(link)
                except Exception as exc:
                    stats.scrape_errors += 1
                    _SCRAPE_FAILS[link] = _SCRAPE_FAILS.get(link, 0) + 1
                    logger.error(f"Failed to scrape {link}: {exc}")
                    continue
                _SCRAPE_FAILS.pop(link, None)
                stats.scraped += 1
                logger.info(f"Scraped: {data['brand']} {data['model']} {data.get('trim', '')} {data['year']} "
                            f"{data['km']}km {data['price']:.0f}TL ({data['city']})")
                try:
                    await process_listing(
                        data, session=session, prices=prices, stats=stats, modes=modes, dealers=dealers,
                        market_chat_id=market_chat_id, threshold_pct=threshold_pct, suspicious_pct=suspicious_pct,
                        bench=bench, compare_out=compare_entries,
                        max_km=compare.max_km if compare else 0,
                        min_year=min_year)
                except PriceServiceError as exc:
                    await session.rollback()
                    stats.deferred += 1
                    stats.price_error_kind = exc.kind
                    sent, _ = await flush_alerts(session, telegram)
                    stats.alerts_sent += sent
                    logger.warning(f"Fiyat servisi hatası, ilan sonraki döngüye bırakıldı ({link}): {exc}")
                    if exc.kind in _FATAL_PRICE_KINDS and stats.deferred >= 3 and "dealer" not in modes:
                        logger.error("Fiyat servisi kullanılamıyor; bu döngüde kalan ilanlar atlanıyor.")
                        break
                    continue
                except Exception as exc:
                    await session.rollback()
                    stats.scrape_errors += 1
                    logger.exception(f"İlan işlenemedi {link}: {exc}")
                    continue

                sent, _ = await flush_alerts(session, telegram)
                stats.alerts_sent += sent
            budget = max(0, budget - len(new_links[:budget]))
            if bench is not None:
                stats.bench_pages += bench.fetched
                stats.bench_empty += bench.empty_pages

    if compare_on:
        await compare_mode.store_candidates(session, compare_entries, compare.min_discount_pct,
                                            compare.max_discount_pct, compare.min_profit_tl,
                                            compare.min_profit_pct)
        stats.alerts_queued += await compare_mode.release(session, compare, market_chat_id)

    sent, _ = await flush_alerts(session, telegram)
    stats.alerts_sent += sent
    stats.price_calls = prices.calls
    stats.price_failures = prices.failures
    stats.price_cache_hits = prices.cache_hits
    stats.price_error_kind = stats.price_error_kind or prices.last_error_kind
    return stats
