"""Sağlık izleme: sessizlik "fırsat yok" mu, "bot bozuk" mu ayırt edilebilsin.

- Başlangıç mesajı, günlük özet (heartbeat)
- Ardışık "0 ilan / engellendi" döngülerinde uyarı, düzelince bildirim
- Perplexity hatalarında (anahtar yok, yetkisiz, bakiye, limit) uyarı
- Aynı türde uyarı soğuma süresiyle tekrar edilir (spam yok)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.domain import BotState
from src.notifications import Telegram

PRICE_HELP = {
    "no_key": "PERPLEXITY_API_KEY tanımlı değil (Railway > Variables).",
    "auth": "Perplexity API anahtarı geçersiz veya yetkisiz.",
    "quota": "Perplexity bakiyesi/kotası bitmiş.",
    "rate": "Perplexity istek limiti aşıldı (429).",
    "http": "Perplexity sunucu/HTTP hatası.",
    "network": "Perplexity'ye bağlanılamadı.",
    "breaker": "Perplexity art arda hata verdi, çağrılar durduruldu.",
}


@dataclass
class CycleStats:
    links_found: int = 0
    known_skipped: int = 0
    scraped: int = 0
    scrape_errors: int = 0
    disqualified: int = 0
    evaluated: int = 0
    alerts_queued: int = 0
    alerts_sent: int = 0
    price_calls: int = 0
    price_failures: int = 0
    price_cache_hits: int = 0
    price_error_kind: str | None = None
    deferred: int = 0  # fiyat servisi hatası yüzünden sonraya bırakılan ilanlar
    bench_pages: int = 0   # bu döngüde açılan karşılaştırma sayfası
    bench_empty: int = 0   # boş gelen karşılaştırma sayfası
    blocked: bool = False
    block_note: str = ""  # engelde siteden gelen sayfanın başlığı/metni (teşhis için)
    timed_out: bool = False


@dataclass
class Totals:
    since: str = ""
    cycles: int = 0
    links_found: int = 0
    scraped: int = 0
    disqualified: int = 0
    evaluated: int = 0
    alerts_queued: int = 0
    alerts_sent: int = 0
    price_calls: int = 0
    price_failures: int = 0
    blocked_cycles: int = 0
    errors: int = 0


class Health:
    def __init__(self, session: AsyncSession, telegram: Telegram, admin_chat: str,
                 heartbeat_hours: int = 24, blocked_alert_after: int = 2):
        self.session = session
        self.telegram = telegram
        self.admin_chat = admin_chat
        self.heartbeat = timedelta(hours=heartbeat_hours)
        self.blocked_alert_after = blocked_alert_after

    # --- anahtar/değer deposu -------------------------------------------------
    async def _get(self, key: str, default: str = "") -> str:
        row = (await self.session.execute(select(BotState).where(BotState.key == key))).scalars().first()
        return row.value if row else default

    async def _set(self, key: str, value: str) -> None:
        row = (await self.session.execute(select(BotState).where(BotState.key == key))).scalars().first()
        if row:
            row.value = value
        else:
            self.session.add(BotState(key=key, value=value))
        await self.session.commit()

    # --- uyarı gönderimi ------------------------------------------------------
    async def notify_admin(self, kind: str, text: str, cooldown_hours: float = 6.0) -> bool:
        """Soğuma süresi dolmadıysa göndermez. Gönderim denendiyse True."""
        last_raw = await self._get(f"alert_last:{kind}")
        if last_raw:
            try:
                if datetime.utcnow() - datetime.fromisoformat(last_raw) < timedelta(hours=cooldown_hours):
                    return False
            except ValueError:
                pass
        ok = await self.telegram.send(self.admin_chat, text)
        if ok:
            await self._set(f"alert_last:{kind}", datetime.utcnow().isoformat())
        return ok

    async def startup(self, mode_text: str) -> None:
        await self.notify_admin("startup", f"🟢 Arbitraj botu başladı\n{mode_text}", cooldown_hours=1)

    # --- döngü sonu -----------------------------------------------------------
    async def record_cycle(self, stats: CycleStats) -> None:
        totals = await self._load_totals()
        totals.cycles += 1
        totals.links_found += stats.links_found
        totals.scraped += stats.scraped
        totals.disqualified += stats.disqualified
        totals.evaluated += stats.evaluated
        totals.alerts_queued += stats.alerts_queued
        totals.alerts_sent += stats.alerts_sent
        totals.price_calls += stats.price_calls
        totals.price_failures += stats.price_failures
        totals.errors += stats.scrape_errors
        if stats.blocked:
            totals.blocked_cycles += 1

        await self._check_blocked(stats)
        await self._check_price_service(stats)
        if stats.timed_out:
            await self.notify_admin("timeout", "⏱️ Tarama döngüsü zaman aşımına uğradı ve iptal edildi. Sonraki döngüde tekrar denenecek.")

        if datetime.utcnow() - datetime.fromisoformat(totals.since) >= self.heartbeat:
            await self._send_heartbeat(totals)
            totals = Totals(since=datetime.utcnow().isoformat())
        await self._set("totals", json.dumps(asdict(totals)))

    async def _load_totals(self) -> Totals:
        raw = await self._get("totals")
        if raw:
            try:
                return Totals(**json.loads(raw))
            except (TypeError, ValueError):
                pass
        return Totals(since=datetime.utcnow().isoformat())

    async def _check_blocked(self, stats: CycleStats) -> None:
        streak = int(await self._get("blocked_streak", "0") or 0)
        if stats.blocked or (stats.links_found == 0 and not stats.timed_out):
            streak += 1
            await self._set("blocked_streak", str(streak))
            if streak >= self.blocked_alert_after:
                await self.notify_admin(
                    "blocked",
                    f"🚧 Son {streak} döngüdür siteden ilan alınamıyor (engel/captcha ya da sayfa yapısı değişmiş olabilir).\n"
                    "Çözüm: PROXY_URL ile konut/mobil proxy ekleyin veya sayfa seçicilerini kontrol edin. "
                    "Bu sürede fırsat mesajı gelmemesi normaldir."
                    + (f"\nSayfadan gelen ipucu: {stats.block_note}" if stats.block_note else ""),
                    cooldown_hours=6,
                )
        elif streak:
            await self._set("blocked_streak", "0")
            if streak >= self.blocked_alert_after:
                await self.telegram.send(self.admin_chat, "✅ Site erişimi düzeldi, taramaya devam ediliyor.")

    async def _check_price_service(self, stats: CycleStats) -> None:
        kind = stats.price_error_kind
        if kind and (stats.price_failures >= 3 or kind in ("no_key", "auth", "quota")):
            await self.notify_admin(
                f"price:{kind}",
                f"💸 Piyasa fiyatı servisi sorunlu: {PRICE_HELP.get(kind, kind)}\n"
                f"Bu döngüde {stats.deferred} ilan değerlendirilemedi; düzelince otomatik yeniden denenecek.",
                cooldown_hours=6,
            )

    async def _send_heartbeat(self, t: Totals) -> None:
        text = (
            "📊 Günlük özet\n"
            f"• {t.cycles} döngü, {t.links_found} ilan görüldü, {t.scraped} yeni ilan incelendi\n"
            f"• {t.disqualified} ilan hasar/yaş filtresine takıldı, {t.evaluated} ilan fiyat kontrolünden geçti\n"
            f"• 🔔 {t.alerts_sent} bildirim gönderildi ({t.alerts_queued} oluştu)\n"
            f"• Perplexity: {t.price_calls} çağrı, {t.price_failures} hata\n"
            f"• Engellenen döngü: {t.blocked_cycles}, hata: {t.errors}\n"
            "Bildirim gelmiyorsa ve burada hata yoksa: o sürede eşiği geçen ilan çıkmamıştır."
        )
        if not await self.telegram.send(self.admin_chat, text):
            logger.warning("Heartbeat gönderilemedi")
