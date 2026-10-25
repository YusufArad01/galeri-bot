import asyncio

from loguru import logger

from src.benchmark import BenchConfig
from src.config import settings
from src.database import engine, Base, AsyncSessionLocal
from src.estimate import PriceService
from src.feedback import poll_forever
from src.health import CycleStats, Health
from src.maintenance import prune
from src.modes import dealer as dealer_mode
from src.modes.compare import CompareConfig
from src.notifications import Telegram
from src.pipeline import build_targets, forget_known, run_cycle
from src.proxy.manager import ProxyManager
from src.scraper.engine import ScraperEngine

_OLD_TABLES = ("price_cache", "market_stats", "opportunities")   # eski sürümlerden kalan, artık kullanılmayan tablolar


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table in _OLD_TABLES:
            await conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    logger.info("Database initialized.")


def load_dealers_safe(previous: list[dealer_mode.Dealer]) -> tuple[list[dealer_mode.Dealer], str | None]:
    """Profil dosyası bozuksa botu düşürmez: son geçerli listeyle devam eder, hatayı döner."""
    try:
        return dealer_mode.load_dealers(settings.DEALERS_JSON, settings.DEALERS_FILE), None
    except (ValueError, OSError) as exc:  # DealerConfigError ve JSONDecodeError ValueError alt sınıfı
        return previous, str(exc)


def describe_mode(dealers: list[dealer_mode.Dealer]) -> str:
    lines = [f"Mod: {settings.BOT_MODE}"]
    if "dealer" in settings.modes:
        rules = sum(len(d.rules) for d in dealers)
        lines.append(f"• Galerici: {len(dealers)} galerici, {rules} kural")
    if "compare" in settings.modes:
        lines.append(f"• Genel: benzerlerinden en az %{settings.COMPARE_MIN_DISCOUNT_PCT:g} ucuz adaylardan "
                     f"{settings.COMPARE_RELEASE_MINUTES} dk'da bir en iyi {settings.COMPARE_MAX_ITEMS}, günde en çok "
                     f"{settings.COMPARE_DAILY_MAX} ({settings.COMPARE_ACTIVE_START}:00-{settings.COMPARE_ACTIVE_END}:00)")
    if settings.PERPLEXITY_API_KEY:
        lines.append(f"• İnternet tahmini yedeği açık: arabam'da emsal yoksa ilan tahminden %{settings.ESTIMATE_MIN_DISCOUNT_PCT:g}-"
                     f"%{settings.ESTIMATE_MAX_DISCOUNT_PCT:g} ucuzsa 'tahmin' etiketiyle")
    lines.append(f"• Döngü: {settings.SCRAPE_INTERVAL_MINUTES} dk, en çok {settings.MAX_LISTINGS_PER_CYCLE} yeni ilan")
    return "\n".join(lines)


