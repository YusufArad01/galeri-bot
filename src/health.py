"""Sağlık izleme: sessizlik "fırsat yok" mu, "bot bozuk" mu ayırt edilebilsin.

- Başlangıç mesajı, günlük yönetici özeti (heartbeat)
- Ardışık "0 ilan / engellendi" döngülerinde uyarı, düzelince bildirim
- İlan sayfası yapısı değişirse (ekspertiz bölümü okunamıyor) uyarı
- Galericilere günlük kısa özet ("bugün size N ilan gönderdim")
- Aynı türde uyarı soğuma süresiyle tekrar edilir (spam yok)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.domain import Alert, BotState, Feedback
from src.notifications import Telegram, notify_once


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
    deferred: int = 0        # emsal sayfası hakkı dolduğu için sonraya bırakılan ilanlar
    bench_pages: int = 0     # bu döngüde açılan karşılaştırma sayfası
    bench_empty: int = 0     # boş gelen karşılaştırma sayfası
    expertise_missing: int = 0   # ekspertiz bölümü okunamayan ilan (sayfa yapısı değişti göstergesi)
    blocked: bool = False
    block_note: str = ""     # engelde siteden gelen sayfanın başlığı/metni (teşhis için)
    timed_out: bool = False


@dataclass
class Totals:
    since: str = ""
    cycles: int = 0
    links_found: int = 0
    scraped: int = 0
    evaluated: int = 0
    alerts_queued: int = 0
    alerts_sent: int = 0
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
        return await notify_once(self.session, self.telegram, self.admin_chat, kind, text, cooldown_hours)

    async def startup(self, mode_text: str) -> None:
        await self.notify_admin("startup", f"🟢 Arbitraj botu başladı\n{mode_text}", cooldown_hours=1)

    # --- döngü sonu -----------------------------------------------------------
    async def record_cycle(self, stats: CycleStats) -> None:
        totals = await self._load_totals()
        totals.cycles += 1
        totals.links_found += stats.links_found
        totals.scraped += stats.scraped
        totals.evaluated += stats.evaluated
        totals.alerts_queued += stats.alerts_queued
        totals.alerts_sent += stats.alerts_sent
        totals.errors += stats.scrape_errors
        if stats.blocked:
            totals.blocked_cycles += 1

        await self._check_blocked(stats)
        await self._check_page_structure(stats)
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
                    "Çözüm: yeni/yedek proxy IP'si ekleyin ya da sayfa seçicilerini kontrol edin. "
                    "Bu sürede fırsat mesajı gelmemesi normaldir."
                    + (f"\nSayfadan gelen ipucu: {stats.block_note}" if stats.block_note else ""),
                    cooldown_hours=6,
                )
        elif streak:
            await self._set("blocked_streak", "0")
            if streak >= self.blocked_alert_after:
                await self.telegram.send(self.admin_chat, "✅ Site erişimi düzeldi, taramaya devam ediliyor.")

    async def _check_page_structure(self, stats: CycleStats) -> None:
        if stats.scraped >= 10 and stats.expertise_missing * 2 >= stats.scraped:
            await self.notify_admin(
                "page_structure",
                f"🧩 Bu turda incelenen {stats.scraped} ilanın {stats.expertise_missing} tanesinde ekspertiz bölümü okunamadı: "
                "arabam sayfa yapısı değişmiş olabilir. Galerici kuralları bu yüzden ilanları eleyebilir.",
                cooldown_hours=12)

    async def _send_heartbeat(self, t: Totals) -> None:
        since = datetime.fromisoformat(t.since)
        votes = dict((await self.session.execute(
            select(Feedback.vote, func.count()).where(Feedback.created_at >= since).group_by(Feedback.vote))).all())
        text = (
            "📊 Günlük özet\n"
            f"• {t.cycles} tur, {t.scraped} yeni ilan incelendi\n"
            f"• 🔔 {t.alerts_sent} mesaj gönderildi ({t.alerts_queued} oluştu)\n"
            f"• Geri bildirim: 👍 {votes.get(1, 0)} / 👎 {votes.get(0, 0)}\n"
            f"• Engellenen tur: {t.blocked_cycles}, hata: {t.errors}\n"
            "Mesaj gelmiyorsa ve burada hata yoksa: o sürede uygun ilan çıkmamıştır."
        )
        if not await self.telegram.send(self.admin_chat, text):
            logger.warning("Heartbeat gönderilemedi")

    # --- galericilere günlük kısa özet ----------------------------------------
    async def send_dealer_summaries(self, dealers: list, now: datetime | None = None) -> int:
        """DEALER_SUMMARY_HOUR (Türkiye saati) içinde günde bir kez: "bugün size N ilan gönderdim, K tanesi güçlü"."""
        now = now or datetime.utcnow()
        tr = now + timedelta(hours=3)
        day = tr.date().isoformat()
        if tr.hour != settings.DEALER_SUMMARY_HOUR or await self._get("dealer_summary_day") == day:
            return 0
        day_start = datetime(tr.year, tr.month, tr.day) - timedelta(hours=3)   # UTC
        count = 0
        for d in dealers:
            if not d.chat_id:
                continue
            reasons = (await self.session.execute(
                select(Alert.reason).where(Alert.dealer_id == d.id, Alert.status == "SENT",
                                           Alert.sent_at >= day_start))).scalars().all()
            n, strong = len(reasons), sum(r.startswith("★") for r in reasons)
            if not n:
                text = "📊 Bugün şartlarınıza uyan yeni ilan çıkmadı. Takipteyim."
            elif strong:
                text = f"📊 Bugün size {n} ilan gönderdim; {strong} tanesi güçlü fırsat görünüyor."
            else:
                text = f"📊 Bugün size {n} ilan gönderdim; güçlü fırsat çıkmadı."
            count += await self.telegram.send(d.chat_id, text)
        await self._set("dealer_summary_day", day)
        return count
