import asyncio

from loguru import logger

from src.config import settings
from src.database import engine, Base, AsyncSessionLocal
from src.health import CycleStats, Health
from src.modes import dealer as dealer_mode
from src.benchmark import BenchConfig
from src.modes.compare import CompareConfig
from src.notifications import Telegram
from src.pipeline import build_targets, run_cycle
from src.pricing.perplexity import PriceService
from src.proxy.manager import ProxyManager
from src.scraper.engine import ScraperEngine
from src.scraper.vavacars_engine import VavaCarsEngine


def engine_factory(kind: str, proxy_manager: ProxyManager):
    return VavaCarsEngine(proxy_manager) if kind == "vava" else ScraperEngine(proxy_manager)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all mevcut tabloya sütun eklemez: eski veritabanları için elle ekle
        cols = [row[1] for row in (await conn.exec_driver_sql("PRAGMA table_info(listings)")).fetchall()]
        if "is_disqualified" not in cols:
            await conn.exec_driver_sql("ALTER TABLE listings ADD COLUMN is_disqualified BOOLEAN NOT NULL DEFAULT 0")
    logger.info("Database initialized.")


def load_dealers_safe(previous: list[dealer_mode.Dealer]) -> tuple[list[dealer_mode.Dealer], str | None]:
    """Profil dosyası bozuksa botu düşürmez: son geçerli listeyle devam eder, hatayı döner."""
    try:
        return dealer_mode.load_dealers(settings.DEALERS_JSON, settings.DEALERS_FILE), None
    except (ValueError, OSError) as exc:  # DealerConfigError ve JSONDecodeError ValueError alt sınıfı
        return previous, str(exc)


def describe_mode(dealers: list[dealer_mode.Dealer]) -> str:
    lines = [f"Mod: {settings.BOT_MODE}"]
    if "market" in settings.modes:
        lines.append(f"• Piyasa: ortalamanın %{settings.DISCOUNT_THRESHOLD_PCT:g} altı")
    if "dealer" in settings.modes:
        rules = sum(len(d.rules) for d in dealers)
        lines.append(f"• Galerici: {len(dealers)} galerici, {rules} kural")
    if "compare" in settings.modes:
        lines.append(f"• Karşılaştırma: benzerlerinden en az %{settings.COMPARE_MIN_DISCOUNT_PCT:g} ucuz adaylardan "
                     f"{settings.COMPARE_RELEASE_MINUTES} dk'da bir en iyi {settings.COMPARE_MAX_ITEMS}, günde en çok "
                     f"{settings.COMPARE_DAILY_MAX} ({settings.COMPARE_ACTIVE_START}:00-{settings.COMPARE_ACTIVE_END}:00)")
    lines.append(f"• Döngü: {settings.SCRAPE_INTERVAL_MINUTES} dk, en çok {settings.MAX_LISTINGS_PER_CYCLE} yeni ilan")
    return "\n".join(lines)