async def one_cycle(telegram: Telegram, dealers: list[dealer_mode.Dealer], dealer_error: str | None) -> CycleStats:
    stats = CycleStats()
    async with AsyncSessionLocal() as session:
        health = Health(session, telegram, settings.admin_chat,
                        settings.HEARTBEAT_HOURS, settings.BLOCKED_CYCLES_ALERT)
        if dealer_error:
            await health.notify_admin("dealer_cfg", f"⚠️ Galerici profil dosyası okunamadı, son geçerli ayarla devam ediliyor:\n{dealer_error}", cooldown_hours=12)

        targets = build_targets(settings.target_urls, dealers, settings.modes, settings.PAGES_PER_TARGET,
                                settings.DEALER_PAGES)
        if not targets:
            logger.error("TARGET_URL tanımlı değil (ve galerici kurallarında search_url yok).")
            await health.notify_admin("no_target", "⚠️ TARGET_URL tanımlı değil; taranacak sayfa yok.", cooldown_hours=12)
            return stats

        try:
            await asyncio.wait_for(
                run_cycle(
                    session=session, engine_factory=ScraperEngine, telegram=telegram,
                    dealers=dealers, targets=targets, modes=settings.modes,
                    default_chat_id=settings.TELEGRAM_CHAT_ID,
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
                        max_age_years=settings.COMPARE_MAX_AGE_YEARS,
                        max_discount_pct=settings.COMPARE_MAX_DISCOUNT_PCT,
                        min_profit_tl=settings.COMPARE_MIN_PROFIT_TL,
                        min_profit_pct=settings.COMPARE_MIN_PROFIT_PCT,
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
                        est_min_discount_pct=settings.ESTIMATE_MIN_DISCOUNT_PCT,
                        est_max_discount_pct=settings.ESTIMATE_MAX_DISCOUNT_PCT,
                    ),
                    estimator=PriceService(session, settings.PERPLEXITY_API_KEY, settings.PERPLEXITY_MODEL,
                                           settings.PERPLEXITY_CACHE_DAYS, settings.PERPLEXITY_MAX_CALLS_PER_CYCLE)
                    if settings.PERPLEXITY_API_KEY else None,
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
        await health.record_cycle(stats)
        await health.send_dealer_summaries(dealers)
        await prune(session)
    logger.info(f"Cycle stats: {stats}")
    return stats


async def main():
    logger.info(f"Starting Arbitrage Engine (mode={settings.BOT_MODE})...")
    settings.modes  # geçersiz BOT_MODE'u başlangıçta yakala
    await init_db()
    if settings.RESCAN_LISTINGS:
        async with AsyncSessionLocal() as session:
            await forget_known(session, settings.RESCAN_LISTINGS)

    telegram = Telegram(settings.TELEGRAM_BOT_TOKEN)
    if not telegram.configured or not settings.TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID eksik: bildirim GİDEMEZ.")

    async with AsyncSessionLocal() as session:
        await ProxyManager(session).seed(settings.proxy_urls)

    dealers, dealer_error = load_dealers_safe([])
    async with AsyncSessionLocal() as session:
        health = Health(session, telegram, settings.admin_chat, settings.HEARTBEAT_HOURS, settings.BLOCKED_CYCLES_ALERT)
        await health.startup(describe_mode(dealers))

    listener = asyncio.create_task(poll_forever(telegram, AsyncSessionLocal, settings.admin_chat))  # 👍/👎 ve /start
    blocked_streak = 0
    while True:
        if "dealer" in settings.modes:
            dealers, dealer_error = load_dealers_safe(dealers)
        stats = None
        try:
            stats = await one_cycle(telegram, dealers, dealer_error)
        except Exception as exc:  # son çare: döngü ne olursa olsun devam etsin
            logger.exception(f"Unhandled error outside cycle: {exc}")
        if listener.done():   # dinleme görevi beklenmedik biçimde bittiyse yeniden başlat
            listener = asyncio.create_task(poll_forever(telegram, AsyncSessionLocal, settings.admin_chat))
        # Engel varsa sıklaştırmak engeli uzatır: 15, 30, 60, en çok 120 dk bekle (engel kalkınca normal aralığa döner)
        blocked_streak = blocked_streak + 1 if stats is not None and stats.blocked and not stats.scraped else 0
        if blocked_streak >= settings.BLOCKED_ROTATE_AFTER:
            async with AsyncSessionLocal() as session:
                pm = ProxyManager(session)
                new_url = await pm.rotate_on_block()
                health = Health(session, telegram, settings.admin_chat, settings.HEARTBEAT_HOURS, settings.BLOCKED_CYCLES_ALERT)
                if new_url:
                    await health.notify_admin("proxy_rotate", f"🔄 {blocked_streak} döngüdür engel: yedek proxy'ye geçildi.", cooldown_hours=1)
                    blocked_streak = 0
                else:
                    await health.notify_admin("proxy_rotate", f"🚧 {blocked_streak} döngüdür engel, yedek proxy yok. Yeni proxy ekleyin.", cooldown_hours=6)
        wait = min(120, 15 * 2 ** (blocked_streak - 1)) if blocked_streak else settings.SCRAPE_INTERVAL_MINUTES
        logger.info(f"Cycle finished. Waiting for {wait} minutes before the next run...")
        await asyncio.sleep(wait * 60)


if __name__ == "__main__":
    asyncio.run(main())