async def one_cycle(telegram: Telegram, dealers: list[dealer_mode.Dealer], dealer_error: str | None) -> CycleStats:
    stats = CycleStats()
    async with AsyncSessionLocal() as session:
        health = Health(session, telegram, settings.admin_chat,
                        settings.HEARTBEAT_HOURS, settings.BLOCKED_CYCLES_ALERT)
        if dealer_error:
            await health.notify_admin("dealer_cfg", f"⚠️ Galerici profil dosyası okunamadı, son geçerli ayarla devam ediliyor:\n{dealer_error}", cooldown_hours=12)

        targets = build_targets(settings.target_urls, dealers, settings.modes, settings.PAGES_PER_TARGET)
        if not targets:
            logger.error("TARGET_URL tanımlı değil (ve galerici kurallarında search_url yok).")
            await health.notify_admin("no_target", "⚠️ TARGET_URL tanımlı değil; taranacak sayfa yok.", cooldown_hours=12)
            return stats

        prices = PriceService(session, settings.PERPLEXITY_API_KEY, settings.PERPLEXITY_MODEL, settings.PRICE_CACHE_DAYS)
        try:
            await asyncio.wait_for(
                run_cycle(
                    session=session, engine_factory=engine_factory, telegram=telegram, prices=prices,
                    dealers=dealers, targets=targets, modes=settings.modes,
                    market_chat_id=settings.TELEGRAM_CHAT_ID,
                    threshold_pct=settings.DISCOUNT_THRESHOLD_PCT,
                    suspicious_pct=settings.SUSPICIOUS_DISCOUNT_PCT,
                    max_listings=settings.MAX_LISTINGS_PER_CYCLE,
                    jitter=(settings.JITTER_MIN_SECONDS, settings.JITTER_MAX_SECONDS),
                    stats=stats,
                    compare=CompareConfig(
                        min_discount_pct=settings.COMPARE_MIN_DISCOUNT_PCT,
                        max_items=settings.COMPARE_MAX_ITEMS,
                        release_minutes=settings.COMPARE_RELEASE_MINUTES,
                        fresh_hours=settings.COMPARE_FRESH_HOURS,
                        daily_max=settings.COMPARE_DAILY_MAX,
                        max_km=settings.COMPARE_MAX_KM,
                        worn_age_years=settings.COMPARE_WORN_AGE_YEARS,
                        worn_km=settings.COMPARE_WORN_KM,
                        worn_max_price=settings.COMPARE_WORN_MAX_PRICE,
                        max_discount_pct=settings.COMPARE_MAX_DISCOUNT_PCT,
                        active_start=settings.COMPARE_ACTIVE_START,
                        active_end=settings.COMPARE_ACTIVE_END,
                    ),
                    bench_cfg=BenchConfig(
                        min_comparables=settings.COMPARE_MIN_COMPARABLES,
                        levels=settings.COMPARE_LEVELS,
                        year_tolerance=settings.COMPARE_YEAR_TOLERANCE,
                        km_tolerance_pct=settings.COMPARE_KM_TOLERANCE_PCT,
                        ttl_hours=settings.BENCH_TTL_HOURS,
                        max_fetch_per_cycle=settings.BENCH_MAX_FETCH_PER_CYCLE,
                    ),
                ),
                timeout=settings.CYCLE_TIMEOUT_MINUTES * 60,
            )
        except asyncio.TimeoutError:
            logger.error(f"Cycle timed out after {settings.CYCLE_TIMEOUT_MINUTES} minutes; aborted.")
            stats.timed_out = True
            await session.rollback()
        except Exception as exc:
            logger.exception(f"Fatal error in scraper cycle: {exc}")
            stats.scrape_errors += 1
            await session.rollback()
            await health.notify_admin("fatal", f"❌ Tarama döngüsünde beklenmeyen hata: {type(exc).__name__}: {str(exc)[:200]}", cooldown_hours=3)
        stats.price_calls = prices.calls
        stats.price_failures = prices.failures
        stats.price_error_kind = stats.price_error_kind or prices.last_error_kind
        await health.record_cycle(stats)
    logger.info(f"Cycle stats: {stats}")
    return stats


async def main():
    logger.info(f"Starting Arbitrage Engine (mode={settings.BOT_MODE})...")
    settings.modes  # geçersiz BOT_MODE'u başlangıçta yakala
    await init_db()

    telegram = Telegram(settings.TELEGRAM_BOT_TOKEN)
    if not telegram.configured or not settings.TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID eksik: bildirim GİDEMEZ.")

    async with AsyncSessionLocal() as session:
        await ProxyManager(session).seed(settings.proxy_urls)

    dealers, dealer_error = load_dealers_safe([])
    async with AsyncSessionLocal() as session:
        health = Health(session, telegram, settings.admin_chat, settings.HEARTBEAT_HOURS, settings.BLOCKED_CYCLES_ALERT)
        await health.startup(describe_mode(dealers))

    while True:
        if "dealer" in settings.modes:
            dealers, dealer_error = load_dealers_safe(dealers)
        try:
            await one_cycle(telegram, dealers, dealer_error)
        except Exception as exc:  # son çare: döngü ne olursa olsun devam etsin
            logger.exception(f"Unhandled error outside cycle: {exc}")
        logger.info(f"Cycle finished. Waiting for {settings.SCRAPE_INTERVAL_MINUTES} minutes before the next run...")
        await asyncio.sleep(settings.SCRAPE_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    asyncio.run(main())
